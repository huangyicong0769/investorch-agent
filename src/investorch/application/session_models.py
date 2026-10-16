"""Host-owned model drafts and the durable model chosen by each Session's last Run."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict, dataclass

from investorch.config import AgentModelConfig, AppConfig
from investorch.storage.sessions import get_session_model, session_exists, set_session_model


@dataclass(frozen=True, slots=True)
class SessionModelSnapshot:
    model: AgentModelConfig
    revision: int | None


@dataclass(frozen=True, slots=True)
class SessionModelView:
    persisted: AgentModelConfig | None
    draft: AgentModelConfig | None
    effective: AgentModelConfig
    active: AgentModelConfig | None


class SessionModelStore:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._drafts: dict[str, SessionModelSnapshot] = {}
        self._revision = 0
        self.on_change: Callable[[str], None] = lambda _: None

    def view(self, session_id: str, *, active: AgentModelConfig | None = None) -> SessionModelView:
        saved = get_session_model(self._config.sessions_db, session_id)
        persisted = AgentModelConfig(**saved) if saved is not None else None
        snapshot = self._drafts.get(session_id)
        draft = snapshot.model if snapshot is not None else None
        effective = draft or persisted or self._config.agent_model("main")
        return SessionModelView(persisted, draft, effective, active)

    def snapshot(self, session_id: str) -> SessionModelSnapshot:
        draft = self._drafts.get(session_id)
        selected = draft.model if draft else self.view(session_id).effective
        return SessionModelSnapshot(self._config.resolve_model(selected), draft.revision if draft else None)

    def update(
        self,
        session_id: str,
        *,
        model: str | None = None,
        name: str | None = None,
        reasoning_effort: str | None = None,
        context_window_tokens: int | None = None,
    ) -> AgentModelConfig:
        if not session_exists(self._config.sessions_db, session_id):
            raise KeyError(session_id)
        selected = self._config.resolve_model(
            self.view(session_id).effective,
            model=model,
            name=name,
            reasoning_effort=reasoning_effort,
            context_window_tokens=context_window_tokens,
        )
        self.set_draft(session_id, selected)
        return selected

    def set_draft(self, session_id: str, model: AgentModelConfig) -> None:
        model = self._config.resolve_model(model)
        self._revision += 1
        self._drafts[session_id] = SessionModelSnapshot(model, self._revision)
        self.on_change(session_id)

    async def commit(self, session_id: str, snapshot: SessionModelSnapshot) -> None:
        task = asyncio.create_task(
            asyncio.to_thread(
                set_session_model,
                self._config.sessions_db,
                session_id,
                asdict(snapshot.model),
            )
        )
        cancellation: asyncio.CancelledError | None = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as error:
                cancellation = error
        task.result()
        current = self._drafts.get(session_id)
        if current is not None and current.revision == snapshot.revision:
            self._drafts.pop(session_id)
        self.on_change(session_id)
        if cancellation is not None:
            raise cancellation

    def discard(self, session_id: str) -> None:
        self._drafts.pop(session_id, None)
        self.on_change(session_id)

    def draft(self, session_id: str) -> AgentModelConfig | None:
        snapshot = self._drafts.get(session_id)
        return snapshot.model if snapshot else None

    def references(self, connection_id: str) -> list[str]:
        return [session_id for session_id, draft in self._drafts.items() if draft.model.model == connection_id]
