from __future__ import annotations

from qmt_agent.agents import CompactionResult, TokenUsage
from qmt_agent.journal import JournalPage
from qmt_agent.output import serialize_output_event
from qmt_agent.runtime import (
    ApprovalRequest,
    RuntimeFollowUpEvent,
    RuntimeOutput,
    RuntimeRunEnded,
    RuntimeSessionSnapshot,
)
from qmt_agent.storage import SessionRecord


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
    return {
        "kind": "run_ended",
        "session_id": event.session_id,
        "run_id": event.run_id,
        "status": event.status,
        "discarded_steer_count": event.discarded_steer_count,
        "main_usage": serialize_token_usage(result.main_usage) if result is not None else None,
        "auxiliary_usage": serialize_token_usage(result.auxiliary_usage) if result is not None else None,
        "main_context_tokens": result.main_usage.last_request_total_tokens if result is not None else None,
        "auto_compaction": serialize_compaction_result(result.auto_compaction) if result is not None and result.auto_compaction is not None else None,
        "auto_compaction_failed": result.auto_compaction_failed if result is not None else None,
        "auto_compaction_consistency_uncertain": result.auto_compaction_consistency_uncertain if result is not None else None,
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
