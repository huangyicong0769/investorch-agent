from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from typing_extensions import TypedDict

from investorch.config import AppConfig

if TYPE_CHECKING:
    from investorch.runtime.models import FollowUpBehavior

TodoStatus = Literal["pending", "in_progress", "completed", "failed"]

BackgroundJobStatus = Literal["running", "exited", "lost"]


class TodoItem(TypedDict):
    content: str
    status: TodoStatus


TodoUpdateHandler = Callable[[list[TodoItem]], Awaitable[None]]


@dataclass
class BackgroundJob:
    job_id: str
    owner_session_id: str
    owner_run_id: str
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
    session_id: str
    run_id: str
    turn: TurnState = field(default_factory=TurnState)
    todo_update_handler: TodoUpdateHandler | None = None


@dataclass
class AppState:
    """Application selection plus defaults captured by future Agent runs."""

    config: AppConfig
    execution: ExecutionState
    selected_session_id: str
    main_reasoning_effort: str
    permission_mode: str

    @property
    def follow_up_behavior(self) -> FollowUpBehavior:
        return self.config["interaction.follow_up_behavior"]

    @follow_up_behavior.setter
    def follow_up_behavior(self, value: FollowUpBehavior) -> None:
        self.config.update("interaction.follow_up_behavior", value, persist=False)
