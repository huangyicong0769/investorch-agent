from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from typing_extensions import TypedDict

from investorch.config import AppConfig, ConfigError
from investorch.config_schema import FOLLOW_UP_BEHAVIORS, PERMISSION_MODES

if TYPE_CHECKING:
    from investorch.application.portfolios import PortfolioOperations
    from investorch.application.session_models import SessionModelStore
    from investorch.application.skills import SkillOperations
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
    portfolios: PortfolioOperations
    skills: SkillOperations | None = None
    turn: TurnState = field(default_factory=TurnState)
    todo_update_handler: TodoUpdateHandler | None = None


class AppState:
    """Application selection plus defaults captured by future Agent runs."""

    def __init__(
        self,
        config: AppConfig,
        execution: ExecutionState,
        selected_session_id: str | None,
        session_models: SessionModelStore | None = None,
        permission_mode: str | None = None,
        follow_up_behavior: FollowUpBehavior | None = None,
    ) -> None:
        self.config = config
        self.execution = execution
        self.selected_session_id = selected_session_id
        from investorch.application.session_models import SessionModelStore

        self.session_models = session_models if session_models is not None else SessionModelStore(config)
        self.permission_mode = permission_mode
        self.follow_up_behavior = follow_up_behavior

    @property
    def permission_mode(self) -> str:
        if self._permission_mode is not None:
            return self._permission_mode
        return self.config["permission.mode"]

    @permission_mode.setter
    def permission_mode(self, value: str | None) -> None:
        if value is not None and value not in PERMISSION_MODES:
            raise ConfigError(f"permission_mode must be one of {', '.join(PERMISSION_MODES)}")
        self._permission_mode = value

    @property
    def follow_up_behavior(self) -> FollowUpBehavior:
        if self._follow_up_behavior is not None:
            return self._follow_up_behavior
        return self.config["interaction.follow_up_behavior"]

    @follow_up_behavior.setter
    def follow_up_behavior(self, value: FollowUpBehavior | None) -> None:
        if value is not None and value not in FOLLOW_UP_BEHAVIORS:
            raise ConfigError(f"follow_up_behavior must be one of {', '.join(FOLLOW_UP_BEHAVIORS)}")
        self._follow_up_behavior = value
