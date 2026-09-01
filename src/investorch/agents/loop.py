from __future__ import annotations

import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agents import Agent, Runner, SQLiteSession, UserError

from investorch.config import AppConfig
from investorch.context import AgentContext, ExecutionState, TodoUpdateHandler
from investorch.output import AssistantMessage, OutputHandler, consume_run_events

from .compact import CompactionResult, compact_session, session_history_restore_failed
from .title import ensure_session_title
from .usage import TokenUsage

if TYPE_CHECKING:
    from investorch.runtime.control import RunControl

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ApprovalOutcome:
    approved: bool
    usage: TokenUsage


ApprovalHandler = Callable[[str, str, str | None], Awaitable[ApprovalOutcome]]


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    output: str
    main_usage: TokenUsage
    auxiliary_usage: TokenUsage
    auto_compaction: CompactionResult | None = None
    auto_compaction_failed: bool = False
    auto_compaction_consistency_uncertain: bool = False


def should_auto_compact(
    *, enabled: bool, context_tokens: int | None, context_window_tokens: int, trigger_ratio: float
) -> bool:
    if not enabled or context_tokens is None:
        return False
    return context_tokens >= math.floor(context_window_tokens * trigger_ratio)


class AgentLoop:
    def __init__(
        self, agent: Agent[AgentContext], title_agent: Agent, compaction_agent: Agent, config: AppConfig
    ) -> None:
        self._agent = agent
        self._title_agent = title_agent
        self._compaction_agent = compaction_agent
        self._config = config

    async def run(
        self,
        user_input: str,
        session: SQLiteSession,
        execution: ExecutionState,
        *,
        run_id: str,
        session_id: str,
        reasoning_effort: str,
        approval_handler: ApprovalHandler,
        output_handler: OutputHandler,
        run_control: RunControl,
        todo_update_handler: TodoUpdateHandler | None = None,
    ) -> AgentRunResult:
        settings = self._agent.model_settings.resolve({"reasoning": {"effort": reasoning_effort}})
        run_agent = self._agent.clone(model_settings=settings)
        agent_context = AgentContext(
            config=self._config,
            execution=execution,
            session_id=session_id,
            run_id=run_id,
            todo_update_handler=todo_update_handler,
        )
        result = Runner.run_streamed(
            run_agent, user_input, session=session, context=agent_context, max_turns=self._config["runtime.max_turns"]
        )

        current_agent_name = run_agent.name
        approval_usage = TokenUsage()

        while True:
            run_control.bind_stream(result)
            try:
                current_agent_name = await consume_run_events(result, output_handler, current_agent_name)
            finally:
                run_control.unbind_stream(result)

            sdk_state = result.to_state() if result.interruptions else None
            if sdk_state is not None:
                for interruption in result.interruptions:
                    outcome = await approval_handler(
                        user_input, interruption.name or "unknown_tool", interruption.arguments
                    )
                    approval_usage += outcome.usage

                    if outcome.approved:
                        sdk_state.approve(interruption, always_approve=False)
                    else:
                        sdk_state.reject(
                            interruption, always_reject=False, rejection_message="The tool action was rejected."
                        )

            pending_steers = await run_control.pending_for_boundary(seal_if_empty=not result.interruptions)
            staged_ids: list[str] = []
            if pending_steers:
                sdk_state = sdk_state or result.to_state()
                try:
                    for steer in pending_steers:
                        sdk_state.add_input(steer.text)
                        staged_ids.append(steer.steer_id)
                except UserError as error:
                    if staged_ids:
                        raise RuntimeError("Steer input staging failed after a partial FIFO write") from error
                    run_control.move_pending_to_fallback()
                    logger.info(
                        "Steer input could not be staged and will continue as a subsequent Run: session=%s run=%s count=%d",
                        session_id,
                        run_id,
                        len(pending_steers),
                    )
                else:
                    run_control.mark_staged(staged_ids)
                    logger.info("Steer staged session=%s run=%s count=%d", session_id, run_id, len(staged_ids))

            if sdk_state is not None and (result.interruptions or staged_ids):
                result = Runner.run_streamed(
                    run_agent, sdk_state, session=session, max_turns=self._config["runtime.max_turns"]
                )
                continue
            break

        output = str(result.final_output)
        main_usage = TokenUsage.from_sdk(result.context_wrapper.usage)
        title_usage = await ensure_session_title(self._title_agent, session, self._config.sessions_db)
        await output_handler(AssistantMessage(text=output))
        auto_compaction, auto_compaction_failed, consistency_uncertain = await self._auto_compact(session, main_usage)
        auxiliary_usage = (
            approval_usage + title_usage + (auto_compaction.usage if auto_compaction is not None else TokenUsage())
        )
        return AgentRunResult(
            output=output,
            main_usage=main_usage,
            auxiliary_usage=auxiliary_usage,
            auto_compaction=auto_compaction,
            auto_compaction_failed=auto_compaction_failed,
            auto_compaction_consistency_uncertain=consistency_uncertain,
        )

    async def compact(self, session: SQLiteSession) -> CompactionResult:
        return await compact_session(self._compaction_agent, session, self._config)

    async def _auto_compact(
        self, session: SQLiteSession, main_usage: TokenUsage
    ) -> tuple[CompactionResult | None, bool, bool]:
        context_tokens = main_usage.last_request_total_tokens
        context_window_tokens = self._config.model("main").context_window_tokens
        assert context_window_tokens is not None
        trigger_ratio = self._config["compaction.trigger_ratio"]
        if not should_auto_compact(
            enabled=self._config["compaction.auto_enabled"],
            context_tokens=context_tokens,
            context_window_tokens=context_window_tokens,
            trigger_ratio=trigger_ratio,
        ):
            return None, False, False

        assert context_tokens is not None
        threshold = math.floor(context_window_tokens * trigger_ratio)
        try:
            result = await self.compact(session)
        except BaseException as exc:
            consistency_uncertain = session_history_restore_failed(exc)
            if consistency_uncertain:
                logger.exception(
                    "Automatic context compaction failed and session history restoration was unsuccessful: session=%s context_tokens=%d threshold=%d",
                    session.session_id,
                    context_tokens,
                    threshold,
                )
                return None, True, True
            if not isinstance(exc, Exception):
                raise
            logger.exception(
                "Automatic context compaction failed; existing context was kept: session=%s context_tokens=%d threshold=%d",
                session.session_id,
                context_tokens,
                threshold,
            )
            return None, True, False

        if result.changed:
            logger.info(
                "Context compaction completed: trigger=auto session=%s context_tokens=%d threshold=%d",
                session.session_id,
                context_tokens,
                threshold,
            )
        return result, False, False

    @property
    def agent_name(self) -> str:
        return self._agent.name
