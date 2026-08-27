from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agents import Agent, Runner, SQLiteSession

from qmt_agent.config import AppConfig
from qmt_agent.context import AgentContext, ExecutionState
from qmt_agent.output import AssistantMessage, OutputHandler, consume_run_events

from .title import ensure_session_title
from .usage import TokenUsage

ApprovalHandler = Callable[[str, str | None], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    output: str
    main_usage: TokenUsage
    auxiliary_usage: TokenUsage


class AgentLoop:
    def __init__(
        self,
        agent: Agent[AgentContext],
        title_agent: Agent,
        config: AppConfig,
        approval_handler: ApprovalHandler,
        output_handler: OutputHandler,
    ) -> None:
        self._agent = agent
        self._title_agent = title_agent
        self._config = config
        self._approval_handler = approval_handler
        self._output_handler = output_handler

    async def run(self, user_input: str, session: SQLiteSession, execution: ExecutionState) -> AgentRunResult:
        agent_context = AgentContext(config=self._config, execution=execution)
        result = Runner.run_streamed(
            self._agent,
            user_input,
            session=session,
            context=agent_context,
            max_turns=self._config["runtime.max_turns"],
        )

        while True:
            await consume_run_events(result, self._output_handler)

            if not result.interruptions:
                break

            sdk_state = result.to_state()

            for interruption in result.interruptions:
                approved = await self._approval_handler(
                    interruption.name or "unknown_tool",
                    interruption.arguments,
                )

                if approved:
                    sdk_state.approve(interruption, always_approve=False)
                else:
                    sdk_state.reject(interruption, rejection_message="The user rejected this tool action.")

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
        return AgentRunResult(output=output, main_usage=main_usage, auxiliary_usage=title_usage)
