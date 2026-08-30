from __future__ import annotations

import asyncio
import logging
from importlib.metadata import version
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict

from qmt_agent.application import ApplicationHost, submit_user_input
from qmt_agent.journal import JournalPage, read_session_journal_page
from qmt_agent.presentation import (
    serialize_background_job,
    serialize_compaction_result,
    serialize_journal_page,
    serialize_queue_item,
    serialize_runtime_snapshot,
    serialize_session_presentation_state,
    serialize_session_record,
    serialize_user_input_submission,
)
from qmt_agent.runtime import SessionBusyError
from qmt_agent.storage import SessionRecord, get_session, list_archived_sessions, list_sessions
from qmt_agent.tools import list_background_jobs

from .errors import APIError, raise_application_error

logger = logging.getLogger(__name__)

APPLICATION_VERSION = version("qmt-agent-trader")
router = APIRouter(prefix="/api")

ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]
PermissionMode = Literal["manual", "review"]
FollowUpBehavior = Literal["steer", "queue"]


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


class RenameSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool


class UpdateDefaultsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasoning_effort: ReasoningEffort | None = None
    permission_mode: PermissionMode | None = None
    follow_up_behavior: FollowUpBehavior | None = None


def _application_host(request: Request) -> ApplicationHost:
    host = getattr(request.app.state, "host", None)
    if not isinstance(host, ApplicationHost):
        raise APIError(503, "service_unavailable", "The application host is not ready.")
    return host


Host = Annotated[ApplicationHost, Depends(_application_host)]


async def _session_record(session_id: str, host: Host) -> SessionRecord:
    record = await asyncio.to_thread(get_session, host.config.sessions_db, session_id)
    if record is None:
        raise APIError(404, "session_not_found", "The requested session does not exist.", details={"session_id": session_id})
    return record


Session = Annotated[SessionRecord, Depends(_session_record)]


def _require_confirmation(request: ConfirmRequest, action: str) -> None:
    if not request.confirm:
        raise APIError(400, "confirmation_required", f"Explicit confirmation is required to {action}.")


def _serialize_defaults(host: ApplicationHost) -> dict[str, str]:
    return {
        "reasoning_effort": host.state.main_reasoning_effort,
        "permission_mode": host.state.permission_mode,
        "follow_up_behavior": host.state.follow_up_behavior,
    }


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": APPLICATION_VERSION}


@router.get("/bootstrap")
async def bootstrap(host: Host) -> dict[str, object]:
    records = await asyncio.to_thread(list_sessions, host.config.sessions_db)
    initial_session_id = host.initial_session_id
    return {
        "version": APPLICATION_VERSION,
        "initial_session_id": initial_session_id,
        "agent_name": host.runtime.agent_name,
        "context_window_tokens": host.config.model("main").context_window_tokens,
        "defaults": _serialize_defaults(host),
        "sessions": [serialize_session_record(record) for record in records],
        "runtime": serialize_runtime_snapshot(host.runtime.session_snapshot(initial_session_id)),
        "presentation": serialize_session_presentation_state(host.presentation_state.get(initial_session_id)),
        "pending_approvals": [],
    }


@router.get("/sessions")
async def get_sessions(host: Host) -> dict[str, object]:
    records = await asyncio.to_thread(list_sessions, host.config.sessions_db)
    return {"sessions": [serialize_session_record(record) for record in records]}


@router.get("/sessions/archived")
async def get_archived_sessions(host: Host) -> dict[str, object]:
    records = await asyncio.to_thread(list_archived_sessions, host.config.sessions_db)
    return {"sessions": [serialize_session_record(record) for record in records]}


@router.post("/sessions")
async def create_session(host: Host) -> dict[str, object]:
    session_id = await host.sessions.create()
    record = await asyncio.to_thread(get_session, host.config.sessions_db, session_id)
    assert record is not None
    return {"session": serialize_session_record(record)}


@router.get("/sessions/{session_id}")
async def get_session_by_id(session: Session) -> dict[str, object]:
    return {"session": serialize_session_record(session)}


@router.get("/sessions/{session_id}/state")
async def get_session_state(session: Session, host: Host) -> dict[str, object]:
    session_id = session.session_id
    return {
        "session": serialize_session_record(session),
        "runtime": serialize_runtime_snapshot(host.runtime.session_snapshot(session_id)),
        "presentation": serialize_session_presentation_state(host.presentation_state.get(session_id)),
        "queue": [serialize_queue_item(item) for item in host.runtime.list_queued_inputs(session_id)],
        "pending_approvals": [],
    }


@router.get("/sessions/{session_id}/history")
async def get_session_history(
    session: Session,
    host: Host,
    before_seq: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1)] = 200,
) -> dict[str, object]:
    try:
        page = await asyncio.to_thread(
            read_session_journal_page,
            host.config.session_journal_dir,
            session.session_id,
            before_seq=before_seq,
            limit=limit,
        )
    except FileNotFoundError:
        page = JournalPage(records=(), has_older=False, oldest_seq=None, newest_seq=None)
    except RuntimeError as error:
        logger.exception("Invalid session journal session=%s", session.session_id)
        raise APIError(500, "journal_invalid", "This session journal is invalid.") from error
    except OSError as error:
        logger.exception("Session journal unavailable session=%s", session.session_id)
        raise APIError(503, "journal_unavailable", "This session journal is temporarily unavailable.") from error
    return serialize_journal_page(page)


@router.patch("/sessions/{session_id}")
async def rename_session(request: RenameSessionRequest, session: Session, host: Host) -> dict[str, object]:
    title = request.title.strip()
    if not title:
        raise APIError(400, "invalid_title", "Session title must not be empty.")
    try:
        await host.sessions.set_title(session.session_id, title)
    except Exception as error:
        raise_application_error(error)
    updated = await asyncio.to_thread(get_session, host.config.sessions_db, session.session_id)
    assert updated is not None
    return {"session": serialize_session_record(updated)}


@router.post("/sessions/{session_id}/messages")
async def send_message(request: SendMessageRequest, session: Session, host: Host) -> dict[str, object]:
    if not request.text.strip():
        raise APIError(400, "invalid_message", "Message text must not be empty.")
    try:
        submission = await submit_user_input(state=host.state, runtime=host.runtime, session_id=session.session_id, text=request.text)
    except Exception as error:
        raise_application_error(error)
    return serialize_user_input_submission(submission)


@router.post("/sessions/{session_id}/stop")
async def stop_session_run(session: Session, host: Host) -> dict[str, object]:
    active_run = host.runtime.get_active_run(session.session_id)
    if active_run is None:
        raise APIError(409, "run_not_active", "This session does not have an active Run.")
    try:
        stopped = host.runtime.cancel_run(session.session_id)
    except SessionBusyError as error:
        raise_application_error(error)
    return {"session_id": session.session_id, "run_id": stopped.run_id, "status": "stopping"}


@router.post("/sessions/{session_id}/fork")
async def fork_session(session: Session, host: Host) -> dict[str, object]:
    try:
        target_session_id = await host.sessions.fork(session.session_id)
    except Exception as error:
        raise_application_error(error)
    target = await asyncio.to_thread(get_session, host.config.sessions_db, target_session_id)
    assert target is not None
    return {"session": serialize_session_record(target)}


@router.post("/sessions/{session_id}/archive")
async def archive_session(session: Session, host: Host) -> dict[str, object]:
    try:
        await host.sessions.archive(session.session_id)
    except Exception as error:
        raise_application_error(error)
    archived = await asyncio.to_thread(get_session, host.config.sessions_db, session.session_id)
    assert archived is not None
    return {"session": serialize_session_record(archived)}


@router.post("/sessions/{session_id}/unarchive")
async def unarchive_session(session: Session, host: Host) -> dict[str, object]:
    await host.sessions.unarchive(session.session_id)
    unarchived = await asyncio.to_thread(get_session, host.config.sessions_db, session.session_id)
    assert unarchived is not None
    return {"session": serialize_session_record(unarchived)}


@router.post("/sessions/{session_id}/clear")
async def clear_session(request: ConfirmRequest, session: Session, host: Host) -> dict[str, object]:
    _require_confirmation(request, "clear this session")
    try:
        replacement_session_id = await host.sessions.clear(session.session_id)
    except Exception as error:
        raise_application_error(error)
    return {"replacement_session_id": replacement_session_id}


@router.post("/sessions/{session_id}/compact")
async def compact_session(session: Session, host: Host) -> dict[str, object]:
    try:
        result = await host.sessions.compact(session.session_id)
    except Exception as error:
        raise_application_error(error)
    host.presentation_state.observe_compaction(session.session_id, result)
    return serialize_compaction_result(result)


@router.post("/sessions/{session_id}/discard-unused")
async def discard_unused_session(session: Session, host: Host) -> dict[str, bool]:
    return {"discarded": await host.sessions.discard_if_unused(session.session_id)}


@router.delete("/sessions/{session_id}/queue/{queue_id}")
async def remove_queued_input(queue_id: str, session: Session, host: Host) -> dict[str, object]:
    try:
        removed = host.runtime.remove_queued_input(session.session_id, queue_id)
    except KeyError as error:
        raise APIError(404, "queue_not_found", "The requested queued follow-up does not exist.") from error
    return {
        "removed": serialize_queue_item(removed),
        "runtime": serialize_runtime_snapshot(host.runtime.session_snapshot(session.session_id)),
        "queue": [serialize_queue_item(item) for item in host.runtime.list_queued_inputs(session.session_id)],
    }


@router.delete("/sessions/{session_id}/queue")
async def clear_session_queue(request: ConfirmRequest, session: Session, host: Host) -> dict[str, object]:
    _require_confirmation(request, "clear this queue")
    cleared_count = host.runtime.clear_queue(session.session_id)
    return {
        "cleared_count": cleared_count,
        "runtime": serialize_runtime_snapshot(host.runtime.session_snapshot(session.session_id)),
        "queue": [],
    }


@router.post("/sessions/{session_id}/queue/resume")
async def resume_session_queue(session: Session, host: Host) -> dict[str, object]:
    queued = host.runtime.list_queued_inputs(session.session_id)
    if not queued:
        raise APIError(404, "queue_not_found", "This session does not have queued follow-ups.")
    if not host.runtime.session_snapshot(session.session_id).queue_paused:
        raise APIError(409, "queue_not_paused", "This session queue is not paused.")
    try:
        await host.runtime.resume_queue(session.session_id)
    except SessionBusyError as error:
        raise_application_error(error)
    return {
        "runtime": serialize_runtime_snapshot(host.runtime.session_snapshot(session.session_id)),
        "queue": [serialize_queue_item(item) for item in host.runtime.list_queued_inputs(session.session_id)],
    }


@router.get("/defaults")
async def get_defaults(host: Host) -> dict[str, str]:
    return _serialize_defaults(host)


@router.patch("/defaults")
async def update_defaults(request: UpdateDefaultsRequest, host: Host) -> dict[str, str]:
    if request.reasoning_effort is None and request.permission_mode is None and request.follow_up_behavior is None:
        raise APIError(400, "empty_update", "At least one default must be provided.")
    if request.reasoning_effort is not None:
        host.state.main_reasoning_effort = request.reasoning_effort
    if request.permission_mode is not None:
        host.state.permission_mode = request.permission_mode
    if request.follow_up_behavior is not None:
        host.state.follow_up_behavior = request.follow_up_behavior
    return _serialize_defaults(host)


@router.get("/processes")
async def get_processes(host: Host) -> dict[str, object]:
    jobs = await list_background_jobs(host.execution)
    return {"processes": [serialize_background_job(job) for job in jobs]}
