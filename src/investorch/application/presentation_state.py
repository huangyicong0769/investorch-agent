from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Literal

from investorch.agents import TokenUsage
from investorch.config import AgentModelConfig
from investorch.context import TodoItem
from investorch.runtime import RuntimeSessionSnapshot

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SessionPresentationState:
    main_usage: TokenUsage = field(default_factory=TokenUsage)
    auxiliary_usage: TokenUsage = field(default_factory=TokenUsage)
    main_context_tokens: int | None = None
    context_window_tokens: int | None = None
    context_model: str | None = None
    context_kind: Literal["unknown", "measured", "estimated"] = "unknown"
    compacting: bool = False
    epoch: str = ""
    revision: int = 0
    last_todo_run_id: str | None = None
    last_todos: tuple[TodoItem, ...] = ()

    @property
    def usage(self) -> TokenUsage:
        return self.main_usage + self.auxiliary_usage


PresentationStateHandler = Callable[[str, SessionPresentationState], None]


class SessionPresentationStore:
    """Process-local presentation snapshots shared by HTTP and live subscribers."""

    def __init__(self, change_handler: PresentationStateHandler | None = None) -> None:
        self._states: dict[str, SessionPresentationState] = {}
        self._epoch = uuid.uuid4().hex
        self._change_handler = change_handler

    def get(self, session_id: str) -> SessionPresentationState:
        state = self._states.get(session_id, SessionPresentationState(epoch=self._epoch))
        return replace(state, last_todos=tuple(dict(todo) for todo in state.last_todos))

    def _put(self, session_id: str, state: SessionPresentationState) -> None:
        previous = self.get(session_id)
        if state == previous:
            return
        self._states[session_id] = replace(state, revision=previous.revision + 1)
        if self._change_handler is not None:
            try:
                self._change_handler(session_id, self.get(session_id))
            except Exception:
                # View failures cannot undo accounted usage or interrupt the Agent.
                logger.exception("Failed to notify presentation state for session %s", session_id)

    def delete(self, session_id: str) -> None:
        self._states.pop(session_id, None)

    def observe_runtime(self, snapshot: RuntimeSessionSnapshot) -> None:
        if snapshot.run_id is not None:
            self._put(
                snapshot.session_id,
                replace(
                    self.get(snapshot.session_id),
                    last_todo_run_id=snapshot.run_id,
                    last_todos=tuple(dict(todo) for todo in snapshot.todos),
                ),
            )

    def add_usage(self, session_id: str, usage: TokenUsage, model: AgentModelConfig | None = None) -> None:
        state = self.get(session_id)
        if model is None:
            self._put(session_id, replace(state, auxiliary_usage=state.auxiliary_usage + usage))
            return
        state = replace(state, main_usage=state.main_usage + usage)
        if usage.last_request_total_tokens is not None:
            state = replace(
                state,
                main_context_tokens=usage.last_request_total_tokens,
                context_window_tokens=model.context_window_tokens,
                context_model=model.name,
                context_kind="measured",
                compacting=False,
            )
        self._put(session_id, state)

    def observe_context(
        self,
        session_id: str,
        tokens: int,
        model: AgentModelConfig,
        *,
        compacting: bool = False,
    ) -> None:
        self._put(
            session_id,
            replace(
                self.get(session_id),
                main_context_tokens=tokens,
                context_window_tokens=model.context_window_tokens,
                context_model=model.name,
                context_kind="estimated",
                compacting=compacting,
            ),
        )
