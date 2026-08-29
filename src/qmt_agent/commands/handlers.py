import asyncio
import logging
import uuid
from dataclasses import dataclass

from agents import SQLiteSession

from qmt_agent.agents import CompactionResult, session_history_restore_failed
from qmt_agent.config import PERMISSION_MODES, REASONING_EFFORTS
from qmt_agent.context import AppState
from qmt_agent.journal import SessionJournal
from qmt_agent.runtime import AgentRuntime, SessionBusyError
from qmt_agent.storage import (
    SessionForkError,
    SessionForkRollbackError,
    create_session,
    delete_session_metadata,
    find_session_ids,
    fork_session,
    get_session_branch_from,
    get_session_title,
    list_sessions,
    set_session_title,
)
from qmt_agent.tools import format_background_jobs, list_background_jobs

from .parser import Command

logger = logging.getLogger(__name__)

HELP = (
    "Commands:\n"
    "  /help              Show this help.\n"
    "  /session           Show the current session.\n"
    "  /new               Start a new session.\n"
    "  /resume [prefix]   List or resume a session.\n"
    "  /fork              Fork the current session.\n"
    "  /title [title]     Show or set the session title.\n"
    "  /effort [level]    Show or set Main reasoning effort.\n"
    "  /permission [mode] Show or set tool permission mode.\n"
    "  /compact           Compact the current Agent context.\n"
    "  /clear             Clear the current session.\n"
    "  /ps                Show background commands.\n"
    "  /exit              Exit QMT Agent."
)


@dataclass(frozen=True)
class CommandResult:
    output: str | None = None
    exit_requested: bool = False
    compaction: CompactionResult | None = None


async def dispatch_command(
    command: Command,
    state: AppState,
    *,
    runtime: AgentRuntime,
    journal: SessionJournal,
) -> CommandResult:
    match command.name:
        case "session":
            title, branch_from = await asyncio.gather(
                asyncio.to_thread(
                    get_session_title,
                    state.config.sessions_db,
                    state.selected_session_id,
                ),
                asyncio.to_thread(
                    get_session_branch_from,
                    state.config.sessions_db,
                    state.selected_session_id,
                ),
            )
            lines = [f"Current session ID: {state.selected_session_id}"]
            if title:
                lines.append(f"Session title: {title}")
            if branch_from:
                lines.append(f"Branched from: {branch_from}")
            return CommandResult("\n".join(lines))

        case "new":
            session_id = uuid.uuid4().hex
            await asyncio.to_thread(create_session, state.config.sessions_db, session_id)
            state.selected_session_id = session_id
            logger.info("Started session %s", session_id)
            return CommandResult(f"Started new session: {session_id}")

        case "resume":
            if not command.args:
                sessions = await asyncio.to_thread(list_sessions, state.config.sessions_db)
                lines = ["Available sessions:"]
                for record in sessions:
                    marker = "*" if record.session_id == state.selected_session_id else " "
                    title = record.title or "(untitled)"
                    lines.append(f"{marker} {record.session_id[:8]} {title}, (updated: {record.updated_at}, created: {record.created_at})")
                return CommandResult("\n".join(lines))

            session_id = command.args[0]
            matches = await asyncio.to_thread(find_session_ids, state.config.sessions_db, session_id)

            if not matches:
                return CommandResult(f"Session ID {session_id} not found.")

            if len(matches) > 1:
                lines = [f"Multiple sessions found with prefix {session_id}:"]
                lines.extend(f"  {match}" for match in matches)
                return CommandResult("\n".join(lines))

            session_id = matches[0]
            if session_id == state.selected_session_id:
                return CommandResult()

            state.selected_session_id = session_id
            logger.info("Resumed session %s", session_id)
            title = await asyncio.to_thread(get_session_title, state.config.sessions_db, session_id)
            lines = [f"Resumed session: {session_id}"]
            if title:
                lines.append(f"Session title: {title}")
            return CommandResult("\n".join(lines))

        case "fork":
            if command.args:
                return CommandResult("Usage: /fork")

            source_session_id = state.selected_session_id
            target_session_id = uuid.uuid4().hex
            try:
                async with runtime.reserve_session(source_session_id):
                    await fork_session(
                        source_session_id=source_session_id,
                        target_session_id=target_session_id,
                        sessions_db=state.config.sessions_db,
                        journal=journal,
                    )
            except SessionBusyError:
                return CommandResult(
                    "Cannot fork this session while it has an active operation."
                )
            except SessionForkRollbackError:
                logger.exception(
                    "Session fork failed with incomplete cleanup source=%s target=%s",
                    source_session_id,
                    target_session_id,
                )
                return CommandResult(
                    "Session fork failed and partial fork cleanup may be incomplete. See the system log."
                )
            except SessionForkError:
                logger.exception(
                    "Session fork failed source=%s target=%s",
                    source_session_id,
                    target_session_id,
                )
                return CommandResult("Session fork failed. See the system log.")

            state.selected_session_id = target_session_id
            return CommandResult(
                f"Forked session {source_session_id[:8]} -> {target_session_id[:8]}."
            )

        case "title":
            if not command.args:
                title = await asyncio.to_thread(
                    get_session_title,
                    state.config.sessions_db,
                    state.selected_session_id,
                )
                return CommandResult(f"Session title: {title}" if title else "Session has no title.")

            title = " ".join(command.args).strip()
            await asyncio.to_thread(
                set_session_title,
                state.config.sessions_db,
                state.selected_session_id,
                title,
            )
            logger.info("Updated title for session %s", state.selected_session_id)
            return CommandResult(f"Set session title to: {title}")

        case "effort":
            if not command.args:
                return CommandResult(f"Main reasoning effort: {state.main_reasoning_effort}")
            if len(command.args) != 1:
                return CommandResult("Usage: /effort [none|minimal|low|medium|high|xhigh|max]")

            effort = command.args[0].lower()
            if effort not in REASONING_EFFORTS:
                return CommandResult(f"Unsupported reasoning effort: {command.args[0]}")

            state.main_reasoning_effort = effort
            return CommandResult(f"Main reasoning effort set to: {effort}")

        case "permission":
            if not command.args:
                return CommandResult(f"Permission mode: {state.permission_mode}")
            if len(command.args) != 1:
                return CommandResult("Usage: /permission [manual|review]")

            mode = command.args[0].lower()
            if mode not in PERMISSION_MODES:
                return CommandResult(f"Unsupported permission mode: {command.args[0]}")

            state.permission_mode = mode
            return CommandResult(f"Permission mode set to: {mode}")

        case "clear":
            session_id = state.selected_session_id
            try:
                async with runtime.reserve_session(session_id):
                    session = SQLiteSession(session_id, state.config.sessions_db)
                    try:
                        await session.clear_session()
                    finally:
                        session.close()
                    await asyncio.to_thread(
                        delete_session_metadata,
                        state.config.sessions_db,
                        session_id,
                    )
                    new_session_id = uuid.uuid4().hex
                    await asyncio.to_thread(
                        create_session,
                        state.config.sessions_db,
                        new_session_id,
                    )
            except SessionBusyError:
                return CommandResult(
                    "Cannot clear this session while it has an active operation."
                )
            state.selected_session_id = new_session_id
            logger.info("Cleared session %s and started session %s", session_id, new_session_id)
            return CommandResult(f"Cleared session and started new session: {new_session_id}")

        case "compact":
            if command.args:
                return CommandResult("Usage: /compact")
            session_id = state.selected_session_id
            try:
                result = await runtime.compact_session(session_id)
            except SessionBusyError:
                return CommandResult(
                    "Cannot compact this session while it has an active operation."
                )
            except BaseException as exc:
                if session_history_restore_failed(exc):
                    logger.exception("Manual context compaction failed and session history restoration was unsuccessful")
                    return CommandResult("Context compaction failed and context storage may be damaged. Stop this session and see the system log.")
                if not isinstance(exc, Exception):
                    raise
                logger.exception("Manual context compaction failed; existing context was kept")
                return CommandResult("Context compaction failed; existing context was kept. See the system log.")
            if not result.changed:
                return CommandResult("Session context is already empty or compacted.")
            logger.info("Context compaction completed: trigger=manual session=%s", session_id)
            return CommandResult("Session context compacted.", compaction=result)

        case "ps":
            jobs = await list_background_jobs(state.execution)
            return CommandResult(format_background_jobs(jobs))

        case "exit":
            if runtime.has_active_runs():
                return CommandResult("There are active Agent runs. Switch to them or wait for completion before exiting.")
            logger.info("Exit requested")
            return CommandResult(output="Exiting...", exit_requested=True)

        case "help":
            return CommandResult(HELP)

        case _:
            return CommandResult(f"Unknown command: /{command.name}. For help, type /help.")
