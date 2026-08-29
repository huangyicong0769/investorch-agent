from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Label, Static

from qmt_agent.runtime import ApprovalRequest

from .timeline import format_json


class ApprovalPanel(Vertical):
    """Global FIFO approval head without taking ownership of the composer."""

    DEFAULT_CSS = """
    ApprovalPanel {
        display: none;
        width: 100%;
        height: auto;
        padding: 0 1;
        background: $panel;
        border-top: solid $primary-background;
    }

    ApprovalPanel #approval-title {
        height: 1;
        text-style: bold;
    }

    ApprovalPanel #approval-tool {
        height: 1;
        color: $warning;
    }

    ApprovalPanel #approval-review {
        height: auto;
        color: $text-muted;
    }

    ApprovalPanel #approval-arguments-scroll,
    ApprovalPanel #approval-arguments {
        height: auto;
    }

    ApprovalPanel #approval-actions {
        height: 3;
        align-horizontal: right;
    }

    ApprovalPanel Button {
        min-width: 12;
        margin-left: 1;
    }
    """

    class Resolved(Message):
        def __init__(self, approval_key: int, run_id: str, approved: bool) -> None:
            super().__init__()
            self.approval_key = approval_key
            self.run_id = run_id
            self.approved = approved

    def __init__(self, arguments_max_height: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self._arguments_max_height = arguments_max_height
        self._approval_key: int | None = None
        self._run_id: str | None = None
        self._awaiting_resolution = False

    def compose(self) -> ComposeResult:
        yield Label("Approval required", id="approval-title")
        yield Static(id="approval-review", markup=False)
        yield Static(id="approval-tool", markup=False)
        yield VerticalScroll(
            Static(id="approval-arguments", markup=False),
            id="approval-arguments-scroll",
        )
        yield Horizontal(
            Button("Reject", variant="error", id="reject-button"),
            Button("Approve", variant="success", id="approve-button"),
            id="approval-actions",
        )

    def on_mount(self) -> None:
        self.query_one("#approval-arguments-scroll").styles.max_height = (
            self._arguments_max_height
        )
        self.query_one("#approval-review").display = False
        self.display = False

    def replace_approval(
        self,
        request: ApprovalRequest | None,
        *,
        approval_key: int | None = None,
        session_title: str | None = None,
        review_reason: str | None = None,
        pending_count: int = 0,
    ) -> None:
        self._approval_key = approval_key
        self._run_id = request.run_id if request is not None else None
        self._awaiting_resolution = False
        self.query_one("#approve-button", Button).disabled = False
        self.query_one("#reject-button", Button).disabled = False
        self.display = request is not None
        if request is None:
            return

        title = session_title or "(untitled)"
        self.query_one("#approval-title", Label).update(
            f"Approval required · {title} · {request.session_id[:8]} · {pending_count} pending"
        )
        self.query_one("#approval-review", Static).update(
            f"AutoReview · ASK\n{review_reason}" if review_reason is not None else ""
        )
        self.query_one("#approval-review").display = review_reason is not None
        self.query_one("#approval-tool", Static).update(f"Tool · {request.tool_name}")
        formatted = format_json(request.arguments)
        self.query_one("#approval-arguments", Static).update(formatted)
        self.query_one("#approval-arguments-scroll").display = bool(formatted)

    def resolve(self, approved: bool) -> None:
        if (
            self.display
            and self._approval_key is not None
            and self._run_id is not None
            and not self._awaiting_resolution
        ):
            self._awaiting_resolution = True
            self.query_one("#approve-button", Button).disabled = True
            self.query_one("#reject-button", Button).disabled = True
            self.post_message(
                self.Resolved(self._approval_key, self._run_id, approved)
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "approve-button":
            self.resolve(True)
            event.stop()
        elif event.button.id == "reject-button":
            self.resolve(False)
            event.stop()
