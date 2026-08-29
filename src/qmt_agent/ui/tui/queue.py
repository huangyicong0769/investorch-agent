from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Label

from qmt_agent.runtime import QueuedInput


class QueuePanel(Vertical):
    """Compact view of pending follow-ups for the selected session."""

    DEFAULT_CSS = """
    QueuePanel {
        display: none;
        width: 100%;
        height: auto;
        max-height: 7;
        padding: 0 1;
        background: $panel;
        border-top: solid $primary-background;
    }

    QueuePanel .queue-heading {
        height: 1;
        text-style: bold;
    }

    QueuePanel .queue-preview {
        height: 1;
        color: $text-muted;
        text-overflow: ellipsis;
    }

    QueuePanel .queue-actions {
        height: 3;
        align-horizontal: right;
    }

    QueuePanel Button {
        min-width: 9;
        margin-left: 1;
    }
    """

    class ClearRequested(Message):
        def __init__(self, session_id: str) -> None:
            super().__init__()
            self.session_id = session_id

    class ResumeRequested(Message):
        def __init__(self, session_id: str) -> None:
            super().__init__()
            self.session_id = session_id

    def __init__(
        self,
        queued_inputs: list[QueuedInput] | None = None,
        *,
        session_id: str = "",
        paused: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._session_id = session_id
        self._queued_inputs = list(queued_inputs or ())
        self._paused = paused

    def compose(self) -> ComposeResult:
        yield Label(id="queue-heading", classes="queue-heading")
        yield Label(markup=False, id="queue-preview-1", classes="queue-preview")
        yield Label(markup=False, id="queue-preview-2", classes="queue-preview")
        yield Horizontal(
            Button("Resume", variant="success", id="queue-resume"),
            Button("Clear", variant="warning", id="queue-clear"),
            classes="queue-actions",
        )

    def on_mount(self) -> None:
        self._refresh()

    def replace_queue(
        self,
        session_id: str,
        queued_inputs: list[QueuedInput],
        *,
        paused: bool,
    ) -> None:
        self._session_id = session_id
        self._queued_inputs = list(queued_inputs)
        self._paused = paused
        if self.is_mounted:
            self._refresh()

    def _refresh(self) -> None:
        count = len(self._queued_inputs)
        self.display = count > 0
        if count == 0:
            return

        heading = f"Paused · {count}" if self._paused else f"Queued follow-ups · {count}"
        self.query_one("#queue-heading", Label).update(heading)

        previews = self._queued_inputs[:2]
        for index in range(2):
            label = self.query_one(f"#queue-preview-{index + 1}", Label)
            if index < len(previews):
                text = " ".join(previews[index].text.split())
                label.update(f"{index + 1}. {text}")
                label.display = True
            else:
                label.display = False

        self.query_one("#queue-resume", Button).display = self._paused

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "queue-clear":
            self.post_message(self.ClearRequested(self._session_id))
            event.stop()
        elif event.button.id == "queue-resume":
            self.post_message(self.ResumeRequested(self._session_id))
            event.stop()
