from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable
from contextlib import nullcontext
from dataclasses import dataclass

from investorch.context import AppState
from investorch.inputs import InputOrigin, UserInput
from investorch.runtime import AgentRuntime, RunCapacityExceededError
from investorch.storage import create_spawn_session, get_session
from investorch.storage.sessions import delete_unstarted_spawn_session

from .interaction import submit_user_input
from .sessions import SessionOperations

logger = logging.getLogger(__name__)


class SpawnError(RuntimeError):
    """Corrective Spawn failure without private storage or provider details."""


@dataclass(frozen=True, slots=True)
class SpawnReceipt:
    session_id: str
    run_id: str | None
    started: bool
    disposition: str


async def _await_mutation[T](awaitable: Awaitable[T]) -> T:
    # Keep the lifecycle lock until a worker really finishes, including cancellation boundaries.
    worker = asyncio.ensure_future(awaitable)
    cancelled: asyncio.CancelledError | None = None
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError as error:
            cancelled = error
    if cancelled is not None:
        if not worker.cancelled():
            worker.exception()
        raise cancelled
    return worker.result()


async def spawn_agent(
    *,
    state: AppState,
    runtime: AgentRuntime,
    sessions: SessionOperations,
    source_session_id: str,
    source_run_id: str,
    tool_call_id: str,
    task: str,
    title: str | None = None,
    model: str | None = None,
    name: str | None = None,
    reasoning_effort: str | None = None,
    context_window_tokens: int | None = None,
) -> SpawnReceipt:
    """Submit an approved delegation through the ordinary Session input path."""
    if (
        not isinstance(task, str)
        or not task.strip()
        or len(task) > state.config["permission.max_user_instruction_chars"]
    ):
        raise SpawnError("spawn_invalid_task: Provide non-empty task text within the instruction length limit.")
    if title is not None:
        title = title.strip()
        if not title or len(title) > 200:
            raise SpawnError("spawn_invalid_task: An explicit title must contain 1 to 200 characters.")
    try:
        origin = InputOrigin("agent", source_session_id, source_run_id)
        if not tool_call_id or not tool_call_id.isprintable():
            raise ValueError("invalid call identity")
    except ValueError as error:
        raise SpawnError("spawn_parent_unavailable: This Tool call has no valid sender identity.") from error
    identity = json.dumps((source_session_id, source_run_id, tool_call_id), ensure_ascii=False)
    child_id = uuid.uuid5(uuid.NAMESPACE_URL, f"urn:investorch:spawn:{identity}").hex

    if runtime.closed:
        raise SpawnError("runtime_closed: This Host is shutting down.")

    async with sessions.lifecycle_lock:
        parent = await asyncio.to_thread(get_session, state.config.sessions_db, source_session_id)
        if parent is None or parent.archived_at is not None:
            raise SpawnError("spawn_parent_unavailable: The parent Session is missing or archived.")
        existing = await asyncio.to_thread(get_session, state.config.sessions_db, child_id)
        if existing is not None:
            active_child = runtime.get_active_run(child_id)
            if (
                active_child is not None
                or await sessions.is_application_workflow_started(child_id)
                or await sessions.has_agent_activity(child_id)
            ):
                return SpawnReceipt(child_id, active_child.run_id if active_child else None, False, "already_submitted")
            raise SpawnError(
                f"spawn_consistency_uncertain: Session {child_id} exists but initial acceptance cannot be proven. Check System Status before retrying."
            )
        parent_run = runtime.get_active_run(source_session_id)
        if parent_run is None or parent_run.run_id != source_run_id:
            raise SpawnError("spawn_parent_unavailable: The parent Run is no longer active.")
        child_model = state.config.resolve_model(
            parent_run.options.model,
            model=model,
            name=name,
            reasoning_effort=reasoning_effort,
            context_window_tokens=context_window_tokens,
        )
        try:
            created = await _await_mutation(
                asyncio.to_thread(
                    create_spawn_session,
                    state.config.sessions_db,
                    child_id,
                    parent_session_id=source_session_id,
                    parent_run_id=source_run_id,
                    tool_call_id=tool_call_id,
                    title=title,
                )
            )
        except KeyError as error:
            raise SpawnError("spawn_parent_unavailable: The parent Session is unavailable.") from error
        except Exception as error:
            logger.exception("Spawn Session creation failed child=%s", child_id)
            raise SpawnError(
                "session_create_failed: Could not create the child Session; check System Status."
            ) from error
        if not created:
            active = runtime.get_active_run(child_id)
            if (
                active is not None
                or await sessions.is_application_workflow_started(child_id)
                or await sessions.has_agent_activity(child_id)
            ):
                return SpawnReceipt(child_id, active.run_id if active else None, False, "already_submitted")
            raise SpawnError(
                f"spawn_consistency_uncertain: Session {child_id} exists but initial acceptance cannot be proven. Check System Status before retrying."
            )
        runtime.session_models.set_draft(child_id, child_model)
        try:
            submitted = await submit_user_input(
                state=state,
                runtime=runtime,
                session_id=child_id,
                user_input=UserInput(task, origin=origin),
                require_existing_session=True,
            )
        except Exception as error:
            if isinstance(error, RunCapacityExceededError) or str(error) == "Agent runtime is closed":
                try:
                    # A closed Runtime's gate proved no Run was registered; it cannot reserve.
                    reservation = (
                        nullcontext() if str(error) == "Agent runtime is closed" else runtime.reserve_session(child_id)
                    )
                    async with reservation:
                        if await sessions.has_agent_activity(child_id):
                            raise RuntimeError("Child already has activity")
                        removed = await _await_mutation(
                            asyncio.to_thread(delete_unstarted_spawn_session, state.config.sessions_db, child_id)
                        )
                        if not removed:
                            raise RuntimeError("Child rollback was refused")
                        runtime.session_models.discard(child_id)
                except Exception as cleanup_error:
                    logger.exception("Spawn rollback uncertain child=%s", child_id)
                    raise SpawnError(
                        f"spawn_consistency_uncertain: Could not safely roll back Session {child_id}; check System Status."
                    ) from cleanup_error
                if isinstance(error, RunCapacityExceededError):
                    raise
                raise SpawnError("runtime_closed: This Host is shutting down.") from error
            logger.exception("Spawn input acceptance uncertain child=%s", child_id)
            raise SpawnError(
                f"spawn_consistency_uncertain: Check Session {child_id} and System Status before retrying."
            ) from error
        try:
            await _await_mutation(sessions.mark_application_workflow_started(child_id))
        except Exception as error:
            logger.exception("Spawn accepted but receipt marker failed child=%s", child_id)
            raise SpawnError(
                f"spawn_consistency_uncertain: Input was submitted to Session {child_id}; check its history before retrying."
            ) from error
        return SpawnReceipt(child_id, submitted.run_id, True, submitted.disposition)
