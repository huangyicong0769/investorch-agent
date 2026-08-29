import asyncio
import logging
import sys
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

from agents import ModelSettings, OpenAIResponsesModel, set_tracing_disabled
from agents.mcp import MCPServer, MCPServerManager, MCPServerStdio
from openai import AsyncOpenAI

from qmt_agent.agents import (
    AgentLoop,
    ApprovalOutcome,
    PermissionReview,
    TokenUsage,
    build_bootstrap_sync_prompt,
    create_activity_agent,
    create_agent,
    create_bootstrap_sync_agent,
    create_compaction_agent,
    create_permission_agent,
    create_title_agent,
    review_permission,
    run_bootstrap_sync,
)
from qmt_agent.commands import dispatch_command, parse_command
from qmt_agent.config import AppConfig, load_config
from qmt_agent.context import AgentContext, AppState, ExecutionState
from qmt_agent.initializer import initialize, sync_bootstrap_files
from qmt_agent.journal import SessionJournal
from qmt_agent.log import configure_logging
from qmt_agent.mcp import load_mcp_servers as load_configured_mcp_servers
from qmt_agent.runtime import (
    AgentRuntime,
    ApprovalRequest,
    RunOptions,
    RuntimeFollowUpEvent,
    RuntimeOutput,
    RuntimeRunEnded,
    RuntimeSessionSnapshot,
)
from qmt_agent.storage import create_session, is_session_archived
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


def _create_model(config: AppConfig, agent: str) -> tuple[OpenAIResponsesModel, ModelSettings]:
    model = config.model(agent)
    client = AsyncOpenAI(
        api_key=config.secret(model.api_key_secret),
        base_url=model.base_url,
    )
    return (
        OpenAIResponsesModel(model=model.name, openai_client=client),
        ModelSettings(reasoning={"effort": model.reasoning_effort}),
    )


async def _run_console(
    state: AppState,
    runtime: AgentRuntime,
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
            result = await dispatch_command(
                command,
                state,
                runtime=runtime,
                journal=journal,
            )
            if result.output:
                ui.write(result.output)
            if result.exit_requested:
                break
            continue

        session_id = state.selected_session_id
        if await asyncio.to_thread(
            is_session_archived,
            state.config.sessions_db,
            session_id,
        ):
            ui.write(
                "Archived sessions are read-only. Unarchive or switch sessions first."
            )
            continue
        active_run = runtime.start_run(
            session_id,
            user_input,
            RunOptions(
                reasoning_effort=state.main_reasoning_effort,
                permission_mode=state.permission_mode,
                follow_up_behavior=state.follow_up_behavior,
            ),
        )
        result = await active_run.task
        if result.auto_compaction is not None and result.auto_compaction.changed:
            ui.write("Context compacted automatically.")
        elif result.auto_compaction_consistency_uncertain:
            ui.write("Automatic context compaction failed and context storage may be damaged. Stop this session and see the system log.")
        elif result.auto_compaction_failed:
            ui.write("Automatic context compaction failed; existing context was kept. Use /compact to retry.")


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
    def report_sync_progress(index: int, total: int, target: Path, status: str) -> None:
        relative_target = target.relative_to(config.workspace_dir)
        ui.write(f"[{index}/{total}] {status.capitalize()} {relative_target}")

    if initialized and not sync_force:
        logger.info("First initialization completed at %s", config.root)
        ui.write(
            f"QMT Agent initialized at {config.root}\n"
            f"Please configure required secrets in {config.root_config_path} and start QMT Agent again."
        )
        return

    if sync_force:
        logger.info("Bootstrap force synchronization started")
        result = await sync_bootstrap_files(config, force=True, progress=report_sync_progress)
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

    if sync:
        logger.info("Bootstrap synchronization started")
        model, model_settings = _create_model(config, "bootstrap")
        agent = create_bootstrap_sync_agent(model, model_settings)

        async def merge_target(target: Path, template: str, exists: bool) -> None:
            context = AgentContext(
                config=config,
                execution=ExecutionState(),
                session_id="bootstrap-sync",
                run_id="bootstrap-sync",
            )
            prompt = build_bootstrap_sync_prompt(target, config.workspace_dir, template, exists)
            await run_bootstrap_sync(agent, context, prompt, target)

        result = await sync_bootstrap_files(config, merge_target, progress=report_sync_progress)
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

    initial_session_id = uuid.uuid4().hex
    create_session(config.sessions_db, initial_session_id)
    state = AppState(
        config=config,
        execution=ExecutionState(),
        selected_session_id=initial_session_id,
        main_reasoning_effort=config.model("main").reasoning_effort,
        permission_mode=config["permission.mode"],
    )
    logger.info("Started session %s", state.selected_session_id)
    title_model, title_model_settings = _create_model(config, "title")
    title_agent = create_title_agent(title_model, title_model_settings)
    compact_model, compact_model_settings = _create_model(config, "compact")
    compact_agent = create_compaction_agent(compact_model, compact_model_settings)
    permission_model, permission_model_settings = _create_model(config, "permission")
    permission_agent = create_permission_agent(permission_model, permission_model_settings)
    journal = SessionJournal(
        config.session_journal_dir,
        ZoneInfo(config["runtime.default_timezone"]),
    )
    renderer = ConsoleRenderer(ui) if plain else None
    tui: QMTAgentTUI | None = None

    async def record_user_message(session_id: str, text: str) -> int | None:
        try:
            return await journal.record_user_message(session_id, text)
        except Exception:
            logger.exception("Failed to append user message to session journal for session %s", session_id)
            return None

    async def record_user_steer(session_id: str, run_id: str, text: str) -> int | None:
        try:
            return await journal.record_user_steer(session_id, run_id, text)
        except Exception:
            logger.exception(
                "Failed to append Steer input to session journal for session %s run %s",
                session_id,
                run_id,
            )
            return None

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
        activity_model, activity_model_settings = _create_model(config, "activity")
        activity_agent = create_activity_agent(activity_model, activity_model_settings)
        tui = QMTAgentTUI(
            state,
            config.session_journal_dir,
            journal,
            activity_agent,
            record_activity_label,
        )

    async def handle_output(output: RuntimeOutput) -> None:
        journal_seq = None
        try:
            journal_seq = await journal.record_output(output.session_id, output.event)
        except Exception:
            logger.exception("Failed to append output to session journal for session %s", output.session_id)

        if renderer is not None:
            await renderer.handle(output.event)
            return

        assert tui is not None
        await tui.handle_output(
            output.event,
            session_id=output.session_id,
            run_id=output.run_id,
            journal_seq=journal_seq,
        )

    async def handle_follow_up(event: RuntimeFollowUpEvent) -> None:
        if tui is not None:
            await tui.handle_follow_up(event)

    async def handle_run_ended(event: RuntimeRunEnded) -> None:
        if tui is not None:
            await tui.handle_run_ended(event)

    def handle_runtime_state(snapshot: RuntimeSessionSnapshot) -> None:
        if tui is not None:
            tui.handle_runtime_state(snapshot)

    async def request_user_approval(
        request: ApprovalRequest,
        review_reason: str | None = None,
    ) -> bool:
        if tui is None:
            return await ui.request_tool_approval(request.tool_name, request.arguments, review_reason)
        return await tui.request_tool_approval(request, review_reason)

    async def handle_approval(request: ApprovalRequest) -> ApprovalOutcome:
        review_usage = TokenUsage()
        review_decision = None
        review_reason = None
        if request.permission_mode == "manual":
            approved = await request_user_approval(request)
            source = "user"
        else:
            try:
                review_result = await review_permission(
                    permission_agent,
                    config,
                    request.user_input,
                    request.tool_name,
                    request.arguments,
                )
                review_usage = review_result.usage
                review = review_result.review
            except Exception:
                logger.exception("Permission review failed for tool %s; falling back to manual approval", request.tool_name)
                review = PermissionReview(
                    decision="ask",
                    reason="AutoReview is unavailable; manual approval is required.",
                )

            review_decision = review.decision
            review_reason = review.reason
            if review.decision == "approve":
                logger.info("Permission auto-approved tool %s", request.tool_name)
                approved = True
                source = "permission"
            elif review.decision == "reject":
                logger.info("Permission auto-rejected tool %s", request.tool_name)
                approved = False
                source = "permission"
            else:
                logger.info("Permission escalated tool %s to user", request.tool_name)
                approved = await request_user_approval(request, review.reason)
                source = "user"

        journal_seq = None
        try:
            journal_seq = await journal.record_approval(
                request.session_id,
                request.tool_name,
                request.arguments,
                approved,
                source=source,
                review_decision=review_decision,
                review_reason=review_reason,
            )
        except Exception:
            logger.exception("Failed to append approval to session journal for session %s", request.session_id)

        if tui is not None:
            await tui.report_tool_approval(
                request.session_id,
                request.tool_name,
                request.arguments,
                approved,
                source=source,
                review_decision=review_decision,
                review_reason=review_reason,
                journal_seq=journal_seq,
            )
        elif source == "permission":
            ui.report_permission_decision(request.tool_name, approved, review_reason or "")

        return ApprovalOutcome(approved=approved, usage=review_usage)

    try:
        await start_execution(state.execution, state.config.workspace_dir)

        logger.info("Starting MCP server manager with %d configured servers", len(mcp_servers))
        async with MCPServerManager(mcp_servers, drop_failed_servers=config["mcp.drop_failed_servers"]) as mcp_manager:
            try:
                logger.info("MCP server manager started with %d active servers", len(mcp_manager.active_servers))
                main_model, main_model_settings = _create_model(config, "main")
                agent = create_agent(
                    model=main_model,
                    model_settings=main_model_settings,
                    config=config,
                    mcp_servers=mcp_manager.active_servers,
                )
                agent_loop = AgentLoop(
                    agent,
                    title_agent,
                    compact_agent,
                    config,
                )
                runtime = AgentRuntime(
                    agent_loop,
                    state.execution,
                    config.sessions_db,
                    handle_output,
                    handle_approval,
                    record_user_message,
                    state_handler=handle_runtime_state,
                    run_ended_handler=handle_run_ended,
                    record_user_steer=record_user_steer,
                    follow_up_handler=handle_follow_up,
                )
                try:
                    if tui is None:
                        await _run_console(state, runtime, ui, journal)
                    else:
                        tui.bind_runtime(runtime)
                        await tui.run_async()
                finally:
                    await runtime.aclose()
            finally:
                await close_execution(state.execution)
    finally:
        if state.execution.sandbox is not None:
            await close_execution(state.execution)
