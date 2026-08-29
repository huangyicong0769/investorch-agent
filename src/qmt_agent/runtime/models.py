from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from qmt_agent.output import OutputEvent

if TYPE_CHECKING:
    from qmt_agent.agents import AgentRunResult


@dataclass(frozen=True, slots=True)
class RunOptions:
    """Future-run defaults captured when a top-level Agent run starts."""

    reasoning_effort: str
    permission_mode: str


@dataclass(slots=True)
class ActiveRun:
    run_id: str
    session_id: str
    user_input: str
    options: RunOptions
    started_at: datetime
    task: asyncio.Task[AgentRunResult]


class SessionBusyError(RuntimeError):
    """Raised when a session already owns an active top-level Agent run."""


@dataclass(frozen=True, slots=True)
class RuntimeOutput:
    run_id: str
    session_id: str
    event: OutputEvent


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    run_id: str
    session_id: str
    user_input: str
    permission_mode: str
    tool_name: str
    arguments: str | None
