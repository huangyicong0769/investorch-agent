from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator
from zoneinfo import ZoneInfo

from agents import ModelSettings, OpenAIResponsesModel
from agents.mcp import MCPServer, MCPServerManager, MCPServerStdio
from openai import AsyncOpenAI

from qmt_agent.agents import AgentLoop, ApprovalOutcome, create_agent, create_compaction_agent, create_title_agent
from qmt_agent.config import AppConfig
from qmt_agent.context import AppState, ExecutionState
from qmt_agent.journal import SessionJournal
from qmt_agent.mcp import load_mcp_servers as load_configured_mcp_servers
from qmt_agent.runtime import (
    AgentRuntime,
    ApprovalRequest,
    RuntimeFollowUpEvent,
    RuntimeOutput,
    RuntimeRunEnded,
    RuntimeSessionSnapshot,
)
from qmt_agent.storage import create_session, delete_unused_session, list_sessions
from qmt_agent.tools import close_execution, start_execution

from .sessions import SessionOperations

logger = logging.getLogger(__name__)

ApplicationOutputHandler = Callable[[RuntimeOutput, int | None], Awaitable[None]]
ApplicationFollowUpHandler = Callable[[RuntimeFollowUpEvent], Awaitable[None]]
ApplicationRunEndedHandler = Callable[[RuntimeRunEnded], Awaitable[None]]
ApplicationRuntimeStateHandler = Callable[[RuntimeSessionSnapshot], None]
RuntimeApprovalHandler = Callable[[ApprovalRequest], Awaitable[ApprovalOutcome]]
ApprovalHandlerFactory = Callable[[AppState, SessionJournal], RuntimeApprovalHandler]


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


@dataclass(slots=True)
class ApplicationHost:
    config: AppConfig
    state: AppState
    execution: ExecutionState
    journal: SessionJournal
    runtime: AgentRuntime
    sessions: SessionOperations
    initial_session_id: str


def create_model(config: AppConfig, agent: str) -> tuple[OpenAIResponsesModel, ModelSettings]:
    model = config.model(agent)
    client = AsyncOpenAI(api_key=config.secret(model.api_key_secret), base_url=model.base_url)
    return OpenAIResponsesModel(model=model.name, openai_client=client), ModelSettings(reasoning={"effort": model.reasoning_effort})


def _load_agent_mcp_servers(config: AppConfig) -> list[MCPServer]:
    servers = load_configured_mcp_servers(config.mcp_config_path, config.secrets, config["mcp.default_timeout_seconds"])
    if not config["backtest.use_cnequity"]:
        return servers

    cnequity_server = MCPServerStdio(
        name="cnequity",
        params={"command": sys.executable, "args": ["-m", "cnequity", "mcp", "--config", str(config.cnequity_config_path)], "cwd": str(config.root)},
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
    approval_handler_factory: ApprovalHandlerFactory,
    callbacks: ApplicationCallbacks | None = None,
) -> AsyncIterator[ApplicationHost]:
    callbacks = callbacks or ApplicationCallbacks()
    journal = SessionJournal(config.session_journal_dir, ZoneInfo(config["runtime.default_timezone"]))
    await _discard_legacy_unused_sessions(config, journal)

    initial_session_id = uuid.uuid4().hex
    await asyncio.to_thread(create_session, config.sessions_db, initial_session_id)
    execution = ExecutionState()
    state = AppState(
        config=config,
        execution=execution,
        selected_session_id=initial_session_id,
        main_reasoning_effort=config.model("main").reasoning_effort,
        permission_mode=config["permission.mode"],
    )
    logger.info("Started session %s", initial_session_id)

    async def record_user_message(session_id: str, text: str) -> int:
        return await journal.record_user_message(session_id, text)

    async def record_user_steer(session_id: str, run_id: str, text: str) -> int:
        return await journal.record_user_steer(session_id, run_id, text)

    async def handle_output(output: RuntimeOutput) -> None:
        journal_seq = None
        try:
            journal_seq = await journal.record_output(output.session_id, output.event)
        except Exception:
            logger.exception("Failed to append output to session journal for session %s", output.session_id)
        await callbacks.handle_output(output, journal_seq)

    runtime: AgentRuntime | None = None
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
                agent = create_agent(model=main_model, model_settings=main_model_settings, config=config, mcp_servers=mcp_manager.active_servers)
                agent_loop = AgentLoop(
                    agent,
                    create_title_agent(title_model, title_model_settings),
                    create_compaction_agent(compact_model, compact_model_settings),
                    config,
                )
                runtime = AgentRuntime(
                    agent_loop,
                    execution,
                    config.sessions_db,
                    handle_output,
                    approval_handler_factory(state, journal),
                    record_user_message,
                    record_user_steer,
                    state_handler=callbacks.handle_runtime_state,
                    run_ended_handler=callbacks.handle_run_ended,
                    follow_up_handler=callbacks.handle_follow_up,
                )
                sessions = SessionOperations(config=config, runtime=runtime, journal=journal)
                yield ApplicationHost(
                    config=config,
                    state=state,
                    execution=execution,
                    journal=journal,
                    runtime=runtime,
                    sessions=sessions,
                    initial_session_id=initial_session_id,
                )
            finally:
                if runtime is not None:
                    await runtime.aclose()
                    runtime = None
                if execution.sandbox is not None:
                    await close_execution(execution)
    finally:
        if runtime is not None:
            await runtime.aclose()
        if execution.sandbox is not None:
            await close_execution(execution)
        await _discard_unused_session(config, journal, state.selected_session_id)
