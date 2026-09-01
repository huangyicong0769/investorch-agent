from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Collapsible, Static

from investorch.context import TodoItem


class TodoPanel(Vertical):
    """Transient todo snapshot for the selected session's latest Run."""

    DEFAULT_CSS = """
    TodoPanel {
        display: none;
        width: 100%;
        height: auto;
        max-height: 7;
        padding: 0 1;
        background: $panel;
        border-top: solid $primary-background;
    }

    TodoPanel Collapsible {
        width: 100%;
        height: auto;
        max-height: 6;
        padding: 0;
        border: none;
    }

    TodoPanel CollapsibleTitle {
        height: 1;
        padding: 0;
    }

    TodoPanel Contents {
        height: auto;
        max-height: 5;
        padding: 0 0 0 2;
        color: $text-muted;
    }

    TodoPanel #todo-items {
        height: auto;
    }
    """

    _SYMBOLS: ClassVar[dict[str, str]] = {"pending": "○", "in_progress": "●", "completed": "✓", "failed": "✗"}

    def __init__(self, todos: tuple[TodoItem, ...] = (), **kwargs) -> None:
        super().__init__(**kwargs)
        self._todos = tuple(dict(todo) for todo in todos)

    def compose(self) -> ComposeResult:
        yield Collapsible(Static(markup=False, id="todo-items"), title="Tasks", collapsed=False, id="todo-collapsible")

    def on_mount(self) -> None:
        self._refresh()

    def replace_todos(self, todos: tuple[TodoItem, ...]) -> None:
        self._todos = tuple(dict(todo) for todo in todos)
        if self.is_mounted:
            self._refresh()

    def _refresh(self) -> None:
        self.display = bool(self._todos)
        if not self._todos:
            return

        completed = sum(todo["status"] == "completed" for todo in self._todos)
        collapsible = self.query_one("#todo-collapsible", Collapsible)
        collapsible.title = f"Tasks · {completed} / {len(self._todos)}"
        lines = [f"{self._SYMBOLS[todo['status']]} {todo['content']}" for todo in self._todos]
        self.query_one("#todo-items", Static).update("\n".join(lines))
