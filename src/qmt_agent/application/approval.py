from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from agents import Agent

from qmt_agent.agents import ApprovalOutcome, PermissionDecision, PermissionReview, TokenUsage, review_permission
from qmt_agent.config import AppConfig
from qmt_agent.journal import SessionJournal
from qmt_agent.runtime import ApprovalRequest

logger = logging.getLogger(__name__)

ApprovalSource = Literal["user", "permission"]
ManualApprovalHandler = Callable[[ApprovalRequest, str | None], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class ApprovalResolvedEvent:
    request: ApprovalRequest
    approved: bool
    source: ApprovalSource
    review_decision: PermissionDecision | None
    review_reason: str | None
    journal_seq: int | None


ApprovalResolvedHandler = Callable[[ApprovalResolvedEvent], Awaitable[None]]


async def _ignore_approval_resolved(_event: ApprovalResolvedEvent) -> None:
    pass


class ApprovalCoordinator:
    def __init__(
        self,
        *,
        config: AppConfig,
        permission_agent: Agent,
        journal: SessionJournal,
        manual_handler: ManualApprovalHandler,
        resolved_handler: ApprovalResolvedHandler = _ignore_approval_resolved,
    ) -> None:
        self._config = config
        self._permission_agent = permission_agent
        self._journal = journal
        self._manual_handler = manual_handler
        self._resolved_handler = resolved_handler

    async def handle(self, request: ApprovalRequest) -> ApprovalOutcome:
        review_usage = TokenUsage()
        review_decision: PermissionDecision | None = None
        review_reason = None
        if request.permission_mode == "manual":
            approved = await self._manual_handler(request, None)
            source: ApprovalSource = "user"
        else:
            try:
                review_result = await review_permission(
                    self._permission_agent,
                    self._config,
                    request.user_input,
                    request.tool_name,
                    request.arguments,
                )
                review_usage = review_result.usage
                review = review_result.review
            except Exception:
                logger.exception(
                    "Permission review failed for tool %s; falling back to manual approval", request.tool_name
                )
                review = PermissionReview(
                    decision="ask", reason="AutoReview is unavailable; manual approval is required."
                )

            review_decision = review.decision
            review_reason = review.reason
            if review.decision == "approve":
                logger.info("Permission auto-approved tool %s", request.tool_name)
                approved = True
                source = "permission"
            elif review.decision == "reject":
                logger.info("Permission auto-rejected tool %s", request.tool_name)
                approved = False
                source = "permission"
            else:
                logger.info("Permission escalated tool %s to user", request.tool_name)
                approved = await self._manual_handler(request, review.reason)
                source = "user"

        journal_seq = None
        try:
            journal_seq = await self._journal.record_approval(
                request.session_id,
                request.run_id,
                request.approval_id,
                request.tool_name,
                request.arguments,
                approved,
                source=source,
                review_decision=review_decision,
                review_reason=review_reason,
            )
        except Exception:
            logger.exception("Failed to append approval to session journal for session %s", request.session_id)

        await self._resolved_handler(
            ApprovalResolvedEvent(
                request=request,
                approved=approved,
                source=source,
                review_decision=review_decision,
                review_reason=review_reason,
                journal_seq=journal_seq,
            )
        )
        return ApprovalOutcome(approved=approved, usage=review_usage)
