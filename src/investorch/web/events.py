from __future__ import annotations

from investorch.application import (
    ActivityLabelEvent,
    ApplicationCallbacks,
    ApprovalResolvedEvent,
    PortfolioToolSucceededEvent,
)
from investorch.presentation import (
    serialize_activity_label,
    serialize_approval_cancelled,
    serialize_approval_request,
    serialize_approval_resolved,
    serialize_follow_up_event,
    serialize_run_ended,
    serialize_runtime_output,
    serialize_runtime_snapshot,
)
from investorch.runtime import (
    ApprovalRequest,
    RuntimeFollowUpEvent,
    RuntimeOutput,
    RuntimeRunEnded,
    RuntimeSessionSnapshot,
)

from .connections import WebConnectionHub


class WebEventBridge:
    def __init__(self, connections: WebConnectionHub) -> None:
        self._connections = connections

    def application_callbacks(self) -> ApplicationCallbacks:
        return ApplicationCallbacks(
            handle_output=self.handle_output,
            handle_follow_up=self.handle_follow_up,
            handle_run_ended=self.handle_run_ended,
            handle_runtime_state=self.handle_runtime_state,
            handle_approval_resolved=self.handle_approval_resolved,
            handle_activity_label=self.handle_activity_label,
            handle_portfolio_tool_succeeded=self.handle_portfolio_tool_succeeded,
        )

    async def handle_output(self, output: RuntimeOutput, journal_seq: int | None) -> None:
        if journal_seq is None:
            return
        self._connections.publish(serialize_runtime_output(output, journal_seq=journal_seq))

    async def handle_follow_up(self, event: RuntimeFollowUpEvent) -> None:
        self._connections.publish(serialize_follow_up_event(event))

    async def handle_run_ended(self, event: RuntimeRunEnded) -> None:
        self._connections.publish(serialize_run_ended(event))

    def handle_runtime_state(self, snapshot: RuntimeSessionSnapshot) -> None:
        self._connections.publish(serialize_runtime_snapshot(snapshot))

    async def handle_approval_resolved(self, event: ApprovalResolvedEvent) -> None:
        request = event.request
        self._connections.publish(
            serialize_approval_resolved(
                approval_id=request.approval_id,
                session_id=request.session_id,
                run_id=request.run_id,
                approved=event.approved,
                source=event.source,
                review_decision=event.review_decision,
                review_reason=event.review_reason,
                journal_seq=event.journal_seq,
            )
        )

    async def handle_activity_label(self, event: ActivityLabelEvent) -> None:
        self._connections.publish(serialize_activity_label(event))

    async def handle_portfolio_tool_succeeded(self, event: PortfolioToolSucceededEvent) -> None:
        self._connections.publish(
            {
                "kind": "portfolio_tool_succeeded",
                "session_id": event.session_id,
                "run_id": event.run_id,
                "portfolio_ids": list(event.portfolio_ids),
                "mutated": event.mutated,
            }
        )

    def publish_approval_required(self, request: ApprovalRequest, review_reason: str | None) -> None:
        self._connections.publish(serialize_approval_request(request, review_reason=review_reason))

    def publish_approval_cancelled(self, request: ApprovalRequest) -> None:
        self._connections.publish(
            serialize_approval_cancelled(
                approval_id=request.approval_id,
                session_id=request.session_id,
                run_id=request.run_id,
            )
        )
