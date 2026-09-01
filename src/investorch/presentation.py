from __future__ import annotations

from investorch.agents import CompactionResult, TokenUsage
from investorch.application.activity import ActivityLabelEvent
from investorch.application.interaction import UserInputSubmission
from investorch.application.presentation_state import SessionPresentationState
from investorch.context import BackgroundJob
from investorch.journal import JournalPage
from investorch.output import serialize_output_event
from investorch.runtime import (
    ApprovalRequest,
    QueuedInput,
    RuntimeFollowUpEvent,
    RuntimeOutput,
    RuntimeRunEnded,
    RuntimeSessionSnapshot,
)
from investorch.storage import SessionRecord


def serialize_runtime_output(output: RuntimeOutput, *, journal_seq: int | None) -> dict[str, object]:
    return {
        "kind": "output",
        "session_id": output.session_id,
        "run_id": output.run_id,
        "journal_seq": journal_seq,
        "event": serialize_output_event(output.event),
    }


def serialize_runtime_snapshot(snapshot: RuntimeSessionSnapshot) -> dict[str, object]:
    return {
        "kind": "runtime_state",
        "session_id": snapshot.session_id,
        "run_id": snapshot.run_id,
        "run_started_at": snapshot.run_started_at.isoformat() if snapshot.run_started_at is not None else None,
        "run_phase": snapshot.run_phase,
        "active_follow_up_behavior": snapshot.active_follow_up_behavior,
        "queued_count": snapshot.queued_count,
        "queue_paused": snapshot.queue_paused,
        "pending_steer_count": snapshot.pending_steer_count,
        "todos": [{"content": todo["content"], "status": todo["status"]} for todo in snapshot.todos],
    }


def serialize_follow_up_event(event: RuntimeFollowUpEvent) -> dict[str, object]:
    return {
        "kind": "follow_up",
        "event_kind": event.kind,
        "session_id": event.session_id,
        "run_id": event.run_id,
        "source_run_id": event.source_run_id,
        "follow_up_id": event.follow_up_id,
        "text": event.text,
        "journal_seq": event.journal_seq,
    }


def serialize_token_usage(usage: TokenUsage) -> dict[str, object]:
    return {
        "requests": usage.requests,
        "input_tokens": usage.input_tokens,
        "cached_input_tokens": usage.cached_input_tokens,
        "cache_write_input_tokens": usage.cache_write_input_tokens,
        "output_tokens": usage.output_tokens,
        "reasoning_output_tokens": usage.reasoning_output_tokens,
        "total_tokens": usage.total_tokens,
        "last_request_total_tokens": usage.last_request_total_tokens,
    }


def serialize_compaction_result(result: CompactionResult) -> dict[str, object]:
    return {
        "changed": result.changed,
        "usage": serialize_token_usage(result.usage),
        "source_items": result.source_items,
        "summary_chars": result.summary_chars,
    }


def serialize_run_ended(event: RuntimeRunEnded) -> dict[str, object]:
    result = event.result
    compacted = result is not None and result.auto_compaction is not None and result.auto_compaction.changed
    return {
        "kind": "run_ended",
        "session_id": event.session_id,
        "run_id": event.run_id,
        "status": event.status,
        "started_at": event.started_at.isoformat(),
        "ended_at": event.ended_at.isoformat(),
        "duration_ms": max(0, round((event.ended_at - event.started_at).total_seconds() * 1000)),
        "discarded_steer_count": event.discarded_steer_count,
        "main_usage": serialize_token_usage(result.main_usage) if result is not None else None,
        "auxiliary_usage": serialize_token_usage(result.auxiliary_usage) if result is not None else None,
        "main_context_tokens": None if result is None or compacted else result.main_usage.last_request_total_tokens,
        "auto_compaction": serialize_compaction_result(result.auto_compaction)
        if result is not None and result.auto_compaction is not None
        else None,
        "auto_compaction_failed": result.auto_compaction_failed if result is not None else None,
        "auto_compaction_consistency_uncertain": result.auto_compaction_consistency_uncertain
        if result is not None
        else None,
    }


def serialize_approval_request(request: ApprovalRequest, *, review_reason: str | None) -> dict[str, object]:
    return {
        "kind": "approval_required",
        "approval_id": request.approval_id,
        "session_id": request.session_id,
        "run_id": request.run_id,
        "tool_name": request.tool_name,
        "arguments": request.arguments,
        "review_reason": review_reason,
    }


def serialize_approval_resolved(
    *,
    approval_id: str,
    session_id: str,
    run_id: str,
    approved: bool,
    source: str,
    review_decision: str | None,
    review_reason: str | None,
    journal_seq: int | None,
) -> dict[str, object]:
    return {
        "kind": "approval_resolved",
        "approval_id": approval_id,
        "session_id": session_id,
        "run_id": run_id,
        "approved": approved,
        "source": source,
        "review_decision": review_decision,
        "review_reason": review_reason,
        "journal_seq": journal_seq,
    }


def serialize_approval_cancelled(*, approval_id: str, session_id: str, run_id: str) -> dict[str, object]:
    return {
        "kind": "approval_cancelled",
        "approval_id": approval_id,
        "session_id": session_id,
        "run_id": run_id,
    }


def serialize_activity_label(event: ActivityLabelEvent) -> dict[str, object]:
    return {
        "kind": "activity_label",
        "session_id": event.session_id,
        "run_id": event.run_id,
        "target_seq": event.target_seq,
        "journal_seq": event.journal_seq,
        "text": event.text,
    }


def serialize_queue_item(item: QueuedInput) -> dict[str, object]:
    return {
        "queue_id": item.queue_id,
        "session_id": item.session_id,
        "text": item.text,
        "created_at": item.created_at.isoformat(),
    }


def serialize_session_presentation_state(state: SessionPresentationState) -> dict[str, object]:
    return {
        "usage": serialize_token_usage(state.usage),
        "main_context_tokens": state.main_context_tokens,
        "last_todo_run_id": state.last_todo_run_id,
        "last_todos": [{"content": todo["content"], "status": todo["status"]} for todo in state.last_todos],
    }


def serialize_user_input_submission(submission: UserInputSubmission) -> dict[str, object]:
    return {
        "session_id": submission.session_id,
        "disposition": submission.disposition,
        "run_id": submission.run_id,
        "follow_up_id": submission.follow_up_id,
    }


def serialize_background_job(job: BackgroundJob) -> dict[str, object]:
    return {
        "job_id": job.job_id,
        "pid": job.pid,
        "process_id": job.process_id,
        "command": job.command,
        "status": job.status,
        "owner_session_id": job.owner_session_id,
        "owner_run_id": job.owner_run_id,
        "started_at": job.started_at.isoformat(),
        "finished_at": job.finished_at.isoformat() if job.finished_at is not None else None,
        "exit_code": job.exit_code,
        "stdout_log": job.stdout_log,
        "stderr_log": job.stderr_log,
    }


def serialize_session_record(record: SessionRecord) -> dict[str, object]:
    return {
        "session_id": record.session_id,
        "title": record.title,
        "branch_from_session_id": record.branch_from_session_id,
        "archived_at": record.archived_at,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def serialize_journal_page(page: JournalPage) -> dict[str, object]:
    return {
        "records": [dict(record) for record in page.records],
        "has_older": page.has_older,
        "oldest_seq": page.oldest_seq,
        "newest_seq": page.newest_seq,
    }
