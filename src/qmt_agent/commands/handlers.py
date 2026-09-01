import asyncio
import logging
from dataclasses import dataclass

from qmt_agent.agents import CompactionResult
from qmt_agent.application import (
    SessionAlreadyArchivedError,
    SessionArchivedError,
    SessionCompactionError,
    SessionHasQueuedInputsError,
    SessionNotFoundError,
    SessionOperations,
)
from qmt_agent.config import FOLLOW_UP_BEHAVIORS, PERMISSION_MODES, REASONING_EFFORTS
from qmt_agent.context import AppState
from qmt_agent.runtime import AgentRuntime, SessionBusyError
from qmt_agent.storage import (
    SessionForkError,
    SessionForkRollbackError,
    find_session_ids,
    get_session_branch_from,
    get_session_title,
    list_archived_sessions,
    list_sessions,
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
    "  /archive           Archive the current session.\n"
    "  /unarchive [prefix] List or restore an archived session.\n"
    "  /fork              Fork the current session.\n"
    "  /title [title]     Show or set the session title.\n"
    "  /effort [level]    Show or set Main reasoning effort.\n"
    "  /permission [mode] Show or set tool permission mode.\n"
    "  /followup [mode]   Show or set follow-up behavior.\n"
    "  /stop              Stop the current Agent run.\n"
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
    command: Command, state: AppState, *, runtime: AgentRuntime, sessions: SessionOperations
) -> CommandResult:
    match command.name:
        case "session":
            title, branch_from = await asyncio.gather(
                asyncio.to_thread(get_session_title, state.config.sessions_db, state.selected_session_id),
                asyncio.to_thread(get_session_branch_from, state.config.sessions_db, state.selected_session_id),
            )
            lines = [f"Current session ID: {state.selected_session_id}"]
            if title:
                lines.append(f"Session title: {title}")
            if branch_from:
                lines.append(f"Branched from: {branch_from}")
            return CommandResult("\n".join(lines))

        case "new":
            previous_session_id = state.selected_session_id
            session_id = await sessions.create()
            state.selected_session_id = session_id
            await sessions.discard_if_unused(previous_session_id)
            return CommandResult(f"Started new session: {session_id}")

        case "resume":
            if not command.args:
                sessions = await asyncio.to_thread(list_sessions, state.config.sessions_db)
                lines = ["Available sessions:"]
                for record in sessions:
                    marker = "*" if record.session_id == state.selected_session_id else " "
                    title = record.title or "(untitled)"
                    lines.append(
                        f"{marker} {record.session_id[:8]} {title}, (updated: {record.updated_at}, created: {record.created_at})"
                    )
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

            previous_session_id = state.selected_session_id
            state.selected_session_id = session_id
            await sessions.discard_if_unused(previous_session_id)
            logger.info("Resumed session %s", session_id)
            title = await asyncio.to_thread(get_session_title, state.config.sessions_db, session_id)
            lines = [f"Resumed session: {session_id}"]
            if title:
                lines.append(f"Session title: {title}")
            return CommandResult("\n".join(lines))

        case "archive":
            if command.args:
                return CommandResult("Usage: /archive")

            session_id = state.selected_session_id
            try:
                await sessions.archive(session_id)
            except SessionHasQueuedInputsError:
                return CommandResult("Cannot archive a session with queued follow-ups.\nFinish or clear them first.")
            except SessionAlreadyArchivedError:
                return CommandResult("This session is already archived.")
            except SessionBusyError:
                return CommandResult("Cannot archive this session while it has an active operation.")
            return CommandResult(f"Session archived: {session_id}")

        case "unarchive":
            if len(command.args) > 1:
                return CommandResult("Usage: /unarchive [prefix]")

            archived_sessions = await asyncio.to_thread(list_archived_sessions, state.config.sessions_db)
            if not command.args:
                if not archived_sessions:
                    return CommandResult("No archived sessions.")
                lines = ["Archived sessions:"]
                for record in archived_sessions:
                    title = record.title or "(untitled)"
                    lines.append(f"  {record.session_id[:8]} {title}, (archived: {record.archived_at})")
                return CommandResult("\n".join(lines))

            session_id_prefix = command.args[0]
            matches = [record for record in archived_sessions if record.session_id.startswith(session_id_prefix)]
            if not matches:
                return CommandResult(f"Archived session ID {session_id_prefix} not found.")
            if len(matches) > 1:
                lines = [f"Multiple archived sessions found with prefix {session_id_prefix}:"]
                lines.extend(f"  {record.session_id}" for record in matches)
                return CommandResult("\n".join(lines))

            session_id = matches[0].session_id
            await sessions.unarchive(session_id)
            previous_session_id = state.selected_session_id
            state.selected_session_id = session_id
            await sessions.discard_if_unused(previous_session_id)
            return CommandResult(f"Session unarchived: {session_id}")

        case "fork":
            if command.args:
                return CommandResult("Usage: /fork")

            source_session_id = state.selected_session_id
            try:
                target_session_id = await sessions.fork(source_session_id)
            except SessionHasQueuedInputsError:
                return CommandResult("Cannot fork a session with queued follow-ups.\nFinish or clear them first.")
            except SessionBusyError:
                return CommandResult("Cannot fork this session while it has an active operation.")
            except SessionForkRollbackError:
                logger.exception("Session fork failed with incomplete cleanup source=%s", source_session_id)
                return CommandResult(
                    "Session fork failed and partial fork cleanup may be incomplete. See the system log."
                )
            except SessionForkError:
                logger.exception("Session fork failed source=%s", source_session_id)
                return CommandResult("Session fork failed. See the system log.")
            except Exception:
                logger.exception("Unexpected session fork failure source=%s", source_session_id)
                return CommandResult("Session fork failed. See the system log.")

            state.selected_session_id = target_session_id
            await sessions.discard_if_unused(source_session_id)
            return CommandResult(f"Forked session {source_session_id[:8]} -> {target_session_id[:8]}.")

        case "title":
            if not command.args:
                title = await asyncio.to_thread(get_session_title, state.config.sessions_db, state.selected_session_id)
                return CommandResult(f"Session title: {title}" if title else "Session has no title.")

            title = " ".join(command.args).strip()
            try:
                await sessions.set_title(state.selected_session_id, title)
            except SessionArchivedError:
                return CommandResult("Archived sessions are read-only. Unarchive or switch sessions first.")
            except SessionNotFoundError:
                return CommandResult("Session not found.")
            except SessionBusyError:
                return CommandResult("Cannot update this session while it has an active operation.")
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

        case "followup":
            if not command.args:
                return CommandResult(f"Follow-up behavior: {state.follow_up_behavior}")
            if len(command.args) != 1:
                return CommandResult("Usage: /followup [steer|queue]")

            behavior = command.args[0].lower()
            if behavior not in FOLLOW_UP_BEHAVIORS:
                return CommandResult(f"Unsupported follow-up behavior: {command.args[0]}")

            state.follow_up_behavior = behavior
            return CommandResult(f"Follow-up behavior set to: {behavior}")

        case "stop":
            if command.args:
                return CommandResult("Usage: /stop")
            session_id = state.selected_session_id
            queued = runtime.has_queued_inputs(session_id)
            try:
                active_run = runtime.cancel_run(session_id)
            except SessionBusyError:
                return CommandResult("No active Run in this session.")

            lines = ["Stopping current Run."]
            if queued:
                lines.append("Queued follow-ups remain paused.")
            if active_run.stopped_pending_steer_count:
                lines.append("Pending Steer messages will not be applied.")
            return CommandResult("\n".join(lines))

        case "clear":
            session_id = state.selected_session_id
            try:
                new_session_id = await sessions.clear(session_id)
            except SessionArchivedError:
                return CommandResult("Archived sessions are read-only. Unarchive or switch sessions first.")
            except SessionHasQueuedInputsError:
                return CommandResult("Cannot clear this session while it has queued follow-ups. Clear the queue first.")
            except SessionBusyError:
                return CommandResult("Cannot clear this session while it has an active operation.")
            state.selected_session_id = new_session_id
            return CommandResult(f"Cleared session and started new session: {new_session_id}")

        case "compact":
            if command.args:
                return CommandResult("Usage: /compact")
            session_id = state.selected_session_id
            try:
                result = await sessions.compact(session_id)
            except SessionArchivedError:
                return CommandResult("Archived sessions are read-only. Unarchive or switch sessions first.")
            except SessionBusyError:
                return CommandResult("Cannot compact this session while it has an active operation.")
            except SessionCompactionError as exc:
                if exc.consistency_uncertain:
                    return CommandResult(
                        "Context compaction failed and context storage may be damaged. Stop this session and see the system log."
                    )
                return CommandResult("Context compaction failed; existing context was kept. See the system log.")
            if not result.changed:
                return CommandResult("Session context is already empty or compacted.")
            return CommandResult("Session context compacted.", compaction=result)

        case "ps":
            jobs = await list_background_jobs(state.execution)
            return CommandResult(format_background_jobs(jobs))

        case "exit":
            if runtime.has_active_runs() or runtime.has_queued_inputs():
                return CommandResult(
                    "There are active or queued Agent tasks.\nStop/finish runs and clear queued follow-ups before exiting."
                )
            logger.info("Exit requested")
            return CommandResult(output="Exiting...", exit_requested=True)

        case "help":
            return CommandResult(HELP)

        case _:
            return CommandResult(f"Unknown command: /{command.name}. For help, type /help.")
