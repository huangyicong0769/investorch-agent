from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from investorch.config import AgentModelConfig
from investorch.context import TodoItem
from investorch.inputs import UserInput
from investorch.output import OutputEvent

if TYPE_CHECKING:
    from investorch.agents import AgentRunResult


FollowUpBehavior = Literal["steer", "queue"]
RunPhase = Literal["running", "waiting_approval", "stopping"]


@dataclass(frozen=True, slots=True)
class RunDefaults:
    """Permission and follow-up choices captured when input is submitted."""

    permission_mode: str
    follow_up_behavior: FollowUpBehavior


@dataclass(frozen=True, slots=True)
class RunOptions:
    """Immutable model and behavior selected for an admitted Run."""

    model: AgentModelConfig
    permission_mode: str
    follow_up_behavior: FollowUpBehavior


@dataclass(slots=True)
class ActiveRun:
    run_id: str
    session_id: str
    user_input: UserInput | None
    options: RunOptions
    started_at: datetime
    task: asyncio.Task[AgentRunResult]
    phase: RunPhase = "running"
    stopped_pending_steer_count: int = 0
    todos: list[TodoItem] = field(default_factory=list)


class SessionBusyError(RuntimeError):
    """Raised when a session already owns an active top-level Agent run."""


class RunCapacityExceededError(RuntimeError):
    """A new top-level Run cannot enter the Host's current global capacity."""

    def __init__(self, *, active: int, limit: int) -> None:
        self.active = active
        self.limit = limit
        super().__init__(
            f"capacity_exceeded: {active}/{limit} Runs are active. Wait for a Run to finish or stop one, then retry."
        )


@dataclass(frozen=True, slots=True)
class RuntimeOutput:
    run_id: str
    session_id: str
    event: OutputEvent


@dataclass(frozen=True, slots=True)
class PendingSteer:
    steer_id: str
    session_id: str
    source_run_id: str
    user_input: UserInput
    options: RunDefaults
    created_at: datetime
    journal_seq: int | None = None

    @property
    def text(self) -> str:
        return self.user_input.text


@dataclass(frozen=True, slots=True)
class QueuedInput:
    queue_id: str
    session_id: str
    user_input: UserInput
    options: RunDefaults
    created_at: datetime

    @property
    def text(self) -> str:
        return self.user_input.text


@dataclass(frozen=True, slots=True)
class FollowUpSubmission:
    session_id: str
    active_run_id: str
    behavior: FollowUpBehavior
    follow_up_id: str


@dataclass(frozen=True, slots=True)
class RuntimeFollowUpEvent:
    kind: Literal["steer_submitted", "steer_fallback_promoted", "queue_submitted", "queue_promoted"]
    session_id: str
    run_id: str
    source_run_id: str
    follow_up_id: str
    user_input: UserInput
    journal_seq: int | None

    @property
    def text(self) -> str:
        return self.user_input.text


@dataclass(frozen=True, slots=True)
class RuntimeSessionSnapshot:
    session_id: str
    run_id: str | None
    run_started_at: datetime | None
    run_phase: RunPhase | None
    active_follow_up_behavior: FollowUpBehavior | None
    queued_count: int
    queue_paused: bool
    pending_steer_count: int
    todos: tuple[TodoItem, ...]


@dataclass(frozen=True, slots=True)
class RuntimeRunEnded:
    session_id: str
    run_id: str
    status: Literal["completed", "cancelled", "failed"]
    started_at: datetime
    ended_at: datetime
    result: AgentRunResult | None
    discarded_steer_count: int = 0
    discarded_steer_seqs: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    approval_id: str
    run_id: str
    session_id: str
    user_input: str
    permission_mode: str
    tool_name: str
    arguments: str | None
    instruction_head_seq: int | None = None
