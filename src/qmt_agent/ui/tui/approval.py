from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from .timeline import format_json


class ApprovalScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "reject", "Reject")]
    CSS = """
    ApprovalScreen {
        align: center middle;
        background: $background 60%;
    }

    #approval-dialog {
        width: 90%;
        max-width: 76;
        max-height: 80%;
        height: auto;
        padding: 1 2;
        background: $panel;
        border: solid $primary;
    }

    #approval-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #approval-tool {
        color: $warning;
        margin-bottom: 1;
    }

    #approval-arguments-scroll {
        height: auto;
        max-height: 12;
        margin-bottom: 1;
    }

    #approval-arguments {
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
    """

    def __init__(self, tool_name: str, arguments: str | None) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.arguments = arguments

    def compose(self) -> ComposeResult:
        content = [
            Label("Approval required", id="approval-title"),
            Static(f"Tool · {self.tool_name}", markup=False, id="approval-tool"),
        ]
        formatted = format_json(self.arguments)
        if formatted:
            content.append(
                VerticalScroll(
                    Static(formatted, markup=False, id="approval-arguments"),
                    id="approval-arguments-scroll",
                )
            )
        content.append(
            Horizontal(
                Button("Reject", variant="error", id="reject-button"),
                Button("Approve", variant="success", id="approve-button"),
                id="approval-actions",
            )
        )
        yield Vertical(*content, id="approval-dialog")

    def on_mount(self) -> None:
        self.query_one("#reject-button", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "approve-button")

    def action_reject(self) -> None:
        self.dismiss(False)
