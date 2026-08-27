from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Label, ListView, Static, TextArea

from agents import Agent

from qmt_agent.agents import TokenUsage, generate_activity_label
from qmt_agent.commands import Command, dispatch_command, parse_command
from qmt_agent.context import AppState
from qmt_agent.journal import read_session_journal
from qmt_agent.output import OutputEvent, ToolCalled
from qmt_agent.storage import get_session_title, list_sessions

from .sidebar import SessionSidebar
from .timeline import ActivityStep, ChatTimeline, format_json

logger = logging.getLogger(__name__)
RUN_BLOCKED_COMMANDS = {"new", "resume", "clear", "effort", "permission"}
RecordUserMessage = Callable[[str, str], Awaitable[None]]
RecordActivityLabel = Callable[[str, int, str], Awaitable[None]]

if TYPE_CHECKING:
    from qmt_agent.agents import AgentLoop


class Composer(Vertical):
    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(self, normal_height: int, approval_arguments_max_height: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self._normal_height = normal_height
        self._approval_arguments_max_height = approval_arguments_max_height
        self._approval_future: asyncio.Future[bool] | None = None

    def compose(self) -> ComposeResult:
        yield Vertical(
            TextArea(
                placeholder="输入消息……",
                show_line_numbers=False,
                soft_wrap=True,
                id="composer-input",
            ),
            Horizontal(
                Label("Ctrl+Enter / Ctrl+S to send", id="send-hint"),
                Button("Send", variant="primary", id="send-button"),
                id="composer-actions",
            ),
            id="composer-normal",
        )
        yield Vertical(
            Label("Approval required", id="approval-title"),
            Static(id="approval-tool", markup=False),
            VerticalScroll(
                Static(id="approval-arguments", markup=False),
                id="approval-arguments-scroll",
            ),
            Horizontal(
                Button("Reject", variant="error", id="reject-button"),
                Button("Approve", variant="success", id="approve-button"),
                id="approval-actions",
            ),
            id="composer-approval",
        )

    def on_mount(self) -> None:
        self.styles.height = self._normal_height
        self.query_one("#approval-arguments-scroll").styles.max_height = self._approval_arguments_max_height
        self.query_one("#composer-approval").display = False

    @property
    def text(self) -> str:
        return self.query_one("#composer-input", TextArea).text

    def clear(self) -> None:
        self.query_one("#composer-input", TextArea).load_text("")

    def focus_input(self) -> None:
        self.query_one("#composer-input", TextArea).focus()

    def submit(self) -> None:
        text = self.text
        if text.strip():
            self.post_message(self.Submitted(text))

    def set_running(self, running: bool) -> None:
        self.query_one("#composer-input", TextArea).disabled = running
        self.query_one("#send-button", Button).disabled = running

    async def request_approval(self, tool_name: str, arguments: str | None) -> bool:
        if self._approval_future is not None and not self._approval_future.done():
            raise RuntimeError("An approval request is already active")

        future = asyncio.get_running_loop().create_future()
        self._approval_future = future
        formatted = format_json(arguments)
        self.query_one("#approval-tool", Static).update(f"Tool · {tool_name}")
        self.query_one("#approval-arguments", Static).update(formatted)
        self.query_one("#approval-arguments-scroll").display = bool(formatted)
        self.query_one("#composer-normal").display = False
        self.query_one("#composer-approval").display = True
        self.styles.height = "auto"
        self.query_one("#reject-button", Button).focus()

        try:
            return await future
        finally:
            if self._approval_future is future:
                self._approval_future = None
            self.query_one("#composer-approval").display = False
            self.query_one("#composer-normal").display = True
            self.styles.height = self._normal_height

    def resolve_approval(self, approved: bool) -> None:
        if self._approval_future is not None and not self._approval_future.done():
            self._approval_future.set_result(approved)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-button":
            self.submit()
        elif event.button.id == "approve-button":
            self.resolve_approval(True)
        elif event.button.id == "reject-button":
            self.resolve_approval(False)


class QMTAgentTUI(App[None]):
    TITLE = "QMT Agent"
    BINDINGS = [
        Binding("ctrl+b", "toggle_sidebar", "Toggle sidebar"),
        Binding("ctrl+enter", "send_message", "Send"),
        Binding("ctrl+s", "send_message", "Send"),
        Binding("escape", "reject_approval", "Reject approval", show=False),
        Binding("ctrl+q", "quit", "Quit"),
    ]
    CSS = """
    Screen {
        background: $surface;
        color: $text;
        layout: vertical;
    }

    #top-bar {
        height: auto;
        min-height: 3;
        padding: 0 1;
        background: $panel;
        border-bottom: solid $primary-background;
    }

    #top-heading {
        height: 1;
    }

    #brand {
        width: auto;
        text-style: bold;
        padding-right: 2;
    }

    #session-heading {
        width: 1fr;
        color: $text-muted;
    }

    #run-status {
        width: auto;
        color: $success;
    }

    #usage-status {
        width: 100%;
        height: auto;
        text-wrap: wrap;
        color: $text-muted;
    }

    #workspace {
        height: 1fr;
    }

    #session-sidebar {
        height: 100%;
        background: $panel;
        border-right: solid $primary-background;
    }

    SessionListItem {
        height: auto;
        padding: 0 1;
    }

    .session-item-content {
        height: auto;
    }

    SessionListItem.current-session {
        border-left: thick $primary;
    }

    .session-title {
        width: 1fr;
    }

    .session-id {
        color: $text-muted;
    }

    #timeline {
        width: 1fr;
        height: 1fr;
        padding: 1 2;
    }

    #conversation-pane {
        width: 1fr;
        height: 100%;
    }

    .user-message, .assistant-message {
        width: 100%;
        height: auto;
        margin-bottom: 1;
    }

    .message-author {
        text-style: bold;
        color: $primary;
    }

    .message-content, .assistant-markdown {
        width: 100%;
        height: auto;
    }

    .activity-group, .activity-step {
        width: 100%;
        height: auto;
        padding-bottom: 0;
        border-top: none;
        color: $text-muted;
    }

    .activity-group > Contents, .activity-step > Contents {
        padding: 0 0 0 1;
    }

    .activity-step-list, .activity-details {
        height: auto;
    }

    .activity-section {
        height: auto;
        padding: 0 1 0 2;
    }

    .activity-section-heading {
        text-style: bold;
        color: $text-muted;
    }

    .activity-section-body {
        height: auto;
        color: $text-muted;
    }

    .system-notice {
        width: 100%;
        height: auto;
        color: $text-muted;
        margin-bottom: 1;
    }

    #composer {
        width: 100%;
        padding: 0 1;
        background: $panel;
        border-top: solid $primary-background;
    }

    #composer-input {
        height: 1fr;
        border: none;
    }

    #composer-actions {
        height: 3;
        align-horizontal: right;
    }

    #send-hint {
        width: 1fr;
        color: $text-muted;
        content-align: left middle;
    }

    #send-button {
        min-width: 10;
    }

    #composer-normal, #composer-approval {
        height: auto;
    }

    #approval-title {
        text-style: bold;
    }

    #approval-tool {
        color: $warning;
    }

    #approval-arguments-scroll, #approval-arguments {
        height: auto;
    }

    #approval-actions {
        height: 3;
        align-horizontal: right;
    }

    #reject-button, #approve-button {
        min-width: 12;
        margin-left: 1;
    }

    .sidebar-hidden #session-sidebar {
        display: none;
    }
    """

    def __init__(
        self,
        state: AppState,
        journal_dir: Path,
        activity_agent: Agent,
        record_user_message: RecordUserMessage,
        record_activity_label: RecordActivityLabel,
    ) -> None:
        super().__init__()
        self.state = state
        self.journal_dir = journal_dir
        self.activity_agent = activity_agent
        self._record_user_message = record_user_message
        self._record_activity_label = record_activity_label
        self.agent_loop: AgentLoop | None = None
        self.session_title: str | None = None
        self._run_active = False
        self._run_status = "● Ready"
        self._run_started_at: float | None = None
        self._known_session_ids: set[str] = set()
        self._current_user_message = ""
        self._session_usage: dict[str, TokenUsage] = {}
        self._main_context_tokens: dict[str, int | None] = {}
        self._main_agent_name: str | None = None

    def compose(self) -> ComposeResult:
        yield Vertical(
            Horizontal(
                Label("QMT Agent", id="brand"),
                Label(self.session_title or "(untitled)", id="session-heading"),
                Label(self._format_run_status(), id="run-status"),
                id="top-heading",
            ),
            Label(self._format_usage_status(), id="usage-status"),
            id="top-bar",
        )
        yield Horizontal(
            SessionSidebar(id="session-sidebar"),
            Vertical(
                ChatTimeline(
                    self.state.config["tui.activity_panel_max_height"],
                    self.state.config["tui.activity_detail_max_height"],
                    self.state.config["tui.message_author_margin_top"],
                    self.state.config["tui.message_author_margin_bottom"],
                    initial_agent_name=self._main_agent_name,
                    id="timeline",
                ),
                Composer(
                    self.state.config["tui.composer_height"],
                    self.state.config["tui.approval_arguments_max_height"],
                    id="composer",
                ),
                id="conversation-pane",
            ),
            id="workspace",
        )

    def on_mount(self) -> None:
        sidebar = self.query_one(SessionSidebar)
        sidebar.styles.width = self.state.config["tui.sidebar_width"]
        sidebar.styles.min_width = self.state.config["tui.sidebar_min_width"]
        self.set_interval(
            self.state.config["tui.run_timer_interval_seconds"],
            self._refresh_run_status,
        )
        self.query_one(Composer).focus_input()
        self.run_worker(
            self._refresh_and_load_current(),
            group="history",
            exclusive=True,
            exit_on_error=False,
        )

    async def on_composer_submitted(self, event: Composer.Submitted) -> None:
        composer = self.query_one(Composer)
        try:
            command = parse_command(event.text)
        except ValueError as exc:
            await self.query_one(ChatTimeline).add_notice(f"Invalid command: {exc}")
            return

        if command is not None:
            composer.clear()
            await self._dispatch_command(command)
            return

        if self._run_active:
            return

        composer.clear()
        await self._start_agent_run(event.text)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        session_id = getattr(item, "session_id", None)
        if not isinstance(session_id, str) or session_id == self.state.session.session_id:
            return
        if self._run_active:
            await self.query_one(ChatTimeline).add_notice("Cannot switch sessions while the Agent is running.")
            return

        await self._dispatch_command(Command("resume", (session_id,)))

    def action_toggle_sidebar(self) -> None:
        self.screen.toggle_class("sidebar-hidden")

    def action_send_message(self) -> None:
        self.query_one(Composer).submit()

    def action_reject_approval(self) -> None:
        self.query_one(Composer).resolve_approval(False)

    def action_quit(self) -> None:
        if self._run_active:
            self.notify("Wait for the active Agent run to finish before exiting.", severity="warning")
            return
        self.exit()

    def set_status(self, status: str) -> None:
        self._run_status = status
        self._refresh_run_status()

    def _format_run_status(self) -> str:
        status = f"{self._run_status} · Effort {self.state.main_reasoning_effort}"
        if self._run_started_at is None:
            return status

        elapsed_seconds = max(0, int(monotonic() - self._run_started_at))
        hours, remainder = divmod(elapsed_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        elapsed = f"{hours:02}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"
        return f"{status} · {elapsed}"

    def _refresh_run_status(self) -> None:
        self.query_one("#run-status", Static).update(self._format_run_status())

    def set_session_title(self, title: str | None) -> None:
        self.session_title = title
        self.query_one("#session-heading", Static).update(title or "(untitled)")

    @staticmethod
    def _format_token_count(value: int) -> str:
        if value >= 1_000_000:
            return f"{value / 1_000_000:.2f}M"
        if value >= 1_000:
            return f"{value / 1_000:.1f}K"
        return str(value)

    def _format_usage_status(self) -> str:
        session_id = self.state.session.session_id
        usage = self._session_usage.get(session_id, TokenUsage())
        context_tokens = self._main_context_tokens.get(session_id)
        context_used = "—" if context_tokens is None else self._format_token_count(context_tokens)
        capacity_tokens = self.state.config.model("main").context_window_tokens
        assert capacity_tokens is not None
        capacity = self._format_token_count(capacity_tokens)
        return (
            f"Tokens · req {usage.requests} · in {self._format_token_count(usage.input_tokens)} "
            f"· cached {self._format_token_count(usage.cached_input_tokens)} "
            f"· write {self._format_token_count(usage.cache_write_input_tokens)} "
            f"· out {self._format_token_count(usage.output_tokens)} "
            f"· reasoning {self._format_token_count(usage.reasoning_output_tokens)} "
            f"· total {self._format_token_count(usage.total_tokens)} "
            f"| Main context · {context_used} / {capacity}"
        )

    def _add_usage(self, session_id: str, usage: TokenUsage, *, main_context: bool = False) -> None:
        self._session_usage[session_id] = self._session_usage.get(session_id, TokenUsage()) + usage
        if main_context:
            self._main_context_tokens[session_id] = usage.last_request_total_tokens
        if session_id == self.state.session.session_id:
            self.query_one("#usage-status", Static).update(self._format_usage_status())

    def bind_agent_loop(self, agent_loop: AgentLoop) -> None:
        self.agent_loop = agent_loop
        self._main_agent_name = agent_loop.agent_name

    async def handle_output(
        self,
        event: OutputEvent,
        *,
        session_id: str,
        journal_seq: int | None,
    ) -> None:
        if session_id != self.state.session.session_id:
            logger.warning("Ignored TUI output for inactive session %s", session_id)
            return

        step = await self.query_one(ChatTimeline).handle_output(event)
        if isinstance(event, ToolCalled) and step is not None:
            step.session_id = session_id
            step.target_seq = journal_seq
            user_message = self._current_user_message
            reasoning = step.label_reasoning
            self.run_worker(
                self._generate_step_activity(
                    step,
                    session_id=session_id,
                    target_seq=journal_seq,
                    user_message=user_message,
                    reasoning=reasoning,
                    tool_name=event.name,
                    arguments=event.arguments,
                ),
                group=f"activity-{session_id}-{journal_seq or id(step)}",
                exit_on_error=False,
            )

    async def request_tool_approval(self, tool_name: str, arguments: str | None) -> bool:
        self.set_status("● Waiting approval")
        try:
            approved = await self.query_one(Composer).request_approval(tool_name, arguments)
        except Exception:
            logger.exception("Inline approval failed for tool %s", tool_name)
            approved = False
        finally:
            self.set_status("● Running" if self._run_active else "● Ready")

        await self.query_one(ChatTimeline).add_approval(tool_name, arguments, approved)
        return approved

    async def refresh_sessions(self) -> None:
        records = await asyncio.to_thread(list_sessions, self.state.config.sessions_db)
        self._known_session_ids = {record.session_id for record in records}
        current_session_id = self.state.session.session_id
        await self.query_one(SessionSidebar).replace_sessions(records, current_session_id)
        title = await asyncio.to_thread(
            get_session_title,
            self.state.config.sessions_db,
            current_session_id,
        )
        self.set_session_title(title)
        self.query_one("#usage-status", Static).update(self._format_usage_status())

    async def _refresh_and_load_current(self) -> None:
        try:
            await self.refresh_sessions()
            await self._load_session_history(self.state.session.session_id)
        except Exception:
            logger.exception("Failed to initialize TUI session history")
            await self.query_one(ChatTimeline).reset()
            await self.query_one(ChatTimeline).add_notice("Unable to load session history. See the system log for details.")
        finally:
            self.query_one(Composer).focus_input()

    async def _load_session_history(self, session_id: str) -> None:
        timeline = self.query_one(ChatTimeline)
        await timeline.reset()
        await timeline.add_notice("Loading session history…")

        try:
            records = await asyncio.to_thread(read_session_journal, self.journal_dir, session_id)
        except FileNotFoundError:
            if session_id != self.state.session.session_id:
                return
            await timeline.reset()
            if session_id in self._known_session_ids:
                await timeline.add_notice("No journal history is available for this older session.")
            else:
                await timeline.add_notice("Ask QMT Agent anything.")
            return
        except Exception:
            logger.exception("Failed to read session journal for session %s", session_id)
            if session_id != self.state.session.session_id:
                return
            await timeline.reset()
            await timeline.add_notice("Session history is unavailable because its journal is invalid. See the system log for details.")
            return

        if session_id != self.state.session.session_id:
            return

        if records:
            await timeline.render_history(records)
        else:
            await timeline.reset()
            await timeline.add_notice("Ask QMT Agent anything.")

    async def _dispatch_command(self, command: Command) -> None:
        timeline = self.query_one(ChatTimeline)
        if self._run_active and command.name in RUN_BLOCKED_COMMANDS:
            await timeline.add_notice(f"Cannot run /{command.name} while the Agent is running.")
            return

        old_session_id = self.state.session.session_id
        result = await dispatch_command(command, self.state)
        if result.exit_requested:
            if result.output:
                await timeline.add_notice(result.output)
            self.exit()
            return

        new_session_id = self.state.session.session_id
        if new_session_id != old_session_id:
            await self.refresh_sessions()
            self.run_worker(
                self._load_session_history_with_notice(new_session_id, result.output),
                group="history",
                exclusive=True,
                exit_on_error=False,
            )
        else:
            if result.output:
                await timeline.add_notice(result.output)
            if command.name == "effort":
                self._refresh_run_status()
            if command.name == "title":
                await self.refresh_sessions()

        self.query_one(Composer).focus_input()

    async def _load_session_history_with_notice(
        self,
        session_id: str,
        notice: str | None,
    ) -> None:
        await self._load_session_history(session_id)
        if notice and session_id == self.state.session.session_id:
            await self.query_one(ChatTimeline).add_notice(notice)

    async def _start_agent_run(self, user_message: str) -> None:
        timeline = self.query_one(ChatTimeline)
        if self.agent_loop is None:
            await timeline.add_notice("Agent runtime is not ready.")
            return

        session_id = self.state.session.session_id
        session = self.state.session
        self._run_active = True
        self._current_user_message = user_message
        self._set_run_controls(running=True)
        await timeline.add_user_message(user_message)

        try:
            await self._record_user_message(session_id, user_message)
        except Exception:
            logger.exception("Failed to append user message to session journal for session %s", session_id)

        self.run_worker(
            self._run_agent(user_message, session_id, session),
            group="agent-run",
            exclusive=True,
            exit_on_error=False,
        )

    async def _run_agent(self, user_message: str, session_id: str, session) -> None:
        try:
            assert self.agent_loop is not None
            result = await self.agent_loop.run(user_message, session, self.state.execution)
            self._add_usage(session_id, result.main_usage, main_context=True)
            self._add_usage(session_id, result.auxiliary_usage)
        except Exception:
            logger.exception("Agent run failed for session %s", session_id)
            await self.query_one(ChatTimeline).add_notice("Agent run failed. See the system log for details.")
        finally:
            self._run_active = False
            self._current_user_message = ""
            self._set_run_controls(running=False)
            try:
                await self.refresh_sessions()
            except Exception:
                logger.exception("Failed to refresh sessions after Agent run")
            self.query_one(Composer).focus_input()

    def _set_run_controls(self, *, running: bool) -> None:
        if running and self._run_started_at is None:
            self._run_started_at = monotonic()
        elif not running:
            self._run_started_at = None
        self.set_status("● Running" if running else "● Ready")
        self.query_one(Composer).set_running(running)
        self.query_one(SessionSidebar).disabled = running

    async def _generate_step_activity(
        self,
        step: ActivityStep,
        *,
        session_id: str,
        target_seq: int | None,
        user_message: str,
        reasoning: str,
        tool_name: str,
        arguments: str | None,
    ) -> None:
        try:
            result = await generate_activity_label(
                self.activity_agent,
                self.state.config,
                user_message,
                reasoning,
                tool_name,
                arguments,
            )
        except Exception as exc:
            logger.warning("Activity label generation failed for tool %s: %s", tool_name, exc)
            return

        self._add_usage(session_id, result.usage)

        if step.is_mounted:
            step.set_activity_label(result.label)

        if target_seq is None:
            return

        try:
            await self._record_activity_label(session_id, target_seq, result.label)
        except Exception:
            logger.exception(
                "Failed to append activity label to session journal for session %s target %d",
                session_id,
                target_seq,
            )
