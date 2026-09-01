from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agents import Agent

from investorch.agents import TokenUsage, generate_activity_label
from investorch.config import AppConfig
from investorch.journal import SessionJournal
from investorch.output import Reasoning, ToolCalled
from investorch.runtime import AgentRuntime, RuntimeOutput

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ActivityLabelEvent:
    session_id: str
    run_id: str
    target_seq: int
    journal_seq: int
    text: str
    usage: TokenUsage


ActivityLabelHandler = Callable[[ActivityLabelEvent], Awaitable[None]]


async def _ignore_activity_label(_event: ActivityLabelEvent) -> None:
    pass


class ActivityCoordinator:
    def __init__(
        self,
        *,
        config: AppConfig,
        activity_agent: Agent,
        journal: SessionJournal,
        runtime: AgentRuntime,
        label_handler: ActivityLabelHandler = _ignore_activity_label,
    ) -> None:
        self._config = config
        self._activity_agent = activity_agent
        self._journal = journal
        self._runtime = runtime
        self._label_handler = label_handler
        self._reasoning_by_run: dict[str, list[str]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    def observe(self, output: RuntimeOutput, *, journal_seq: int | None) -> None:
        if self._closed:
            return
        if isinstance(output.event, Reasoning):
            self._reasoning_by_run.setdefault(output.run_id, []).append(output.event.text)
            return
        if not isinstance(output.event, ToolCalled):
            return

        reasoning = "".join(self._reasoning_by_run.pop(output.run_id, []))
        if journal_seq is None:
            return
        active_run = self._runtime.get_active_run(output.session_id)
        user_message = active_run.user_input if active_run is not None and active_run.run_id == output.run_id else ""
        task = asyncio.create_task(
            self._generate_label(
                output=output,
                target_seq=journal_seq,
                user_message=user_message,
                reasoning=reasoning,
            ),
            name=f"activity-label-{output.session_id}-{journal_seq}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def finish_run(self, run_id: str) -> None:
        self._reasoning_by_run.pop(run_id, None)

    async def aclose(self) -> None:
        self._closed = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._reasoning_by_run.clear()

    async def _generate_label(
        self, *, output: RuntimeOutput, target_seq: int, user_message: str, reasoning: str
    ) -> None:
        event = output.event
        assert isinstance(event, ToolCalled)
        try:
            result = await generate_activity_label(
                self._activity_agent,
                self._config,
                user_message,
                reasoning,
                event.name,
                event.arguments,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Activity label generation failed for tool %s: %s", event.name, exc)
            return

        try:
            journal_seq = await self._journal.record_activity_label(output.session_id, target_seq, result.label)
        except Exception:
            logger.exception(
                "Failed to append activity label to session journal for session %s target %d",
                output.session_id,
                target_seq,
            )
            return

        try:
            await self._label_handler(
                ActivityLabelEvent(
                    session_id=output.session_id,
                    run_id=output.run_id,
                    target_seq=target_seq,
                    journal_seq=journal_seq,
                    text=result.label,
                    usage=result.usage,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Activity label handler failed for session %s target %d", output.session_id, target_seq)
