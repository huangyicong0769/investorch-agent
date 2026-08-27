import logging
import sys
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

from agents import OpenAIResponsesModel, SQLiteSession, set_tracing_disabled
from agents.mcp import MCPServer, MCPServerManager, MCPServerStdio
from openai import AsyncOpenAI

from qmt_agent.agents import (
    AgentLoop,
    build_bootstrap_sync_prompt,
    create_activity_agent,
    create_agent,
    create_bootstrap_sync_agent,
    create_title_agent,
    run_bootstrap_sync,
)
from qmt_agent.commands import dispatch_command, parse_command
from qmt_agent.config import AppConfig, load_config
from qmt_agent.context import AgentContext, AppState, ExecutionState
from qmt_agent.initializer import initialize, sync_bootstrap_files
from qmt_agent.journal import SessionJournal
from qmt_agent.log import configure_logging
from qmt_agent.mcp import load_mcp_servers as load_configured_mcp_servers
from qmt_agent.output import OutputEvent
from qmt_agent.tools import close_execution, start_execution
from qmt_agent.ui import ConsoleRenderer, ConsoleUI, QMTAgentTUI

logger = logging.getLogger(__name__)


def _load_agent_mcp_servers(config: AppConfig) -> list[MCPServer]:
    servers = load_configured_mcp_servers(
        config.mcp_config_path,
        config.secrets,
        config["mcp.default_timeout_seconds"],
    )
    if not config["backtest.use_cnequity"]:
        return servers

    cnequity_server = MCPServerStdio(
        name="cnequity",
        params={"command": sys.executable, "args": ["-m", "cnequity", "mcp", "--config", str(config.cnequity_config_path)], "cwd": str(config.root)},
        cache_tools_list=config["cnequity.mcp_cache_tools_list"],
        client_session_timeout_seconds=config["mcp.default_timeout_seconds"],
    )
    return [cnequity_server, *servers]


def _create_model(config: AppConfig) -> OpenAIResponsesModel:
    client = AsyncOpenAI(
        api_key=config.secret("DEEPSEEK_API_KEY"),
        base_url=config["model.base_url"],
    )
    return OpenAIResponsesModel(model=config["model.name"], openai_client=client)


async def _run_console(
    state: AppState,
    agent_loop: AgentLoop,
    ui: ConsoleUI,
    journal: SessionJournal,
) -> None:
    while True:
        user_input = (await ui.read_user_input()).strip()

        try:
            command = parse_command(user_input)
        except ValueError as e:
            ui.write(f"Invalid command: {e}")
            continue

        if command is not None:
            result = await dispatch_command(command, state)
            if result.output:
                ui.write(result.output)
            if result.exit_requested:
                break
            continue

        session_id = state.session.session_id
        try:
            await journal.record_user_message(session_id, user_input)
        except Exception:
            logger.exception("Failed to append user message to session journal for session %s", session_id)

        await agent_loop.run(user_input, state.session, state.execution)


async def run_app(sync: bool = False, sync_force: bool = False, plain: bool = False) -> None:
    ui = ConsoleUI()
    config = load_config()
    set_tracing_disabled(not config["observability.sdk_tracing_enabled"])

    initialized = initialize(config, copy_bootstrap=not (sync or sync_force))
    configure_logging(config)
    logger.info("QMT Agent started")

    try:
        await _run_configured_app(ui, config, initialized, sync, sync_force, plain)
    except Exception:
        logger.exception("QMT Agent failed")
        raise
    finally:
        logger.info("QMT Agent stopped")


async def _run_configured_app(
    ui: ConsoleUI,
    config: AppConfig,
    initialized: bool,
    sync: bool,
    sync_force: bool,
    plain: bool,
) -> None:
    if initialized and not sync_force:
        logger.info("First initialization completed at %s", config.root)
        ui.write(
            f"QMT Agent initialized at {config.root}\n"
            f"Please configure required secrets in {config.root_config_path} and start QMT Agent again."
        )
        return

    if sync_force:
        logger.info("Bootstrap force synchronization started")
        result = await sync_bootstrap_files(config, force=True)
        backup = result.backup_dir or "none"
        logger.info(
            "Bootstrap force synchronization completed: created=%d updated=%d unchanged=%d backup=%s",
            result.created,
            result.updated,
            result.unchanged,
            backup,
        )
        ui.write(f"Bootstrap files force-synchronized: created={result.created}, updated={result.updated}, unchanged={result.unchanged}, backup={backup}")
        if initialized:
            logger.info("First initialization completed at %s", config.root)
            ui.write(f"QMT Agent initialized at {config.root}\nPlease configure required secrets in {config.root_config_path} before starting QMT Agent.")
        return

    model = _create_model(config)

    if sync:
        logger.info("Bootstrap synchronization started")
        agent = create_bootstrap_sync_agent(model)

        async def merge_target(target: Path, template: str, exists: bool) -> None:
            context = AgentContext(config=config, execution=ExecutionState())
            prompt = build_bootstrap_sync_prompt(target, config.workspace_dir, template, exists)
            await run_bootstrap_sync(agent, context, prompt, target)

        result = await sync_bootstrap_files(config, merge_target)
        backup = result.backup_dir or "none"
        logger.info(
            "Bootstrap synchronization completed: created=%d updated=%d unchanged=%d backup=%s",
            result.created,
            result.updated,
            result.unchanged,
            backup,
        )
        ui.write(f"Bootstrap files synchronized: created={result.created}, updated={result.updated}, unchanged={result.unchanged}, backup={backup}")
        return

    mcp_servers = _load_agent_mcp_servers(config)

    state = AppState(
        config=config,
        execution=ExecutionState(),
        session=SQLiteSession(uuid.uuid4().hex, config.sessions_db),
    )
    logger.info("Started session %s", state.session.session_id)
    title_agent = create_title_agent(model)
    journal = SessionJournal(
        config.session_journal_dir,
        ZoneInfo(config["runtime.default_timezone"]),
    )
    renderer = ConsoleRenderer(ui) if plain else None
    tui: QMTAgentTUI | None = None

    async def record_user_message(session_id: str, text: str) -> None:
        try:
            await journal.record_user_message(session_id, text)
        except Exception:
            logger.exception("Failed to append user message to session journal for session %s", session_id)

    async def record_activity_label(session_id: str, target_seq: int, text: str) -> None:
        try:
            await journal.record_activity_label(session_id, target_seq, text)
        except Exception:
            logger.exception(
                "Failed to append activity label to session journal for session %s target %d",
                session_id,
                target_seq,
            )

    if not plain:
        activity_agent = create_activity_agent(model)
        tui = QMTAgentTUI(
            state,
            config.session_journal_dir,
            activity_agent,
            record_user_message,
            record_activity_label,
        )

    async def handle_output(event: OutputEvent) -> None:
        session_id = state.session.session_id
        if renderer is not None:
            await renderer.handle(event)

            try:
                await journal.record_output(session_id, event)
            except Exception:
                logger.exception("Failed to append output to session journal for session %s", session_id)
            return

        journal_seq = None
        try:
            journal_seq = await journal.record_output(session_id, event)
        except Exception:
            logger.exception("Failed to append output to session journal for session %s", session_id)

        assert tui is not None
        await tui.handle_output(event, session_id=session_id, journal_seq=journal_seq)

    async def handle_approval(tool_name: str, arguments: str | None) -> bool:
        session_id = state.session.session_id
        if tui is None:
            approved = await ui.request_tool_approval(tool_name, arguments)
        else:
            approved = await tui.request_tool_approval(tool_name, arguments)

        try:
            await journal.record_approval(session_id, tool_name, arguments, approved)
        except Exception:
            logger.exception("Failed to append approval to session journal for session %s", session_id)

        return approved

    try:
        await start_execution(state.execution, state.config.workspace_dir)

        logger.info("Starting MCP server manager with %d configured servers", len(mcp_servers))
        async with MCPServerManager(mcp_servers, drop_failed_servers=config["mcp.drop_failed_servers"]) as mcp_manager:
            logger.info("MCP server manager started with %d active servers", len(mcp_manager.active_servers))
            agent = create_agent(
                model=model,
                config=config,
                mcp_servers=mcp_manager.active_servers,
            )
            agent_loop = AgentLoop(
                agent,
                title_agent,
                config,
                handle_approval,
                handle_output,
            )
            if tui is None:
                await _run_console(state, agent_loop, ui, journal)
            else:
                tui.bind_agent_loop(agent_loop)
                await tui.run_async()
    finally:
        try:
            await close_execution(state.execution)
        finally:
            state.session.close()
