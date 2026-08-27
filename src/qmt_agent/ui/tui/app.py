from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Label, ListView, Static, TextArea

from qmt_agent.commands import Command, dispatch_command, parse_command
from qmt_agent.context import AppState
from qmt_agent.journal import read_session_journal
from qmt_agent.output import OutputEvent, ToolCalled
from qmt_agent.storage import get_session_title, list_sessions

from .sidebar import SessionSidebar
from .timeline import ChatTimeline

logger = logging.getLogger(__name__)
SESSION_MUTATION_COMMANDS = {"new", "resume", "clear"}
RecordUserMessage = Callable[[str, str], Awaitable[None]]

if TYPE_CHECKING:
    from qmt_agent.agents import AgentLoop


class Composer(Vertical):
    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def compose(self) -> ComposeResult:
        yield TextArea(
            placeholder="输入消息……",
            show_line_numbers=False,
            soft_wrap=True,
            id="composer-input",
        )
        yield Horizontal(
            Label("Ctrl+Enter / Ctrl+S to send", id="send-hint"),
            Button("Send", variant="primary", id="send-button"),
            id="composer-actions",
        )

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

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-button":
            self.submit()


class QMTAgentTUI(App[None]):
    TITLE = "QMT Agent"
    BINDINGS = [
        Binding("ctrl+b", "toggle_sidebar", "Toggle sidebar"),
        Binding("ctrl+enter", "send_message", "Send"),
        Binding("ctrl+s", "send_message", "Send"),
        Binding("ctrl+q", "quit", "Quit"),
    ]
    CSS = """
    Screen {
        background: $surface;
        color: $text;
        layout: vertical;
    }

    #top-bar {
        height: 3;
        padding: 0 1;
        background: $panel;
        border-bottom: solid $primary-background;
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

    #workspace {
        height: 1fr;
    }

    #session-sidebar {
        width: 30;
        min-width: 20;
        background: $panel;
        border-right: solid $primary-background;
    }

    SessionListItem {
        height: 3;
        padding: 0 1;
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

    .activity-step {
        width: 100%;
        height: auto;
        margin-bottom: 1;
        color: $text-muted;
    }

    .activity-section {
        height: auto;
        padding: 0 1 1 2;
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
        height: 7;
        padding: 0 1;
        background: $panel;
        border-top: solid $primary-background;
    }

    #composer-input {
        height: 4;
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

    .sidebar-hidden #session-sidebar {
        display: none;
    }
    """

    def __init__(
        self,
        state: AppState,
        journal_dir: Path,
        record_user_message: RecordUserMessage,
    ) -> None:
        super().__init__()
        self.state = state
        self.journal_dir = journal_dir
        self._record_user_message = record_user_message
        self.agent_loop: AgentLoop | None = None
        self.session_title: str | None = None
        self._run_active = False
        self._known_session_ids: set[str] = set()
        self._current_user_message = ""

    def compose(self) -> ComposeResult:
        yield Horizontal(
            Label("QMT Agent", id="brand"),
            Label(self.session_title or "(untitled)", id="session-heading"),
            Label("● Ready", id="run-status"),
            id="top-bar",
        )
        yield Horizontal(
            SessionSidebar(id="session-sidebar"),
            ChatTimeline(id="timeline"),
            id="workspace",
        )
        yield Composer(id="composer")

    def on_mount(self) -> None:
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

    def action_quit(self) -> None:
        if self._run_active:
            self.notify("Wait for the active Agent run to finish before exiting.", severity="warning")
            return
        self.exit()

    def set_status(self, status: str) -> None:
        self.query_one("#run-status", Static).update(status)

    def set_session_title(self, title: str | None) -> None:
        self.session_title = title
        self.query_one("#session-heading", Static).update(title or "(untitled)")

    def bind_agent_loop(self, agent_loop: AgentLoop) -> None:
        self.agent_loop = agent_loop

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
        if self._run_active and command.name in SESSION_MUTATION_COMMANDS:
            await timeline.add_notice(f"Cannot run /{command.name} while the Agent is running.")
            return

        old_session_id = self.state.session.session_id
        result = await dispatch_command(command, self.state)
        if result.output:
            await timeline.add_notice(result.output)
        if result.exit_requested:
            self.exit()
            return

        new_session_id = self.state.session.session_id
        if new_session_id != old_session_id:
            await self.refresh_sessions()
            self.run_worker(
                self._load_session_history(new_session_id),
                group="history",
                exclusive=True,
                exit_on_error=False,
            )
        elif command.name == "title":
            await self.refresh_sessions()

        self.query_one(Composer).focus_input()

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
            await self.agent_loop.run(user_message, session, self.state.execution)
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
        self.set_status("● Running" if running else "● Ready")
        self.query_one(Composer).disabled = running
        self.query_one(SessionSidebar).disabled = running
