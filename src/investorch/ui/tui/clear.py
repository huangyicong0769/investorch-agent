from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ClearScopeScreen(ModalScreen[bool]):
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel", priority=True)]

    DEFAULT_CSS = """
    ClearScopeScreen { align: center middle; background: $background 70%; }
    ClearScopeScreen #clear-dialog {
        width: 80; max-width: 95%; height: auto; max-height: 90%;
        padding: 1 2; border: thick $error; background: $surface;
    }
    ClearScopeScreen Static { height: auto; margin-bottom: 1; }
    ClearScopeScreen #clear-members-scroll { height: auto; max-height: 16; }
    ClearScopeScreen #clear-actions { height: 3; align-horizontal: right; }
    ClearScopeScreen Button { width: auto; margin-left: 1; }
    """

    def __init__(self, preview: dict[str, object]) -> None:
        super().__init__()
        self.members = preview["sessions"]

    def compose(self) -> ComposeResult:
        with Vertical(id="clear-dialog"):
            yield Static(f"Clear all {len(self.members)} Sessions?", markup=False)
            yield Static(
                "Permanently delete these Sessions and their histories, then start a new Session. This cannot be undone.",
                markup=False,
            )
            yield VerticalScroll(
                Static(
                    "\n\n".join(
                        f"{member['title'] or '(untitled)'}\n{member['session_id']}" for member in self.members
                    ),
                    markup=False,
                    id="clear-scope-members",
                ),
                id="clear-members-scroll",
            )
            yield Horizontal(
                Button("Cancel", id="clear-cancel"),
                Button(f"Clear all {len(self.members)} Sessions", variant="error", id="clear-confirm"),
                id="clear-actions",
            )

    def on_mount(self) -> None:
        self.query_one("#clear-cancel", Button).focus()

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id not in {"clear-cancel", "clear-confirm"} or event.button.disabled:
            return
        event.stop()
        for button in self.query(Button):
            button.disabled = True
        self.dismiss(event.button.id == "clear-confirm")
