import asyncio
import logging
from pathlib import Path

from agents import set_tracing_disabled

from qmt_agent.agents import (
    build_bootstrap_sync_prompt,
    create_bootstrap_sync_agent,
    run_bootstrap_sync,
)
from qmt_agent.application import (
    ActivityLabelEvent,
    ApplicationCallbacks,
    ApprovalResolvedEvent,
    SessionOperations,
    create_model,
    open_application_host,
)
from qmt_agent.commands import dispatch_command, parse_command
from qmt_agent.config import AppConfig, load_config
from qmt_agent.context import AgentContext, AppState, ExecutionState
from qmt_agent.initializer import initialize, sync_bootstrap_files
from qmt_agent.log import configure_logging
from qmt_agent.runtime import (
    AgentRuntime,
    ApprovalRequest,
    RunOptions,
    RuntimeFollowUpEvent,
    RuntimeOutput,
    RuntimeRunEnded,
    RuntimeSessionSnapshot,
)
from qmt_agent.storage import is_session_archived
from qmt_agent.ui import ConsoleRenderer, ConsoleUI, QMTAgentTUI

logger = logging.getLogger(__name__)


async def _run_console(state: AppState, runtime: AgentRuntime, sessions: SessionOperations, ui: ConsoleUI) -> None:
    while True:
        user_input = (await ui.read_user_input()).strip()

        try:
            command = parse_command(user_input)
        except ValueError as e:
            ui.write(f"Invalid command: {e}")
            continue

        if command is not None:
            result = await dispatch_command(command, state, runtime=runtime, sessions=sessions)
            if result.output:
                ui.write(result.output)
            if result.exit_requested:
                break
            continue

        session_id = state.selected_session_id
        if await asyncio.to_thread(is_session_archived, state.config.sessions_db, session_id):
            ui.write("Archived sessions are read-only. Unarchive or switch sessions first.")
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
            ui.write(
                "Automatic context compaction failed and context storage may be damaged. Stop this session and see the system log."
            )
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
    ui: ConsoleUI, config: AppConfig, initialized: bool, sync: bool, sync_force: bool, plain: bool
) -> None:
    def report_sync_progress(index: int, total: int, target: Path, status: str) -> None:
        relative_target = target.relative_to(config.workspace_dir)
        ui.write(f"[{index}/{total}] {status.capitalize()} {relative_target}")

    if initialized and not sync_force:
        logger.info("First initialization completed at %s", config.root)
        ui.write(
            f"QMT Agent initialized at {config.root}\nPlease configure required secrets in {config.root_config_path} and start QMT Agent again."
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
        ui.write(
            f"Bootstrap files force-synchronized: created={result.created}, updated={result.updated}, unchanged={result.unchanged}, backup={backup}"
        )
        if initialized:
            logger.info("First initialization completed at %s", config.root)
            ui.write(
                f"QMT Agent initialized at {config.root}\nPlease configure required secrets in {config.root_config_path} before starting QMT Agent."
            )
        return

    if sync:
        logger.info("Bootstrap synchronization started")
        model, model_settings = create_model(config, "bootstrap")
        agent = create_bootstrap_sync_agent(model, model_settings)

        async def merge_target(target: Path, template: str, exists: bool) -> None:
            context = AgentContext(
                config=config, execution=ExecutionState(), session_id="bootstrap-sync", run_id="bootstrap-sync"
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
        ui.write(
            f"Bootstrap files synchronized: created={result.created}, updated={result.updated}, unchanged={result.unchanged}, backup={backup}"
        )
        return

    renderer = ConsoleRenderer(ui) if plain else None
    tui: QMTAgentTUI | None = None

    async def handle_output(output: RuntimeOutput, journal_seq: int | None) -> None:
        if renderer is not None:
            await renderer.handle(output.event)
            return

        assert tui is not None
        await tui.handle_output(
            output.event, session_id=output.session_id, run_id=output.run_id, journal_seq=journal_seq
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

    async def request_user_approval(request: ApprovalRequest, review_reason: str | None = None) -> bool:
        if tui is None:
            return await ui.request_tool_approval(request.tool_name, request.arguments, review_reason)
        return await tui.request_tool_approval(request, review_reason)

    async def handle_approval_resolved(event: ApprovalResolvedEvent) -> None:
        request = event.request
        if tui is not None:
            await tui.report_tool_approval(
                request.session_id,
                request.tool_name,
                request.arguments,
                event.approved,
                source=event.source,
                review_decision=event.review_decision,
                review_reason=event.review_reason,
                journal_seq=event.journal_seq,
            )
        elif event.source == "permission":
            ui.report_permission_decision(request.tool_name, event.approved, event.review_reason or "")

    async def handle_activity_label(event: ActivityLabelEvent) -> None:
        if tui is not None:
            await tui.handle_activity_label(event)

    callbacks = ApplicationCallbacks(
        handle_output=handle_output,
        handle_follow_up=handle_follow_up,
        handle_run_ended=handle_run_ended,
        handle_runtime_state=handle_runtime_state,
        handle_approval_resolved=handle_approval_resolved,
        handle_activity_label=handle_activity_label,
    )

    async with open_application_host(
        config, manual_approval_handler=request_user_approval, callbacks=callbacks, enable_activity=not plain
    ) as host:
        if plain:
            await _run_console(host.state, host.runtime, host.sessions, ui)
            return

        tui = QMTAgentTUI(host.state, config.session_journal_dir, host.journal)
        tui.bind_runtime(host.runtime, host.sessions)
        await tui.run_async()
