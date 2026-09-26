import asyncio
import logging

from agents import set_tracing_disabled

from investorch.application import (
    ActivityLabelEvent,
    ApplicationCallbacks,
    ApprovalResolvedEvent,
    SessionOperations,
    open_application_host,
)
from investorch.commands import dispatch_command, parse_command
from investorch.config import AppConfig, load_config
from investorch.context import AppState
from investorch.initializer import initialize
from investorch.log import configure_logging
from investorch.runtime import (
    AgentRuntime,
    ApprovalRequest,
    RunOptions,
    RuntimeFollowUpEvent,
    RuntimeOutput,
    RuntimeRunEnded,
    RuntimeSessionSnapshot,
)
from investorch.storage import is_session_archived
from investorch.ui import ConsoleRenderer, ConsoleUI, InvestOrchAgentTUI

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


async def run_app(plain: bool = False) -> None:
    ui = ConsoleUI()
    config = load_config()
    set_tracing_disabled(not config["observability.sdk_tracing_enabled"])

    initialized = initialize(config)
    configure_logging(config)
    logger.info("InvestOrch Agent started")

    try:
        await _run_configured_app(ui, config, initialized, plain)
    except Exception:
        logger.exception("InvestOrch Agent failed")
        raise
    finally:
        logger.info("InvestOrch Agent stopped")


async def _run_configured_app(ui: ConsoleUI, config: AppConfig, initialized: bool, plain: bool) -> None:
    if initialized:
        logger.info("First initialization completed at %s", config.root)
        ui.write(
            f"InvestOrch Agent initialized at {config.root}\nPlease configure required secrets in {config.root_config_path} and start InvestOrch Agent again."
        )
        return

    renderer = ConsoleRenderer(ui) if plain else None
    tui: InvestOrchAgentTUI | None = None

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

        tui = InvestOrchAgentTUI(host.state, config.session_journal_dir, host.journal)
        tui.bind_runtime(host.runtime, host.sessions)
        await tui.run_async()
