from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

from qmt_agent.agents import AgentLoop, ApprovalOutcome, TokenUsage
from qmt_agent.config import AppConfig
from qmt_agent.context import ExecutionState
from qmt_agent.journal import SessionJournal
from qmt_agent.runtime import AgentRuntime, RunOptions, RuntimeFollowUpEvent, RuntimeRunEnded, RuntimeSessionSnapshot
from qmt_agent.runtime.models import ApprovalRequest, FollowUpBehavior, RuntimeOutput
from tests.support.config import make_test_config
from tests.support.controlled_agent import ControlledAgentLoop

RecordUserMessage = Callable[[str, str], Awaitable[int]]
RecordUserSteer = Callable[[str, str, str], Awaitable[int]]


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
    _state_changed: asyncio.Event = field(default_factory=asyncio.Event)

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

    async def wait_for_snapshot(
        self,
        session_id: str,
        predicate: Callable[[RuntimeSessionSnapshot], bool],
    ) -> RuntimeSessionSnapshot:
        async with asyncio.timeout(2):
            while True:
                snapshot = self.runtime.session_snapshot(session_id)
                if predicate(snapshot):
                    return snapshot
                self._state_changed.clear()
                snapshot = self.runtime.session_snapshot(session_id)
                if predicate(snapshot):
                    return snapshot
                await self._state_changed.wait()

    def _ended_for(self, session_id: str) -> list[RuntimeRunEnded]:
        return [event for event in self.run_ended if event.session_id == session_id]

    def _follow_ups_of_kind(self, kind: str) -> list[RuntimeFollowUpEvent]:
        return [event for event in self.follow_ups if event.kind == kind]


class ControllableUserMessageSink:
    def __init__(self, journal: SessionJournal, *, error: Exception | None = None) -> None:
        self._journal = journal
        self._error = error
        self._write_started = asyncio.Event()
        self._release = asyncio.Event()

    async def record(self, session_id: str, text: str) -> int:
        self._write_started.set()
        await self._release.wait()
        if self._error is not None:
            raise self._error
        return await self._journal.record_user_message(session_id, text)

    async def wait_until_write_started(self) -> None:
        async with asyncio.timeout(2):
            await self._write_started.wait()

    def release(self) -> None:
        self._release.set()


class ControllableSteerSink:
    def __init__(self, journal: SessionJournal, *, error: Exception | None = None) -> None:
        self._journal = journal
        self._error = error
        self._write_started = asyncio.Event()
        self._release = asyncio.Event()

    async def record(self, session_id: str, run_id: str, text: str) -> int:
        self._write_started.set()
        await self._release.wait()
        if self._error is not None:
            raise self._error
        return await self._journal.record_user_steer(session_id, run_id, text)

    async def wait_until_write_started(self) -> None:
        async with asyncio.timeout(2):
            await self._write_started.wait()

    def release(self) -> None:
        self._release.set()


class FailingTextUserMessageSink:
    def __init__(self, journal: SessionJournal, failing_text: str) -> None:
        self._journal = journal
        self._failing_text = failing_text

    async def record(self, session_id: str, text: str) -> int:
        if text == self._failing_text:
            raise RuntimeError("controlled journal failure")
        return await self._journal.record_user_message(session_id, text)


def make_runtime_harness(
    tmp_path: Path,
    *,
    record_user_message: RecordUserMessage | None = None,
    record_user_steer: RecordUserSteer | None = None,
) -> RuntimeHarness:
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

    def handle_state(_snapshot: RuntimeSessionSnapshot) -> None:
        harness._state_changed.set()

    runtime = AgentRuntime(
        cast(AgentLoop, agent_loop),
        ExecutionState(workspace_root=config.workspace_dir),
        config.sessions_db,
        handle_output,
        handle_approval,
        record_user_message or journal.record_user_message,
        record_user_steer or journal.record_user_steer,
        state_handler=handle_state,
        run_ended_handler=handle_run_ended,
        follow_up_handler=handle_follow_up,
    )
    harness = RuntimeHarness(config=config, journal=journal, agent_loop=agent_loop, runtime=runtime)
    return harness
