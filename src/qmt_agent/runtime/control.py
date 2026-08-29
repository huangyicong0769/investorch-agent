from __future__ import annotations

import asyncio
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from agents import RunResultStreaming

from .models import PendingSteer, RunOptions


@dataclass(slots=True)
class _SteerEntry:
    steer: PendingSteer
    ready: bool = False


class RunControl:
    """Coordinates cooperative steering for one active top-level Run."""

    def __init__(self, session_id: str, run_id: str, state_changed: Callable[[], None]) -> None:
        self._session_id = session_id
        self._run_id = run_id
        self._state_changed = state_changed
        self._pending: deque[_SteerEntry] = deque()
        self._fallback: deque[_SteerEntry] = deque()
        self._bound_stream: RunResultStreaming | None = None
        self._after_turn_requested = False
        self._accepting_current = True
        self._accepting_submissions = True
        self._changed = asyncio.Event()

    def bind_stream(self, result: RunResultStreaming) -> None:
        if self._bound_stream is not None:
            raise RuntimeError("A streaming result is already bound to this Run")
        self._bound_stream = result
        self._after_turn_requested = False
        if any(entry.ready for entry in self._pending):
            self._request_after_turn()

    def unbind_stream(self, result: RunResultStreaming) -> None:
        if self._bound_stream is not result:
            raise RuntimeError("Cannot unbind a streaming result that is not current")
        self._bound_stream = None
        self._after_turn_requested = False

    def reserve_steer(self, text: str, options: RunOptions) -> PendingSteer:
        if not self._accepting_submissions:
            raise RuntimeError("Run no longer accepts follow-up submissions")
        steer = PendingSteer(
            steer_id=uuid.uuid4().hex,
            session_id=self._session_id,
            source_run_id=self._run_id,
            text=text,
            options=options,
            created_at=datetime.now(UTC),
        )
        target = self._pending if self._accepting_current else self._fallback
        target.append(_SteerEntry(steer=steer))
        self._state_changed()
        return steer

    def mark_ready(self, steer_id: str, journal_seq: int | None = None) -> None:
        entry = self._find_entry(steer_id)
        entry.steer = replace(entry.steer, journal_seq=journal_seq)
        entry.ready = True
        if self._accepting_current and any(candidate is entry for candidate in self._pending):
            self._request_after_turn()
        self._changed.set()

    def discard_submission(self, steer_id: str) -> None:
        for entries in (self._pending, self._fallback):
            for entry in entries:
                if entry.steer.steer_id == steer_id:
                    entries.remove(entry)
                    self._changed.set()
                    self._state_changed()
                    return
        raise KeyError(f"Unknown Steer input: {steer_id}")

    async def pending_for_boundary(self, *, seal_if_empty: bool) -> tuple[PendingSteer, ...]:
        while self._pending and any(not entry.ready for entry in self._pending):
            self._changed.clear()
            if all(entry.ready for entry in self._pending):
                break
            await self._changed.wait()
        if self._pending:
            return tuple(entry.steer for entry in self._pending)
        if seal_if_empty:
            self._accepting_current = False
        return ()

    def mark_staged(self, steer_ids: list[str]) -> None:
        self._remove_pending_prefix(steer_ids)
        self._state_changed()

    def move_pending_to_fallback(self) -> None:
        self._accepting_current = False
        while self._pending:
            self._fallback.append(self._pending.popleft())

    def close_submissions(self) -> None:
        self._accepting_current = False
        self._accepting_submissions = False
        self._changed.set()

    async def wait_until_ready(self) -> None:
        while any(not entry.ready for entry in (*self._pending, *self._fallback)):
            self._changed.clear()
            if all(entry.ready for entry in (*self._pending, *self._fallback)):
                break
            await self._changed.wait()

    def take_fallbacks(self) -> tuple[PendingSteer, ...]:
        if self._pending:
            raise RuntimeError("Pending Steer inputs were not assigned a terminal disposition")
        if any(not entry.ready for entry in self._fallback):
            raise RuntimeError("Cannot take Steer fallbacks before their submission completes")
        steers = tuple(entry.steer for entry in self._fallback)
        self._fallback.clear()
        return steers

    def discard(self) -> int:
        count = len(self._pending) + len(self._fallback)
        self._pending.clear()
        self._fallback.clear()
        self._accepting_current = False
        self._accepting_submissions = False
        self._changed.set()
        return count

    def pending_count(self) -> int:
        return len(self._pending) + len(self._fallback)

    def _request_after_turn(self) -> None:
        if self._bound_stream is None or self._after_turn_requested:
            return
        self._bound_stream.cancel(mode="after_turn")
        self._after_turn_requested = True

    def _find_entry(self, steer_id: str) -> _SteerEntry:
        for entry in (*self._pending, *self._fallback):
            if entry.steer.steer_id == steer_id:
                return entry
        raise KeyError(f"Unknown Steer input: {steer_id}")

    def _remove_pending_prefix(self, steer_ids: list[str]) -> None:
        for steer_id in steer_ids:
            if not self._pending or self._pending[0].steer.steer_id != steer_id:
                raise RuntimeError("Steer staging did not preserve FIFO order")
            if not self._pending[0].ready:
                raise RuntimeError("Cannot stage a Steer input before submission completes")
            self._pending.popleft()
