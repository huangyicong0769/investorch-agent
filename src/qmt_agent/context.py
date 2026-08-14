from dataclasses import dataclass, field
from typing import Literal

from typing_extensions import TypedDict

from qmt_agent.config import AppConfig

TodoStatus = Literal[
    "pending",
    "in_progress",
    "completed",
    "failed",
]

class TodoItem(TypedDict):
    content: str
    status: TodoStatus

@dataclass
class AgentContext:
    config: AppConfig
    todos: list[TodoItem] = field(default_factory=list)