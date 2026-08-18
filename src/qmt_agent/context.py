from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

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
class BackgroundJob:
    process_id: int | None
    pid: int
    command: str
    started_at: datetime
    stdout_log: str
    stderr_log: str
    exit_code: int | None = None
    finished_at: datetime | None = None


@dataclass
class AgentContext:
    config: AppConfig
    todos: list[TodoItem] = field(default_factory=list)
    sandbox: Any | None = None
    background_jobs: dict[int, BackgroundJob] = field(default_factory=dict)
