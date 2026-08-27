from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Label, Static, TextArea

from .sidebar import SessionSidebar
from .timeline import ChatTimeline


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

    def __init__(self, current_session_id: str, session_title: str | None = None) -> None:
        super().__init__()
        self.current_session_id = current_session_id
        self.session_title = session_title

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

    async def on_composer_submitted(self, event: Composer.Submitted) -> None:
        composer = self.query_one(Composer)
        composer.clear()
        await self.query_one(ChatTimeline).add_user_message(event.text)

    def action_toggle_sidebar(self) -> None:
        self.screen.toggle_class("sidebar-hidden")

    def action_send_message(self) -> None:
        self.query_one(Composer).submit()

    def set_status(self, status: str) -> None:
        self.query_one("#run-status", Static).update(status)

    def set_session_title(self, title: str | None) -> None:
        self.session_title = title
        self.query_one("#session-heading", Static).update(title or "(untitled)")
