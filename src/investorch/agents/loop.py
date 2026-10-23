from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agents import Agent, Model, RunConfig, RunContextWrapper, Runner, SQLiteSession, TResponseInputItem, UserError
from agents.items import ModelResponse
from agents.lifecycle import RunHooksBase
from agents.run_config import CallModelData, CallModelInputFilter, ModelInputData
from agents.tool import FunctionTool, Tool

from investorch.config import AgentModelConfig, AppConfig
from investorch.context import AgentContext, ExecutionState, TodoUpdateHandler
from investorch.inputs import UserInput
from investorch.llm.models import ChatGPTResponsesModel, chatgpt_tools
from investorch.output import AssistantMessage, OutputEvent, OutputHandler, consume_run_events
from investorch.output.stream import assistant_message_from_result

from .compact import CompactionResult, compact_session, session_history_restore_failed
from .context_projection import ContextBudget, ContextProjection, ContextProjectionBudgetError
from .continuation import portable_history_filter
from .title import ensure_session_title
from .usage import TokenUsage, UsageTracker

if TYPE_CHECKING:
    from investorch.application.portfolios import PortfolioOperations
    from investorch.application.skills import SkillOperations
    from investorch.runtime.control import RunControl

logger = logging.getLogger(__name__)


class ContextBudgetError(ValueError):
    """A safe preflight failure containing only computed counts and fixed recovery guidance."""

    def __init__(
        self, estimated_tokens: int, budget_tokens: int, *, after_compaction: bool = False, during_run: bool = False
    ) -> None:
        reason = (
            "Context still exceeds the selected model budget after Compact"
            if after_compaction
            else "The new input, instructions and Tools exceed the selected model context budget"
        )
        if during_run:
            reason = "The next model request exceeds the selected model input budget"
        execution = (
            "Next model request was not sent; completed work is preserved." if during_run else "Main was not executed."
        )
        super().__init__(
            f"{reason} (estimated {estimated_tokens:d} tokens; available budget {max(0, budget_tokens):d} tokens). "
            f"{execution} Please shorten the input or select a larger window supported by the model."
        )


@dataclass(frozen=True, slots=True)
class ApprovalOutcome:
    approved: bool
    usage: TokenUsage
    feedback: dict[str, object] | None = None


ApprovalHandler = Callable[[int | None, str, str | None], Awaitable[ApprovalOutcome]]
SuccessfulToolHandler = Callable[[str, str, str, object], Awaitable[None]]
SteerActivatedHandler = Callable[
    [tuple[int, ...]],
    Awaitable[tuple[int, asyncio.CancelledError | None]],
]


async def _ignore_successful_tool(_session_id: str, _run_id: str, _tool_name: str, _result: object) -> None:
    pass


class _SuccessfulToolHooks(RunHooksBase[AgentContext, Agent[AgentContext]]):
    def __init__(
        self,
        *,
        session_id: str,
        run_id: str,
        handler: SuccessfulToolHandler,
        usage_tracker: UsageTracker,
        context_budget: ContextBudget,
    ) -> None:
        self._session_id = session_id
        self._run_id = run_id
        self._handler = handler
        self._usage_tracker = usage_tracker
        self._context_budget = context_budget

    async def on_llm_end(
        self,
        context: RunContextWrapper[AgentContext],
        _agent: Agent[AgentContext],
        response: ModelResponse,
    ) -> None:
        self._context_budget.record_response(response)
        self._usage_tracker.record(context.usage)
        context.context.turn.review_feedback.clear()

    async def on_tool_end(
        self,
        _context: RunContextWrapper[AgentContext],
        _agent: Agent[AgentContext],
        tool: Tool,
        result: object,
    ) -> None:
        await self._handler(self._session_id, self._run_id, tool.name, result)


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


def _run_model_input_filter(
    history: list[TResponseInputItem],
    *,
    projection: ContextProjection | None = None,
    context_window_tokens: int = 0,
) -> CallModelInputFilter:
    """Budget portable history and current Run state for each request."""
    portable_filter = portable_history_filter(history)

    async def filter_input(data: CallModelData[AgentContext]) -> ModelInputData:
        filtered = portable_filter(data)
        if inspect.isawaitable(filtered):
            filtered = await filtered
        tools = await data.agent.get_all_tools(RunContextWrapper(data.context)) if projection is not None else []
        extra_input: list[TResponseInputItem] = []
        if data.context is not None:
            extra_input.extend(
                {"role": "developer", "content": feedback} for feedback in data.context.turn.review_feedback
            )
            if data.context.turn.todos:
                extra_input.append(
                    {
                        "role": "developer",
                        "content": (
                            "Current Run Todo snapshot (state data). Apply the Todo workflow from your instructions "
                            "when transitioning stages and finalizing verified results:\n"
                            + json.dumps(data.context.turn.todos, ensure_ascii=False)
                        ),
                    }
                )
            if data.context.turn.mcp_errors:
                extra_input.append(
                    {
                        "role": "developer",
                        "content": (
                            "Current Run MCP connection failures (state data). Work using available tools can continue. "
                            "Identify the server by name when reporting an affected capability:\n"
                            + json.dumps(
                                [{"server": name, **failure} for name, failure in data.context.turn.mcp_errors.items()],
                                ensure_ascii=False,
                            )
                        ),
                    }
                )
        if projection is None:
            return ModelInputData(input=[*filtered.input, *extra_input], instructions=filtered.instructions)
        try:
            prepared = await projection.prepare(
                filtered.input,
                instructions=filtered.instructions,
                tool_schemas=_tool_schemas(tools),
                context_window_tokens=context_window_tokens,
                extra_input=extra_input,
            )
        except ContextProjectionBudgetError as error:
            raise ContextBudgetError(error.estimated_tokens, error.budget_tokens, during_run=True) from error
        return ModelInputData(input=prepared, instructions=filtered.instructions)

    return filter_input


def _tool_schemas(tools: list[Tool]) -> list[dict[str, Any]]:
    return [
        {"name": tool.name, "description": tool.description, "parameters": tool.params_json_schema}
        if isinstance(tool, FunctionTool)
        else {"type": type(tool).__name__, "protocol_reserve": " " * 1024}
        for tool in tools
    ]


class AgentLoop:
    def __init__(
        self,
        agent: Agent[AgentContext],
        title_agent: Agent,
        compaction_agent: Agent,
        config: AppConfig,
        portfolios: PortfolioOperations,
        model_factory: Callable[[AgentModelConfig], Model],
        successful_tool_handler: SuccessfulToolHandler = _ignore_successful_tool,
        skills: SkillOperations | None = None,
        preflight_compaction_handler: Callable[[str, CompactionResult], None] | None = None,
        usage_handler: Callable[[str, TokenUsage, AgentModelConfig | None], None] | None = None,
        context_handler: Callable[..., None] | None = None,
    ) -> None:
        self._agent = agent
        self._model_factory = model_factory
        self._title_agent = title_agent
        self._compaction_agent = compaction_agent
        self._config = config
        self._portfolios = portfolios
        self._skills = skills
        self._successful_tool_handler = successful_tool_handler
        self._preflight_compaction_handler = preflight_compaction_handler
        self._usage_handler = usage_handler
        self._context_handler = context_handler
        self._context_budgets: dict[str, tuple[AgentModelConfig, ContextBudget]] = {}

    def _context_budget(self, session_id: str, model: AgentModelConfig) -> ContextBudget:
        cached = self._context_budgets.get(session_id)
        if cached is None or cached[0] != model:
            cached = (model, ContextBudget())
            self._context_budgets[session_id] = cached
        return cached[1]

    async def run(
        self,
        user_input: UserInput | str | None,
        session: SQLiteSession,
        execution: ExecutionState,
        *,
        run_id: str,
        session_id: str,
        model_config: AgentModelConfig,
        approval_handler: ApprovalHandler,
        output_handler: OutputHandler,
        run_control: RunControl,
        todo_update_handler: TodoUpdateHandler | None = None,
        instruction_head_seq: int | None = None,
        steer_activated_handler: SteerActivatedHandler | None = None,
        application_instruction: str | None = None,
        preflight_usage_handler: Callable[[TokenUsage], None] | None = None,
    ) -> AgentRunResult:
        settings = self._agent.model_settings.resolve({"reasoning": {"effort": model_config.reasoning_effort}})
        run_agent = self._agent.clone(model=self._model_factory(model_config), model_settings=settings)
        agent_context = AgentContext(
            config=self._config,
            execution=execution,
            session_id=session_id,
            run_id=run_id,
            portfolios=self._portfolios,
            skills=self._skills,
            todo_update_handler=todo_update_handler,
        )
        if isinstance(run_agent.model, ChatGPTResponsesModel):
            all_tools = await run_agent.get_all_tools(RunContextWrapper(agent_context))
            run_agent = run_agent.clone(tools=chatgpt_tools(all_tools), mcp_servers=[])

        def record_main_usage(usage: TokenUsage) -> None:
            if self._usage_handler is not None:
                self._usage_handler(session_id, usage, model_config)

        observed_auxiliary_usage = TokenUsage()

        def record_auxiliary_usage(usage: TokenUsage) -> None:
            nonlocal observed_auxiliary_usage
            observed_auxiliary_usage += usage
            if self._usage_handler is not None:
                self._usage_handler(session_id, usage, None)

        def record_preflight_usage(usage: TokenUsage) -> None:
            record_auxiliary_usage(usage)
            if preflight_usage_handler is not None:
                preflight_usage_handler(usage)

        agent_context.auxiliary_usage_handler = record_auxiliary_usage
        main_usage_tracker = UsageTracker(record_main_usage)
        context_budget = self._context_budget(session_id, model_config)
        hooks = _SuccessfulToolHooks(
            session_id=session_id,
            run_id=run_id,
            handler=self._successful_tool_handler,
            usage_tracker=main_usage_tracker,
            context_budget=context_budget,
        )
        # Application-started workflows contain only developer instructions.
        if isinstance(user_input, str):
            user_input = UserInput(user_input) if user_input else None
        model_input: list[TResponseInputItem] = []
        if application_instruction is not None:
            if not application_instruction.strip():
                raise ValueError("Application instruction must not be empty")
            model_input.append({"role": "developer", "content": application_instruction})
        if user_input is not None:
            model_input.append(user_input_to_response_item(user_input))
        if not model_input:
            raise ValueError("Run requires user input or an application instruction")

        await self._preflight_compact(
            session,
            run_agent,
            agent_context,
            model_input,
            model_config,
            record_preflight_usage,
            context_budget,
        )
        history = await session.get_items()

        def record_context(tokens: int, compacting: bool) -> None:
            if self._context_handler is not None:
                self._context_handler(session_id, tokens, model_config, compacting=compacting)

        projection = ContextProjection(
            self._compaction_agent,
            self._config,
            initial_history_count=sum(item.get("type") != "reasoning" for item in history),
            usage_handler=record_auxiliary_usage,
            context_handler=record_context,
            context_budget=context_budget,
        )
        run_config = RunConfig(
            call_model_input_filter=_run_model_input_filter(
                history, projection=projection, context_window_tokens=model_config.context_window_tokens
            )
        )
        result = Runner.run_streamed(
            run_agent,
            model_input,
            session=session,
            context=agent_context,
            max_turns=self._config["runtime.max_turns"],
            run_config=run_config,
            hooks=hooks,
        )

        current_agent_name = run_agent.name
        approval_usage = TokenUsage()
        last_message: AssistantMessage | None = None

        async def stream_output(event: OutputEvent) -> None:
            nonlocal last_message
            await output_handler(event)
            if isinstance(event, AssistantMessage):
                last_message = event

        try:
            while True:
                run_control.bind_stream(result)
                try:
                    current_agent_name = await consume_run_events(result, stream_output, current_agent_name)
                finally:
                    main_usage_tracker.record(result.context_wrapper.usage)
                    run_control.unbind_stream(result)

                sdk_state = result.to_state() if result.interruptions else None
                if sdk_state is not None:
                    for interruption in result.interruptions:
                        outcome = await approval_handler(
                            instruction_head_seq,
                            interruption.name or "unknown_tool",
                            interruption.arguments,
                        )
                        approval_usage += outcome.usage
                        feedback = None
                        if outcome.feedback:
                            feedback = "Tool approval review feedback (state data):\n" + json.dumps(
                                {**outcome.feedback, "tool_call_id": interruption.call_id},
                                ensure_ascii=False,
                            )

                        if outcome.approved:
                            sdk_state.approve(interruption, always_approve=False)
                            if feedback:
                                agent_context.turn.review_feedback.append(feedback)
                        else:
                            sdk_state.reject(
                                interruption,
                                always_reject=False,
                                rejection_message=feedback or "The tool action was rejected.",
                            )

                pending_steers = await run_control.pending_for_boundary(seal_if_empty=not result.interruptions)
                staged_ids: list[str] = []
                if pending_steers:
                    sdk_state = sdk_state or result.to_state()
                    try:
                        for steer in pending_steers:
                            sdk_state.add_input([user_input_to_response_item(steer.user_input)])
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
                        steer_seqs = tuple(steer.journal_seq for steer in pending_steers)
                        if any(seq is None for seq in steer_seqs):
                            raise RuntimeError(
                                "Steer input reached the Agent boundary without durable journal sequence"
                            )
                        if steer_activated_handler is None:
                            raise RuntimeError("Steer input reached the Agent boundary without an activation recorder")
                        instruction_head_seq, activation_cancellation = await steer_activated_handler(
                            tuple(seq for seq in steer_seqs if seq is not None)
                        )
                        run_control.mark_staged(staged_ids)
                        if activation_cancellation is not None:
                            raise activation_cancellation
                        logger.info("Steer staged session=%s run=%s count=%d", session_id, run_id, len(staged_ids))

                if sdk_state is not None and (result.interruptions or staged_ids):
                    result = Runner.run_streamed(
                        run_agent,
                        sdk_state,
                        session=session,
                        max_turns=self._config["runtime.max_turns"],
                        run_config=run_config,
                        hooks=hooks,
                    )
                    continue
                break
        finally:
            # A cancelled stream may return before the SDK has finished writing tool results.
            # Await its public run task before rewriting the compacted history prefix.
            if result.run_loop_task is not None and not result.run_loop_task.done():
                result.cancel()
                await asyncio.gather(result.run_loop_task, return_exceptions=True)
            main_usage_tracker.record(result.context_wrapper.usage)
            flushed = await projection.flush(session)
            if projection.changed and not flushed and self._context_handler is not None:
                retained = await session.get_items()
                portable = await _run_model_input_filter(retained)(
                    CallModelData(
                        model_data=ModelInputData(input=retained, instructions=None), agent=run_agent, context=None
                    )
                )
                wrapper = RunContextWrapper(agent_context)
                estimate = context_budget.estimate(
                    portable.input,
                    instructions=await run_agent.get_system_prompt(wrapper),
                    tool_schemas=_tool_schemas(await run_agent.get_all_tools(wrapper)),
                )
                self._context_handler(session_id, estimate, model_config)

        message = assistant_message_from_result(result)
        output = message.text
        main_usage = TokenUsage.from_sdk(result.context_wrapper.usage)
        if last_message is None or last_message.text != message.text:
            await output_handler(message)
        elif message.images:
            # Image generation results are separate SDK items; keep them without
            # repeating the final text already delivered by message_output_created.
            await output_handler(AssistantMessage(text="", images=message.images))
        await ensure_session_title(
            self._title_agent, session, self._config.sessions_db, usage_handler=record_auxiliary_usage
        )
        auto_compaction, auto_compaction_failed, consistency_uncertain = await self._auto_compact(
            session, main_usage, model_config, agent_context, record_auxiliary_usage
        )
        auxiliary_usage = observed_auxiliary_usage + approval_usage

        return AgentRunResult(
            output=output,
            main_usage=main_usage,
            auxiliary_usage=auxiliary_usage,
            auto_compaction=auto_compaction,
            auto_compaction_failed=auto_compaction_failed,
            auto_compaction_consistency_uncertain=consistency_uncertain,
        )

    async def _preflight_compact(
        self,
        session: SQLiteSession,
        agent: Agent[AgentContext],
        context: AgentContext,
        model_input: list[TResponseInputItem],
        model_config: AgentModelConfig,
        usage_handler: Callable[[TokenUsage], None] | None,
        context_budget: ContextBudget,
    ) -> CompactionResult | None:
        context_window_tokens = model_config.context_window_tokens
        wrapper = RunContextWrapper(context)
        instructions = await agent.get_system_prompt(wrapper)
        tool_schemas = _tool_schemas(await agent.get_all_tools(wrapper))
        output_reserve = min(8192, max(256, context_window_tokens // 20))
        budget = math.floor(context_window_tokens * self._config["compaction.trigger_ratio"]) - output_reserve

        async def estimate():
            history = await session.get_items()
            portable = portable_history_filter(history)(
                CallModelData(model_data=ModelInputData(input=history, instructions=None), agent=agent, context=None)
            )
            return context_budget.estimate(
                [*portable.input, *model_input],
                instructions=instructions,
                tool_schemas=tool_schemas,
            )

        estimated_tokens = await estimate()
        if self._context_handler is not None:
            self._context_handler(session.session_id, estimated_tokens, model_config)
        if estimated_tokens < budget:
            return None
        try:
            if self._context_handler is not None:
                self._context_handler(session.session_id, estimated_tokens, model_config, compacting=True)
            result = await compact_session(self._compaction_agent, session, self._config, usage_handler=usage_handler)
            if result.changed:
                context_budget.reset()
        except BaseException as error:
            if session_history_restore_failed(error):
                raise RuntimeError(
                    "Preflight Compact failed: continuation consistency uncertain; Main was not executed."
                ) from error
            raise
        finally:
            estimated_tokens = await estimate()
            if self._context_handler is not None:
                self._context_handler(session.session_id, estimated_tokens, model_config)
        if self._preflight_compaction_handler is not None:
            self._preflight_compaction_handler(session.session_id, result)
        estimated_tokens = await estimate()
        if estimated_tokens >= budget:
            raise ContextBudgetError(estimated_tokens, budget, after_compaction=True)
        return result

    async def compact(
        self,
        session: SQLiteSession,
        *,
        model_config: AgentModelConfig | None = None,
        context: AgentContext | None = None,
        usage_handler: Callable[[TokenUsage], None] | None = None,
    ) -> CompactionResult:
        model_config = model_config or self._config.agent_model("main")
        context = context or AgentContext(
            config=self._config,
            execution=ExecutionState(),
            session_id=session.session_id,
            run_id="",
            portfolios=self._portfolios,
            skills=self._skills,
        )
        wrapper = RunContextWrapper(context)
        instructions = await self._agent.get_system_prompt(wrapper)
        schemas = _tool_schemas(await self._agent.get_all_tools(wrapper))
        context_budget = self._context_budget(session.session_id, model_config)

        async def notify(compacting: bool) -> None:
            if self._context_handler is not None:
                history = await session.get_items()
                filtered = await _run_model_input_filter(history)(
                    CallModelData(
                        model_data=ModelInputData(input=history, instructions=None), agent=self._agent, context=None
                    )
                )
                estimate = context_budget.estimate(filtered.input, instructions=instructions, tool_schemas=schemas)
                self._context_handler(session.session_id, estimate, model_config, compacting=compacting)

        await notify(True)
        try:
            result = await compact_session(
                self._compaction_agent,
                session,
                self._config,
                usage_handler=usage_handler
                or (
                    (lambda usage: self._usage_handler(session.session_id, usage, None))
                    if self._usage_handler is not None
                    else None
                ),
            )
            if result.changed:
                context_budget.reset()
            return result
        finally:
            await notify(False)

    async def _auto_compact(
        self,
        session: SQLiteSession,
        main_usage: TokenUsage,
        model_config: AgentModelConfig,
        context: AgentContext,
        usage_handler: Callable[[TokenUsage], None],
    ) -> tuple[CompactionResult | None, bool, bool]:
        context_window_tokens = model_config.context_window_tokens
        context_tokens = main_usage.last_request_total_tokens
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
            result = await self.compact(
                session, model_config=model_config, context=context, usage_handler=usage_handler
            )
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


def user_input_to_response_item(user_input: UserInput) -> dict[str, Any]:
    content = []
    if user_input.origin.kind == "agent":
        # Provenance accompanies ordinary user content; it never gains system/developer authority.
        session_id = json.dumps(user_input.origin.sender_session_id, ensure_ascii=False)
        run_id = json.dumps(user_input.origin.sender_run_id, ensure_ascii=False)
        content.append(
            {"type": "input_text", "text": f"Message sent by another InvestOrch Session: {session_id} (Run {run_id})."}
        )
    if user_input.text.strip():
        content.append({"type": "input_text", "text": user_input.text})
    content.extend(
        {"type": "input_image", "image_url": image.image_url, "detail": image.detail} for image in user_input.images
    )
    return {"role": "user", "content": content}
