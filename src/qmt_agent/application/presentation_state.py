from __future__ import annotations

from dataclasses import dataclass

from qmt_agent.agents import CompactionResult, TokenUsage
from qmt_agent.context import TodoItem
from qmt_agent.runtime import RuntimeRunEnded, RuntimeSessionSnapshot


@dataclass(frozen=True, slots=True)
class SessionPresentationState:
    usage: TokenUsage = TokenUsage()
    main_context_tokens: int | None = None
    last_todo_run_id: str | None = None
    last_todos: tuple[TodoItem, ...] = ()


class SessionPresentationStore:
    """Process-local UI state that has no durable source of truth."""

    def __init__(self) -> None:
        self._states: dict[str, SessionPresentationState] = {}

    def get(self, session_id: str) -> SessionPresentationState:
        state = self._states.get(session_id, SessionPresentationState())
        return SessionPresentationState(
            usage=state.usage,
            main_context_tokens=state.main_context_tokens,
            last_todo_run_id=state.last_todo_run_id,
            last_todos=tuple(dict(todo) for todo in state.last_todos),
        )

    def observe_runtime(self, snapshot: RuntimeSessionSnapshot) -> None:
        if snapshot.run_id is None:
            return
        state = self._states.get(snapshot.session_id, SessionPresentationState())
        self._states[snapshot.session_id] = SessionPresentationState(
            usage=state.usage,
            main_context_tokens=state.main_context_tokens,
            last_todo_run_id=snapshot.run_id,
            last_todos=tuple(dict(todo) for todo in snapshot.todos),
        )

    def observe_run_ended(self, event: RuntimeRunEnded) -> None:
        if event.status != "completed" or event.result is None:
            return
        result = event.result
        state = self._states.get(event.session_id, SessionPresentationState())
        compacted = result.auto_compaction is not None and result.auto_compaction.changed
        self._states[event.session_id] = SessionPresentationState(
            usage=state.usage + result.main_usage + result.auxiliary_usage,
            main_context_tokens=None if compacted else result.main_usage.last_request_total_tokens,
            last_todo_run_id=state.last_todo_run_id,
            last_todos=state.last_todos,
        )

    def add_usage(self, session_id: str, usage: TokenUsage) -> None:
        state = self._states.get(session_id, SessionPresentationState())
        self._states[session_id] = SessionPresentationState(
            usage=state.usage + usage,
            main_context_tokens=state.main_context_tokens,
            last_todo_run_id=state.last_todo_run_id,
            last_todos=state.last_todos,
        )

    def observe_compaction(self, session_id: str, result: CompactionResult) -> None:
        state = self._states.get(session_id, SessionPresentationState())
        self._states[session_id] = SessionPresentationState(
            usage=state.usage + result.usage,
            main_context_tokens=None if result.changed else state.main_context_tokens,
            last_todo_run_id=state.last_todo_run_id,
            last_todos=state.last_todos,
        )
