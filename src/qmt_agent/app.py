import asyncio
import logging
from pathlib import Path

from agents import set_tracing_disabled

from qmt_agent.agents import (
    ApprovalOutcome,
    PermissionReview,
    TokenUsage,
    build_bootstrap_sync_prompt,
    create_activity_agent,
    create_bootstrap_sync_agent,
    create_permission_agent,
    review_permission,
    run_bootstrap_sync,
)
from qmt_agent.application import ApplicationCallbacks, SessionOperations, create_model, open_application_host
from qmt_agent.commands import dispatch_command, parse_command
from qmt_agent.config import AppConfig, load_config
from qmt_agent.context import AgentContext, AppState, ExecutionState
from qmt_agent.initializer import initialize, sync_bootstrap_files
from qmt_agent.journal import SessionJournal
from qmt_agent.log import configure_logging
from qmt_agent.runtime import (
    ApprovalRequest,
    AgentRuntime,
    RunOptions,
    RuntimeFollowUpEvent,
    RuntimeRunEnded,
    RuntimeOutput,
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
            RunOptions(reasoning_effort=state.main_reasoning_effort, permission_mode=state.permission_mode, follow_up_behavior=state.follow_up_behavior),
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


async def _run_configured_app(ui: ConsoleUI, config: AppConfig, initialized: bool, sync: bool, sync_force: bool, plain: bool) -> None:
    def report_sync_progress(index: int, total: int, target: Path, status: str) -> None:
        relative_target = target.relative_to(config.workspace_dir)
        ui.write(f"[{index}/{total}] {status.capitalize()} {relative_target}")

    if initialized and not sync_force:
        logger.info("First initialization completed at %s", config.root)
        ui.write(f"QMT Agent initialized at {config.root}\nPlease configure required secrets in {config.root_config_path} and start QMT Agent again.")
        return

    if sync_force:
        logger.info("Bootstrap force synchronization started")
        result = await sync_bootstrap_files(config, force=True, progress=report_sync_progress)
        backup = result.backup_dir or "none"
        logger.info(
            "Bootstrap force synchronization completed: created=%d updated=%d unchanged=%d backup=%s", result.created, result.updated, result.unchanged, backup
        )
        ui.write(f"Bootstrap files force-synchronized: created={result.created}, updated={result.updated}, unchanged={result.unchanged}, backup={backup}")
        if initialized:
            logger.info("First initialization completed at %s", config.root)
            ui.write(f"QMT Agent initialized at {config.root}\nPlease configure required secrets in {config.root_config_path} before starting QMT Agent.")
        return

    if sync:
        logger.info("Bootstrap synchronization started")
        model, model_settings = create_model(config, "bootstrap")
        agent = create_bootstrap_sync_agent(model, model_settings)

        async def merge_target(target: Path, template: str, exists: bool) -> None:
            context = AgentContext(config=config, execution=ExecutionState(), session_id="bootstrap-sync", run_id="bootstrap-sync")
            prompt = build_bootstrap_sync_prompt(target, config.workspace_dir, template, exists)
            await run_bootstrap_sync(agent, context, prompt, target)

        result = await sync_bootstrap_files(config, merge_target, progress=report_sync_progress)
        backup = result.backup_dir or "none"
        logger.info(
            "Bootstrap synchronization completed: created=%d updated=%d unchanged=%d backup=%s", result.created, result.updated, result.unchanged, backup
        )
        ui.write(f"Bootstrap files synchronized: created={result.created}, updated={result.updated}, unchanged={result.unchanged}, backup={backup}")
        return

    permission_model, permission_model_settings = create_model(config, "permission")
    permission_agent = create_permission_agent(permission_model, permission_model_settings)
    renderer = ConsoleRenderer(ui) if plain else None
    tui: QMTAgentTUI | None = None

    async def handle_output(output: RuntimeOutput, journal_seq: int | None) -> None:
        if renderer is not None:
            await renderer.handle(output.event)
            return

        assert tui is not None
        await tui.handle_output(output.event, session_id=output.session_id, run_id=output.run_id, journal_seq=journal_seq)

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

    def create_approval_handler(_state: AppState, journal: SessionJournal):
        async def handle_approval(request: ApprovalRequest) -> ApprovalOutcome:
            review_usage = TokenUsage()
            review_decision = None
            review_reason = None
            if request.permission_mode == "manual":
                approved = await request_user_approval(request)
                source = "user"
            else:
                try:
                    review_result = await review_permission(permission_agent, config, request.user_input, request.tool_name, request.arguments)
                    review_usage = review_result.usage
                    review = review_result.review
                except Exception:
                    logger.exception("Permission review failed for tool %s; falling back to manual approval", request.tool_name)
                    review = PermissionReview(decision="ask", reason="AutoReview is unavailable; manual approval is required.")

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
                    request.run_id,
                    request.approval_id,
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

        return handle_approval

    callbacks = ApplicationCallbacks(
        handle_output=handle_output,
        handle_follow_up=handle_follow_up,
        handle_run_ended=handle_run_ended,
        handle_runtime_state=handle_runtime_state,
    )
    activity_agent = None
    if not plain:
        activity_model, activity_model_settings = create_model(config, "activity")
        activity_agent = create_activity_agent(activity_model, activity_model_settings)

    async with open_application_host(config, approval_handler_factory=create_approval_handler, callbacks=callbacks) as host:
        if plain:
            await _run_console(host.state, host.runtime, host.sessions, ui)
            return

        assert activity_agent is not None

        async def record_activity_label(session_id: str, target_seq: int, text: str) -> None:
            try:
                await host.journal.record_activity_label(session_id, target_seq, text)
            except Exception:
                logger.exception("Failed to append activity label to session journal for session %s target %d", session_id, target_seq)

        tui = QMTAgentTUI(host.state, config.session_journal_dir, host.journal, activity_agent, record_activity_label)
        tui.bind_runtime(host.runtime, host.sessions)
        await tui.run_async()
