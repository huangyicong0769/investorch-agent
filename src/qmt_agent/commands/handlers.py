import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agents import SQLiteSession

from qmt_agent.agents import CompactionResult, SessionHistoryRestoreError
from qmt_agent.config import PERMISSION_MODES, REASONING_EFFORTS
from qmt_agent.context import AppState
from qmt_agent.storage import (
    delete_session_metadata,
    find_session_ids,
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


CompactHandler = Callable[[SQLiteSession], Awaitable[CompactionResult]]


async def dispatch_command(command: Command, state: AppState, *, compact_handler: CompactHandler | None = None) -> CommandResult:
    match command.name:
        case "session":
            title = await asyncio.to_thread(
                get_session_title,
                state.config.sessions_db,
                state.session.session_id,
            )
            lines = [f"Current session ID: {state.session.session_id}"]
            if title:
                lines.append(f"Session title: {title}")
            return CommandResult("\n".join(lines))

        case "new":
            state.session.close()
            session_id = uuid.uuid4().hex
            state.session = SQLiteSession(session_id, state.config.sessions_db)
            logger.info("Started session %s", session_id)
            return CommandResult(f"Started new session: {session_id}")

        case "resume":
            if not command.args:
                sessions = await asyncio.to_thread(list_sessions, state.config.sessions_db)
                lines = ["Available sessions:"]
                for record in sessions:
                    marker = "*" if record.session_id == state.session.session_id else " "
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
            if session_id == state.session.session_id:
                return CommandResult()

            state.session.close()
            state.session = SQLiteSession(session_id, state.config.sessions_db)
            logger.info("Resumed session %s", session_id)
            title = await asyncio.to_thread(get_session_title, state.config.sessions_db, session_id)
            lines = [f"Resumed session: {session_id}"]
            if title:
                lines.append(f"Session title: {title}")
            return CommandResult("\n".join(lines))

        case "title":
            if not command.args:
                title = await asyncio.to_thread(
                    get_session_title,
                    state.config.sessions_db,
                    state.session.session_id,
                )
                return CommandResult(f"Session title: {title}" if title else "Session has no title.")

            title = " ".join(command.args).strip()
            await asyncio.to_thread(
                set_session_title,
                state.config.sessions_db,
                state.session.session_id,
                title,
            )
            logger.info("Updated title for session %s", state.session.session_id)
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
            session_id = state.session.session_id
            await state.session.clear_session()
            await asyncio.to_thread(delete_session_metadata, state.config.sessions_db, session_id)
            state.session.close()
            new_session_id = uuid.uuid4().hex
            state.session = SQLiteSession(new_session_id, state.config.sessions_db)
            logger.info("Cleared session %s and started session %s", session_id, new_session_id)
            return CommandResult(f"Cleared session and started new session: {new_session_id}")

        case "compact":
            if command.args:
                return CommandResult("Usage: /compact")
            if compact_handler is None:
                return CommandResult("Context compaction is unavailable in this runtime.")
            try:
                result = await compact_handler(state.session)
            except SessionHistoryRestoreError:
                logger.exception("Manual context compaction failed and session history restoration was unsuccessful")
                return CommandResult("Context compaction failed and context storage may be damaged. Stop this session and see the system log.")
            except Exception:
                logger.exception("Manual context compaction failed; existing context was kept")
                return CommandResult("Context compaction failed; existing context was kept. See the system log.")
            if not result.changed:
                return CommandResult("Session context is already empty or compacted.")
            logger.info("Context compaction completed: trigger=manual session=%s", state.session.session_id)
            return CommandResult("Session context compacted.", compaction=result)

        case "ps":
            jobs = await list_background_jobs(state.execution)
            return CommandResult(format_background_jobs(jobs))

        case "exit":
            logger.info("Exit requested")
            return CommandResult(output="Exiting...", exit_requested=True)

        case "help":
            return CommandResult(HELP)

        case _:
            return CommandResult(f"Unknown command: /{command.name}. For help, type /help.")
