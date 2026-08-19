from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from typing_extensions import TypedDict

from qmt_agent.config import AppConfig

TodoStatus = Literal[
    "pending",
    "in_progress",
    "completed",
    "failed",
]

BackgroundJobStatus = Literal[
    "running",
    "exited",
    "lost",
    "stopped",
]

class TodoItem(TypedDict):
    content: str
    status: TodoStatus


@dataclass
class BackgroundJob:
    job_id: str
    process_id: int | None
    pid: int
    command: str
    started_at: datetime
    stdout_log: str
    stderr_log: str
    status: BackgroundJobStatus
    exit_code: int | None = None
    finished_at: datetime | None = None
    termination: str | None = None
    escalated: bool | None = None
    group_terminated: bool | None = None


@dataclass
class ExecutionState:
    workspace_root: Path | None = None
    sandbox: Any | None = None
    background_jobs: dict[str, BackgroundJob] = field(default_factory=dict)


@dataclass
class AgentContext:
    config: AppConfig
    execution: ExecutionState
    todos: list[TodoItem] = field(default_factory=list)
