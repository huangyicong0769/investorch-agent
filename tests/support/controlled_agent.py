from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from qmt_agent.agents import AgentRunResult, CompactionResult, TokenUsage


@dataclass(slots=True)
class _ControlledRun:
    session_id: str
    run_id: str
    user_input: str
    release: asyncio.Event = field(default_factory=asyncio.Event)


class ControlledAgentLoop:
    agent_name = "Controlled Test Agent"

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._runs: list[_ControlledRun] = []
        self._failing_inputs: set[str] = set()

    async def run(self, user_input: str, _session: object, _execution: object, **kwargs: object) -> AgentRunResult:
        run = _ControlledRun(
            session_id=str(kwargs["session_id"]),
            run_id=str(kwargs["run_id"]),
            user_input=user_input,
        )
        async with self._condition:
            self._runs.append(run)
            self._condition.notify_all()

        await run.release.wait()
        if user_input in self._failing_inputs:
            raise RuntimeError("controlled Agent failure")
        return AgentRunResult(output=f"completed: {user_input}", main_usage=TokenUsage(), auxiliary_usage=TokenUsage())

    async def compact(self, _session: object) -> CompactionResult:
        return CompactionResult(changed=False, usage=TokenUsage(), source_items=0, summary_chars=0)

    def fail_input(self, user_input: str) -> None:
        self._failing_inputs.add(user_input)

    async def wait_until_started(self, session_id: str, occurrence: int = 1) -> str:
        async with asyncio.timeout(2):
            async with self._condition:
                await self._condition.wait_for(lambda: len(self._matching_runs(session_id)) >= occurrence)
                return self._matching_runs(session_id)[occurrence - 1].run_id

    def complete(self, session_id: str, occurrence: int = -1) -> None:
        runs = self._matching_runs(session_id)
        if not runs:
            raise AssertionError(f"No controlled run has started for {session_id}")
        runs[occurrence].release.set()

    def started_inputs(self, session_id: str) -> list[str]:
        return [run.user_input for run in self._matching_runs(session_id)]

    def _matching_runs(self, session_id: str) -> list[_ControlledRun]:
        return [run for run in self._runs if run.session_id == session_id]
