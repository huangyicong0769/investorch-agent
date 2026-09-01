from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

from qmt_agent.agents import AgentLoop, ApprovalOutcome, TokenUsage
from qmt_agent.config import AppConfig
from qmt_agent.context import ExecutionState
from qmt_agent.journal import SessionJournal
from qmt_agent.runtime import AgentRuntime, RunOptions, RuntimeFollowUpEvent, RuntimeRunEnded
from qmt_agent.runtime.models import ApprovalRequest, FollowUpBehavior, RuntimeOutput
from tests.support.config import make_test_config
from tests.support.controlled_agent import ControlledAgentLoop


def run_options(follow_up_behavior: FollowUpBehavior = "steer") -> RunOptions:
    return RunOptions(
        reasoning_effort="none",
        permission_mode="manual",
        follow_up_behavior=follow_up_behavior,
    )


@dataclass(slots=True)
class RuntimeHarness:
    config: AppConfig
    journal: SessionJournal
    agent_loop: ControlledAgentLoop
    runtime: AgentRuntime
    run_ended: list[RuntimeRunEnded] = field(default_factory=list)
    follow_ups: list[RuntimeFollowUpEvent] = field(default_factory=list)
    _condition: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def wait_for_run_ended(self, session_id: str, occurrence: int = 1) -> RuntimeRunEnded:
        async with asyncio.timeout(2):
            async with self._condition:
                await self._condition.wait_for(lambda: len(self._ended_for(session_id)) >= occurrence)
                return self._ended_for(session_id)[occurrence - 1]

    async def wait_for_follow_up(self, kind: str, occurrence: int = 1) -> RuntimeFollowUpEvent:
        async with asyncio.timeout(2):
            async with self._condition:
                await self._condition.wait_for(lambda: len(self._follow_ups_of_kind(kind)) >= occurrence)
                return self._follow_ups_of_kind(kind)[occurrence - 1]

    def _ended_for(self, session_id: str) -> list[RuntimeRunEnded]:
        return [event for event in self.run_ended if event.session_id == session_id]

    def _follow_ups_of_kind(self, kind: str) -> list[RuntimeFollowUpEvent]:
        return [event for event in self.follow_ups if event.kind == kind]


def make_runtime_harness(tmp_path: Path) -> RuntimeHarness:
    config = make_test_config(tmp_path)
    journal = SessionJournal(config.session_journal_dir, ZoneInfo("UTC"))
    agent_loop = ControlledAgentLoop()
    harness: RuntimeHarness

    async def handle_output(_output: RuntimeOutput) -> None:
        return None

    async def handle_approval(_request: ApprovalRequest) -> ApprovalOutcome:
        return ApprovalOutcome(approved=True, usage=TokenUsage())

    async def handle_run_ended(event: RuntimeRunEnded) -> None:
        async with harness._condition:
            harness.run_ended.append(event)
            harness._condition.notify_all()

    async def handle_follow_up(event: RuntimeFollowUpEvent) -> None:
        async with harness._condition:
            harness.follow_ups.append(event)
            harness._condition.notify_all()

    runtime = AgentRuntime(
        cast(AgentLoop, agent_loop),
        ExecutionState(workspace_root=config.workspace_dir),
        config.sessions_db,
        handle_output,
        handle_approval,
        journal.record_user_message,
        journal.record_user_steer,
        run_ended_handler=handle_run_ended,
        follow_up_handler=handle_follow_up,
    )
    harness = RuntimeHarness(config=config, journal=journal, agent_loop=agent_loop, runtime=runtime)
    return harness
