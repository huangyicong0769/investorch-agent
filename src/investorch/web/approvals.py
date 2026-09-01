from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from investorch.runtime import ApprovalRequest

from .events import WebEventBridge


class ApprovalNotPendingError(LookupError):
    pass


@dataclass(slots=True)
class PendingWebApproval:
    request: ApprovalRequest
    review_reason: str | None
    created_at: datetime
    future: asyncio.Future[bool]


class WebApprovalBroker:
    def __init__(self, events: WebEventBridge) -> None:
        self._events = events
        self._pending: dict[str, PendingWebApproval] = {}
        self._closed = False

    async def request(self, request: ApprovalRequest, review_reason: str | None) -> bool:
        if self._closed:
            raise RuntimeError("Web approval broker is closed")
        if request.approval_id in self._pending:
            raise RuntimeError(f"Duplicate pending approval: {request.approval_id}")

        pending = PendingWebApproval(
            request=request,
            review_reason=review_reason,
            created_at=datetime.now(UTC),
            future=asyncio.get_running_loop().create_future(),
        )
        self._pending[request.approval_id] = pending
        cancelled = False
        try:
            self._events.publish_approval_required(request, review_reason)
            return await pending.future
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            if self._pending.get(request.approval_id) is pending:
                del self._pending[request.approval_id]
            if cancelled:
                self._events.publish_approval_cancelled(request)

    def resolve(self, approval_id: str, approved: bool) -> PendingWebApproval:
        if type(approved) is not bool:
            raise ValueError("approved must be a boolean")
        pending = self._pending.get(approval_id)
        if pending is None or pending.future.done():
            raise ApprovalNotPendingError(approval_id)
        pending.future.set_result(approved)
        return pending

    def list_pending(self, *, session_id: str | None = None) -> tuple[PendingWebApproval, ...]:
        pending = (
            item
            for item in self._pending.values()
            if not item.future.done() and (session_id is None or item.request.session_id == session_id)
        )
        return tuple(sorted(pending, key=lambda item: item.created_at))

    def close(self) -> None:
        self._closed = True
        for pending in tuple(self._pending.values()):
            if not pending.future.done():
                pending.future.cancel()
