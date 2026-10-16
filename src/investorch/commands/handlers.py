import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from investorch.agents import CompactionResult
from investorch.application import (
    SessionAlreadyArchivedError,
    SessionArchivedError,
    SessionCompactionError,
    SessionHasChildrenError,
    SessionHasQueuedInputsError,
    SessionNotFoundError,
    SessionOperations,
)
from investorch.application.sessions import (
    SessionClearReplacementError,
    SessionDeletionCleanupError,
    SessionDeletionUnsafeError,
)
from investorch.config import FOLLOW_UP_BEHAVIORS, PERMISSION_MODES, REASONING_EFFORTS, ConfigError
from investorch.context import AppState
from investorch.runtime import AgentRuntime, SessionBusyError
from investorch.storage import (
    SessionDeletionScopeChangedError,
    SessionForkError,
    SessionForkRollbackError,
    SessionMetadataError,
    find_session_ids,
    get_session_branch_from,
    get_session_title,
    list_archived_sessions,
    list_sessions,
)
from investorch.tools import format_background_jobs, list_background_jobs

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
    "  /model [connection name effort window] Show or set the Session model.\n"
    "  /effort [level]    Show or set the next Run reasoning effort.\n"
    "  /permission [mode] Show or set tool permission mode.\n"
    "  /followup [mode]   Show or set follow-up behavior.\n"
    "  /stop              Stop the current Agent run.\n"
    "  /compact           Compact the current Agent context.\n"
    "  /clear             Delete this Session and Spawn descendants, then start a new Session.\n"
    "  /ps                Show background commands.\n"
    "  /exit              Exit InvestOrch Agent."
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
    sessions: SessionOperations,
    confirm_clear_scope: Callable[[dict[str, object]], Awaitable[bool]] | None = None,
) -> CommandResult:
    if state.selected_session_id is None and command.name in {
        "session",
        "archive",
        "fork",
        "title",
        "stop",
        "clear",
        "compact",
        "model",
        "effort",
    }:
        return CommandResult("No Session selected. Use /new or /resume to continue.")
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
            if previous_session_id is not None:
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
            if previous_session_id is not None:
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
            if previous_session_id is not None:
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

        case "model":
            session_id = state.selected_session_id
            if not command.args:
                active = runtime.get_active_run(session_id)
                view = runtime.session_models.view(session_id, active=active.options.model if active else None)

                def describe(model):
                    return (
                        f"{model.model} / {model.name} · effort {model.reasoning_effort} · window {model.context_window_tokens}"
                        if model
                        else "none"
                    )

                return CommandResult(
                    f"Active: {describe(view.active)}\nSaved: {describe(view.persisted)}\nNext Run: {describe(view.effective)}"
                )
            if len(command.args) != 4:
                return CommandResult("Usage: /model [connection name effort context_window_tokens]")
            try:
                selected = runtime.session_models.update(
                    session_id,
                    model=command.args[0],
                    name=command.args[1],
                    reasoning_effort=command.args[2],
                    context_window_tokens=int(command.args[3]),
                )
            except (ConfigError, ValueError, KeyError) as error:
                return CommandResult(str(error))
            return CommandResult(
                f"Next Run model: {selected.model} / {selected.name}. Draft is saved when a Run starts; otherwise lost on exit."
            )

        case "effort":
            if not command.args:
                return CommandResult(
                    f"Next Run reasoning effort: {runtime.session_models.view(state.selected_session_id).effective.reasoning_effort}"
                )
            if len(command.args) != 1:
                return CommandResult("Usage: /effort [none|minimal|low|medium|high|xhigh|max]")

            effort = command.args[0].lower()
            if effort not in REASONING_EFFORTS:
                return CommandResult(f"Unsupported reasoning effort: {command.args[0]}")

            runtime.session_models.update(state.selected_session_id, reasoning_effort=effort)
            return CommandResult(f"Next Run reasoning effort set to: {effort}. Draft is saved when a Run starts.")

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
            if command.args:
                return CommandResult("Usage: /clear")
            session_id = state.selected_session_id
            try:
                preview = await sessions.deletion_preview(session_id)
                if preview["blocked"]:
                    reasons = {
                        "session_busy": "Stop its Run or wait for its current operation, then retry.",
                        "pending_inputs": "Resolve its queued or pending inputs, then retry.",
                        "fork_referenced": "An independent Fork references this scope; handle the Fork before retrying.",
                        "journal_delete_unsafe": "History cannot be safely deleted; check System Status.",
                    }
                    lines = ["Cannot clear these Sessions:"]
                    lines.extend(
                        f"  {item['session_id']}: {reasons.get(item['code'], 'Check System Status.')}"
                        for item in preview["blocked"]
                    )
                    return CommandResult("\n".join(lines))
                if len(preview["sessions"]) > 1:
                    if confirm_clear_scope is None:
                        return CommandResult(
                            "Recursive Clear requires explicit confirmation of the full Session scope."
                        )
                    if not await confirm_clear_scope(preview):
                        return CommandResult("Clear cancelled. All Sessions were kept.")
                if state.selected_session_id != session_id:
                    return CommandResult("The selected Session changed. Clear cancelled; all Sessions were kept.")
                expected_ids = tuple(member["session_id"] for member in preview["sessions"])
                new_session_id = await sessions.clear(session_id, expected_session_ids=expected_ids)
            except SessionArchivedError:
                return CommandResult("Archived sessions are read-only. Unarchive or switch sessions first.")
            except SessionNotFoundError:
                return CommandResult("Session not found. Use /new or /resume to select an existing Session.")
            except SessionHasChildrenError:
                return CommandResult(
                    "An independent Fork references the deletion scope. Handle the Fork before retrying."
                )
            except SessionDeletionScopeChangedError:
                return CommandResult(
                    "The deletion scope changed. Run /clear again to preview and confirm the new scope."
                )
            except (SessionDeletionUnsafeError, SessionMetadataError):
                return CommandResult("Session history or provenance is unsafe to delete. Check System Status.")
            except SessionHasQueuedInputsError:
                return CommandResult("Cannot clear this session while it has queued follow-ups. Clear the queue first.")
            except SessionBusyError:
                return CommandResult("Cannot clear this scope while it has an active operation or pending input.")
            except (SessionClearReplacementError, SessionDeletionCleanupError) as error:
                if state.selected_session_id in error.deleted_session_ids:
                    state.selected_session_id = None
                stage = (
                    "creating the new Session failed"
                    if isinstance(error, SessionClearReplacementError)
                    else "history cleanup is incomplete"
                )
                return CommandResult(
                    f"Deleted {len(error.deleted_session_ids)} Sessions, but {stage}.\n"
                    + "\n".join(f"  {member}" for member in error.deleted_session_ids)
                    + "\nUse /new or /resume to continue. Check System Status before retrying the failed step."
                )
            if state.selected_session_id in expected_ids:
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
