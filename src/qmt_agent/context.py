from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from typing_extensions import TypedDict

from qmt_agent.config import AppConfig

if TYPE_CHECKING:
    from agents import SQLiteSession

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


@dataclass
class ExecutionState:
    workspace_root: Path | None = None
    sandbox: Any | None = None
    background_jobs: dict[str, BackgroundJob] = field(default_factory=dict)


@dataclass
class TurnState:
    """Scratch state owned by one user task and recreated for every Agent turn."""

    todos: list[TodoItem] = field(default_factory=list)


@dataclass
class AgentContext:
    config: AppConfig
    execution: ExecutionState
    turn: TurnState = field(default_factory=TurnState)


@dataclass
class AppState:
    """Mutable state shared for the lifetime of one application process."""

    config: AppConfig
    execution: ExecutionState
    session: SQLiteSession
    main_reasoning_effort: str
