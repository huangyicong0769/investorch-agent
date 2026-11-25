from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

from investorch.agents import AgentLoop, ApprovalOutcome, TokenUsage
from investorch.application.session_models import SessionModelStore
from investorch.config import AppConfig
from investorch.context import ExecutionState
from investorch.journal import SessionJournal
from investorch.runtime import AgentRuntime, RunDefaults, RuntimeRunEnded
from investorch.runtime.models import ApprovalRequest, FollowUpBehavior, RuntimeOutput
from tests.support.config import make_test_config
from tests.support.controlled_agent import ControlledAgentLoop


def run_options(follow_up_behavior: FollowUpBehavior = "steer") -> RunDefaults:
    return RunDefaults(
        permission_mode="manual",
        follow_up_behavior=follow_up_behavior,
    )


@dataclass(slots=True)
class RuntimeHarness:
    config: AppConfig
    journal: SessionJournal
    agent_loop: ControlledAgentLoop
    execution: ExecutionState
    runtime: AgentRuntime
    run_ended: list[RuntimeRunEnded] = field(default_factory=list)
    _condition: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def wait_for_run_ended(self, session_id: str, occurrence: int = 1) -> RuntimeRunEnded:
        async with asyncio.timeout(2):
            async with self._condition:
                await self._condition.wait_for(lambda: len(self._ended_for(session_id)) >= occurrence)
                return self._ended_for(session_id)[occurrence - 1]

    def _ended_for(self, session_id: str) -> list[RuntimeRunEnded]:
        return [event for event in self.run_ended if event.session_id == session_id]


def make_runtime_harness(
    tmp_path: Path,
    *,
    config_overrides: dict[str, dict[str, object]] | None = None,
) -> RuntimeHarness:
    config = make_test_config(tmp_path, config_overrides)
    journal = SessionJournal(config.session_journal_dir, ZoneInfo("UTC"))
    agent_loop = ControlledAgentLoop()
    harness: RuntimeHarness

    async def handle_output(_output: RuntimeOutput) -> None:
        pass

    async def handle_approval(_request: ApprovalRequest) -> ApprovalOutcome:
        return ApprovalOutcome(approved=True, usage=TokenUsage())

    async def handle_run_ended(event: RuntimeRunEnded) -> None:
        async with harness._condition:
            harness.run_ended.append(event)
            harness._condition.notify_all()

    execution = ExecutionState(workspace_root=config.workspace_dir)
    runtime = AgentRuntime(
        cast(AgentLoop, agent_loop),
        execution,
        config.sessions_db,
        handle_output,
        handle_approval,
        journal.record_user_message,
        journal.record_user_steer,
        journal.record_user_steers_activated,
        journal.record_user_steers_discarded,
        session_models=SessionModelStore(config),
        run_ended_handler=handle_run_ended,
        max_concurrent_runs=lambda: config["runtime.max_concurrent_runs"],
    )
    harness = RuntimeHarness(
        config=config,
        journal=journal,
        agent_loop=agent_loop,
        execution=execution,
        runtime=runtime,
    )
    return harness
