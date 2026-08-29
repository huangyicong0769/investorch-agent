import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agents import Agent, Runner, SQLiteSession

from qmt_agent.config import AppConfig
from qmt_agent.context import AgentContext, ExecutionState
from qmt_agent.output import AssistantMessage, OutputHandler, consume_run_events

from .compact import CompactionResult, compact_session, session_history_restore_failed
from .title import ensure_session_title
from .usage import TokenUsage

logger = logging.getLogger(__name__)

ApprovalHandler = Callable[[str, str, str | None], Awaitable[bool]]
ReasoningEffortProvider = Callable[[], str]


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    output: str
    main_usage: TokenUsage
    auxiliary_usage: TokenUsage
    auto_compaction: CompactionResult | None = None
    auto_compaction_failed: bool = False
    auto_compaction_consistency_uncertain: bool = False


def should_auto_compact(*, enabled: bool, context_tokens: int | None, context_window_tokens: int, trigger_ratio: float) -> bool:
    if not enabled or context_tokens is None:
        return False
    return context_tokens >= math.floor(context_window_tokens * trigger_ratio)


class AgentLoop:
    def __init__(
        self,
        agent: Agent[AgentContext],
        title_agent: Agent,
        compaction_agent: Agent,
        config: AppConfig,
        reasoning_effort: ReasoningEffortProvider,
        approval_handler: ApprovalHandler,
        output_handler: OutputHandler,
    ) -> None:
        self._agent = agent
        self._title_agent = title_agent
        self._compaction_agent = compaction_agent
        self._config = config
        self._reasoning_effort = reasoning_effort
        self._approval_handler = approval_handler
        self._output_handler = output_handler

    async def run(self, user_input: str, session: SQLiteSession, execution: ExecutionState) -> AgentRunResult:
        self._agent.model_settings = self._agent.model_settings.resolve(
            {"reasoning": {"effort": self._reasoning_effort()}}
        )
        agent_context = AgentContext(config=self._config, execution=execution)
        result = Runner.run_streamed(
            self._agent,
            user_input,
            session=session,
            context=agent_context,
            max_turns=self._config["runtime.max_turns"],
        )

        current_agent_name = self._agent.name

        while True:
            current_agent_name = await consume_run_events(
                result,
                self._output_handler,
                current_agent_name,
            )

            if not result.interruptions:
                break

            sdk_state = result.to_state()

            for interruption in result.interruptions:
                approved = await self._approval_handler(
                    user_input,
                    interruption.name or "unknown_tool",
                    interruption.arguments,
                )

                if approved:
                    sdk_state.approve(interruption, always_approve=False)
                else:
                    sdk_state.reject(
                        interruption,
                        always_reject=False,
                        rejection_message="The tool action was rejected.",
                    )

            result = Runner.run_streamed(
                self._agent,
                sdk_state,
                session=session,
                max_turns=self._config["runtime.max_turns"],
            )

        output = str(result.final_output)
        main_usage = TokenUsage.from_sdk(result.context_wrapper.usage)
        title_usage = await ensure_session_title(self._title_agent, session, self._config.sessions_db)
        await self._output_handler(AssistantMessage(text=output))
        auto_compaction, auto_compaction_failed, consistency_uncertain = await self._auto_compact(session, main_usage)
        auxiliary_usage = title_usage + (auto_compaction.usage if auto_compaction is not None else TokenUsage())
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

    async def _auto_compact(self, session: SQLiteSession, main_usage: TokenUsage) -> tuple[CompactionResult | None, bool, bool]:
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
        except Exception as exc:
            consistency_uncertain = session_history_restore_failed(exc)
            if consistency_uncertain:
                logger.exception(
                    "Automatic context compaction failed and session history restoration was unsuccessful: session=%s context_tokens=%d threshold=%d",
                    session.session_id,
                    context_tokens,
                    threshold,
                )
            else:
                logger.exception(
                    "Automatic context compaction failed; existing context was kept: session=%s context_tokens=%d threshold=%d",
                    session.session_id,
                    context_tokens,
                    threshold,
                )
            return None, True, consistency_uncertain

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
