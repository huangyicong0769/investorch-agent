from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from agents import ModelSettings, OpenAIResponsesModel
from agents.mcp import MCPServer, MCPServerManager, MCPServerStdio
from openai import AsyncOpenAI

from investorch.agents import (
    AgentLoop,
    create_activity_agent,
    create_agent,
    create_compaction_agent,
    create_permission_agent,
    create_review_instruction_compactor,
    create_title_agent,
)
from investorch.config import AppConfig
from investorch.context import AppState, ExecutionState
from investorch.journal import SessionJournal
from investorch.mcp import load_mcp_servers as load_configured_mcp_servers
from investorch.runtime import (
    AgentRuntime,
    RuntimeFollowUpEvent,
    RuntimeOutput,
    RuntimeRunEnded,
    RuntimeSessionSnapshot,
)
from investorch.storage import create_session, delete_unused_session, list_sessions
from investorch.tools import close_execution, start_execution

from .activity import ActivityCoordinator, ActivityLabelEvent, ActivityLabelHandler, _ignore_activity_label
from .approval import ApprovalCoordinator, ApprovalResolvedHandler, ManualApprovalHandler, _ignore_approval_resolved
from .portfolio_context import (
    PortfolioContextOperations,
    PortfolioToolSucceededHandler,
    _ignore_portfolio_tool_succeeded,
)
from .portfolio_sessions import PortfolioSessionWorkflows
from .portfolios import PortfolioOperations
from .presentation_state import SessionPresentationStore
from .sessions import SessionOperations

logger = logging.getLogger(__name__)

ApplicationOutputHandler = Callable[[RuntimeOutput, int | None], Awaitable[None]]
ApplicationFollowUpHandler = Callable[[RuntimeFollowUpEvent], Awaitable[None]]
ApplicationRunEndedHandler = Callable[[RuntimeRunEnded], Awaitable[None]]
ApplicationRuntimeStateHandler = Callable[[RuntimeSessionSnapshot], None]


async def _ignore_output(_output: RuntimeOutput, _journal_seq: int | None) -> None:
    pass


async def _ignore_follow_up(_event: RuntimeFollowUpEvent) -> None:
    pass


async def _ignore_run_ended(_event: RuntimeRunEnded) -> None:
    pass


def _ignore_runtime_state(_snapshot: RuntimeSessionSnapshot) -> None:
    pass


@dataclass(slots=True)
class ApplicationCallbacks:
    handle_output: ApplicationOutputHandler = _ignore_output
    handle_follow_up: ApplicationFollowUpHandler = _ignore_follow_up
    handle_run_ended: ApplicationRunEndedHandler = _ignore_run_ended
    handle_runtime_state: ApplicationRuntimeStateHandler = _ignore_runtime_state
    handle_approval_resolved: ApprovalResolvedHandler = _ignore_approval_resolved
    handle_activity_label: ActivityLabelHandler = _ignore_activity_label
    handle_portfolio_tool_succeeded: PortfolioToolSucceededHandler = _ignore_portfolio_tool_succeeded


@dataclass(slots=True)
class ApplicationHost:
    config: AppConfig
    state: AppState
    execution: ExecutionState
    journal: SessionJournal
    runtime: AgentRuntime
    sessions: SessionOperations
    portfolios: PortfolioOperations
    portfolio_sessions: PortfolioSessionWorkflows
    approvals: ApprovalCoordinator
    activity: ActivityCoordinator | None
    presentation_state: SessionPresentationStore
    session_lifecycle_lock: asyncio.Lock
    initial_session_id: str | None


def create_model(config: AppConfig, agent: str) -> tuple[OpenAIResponsesModel, ModelSettings]:
    model = config.model(agent)
    client = AsyncOpenAI(api_key=config.secret(model.api_key_secret), base_url=model.base_url)
    return OpenAIResponsesModel(model=model.name, openai_client=client), ModelSettings(
        reasoning={"effort": model.reasoning_effort}
    )


def _load_agent_mcp_servers(config: AppConfig) -> list[MCPServer]:
    servers = load_configured_mcp_servers(config.mcp_config_path, config.secrets, config["mcp.default_timeout_seconds"])
    if not config["backtest.use_cnequity"]:
        return servers

    cnequity_server = MCPServerStdio(
        name="cnequity",
        params={
            "command": sys.executable,
            "args": ["-m", "cnequity", "mcp", "--config", str(config.cnequity_config_path)],
            "cwd": str(config.root),
        },
        cache_tools_list=config["cnequity.mcp_cache_tools_list"],
        client_session_timeout_seconds=config["mcp.default_timeout_seconds"],
    )
    return [cnequity_server, *servers]


async def _discard_unused_session(config: AppConfig, journal: SessionJournal, session_id: str) -> bool:
    try:
        if await journal.session_exists(session_id):
            return False
        return await asyncio.to_thread(delete_unused_session, config.sessions_db, session_id)
    except Exception:
        logger.exception("Failed to discard unused session %s", session_id)
        return False


async def _discard_legacy_unused_sessions(config: AppConfig, journal: SessionJournal) -> None:
    records = await asyncio.to_thread(list_sessions, config.sessions_db, include_archived=True)
    discarded = 0
    for record in records:
        discarded += await _discard_unused_session(config, journal, record.session_id)
    if discarded:
        logger.info("Discarded %d legacy unused sessions", discarded)


@asynccontextmanager
async def open_application_host(
    config: AppConfig,
    *,
    manual_approval_handler: ManualApprovalHandler,
    callbacks: ApplicationCallbacks | None = None,
    create_initial_session: bool = True,
    enable_activity: bool = True,
) -> AsyncIterator[ApplicationHost]:
    callbacks = callbacks or ApplicationCallbacks()
    journal = SessionJournal(config.session_journal_dir, ZoneInfo(config["runtime.default_timezone"]))
    presentation_state = SessionPresentationStore()
    await _discard_legacy_unused_sessions(config, journal)

    selected_session_id = uuid.uuid4().hex
    initial_session_id = selected_session_id if create_initial_session else None
    if initial_session_id is not None:
        await asyncio.to_thread(create_session, config.sessions_db, initial_session_id)
        logger.info("Started session %s", initial_session_id)
    execution = ExecutionState()
    portfolios = PortfolioOperations(config=config)
    state = AppState(
        config=config,
        execution=execution,
        selected_session_id=selected_session_id,
        main_reasoning_effort=config.model("main").reasoning_effort,
        permission_mode=config["permission.mode"],
    )

    async def record_user_message(session_id: str, text: str) -> int:
        return await journal.record_user_message(session_id, text)

    async def record_user_steer(session_id: str, run_id: str, text: str) -> int:
        return await journal.record_user_steer(session_id, run_id, text)

    async def record_user_steers_activated(session_id: str, run_id: str, steer_seqs: tuple[int, ...]) -> int:
        return await journal.record_user_steers_activated(session_id, run_id, steer_seqs)

    async def handle_output(output: RuntimeOutput) -> None:
        journal_seq = None
        try:
            journal_seq = await journal.record_output(output.session_id, output.event)
        except Exception:
            logger.exception("Failed to append output to session journal for session %s", output.session_id)
        if activity is not None:
            activity.observe(output, journal_seq=journal_seq)
        await callbacks.handle_output(output, journal_seq)

    async def handle_run_ended(event: RuntimeRunEnded) -> None:
        if activity is not None:
            activity.finish_run(event.run_id)
        try:
            await journal.record_run_ended(
                event.session_id, event.run_id, event.status, event.started_at, event.ended_at
            )
        except Exception:
            logger.exception(
                "Failed to append run-ended event to session journal for session %s run %s",
                event.session_id,
                event.run_id,
            )
        presentation_state.observe_run_ended(event)
        await callbacks.handle_run_ended(event)

    def handle_runtime_state(snapshot: RuntimeSessionSnapshot) -> None:
        presentation_state.observe_runtime(snapshot)
        callbacks.handle_runtime_state(snapshot)

    async def handle_activity_label(event: ActivityLabelEvent) -> None:
        presentation_state.add_usage(event.session_id, event.usage)
        await callbacks.handle_activity_label(event)

    runtime: AgentRuntime | None = None
    activity: ActivityCoordinator | None = None
    try:
        await start_execution(execution, config.workspace_dir)
        mcp_servers = _load_agent_mcp_servers(config)
        logger.info("Starting MCP server manager with %d configured servers", len(mcp_servers))
        async with MCPServerManager(mcp_servers, drop_failed_servers=config["mcp.drop_failed_servers"]) as mcp_manager:
            try:
                logger.info("MCP server manager started with %d active servers", len(mcp_manager.active_servers))
                main_model, main_model_settings = create_model(config, "main")
                title_model, title_model_settings = create_model(config, "title")
                compact_model, compact_model_settings = create_model(config, "compact")
                permission_model, permission_model_settings = create_model(config, "permission")
                portfolio_context = PortfolioContextOperations(
                    config=config,
                    succeeded_handler=callbacks.handle_portfolio_tool_succeeded,
                )
                agent = create_agent(
                    model=main_model,
                    model_settings=main_model_settings,
                    config=config,
                    mcp_servers=mcp_manager.active_servers,
                )
                agent_loop = AgentLoop(
                    agent,
                    create_title_agent(title_model, title_model_settings),
                    create_compaction_agent(compact_model, compact_model_settings),
                    config,
                    portfolios,
                    successful_tool_handler=portfolio_context.observe_successful_tool,
                )
                approvals = ApprovalCoordinator(
                    config=config,
                    permission_agent=create_permission_agent(permission_model, permission_model_settings),
                    review_compaction_agent=create_review_instruction_compactor(
                        permission_model,
                        permission_model_settings,
                    ),
                    journal=journal,
                    manual_handler=manual_approval_handler,
                    resolved_handler=callbacks.handle_approval_resolved,
                )
                runtime = AgentRuntime(
                    agent_loop,
                    execution,
                    config.sessions_db,
                    handle_output,
                    approvals.handle,
                    record_user_message,
                    record_user_steer,
                    record_user_steers_activated,
                    state_handler=handle_runtime_state,
                    run_ended_handler=handle_run_ended,
                    follow_up_handler=callbacks.handle_follow_up,
                )
                if enable_activity:
                    activity_model, activity_model_settings = create_model(config, "activity")
                    activity = ActivityCoordinator(
                        config=config,
                        activity_agent=create_activity_agent(activity_model, activity_model_settings),
                        journal=journal,
                        runtime=runtime,
                        label_handler=handle_activity_label,
                    )
                sessions = SessionOperations(
                    config=config,
                    runtime=runtime,
                    journal=journal,
                    presentation_state=presentation_state,
                )
                portfolio_sessions = PortfolioSessionWorkflows(
                    state=state,
                    runtime=runtime,
                    sessions=sessions,
                    portfolios=portfolios,
                )
                yield ApplicationHost(
                    config=config,
                    state=state,
                    execution=execution,
                    journal=journal,
                    runtime=runtime,
                    sessions=sessions,
                    portfolios=portfolios,
                    portfolio_sessions=portfolio_sessions,
                    approvals=approvals,
                    activity=activity,
                    presentation_state=presentation_state,
                    session_lifecycle_lock=asyncio.Lock(),
                    initial_session_id=initial_session_id,
                )
            finally:
                try:
                    if activity is not None:
                        await activity.aclose()
                finally:
                    activity = None
                    try:
                        if runtime is not None:
                            await runtime.aclose()
                    finally:
                        runtime = None
                        if execution.sandbox is not None:
                            await close_execution(execution)
    finally:
        if runtime is not None:
            await runtime.aclose()
        if execution.sandbox is not None:
            await close_execution(execution)
        if create_initial_session:
            await _discard_unused_session(config, journal, state.selected_session_id)
