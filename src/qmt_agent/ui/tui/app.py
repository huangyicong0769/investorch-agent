from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from agents import Agent
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Button, Label, ListView, Static, TextArea

from qmt_agent.agents import TokenUsage, generate_activity_label
from qmt_agent.application import (
    ActiveRunChangedError,
    ArchivedSessionInputError,
    QueuedFollowUpsPendingError,
    SteerPromotionPendingError,
    SessionOperations,
    submit_user_input,
)
from qmt_agent.commands import Command, dispatch_command, parse_command
from qmt_agent.context import AppState, TodoItem
from qmt_agent.journal import SessionJournal, read_session_journal
from qmt_agent.output import OutputEvent, Reasoning, ToolCalled
from qmt_agent.runtime import (
    AgentRuntime,
    ApprovalRequest,
    RuntimeFollowUpEvent,
    RuntimeRunEnded,
    RuntimeSessionSnapshot,
    SessionBusyError,
)
from qmt_agent.storage import (
    SessionRecord,
    get_session_title,
    list_sessions,
)

from .approval import ApprovalPanel
from .queue import QueuePanel
from .sidebar import SessionSidebar, session_status_label
from .timeline import ActivityStep, ChatTimeline
from .todo import TodoPanel

logger = logging.getLogger(__name__)
RecordActivityLabel = Callable[[str, int, str], Awaitable[None]]
_STEER_FALLBACK_NOTICE = "The Run finished before the follow-up could be steered; it will continue as the next turn."


@dataclass(frozen=True, slots=True)
class BufferedOutput:
    session_id: str
    journal_seq: int | None
    event: OutputEvent
    activity_reasoning: str
    activity_user_message: str


@dataclass(frozen=True, slots=True)
class BufferedApproval:
    session_id: str
    journal_seq: int | None
    tool_name: str
    arguments: str | None
    approved: bool
    source: str
    review_decision: str | None
    review_reason: str | None


@dataclass(slots=True)
class PendingApproval:
    request: ApprovalRequest
    session_title: str | None
    review_reason: str | None
    future: asyncio.Future[bool]


class Composer(Vertical):
    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class StopRequested(Message):
        pass

    def __init__(self, normal_height: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self._normal_height = normal_height
        self._loading = False

    def compose(self) -> ComposeResult:
        yield TextArea(placeholder="输入消息……", show_line_numbers=False, soft_wrap=True, id="composer-input")
        yield Horizontal(
            Label("Ctrl+Enter / Ctrl+S to send", id="send-hint"),
            Button("Stop", variant="error", id="stop-button"),
            Button("Send", variant="primary", id="send-button"),
            id="composer-actions",
        )

    def on_mount(self) -> None:
        self.styles.height = self._normal_height

    @property
    def text(self) -> str:
        return self.query_one("#composer-input", TextArea).text

    def clear(self) -> None:
        self.query_one("#composer-input", TextArea).load_text("")

    def focus_input(self) -> None:
        self.query_one("#composer-input", TextArea).focus()

    def submit(self) -> None:
        if self._loading:
            return
        text = self.text
        if text.strip():
            self.post_message(self.Submitted(text))

    def set_input_state(self, *, loading: bool, running: bool, run_phase: str | None, follow_up_behavior: str) -> None:
        self._loading = loading
        self.query_one("#composer-input", TextArea).disabled = loading
        self.query_one("#send-button", Button).disabled = loading

        stopping = run_phase == "stopping"
        self.query_one("#stop-button", Button).display = running
        self.query_one("#stop-button", Button).disabled = stopping

        if loading:
            hint = "Loading session history…"
        elif stopping:
            hint = "Stopping current Run…"
        elif running and follow_up_behavior == "steer":
            hint = "Ctrl+Enter / Ctrl+S to send · Follow-ups steer current Run"
        elif running:
            hint = "Ctrl+Enter / Ctrl+S to send · Follow-ups queue next turn"
        else:
            hint = f"Ctrl+Enter / Ctrl+S to send · Follow-ups: {follow_up_behavior.title()}"
        self.query_one("#send-hint", Label).update(hint)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-button":
            self.submit()
        elif event.button.id == "stop-button":
            self.post_message(self.StopRequested())


class QMTAgentTUI(App[None]):
    TITLE = "QMT Agent"
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+b", "toggle_sidebar", "Toggle sidebar"),
        Binding("ctrl+enter", "send_message", "Send"),
        Binding("ctrl+s", "send_message", "Send"),
        Binding("ctrl+g", "stop_run", "Stop Run"),
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

    .session-status {
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

    #interaction-scroll {
        width: 100%;
        height: auto;
        max-height: 9;
        scrollbar-size-vertical: 1;
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

    #send-button, #stop-button {
        min-width: 10;
    }

    #stop-button {
        margin-left: 1;
    }

    .sidebar-hidden #session-sidebar {
        display: none;
    }
    """

    def __init__(self, state: AppState, journal_dir: Path, journal: SessionJournal, activity_agent: Agent, record_activity_label: RecordActivityLabel) -> None:
        super().__init__()
        self.state = state
        self.journal_dir = journal_dir
        self.journal = journal
        self.activity_agent = activity_agent
        self._record_activity_label = record_activity_label
        self.runtime: AgentRuntime | None = None
        self.sessions: SessionOperations | None = None
        self.session_title: str | None = None
        self._known_session_ids: set[str] = set()
        self._session_titles: dict[str, str | None] = {}
        self._session_records: list[SessionRecord] = []
        self._runtime_snapshots: dict[str, RuntimeSessionSnapshot] = {}
        self._session_usage: dict[str, TokenUsage] = {}
        self._main_context_tokens: dict[str, int | None] = {}
        self._main_agent_name: str | None = None
        self._loading_session_id: str | None = None
        self._buffered_live_events: list[BufferedOutput | BufferedApproval | RuntimeFollowUpEvent] = []
        self._last_rendered_seq: dict[str, int] = {}
        self._rendered_steer_seqs: dict[str, set[int]] = {}
        self._pending_follow_up_notices: dict[str, list[str]] = {}
        self._timeline_lock = asyncio.Lock()
        self._reasoning_by_run: dict[str, list[str]] = {}
        self._pending_approvals: deque[PendingApproval] = deque()
        self._session_todos: dict[str, tuple[TodoItem, ...]] = {}
        self._todo_run_ids: dict[str, str] = {}

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
                VerticalScroll(
                    TodoPanel(id="todo-panel"),
                    QueuePanel(id="queue-panel"),
                    ApprovalPanel(self.state.config["tui.approval_arguments_max_height"], id="approval-panel"),
                    id="interaction-scroll",
                ),
                Composer(self.state.config["tui.composer_height"], id="composer"),
                id="conversation-pane",
            ),
            id="workspace",
        )

    def on_mount(self) -> None:
        sidebar = self.query_one(SessionSidebar)
        sidebar.styles.width = self.state.config["tui.sidebar_width"]
        sidebar.styles.min_width = self.state.config["tui.sidebar_min_width"]
        self.set_interval(self.state.config["tui.run_timer_interval_seconds"], self._refresh_run_status)
        self._loading_session_id = self.state.selected_session_id
        self._refresh_selected_controls()
        self.run_worker(self._refresh_and_load_current(), group="history", exclusive=True, exit_on_error=False)

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

        session_id = self.state.selected_session_id
        if self._loading_session_id == session_id:
            return

        if self.runtime is None:
            await self.query_one(ChatTimeline).add_notice("Agent runtime is not ready.")
            return

        try:
            submission = await submit_user_input(state=self.state, runtime=self.runtime, session_id=session_id, text=event.text)
        except ArchivedSessionInputError:
            await self.query_one(ChatTimeline).add_notice("Archived sessions are read-only. Unarchive or switch sessions first.")
            return
        except SteerPromotionPendingError:
            await self.query_one(ChatTimeline).add_notice("A Steer follow-up is being promoted. Please send this message again after it starts.")
            self._refresh_selected_controls()
            return
        except QueuedFollowUpsPendingError as exc:
            notice = (
                "Queued follow-ups are paused. Resume or clear them before sending a new message."
                if exc.paused
                else "Queued follow-ups are pending. Wait for the next Run or clear them first."
            )
            await self.query_one(ChatTimeline).add_notice(notice)
            self._refresh_selected_controls()
            return
        except ActiveRunChangedError:
            await self.query_one(ChatTimeline).add_notice("The active Run changed before this input could be submitted. Please send it again.")
            self._refresh_selected_controls()
            return
        except Exception:
            logger.exception("Failed to submit user input session=%s", session_id)
            await self.query_one(ChatTimeline).add_notice("Input could not be saved and was not sent. Please try again.")
            self._refresh_selected_controls()
            return

        composer.clear()
        if submission.disposition == "run_started":
            await self.query_one(ChatTimeline).add_user_message(event.text)
            await self.refresh_sessions()
        self._refresh_selected_controls()

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        session_id = getattr(item, "session_id", None)
        if not isinstance(session_id, str) or session_id == self.state.selected_session_id:
            return
        await self._dispatch_command(Command("resume", (session_id,)))

    def action_toggle_sidebar(self) -> None:
        self.screen.toggle_class("sidebar-hidden")

    def action_send_message(self) -> None:
        self.query_one(Composer).submit()

    async def action_stop_run(self) -> None:
        await self._dispatch_command(Command("stop", ()))

    def action_reject_approval(self) -> None:
        self.query_one(ApprovalPanel).resolve(False)

    def on_approval_panel_resolved(self, event: ApprovalPanel.Resolved) -> None:
        if not self._pending_approvals:
            return
        pending = self._pending_approvals[0]
        if pending.request.approval_id != event.approval_id or pending.request.run_id != event.run_id:
            self._show_pending_approval()
            return
        self._pending_approvals.popleft()
        if not pending.future.done():
            pending.future.set_result(event.approved)
        self._show_pending_approval()

    async def on_composer_stop_requested(self, event: Composer.StopRequested) -> None:
        await self._dispatch_command(Command("stop", ()))

    def action_quit(self) -> None:
        if self.runtime is not None and (self.runtime.has_active_runs() or self.runtime.has_queued_inputs()):
            self.notify("Stop or finish active Runs and clear queued follow-ups before exiting.", severity="warning")
            return
        self.exit()

    async def on_queue_panel_clear_requested(self, event: QueuePanel.ClearRequested) -> None:
        if self.runtime is None:
            return
        count = self.runtime.clear_queue(event.session_id)
        if event.session_id == self.state.selected_session_id:
            await self.query_one(ChatTimeline).add_notice(f"Cleared {count} queued follow-up{'s' if count != 1 else ''}.")
        self._refresh_selected_controls()

    async def on_queue_panel_resume_requested(self, event: QueuePanel.ResumeRequested) -> None:
        if self.runtime is None:
            return
        try:
            await self.runtime.resume_queue(event.session_id)
        except (SessionBusyError, ValueError) as exc:
            if event.session_id == self.state.selected_session_id:
                await self.query_one(ChatTimeline).add_notice(str(exc))
        self._refresh_selected_controls()

    def _format_run_status(self) -> str:
        selected_session_id = self.state.selected_session_id
        snapshot = self.runtime.session_snapshot(selected_session_id) if self.runtime is not None else None
        active_run = self.runtime.get_active_run(selected_session_id) if self.runtime is not None else None
        follow_up_behavior = (
            snapshot.active_follow_up_behavior if snapshot is not None and snapshot.active_follow_up_behavior is not None else self.state.follow_up_behavior
        )
        status = f"● {session_status_label(snapshot)} · Follow-ups: {follow_up_behavior.title()}"
        if snapshot is not None and snapshot.queued_count:
            status += f" · {snapshot.queued_count} queued"
        if snapshot is not None and snapshot.pending_steer_count:
            status += f" · {snapshot.pending_steer_count} steer pending"
        status += f" · Default effort {self.state.main_reasoning_effort}"
        if active_run is None:
            return status

        elapsed_seconds = max(0, int((datetime.now(UTC) - active_run.started_at).total_seconds()))
        hours, remainder = divmod(elapsed_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        elapsed = f"{hours:02}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"
        return f"{status} · {elapsed}"

    def _refresh_run_status(self) -> None:
        try:
            status = self.query_one("#run-status", Static)
        except NoMatches:
            return
        status.update(self._format_run_status())

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
        session_id = self.state.selected_session_id
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

    def _add_usage(self, session_id: str, usage: TokenUsage) -> None:
        self._session_usage[session_id] = self._session_usage.get(session_id, TokenUsage()) + usage
        if session_id == self.state.selected_session_id:
            self.query_one("#usage-status", Static).update(self._format_usage_status())

    def _set_main_context_tokens(self, session_id: str, context_tokens: int | None) -> None:
        self._main_context_tokens[session_id] = context_tokens
        if session_id == self.state.selected_session_id:
            self.query_one("#usage-status", Static).update(self._format_usage_status())

    def bind_runtime(self, runtime: AgentRuntime, sessions: SessionOperations) -> None:
        self.runtime = runtime
        self.sessions = sessions
        self._main_agent_name = runtime.agent_name

    def handle_runtime_state(self, snapshot: RuntimeSessionSnapshot) -> None:
        previous = self._runtime_snapshots.get(snapshot.session_id)
        self._runtime_snapshots[snapshot.session_id] = snapshot
        if snapshot.run_id is not None:
            if self._todo_run_ids.get(snapshot.session_id) != snapshot.run_id:
                self._todo_run_ids[snapshot.session_id] = snapshot.run_id
            self._session_todos[snapshot.session_id] = tuple(dict(todo) for todo in snapshot.todos)
        if self.is_running and snapshot.session_id == self.state.selected_session_id:
            self.call_later(self._refresh_selected_controls)
        if self.is_running and self._snapshot_presentation(previous) != self._snapshot_presentation(snapshot):
            self.call_later(self._refresh_sidebar_from_cache)

    @staticmethod
    def _snapshot_presentation(snapshot: RuntimeSessionSnapshot | None) -> tuple[object, ...] | None:
        if snapshot is None:
            return None
        return (
            snapshot.run_id,
            snapshot.run_phase,
            snapshot.active_follow_up_behavior,
            snapshot.queued_count,
            snapshot.queue_paused,
            snapshot.pending_steer_count,
        )

    async def _refresh_sidebar_from_cache(self) -> None:
        await self.query_one(SessionSidebar).replace_sessions(self._session_records, self.state.selected_session_id, self._runtime_snapshots)

    async def handle_output(self, event: OutputEvent, *, session_id: str, run_id: str, journal_seq: int | None) -> None:
        activity_reasoning = ""
        activity_user_message = ""
        if isinstance(event, Reasoning):
            self._reasoning_by_run.setdefault(run_id, []).append(event.text)
        elif isinstance(event, ToolCalled):
            activity_reasoning = "".join(self._reasoning_by_run.pop(run_id, []))
            active_run = self.runtime.get_active_run(session_id) if self.runtime is not None else None
            if active_run is not None and active_run.run_id == run_id:
                activity_user_message = active_run.user_input

        async with self._timeline_lock:
            if session_id != self.state.selected_session_id:
                if isinstance(event, ToolCalled):
                    self._start_activity_generation(
                        None,
                        session_id=session_id,
                        target_seq=journal_seq,
                        user_message=activity_user_message,
                        reasoning=activity_reasoning,
                        tool_name=event.name,
                        arguments=event.arguments,
                    )
                return
            if self._loading_session_id == session_id:
                self._buffered_live_events.append(
                    BufferedOutput(
                        session_id=session_id,
                        journal_seq=journal_seq,
                        event=event,
                        activity_reasoning=activity_reasoning,
                        activity_user_message=activity_user_message,
                    )
                )
                return
            await self._render_live_output(
                event, session_id=session_id, journal_seq=journal_seq, activity_reasoning=activity_reasoning, activity_user_message=activity_user_message
            )

    async def _render_live_output(
        self, event: OutputEvent, *, session_id: str, journal_seq: int | None, activity_reasoning: str, activity_user_message: str
    ) -> None:
        if journal_seq is not None and journal_seq <= self._last_rendered_seq.get(session_id, 0):
            return

        step = await self.query_one(ChatTimeline).handle_output(event)
        if journal_seq is not None:
            self._last_rendered_seq[session_id] = journal_seq
        if isinstance(event, ToolCalled) and step is not None:
            step.session_id = session_id
            step.target_seq = journal_seq
            self._start_activity_generation(
                step,
                session_id=session_id,
                target_seq=journal_seq,
                user_message=activity_user_message,
                reasoning=activity_reasoning or step.label_reasoning,
                tool_name=event.name,
                arguments=event.arguments,
            )

    def _start_activity_generation(
        self, step: ActivityStep | None, *, session_id: str, target_seq: int | None, user_message: str, reasoning: str, tool_name: str, arguments: str | None
    ) -> None:
        self.run_worker(
            self._generate_step_activity(
                step, session_id=session_id, target_seq=target_seq, user_message=user_message, reasoning=reasoning, tool_name=tool_name, arguments=arguments
            ),
            group=f"activity-{session_id}-{target_seq or id(step)}",
            exit_on_error=False,
        )

    async def request_tool_approval(self, request: ApprovalRequest, review_reason: str | None = None) -> bool:
        future = asyncio.get_running_loop().create_future()
        pending = PendingApproval(
            request=request, session_title=self._session_titles.get(request.session_id), review_reason=review_reason, future=future
        )
        self._pending_approvals.append(pending)
        self._show_pending_approval()
        try:
            return await future
        finally:
            if pending in self._pending_approvals:
                self._pending_approvals.remove(pending)
                self._show_pending_approval()

    def _show_pending_approval(self) -> None:
        try:
            panel = self.query_one(ApprovalPanel)
        except NoMatches:
            return
        interaction_scroll = self.query_one("#interaction-scroll", VerticalScroll)
        if self._pending_approvals:
            pending = self._pending_approvals[0]
            panel.replace_approval(
                pending.request,
                session_title=pending.session_title,
                review_reason=pending.review_reason,
                pending_count=len(self._pending_approvals),
            )
            self.call_after_refresh(interaction_scroll.scroll_end, animate=False, immediate=True)
        else:
            panel.replace_approval(None)
            self.call_after_refresh(interaction_scroll.scroll_home, animate=False)
        self._refresh_selected_controls()

    async def report_tool_approval(
        self,
        session_id: str,
        tool_name: str,
        arguments: str | None,
        approved: bool,
        *,
        source: str,
        review_decision: str | None = None,
        review_reason: str | None = None,
        journal_seq: int | None = None,
    ) -> None:
        async with self._timeline_lock:
            if session_id != self.state.selected_session_id:
                return
            approval = BufferedApproval(
                session_id=session_id,
                journal_seq=journal_seq,
                tool_name=tool_name,
                arguments=arguments,
                approved=approved,
                source=source,
                review_decision=review_decision,
                review_reason=review_reason,
            )
            if self._loading_session_id == session_id:
                self._buffered_live_events.append(approval)
                return
            await self._render_live_approval(approval)

    async def handle_follow_up(self, event: RuntimeFollowUpEvent) -> None:
        async with self._timeline_lock:
            if event.session_id != self.state.selected_session_id:
                if event.kind == "steer_fallback_promoted":
                    self._pending_follow_up_notices.setdefault(event.session_id, []).append(_STEER_FALLBACK_NOTICE)
            elif self._loading_session_id == event.session_id:
                self._buffered_live_events.append(event)
            else:
                await self._render_live_follow_up(event)

        if event.kind in {"steer_fallback_promoted", "queue_promoted"} and self.is_running:
            try:
                await self.refresh_sessions()
            except Exception:
                logger.exception("Failed to refresh sessions after follow-up promotion")

    async def _render_live_follow_up(self, event: RuntimeFollowUpEvent) -> None:
        if event.kind == "steer_submitted":
            if event.journal_seq is not None and event.journal_seq in self._rendered_steer_seqs.setdefault(event.session_id, set()):
                return
            await self.query_one(ChatTimeline).add_steer_message(event.text)
            if event.journal_seq is not None:
                self._rendered_steer_seqs[event.session_id].add(event.journal_seq)
                self._last_rendered_seq[event.session_id] = max(event.journal_seq, self._last_rendered_seq.get(event.session_id, 0))
            return

        if event.kind == "queue_submitted":
            return

        if event.kind == "queue_promoted":
            if event.journal_seq is not None and event.journal_seq <= self._last_rendered_seq.get(event.session_id, 0):
                return
            await self.query_one(ChatTimeline).add_user_message(event.text)
            if event.journal_seq is not None:
                self._last_rendered_seq[event.session_id] = event.journal_seq
            return

        await self.query_one(ChatTimeline).add_notice(_STEER_FALLBACK_NOTICE)

    async def handle_run_ended(self, event: RuntimeRunEnded) -> None:
        self._reasoning_by_run.pop(event.run_id, None)
        if not self.is_running:
            return

        try:
            if event.status == "completed":
                if event.result is None:
                    logger.error("Completed Agent run has no result for session %s run %s", event.session_id, event.run_id)
                    if event.session_id == self.state.selected_session_id:
                        await self.query_one(ChatTimeline).add_notice("Agent run failed. See the system log for details.")
                else:
                    result = event.result
                    self._add_usage(event.session_id, result.main_usage)
                    compacted = result.auto_compaction is not None and result.auto_compaction.changed
                    self._set_main_context_tokens(event.session_id, None if compacted else result.main_usage.last_request_total_tokens)
                    self._add_usage(event.session_id, result.auxiliary_usage)
                    if compacted:
                        context_tokens = result.main_usage.last_request_total_tokens
                        capacity = self.state.config.model("main").context_window_tokens
                        assert context_tokens is not None and capacity is not None
                        if event.session_id == self.state.selected_session_id:
                            await self.query_one(ChatTimeline).add_notice(
                                f"Context compacted automatically after Main context reached {context_tokens / capacity:.1%} of capacity."
                            )
                    elif result.auto_compaction_consistency_uncertain:
                        if event.session_id == self.state.selected_session_id:
                            await self.query_one(ChatTimeline).add_notice(
                                "Automatic context compaction failed and context storage may be damaged. Stop this session and see the system log."
                            )
                    elif result.auto_compaction_failed:
                        if event.session_id == self.state.selected_session_id:
                            await self.query_one(ChatTimeline).add_notice(
                                "Automatic context compaction failed; existing context was kept. Use /compact to retry."
                            )
            elif event.status == "cancelled":
                if event.session_id == self.state.selected_session_id:
                    await self.query_one(ChatTimeline).add_notice("Run stopped.")
                    if event.discarded_steer_count:
                        await self.query_one(ChatTimeline).add_notice("Run stopped; pending Steer messages were not applied.")
            else:
                if event.session_id == self.state.selected_session_id:
                    await self.query_one(ChatTimeline).add_notice("Agent run failed. See the system log for details.")
        finally:
            try:
                await self.refresh_sessions()
            except Exception:
                logger.exception("Failed to refresh sessions after Agent run")
                self._refresh_selected_controls()
            self.query_one(Composer).focus_input()

    async def _render_live_approval(self, approval: BufferedApproval) -> None:
        if approval.journal_seq is not None and approval.journal_seq <= self._last_rendered_seq.get(approval.session_id, 0):
            return
        await self.query_one(ChatTimeline).add_approval(
            approval.tool_name,
            approval.arguments,
            approval.approved,
            source=approval.source,
            review_decision=approval.review_decision,
            review_reason=approval.review_reason,
        )
        if approval.journal_seq is not None:
            self._last_rendered_seq[approval.session_id] = approval.journal_seq

    async def refresh_sessions(self) -> None:
        current_session_id = self.state.selected_session_id
        all_records = await asyncio.to_thread(list_sessions, self.state.config.sessions_db, include_archived=True)
        records = [record for record in all_records if record.archived_at is None or record.session_id == current_session_id]
        self._session_records = records
        self._known_session_ids = {record.session_id for record in records}
        self._session_titles = {record.session_id: record.title for record in records}
        if self.runtime is not None:
            for record in records:
                self._runtime_snapshots[record.session_id] = self.runtime.session_snapshot(record.session_id)
        await self._refresh_sidebar_from_cache()
        title = await asyncio.to_thread(get_session_title, self.state.config.sessions_db, current_session_id)
        self.set_session_title(title)
        self.query_one("#usage-status", Static).update(self._format_usage_status())
        self._refresh_selected_controls()

    async def _refresh_and_load_current(self) -> None:
        session_id = self.state.selected_session_id
        try:
            await self.refresh_sessions()
            await self._load_session_history(session_id)
        except Exception:
            logger.exception("Failed to initialize TUI session history")
            await self.query_one(ChatTimeline).reset()
            await self.query_one(ChatTimeline).add_notice("Unable to load session history. See the system log for details.")
        finally:
            if self._loading_session_id == session_id:
                self._loading_session_id = None
                self._refresh_selected_controls()
            self.query_one(Composer).focus_input()

    async def _load_session_history(self, session_id: str) -> None:
        self._loading_session_id = session_id
        self._buffered_live_events.clear()
        self._rendered_steer_seqs.pop(session_id, None)
        self._refresh_selected_controls()
        timeline = self.query_one(ChatTimeline)
        async with self._timeline_lock:
            if session_id != self.state.selected_session_id:
                return
            await timeline.reset()
            await timeline.add_notice("Loading session history…")

        try:
            try:
                records = await asyncio.to_thread(read_session_journal, self.journal_dir, session_id)
            except FileNotFoundError:
                async with self._timeline_lock:
                    if session_id != self.state.selected_session_id:
                        return
                    await timeline.reset()
                    self._last_rendered_seq[session_id] = 0
                    if session_id in self._known_session_ids:
                        await timeline.add_notice("No journal history is available for this older session.")
                    else:
                        await timeline.add_notice("Ask QMT Agent anything.")
                return
            except Exception:
                logger.exception("Failed to read session journal for session %s", session_id)
                async with self._timeline_lock:
                    if session_id != self.state.selected_session_id:
                        return
                    await timeline.reset()
                    await timeline.add_notice("Session history is unavailable because its journal is invalid. See the system log for details.")
                return

            async with self._timeline_lock:
                if session_id != self.state.selected_session_id:
                    return
                if records:
                    await timeline.render_history(records)
                    self._last_rendered_seq[session_id] = max(record["seq"] for record in records if type(record.get("seq")) is int)
                    self._rendered_steer_seqs[session_id] = {
                        record["seq"] for record in records if record.get("type") == "user_steer" and type(record.get("seq")) is int
                    }
                else:
                    self._last_rendered_seq[session_id] = 0
                    self._rendered_steer_seqs[session_id] = set()
                    await timeline.reset()
                    await timeline.add_notice("Ask QMT Agent anything.")
        finally:
            if self._loading_session_id == session_id:
                async with self._timeline_lock:
                    buffered = self._buffered_live_events
                    self._buffered_live_events = []
                    self._loading_session_id = None
                    if session_id == self.state.selected_session_id:
                        buffered.sort(
                            key=lambda item: (
                                float("inf")
                                if isinstance(item, RuntimeFollowUpEvent) and item.kind == "steer_fallback_promoted"
                                else item.journal_seq
                                if item.journal_seq is not None
                                else float("inf")
                            )
                        )
                        for item in buffered:
                            if isinstance(item, BufferedOutput):
                                await self._render_live_output(
                                    item.event,
                                    session_id=item.session_id,
                                    journal_seq=item.journal_seq,
                                    activity_reasoning=item.activity_reasoning,
                                    activity_user_message=item.activity_user_message,
                                )
                            elif isinstance(item, BufferedApproval):
                                await self._render_live_approval(item)
                            else:
                                await self._render_live_follow_up(item)
                        for notice in self._pending_follow_up_notices.pop(session_id, []):
                            await timeline.add_notice(notice)
                self._refresh_selected_controls()

    async def _dispatch_command(self, command: Command) -> None:
        timeline = self.query_one(ChatTimeline)
        if self.runtime is None or self.sessions is None:
            await timeline.add_notice("Agent runtime is not ready.")
            return
        old_session_id = self.state.selected_session_id
        result = await dispatch_command(command, self.state, runtime=self.runtime, sessions=self.sessions)
        if result.exit_requested:
            if result.output:
                await timeline.add_notice(result.output)
            self.exit()
            return

        new_session_id = self.state.selected_session_id
        if new_session_id != old_session_id:
            self._loading_session_id = new_session_id
            self._buffered_live_events.clear()
            self._refresh_selected_controls()
            await self.refresh_sessions()
            self.run_worker(self._load_session_history_with_notice(new_session_id, result.output), group="history", exclusive=True, exit_on_error=False)
        else:
            if result.compaction is not None:
                self._add_usage(new_session_id, result.compaction.usage)
                self._set_main_context_tokens(new_session_id, None)
            if result.output:
                await timeline.add_notice(result.output)
            if command.name in {"effort", "followup", "stop"}:
                self._refresh_selected_controls()
            if command.name in {"archive", "title", "unarchive"}:
                await self.refresh_sessions()

        self.query_one(Composer).focus_input()

    async def _load_session_history_with_notice(self, session_id: str, notice: str | None) -> None:
        await self._load_session_history(session_id)
        if notice and session_id == self.state.selected_session_id:
            await self.query_one(ChatTimeline).add_notice(notice)

    def _refresh_selected_controls(self) -> None:
        selected_session_id = self.state.selected_session_id
        active_run = self.runtime.get_active_run(selected_session_id) if self.runtime is not None else None
        loading = self._loading_session_id == selected_session_id
        follow_up_behavior = active_run.options.follow_up_behavior if active_run is not None else self.state.follow_up_behavior
        self.query_one(Composer).set_input_state(
            loading=loading,
            running=active_run is not None,
            run_phase=active_run.phase if active_run is not None else None,
            follow_up_behavior=follow_up_behavior,
        )
        if self.runtime is not None:
            snapshot = self.runtime.session_snapshot(selected_session_id)
            self.query_one(TodoPanel).replace_todos(self._session_todos.get(selected_session_id, ()))
            self.query_one(QueuePanel).replace_queue(selected_session_id, self.runtime.list_queued_inputs(selected_session_id), paused=snapshot.queue_paused)
        self._refresh_run_status()

    async def _generate_step_activity(
        self, step: ActivityStep | None, *, session_id: str, target_seq: int | None, user_message: str, reasoning: str, tool_name: str, arguments: str | None
    ) -> None:
        try:
            result = await generate_activity_label(self.activity_agent, self.state.config, user_message, reasoning, tool_name, arguments)
        except Exception as exc:
            logger.warning("Activity label generation failed for tool %s: %s", tool_name, exc)
            return

        self._add_usage(session_id, result.usage)

        if step is not None and step.is_mounted:
            step.set_activity_label(result.label)

        if target_seq is None:
            return

        try:
            await self._record_activity_label(session_id, target_seq, result.label)
        except Exception:
            logger.exception("Failed to append activity label to session journal for session %s target %d", session_id, target_seq)
