from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Literal

from investorch.config import AppConfig
from investorch.journal import SessionJournal
from investorch.runtime.models import RuntimeSessionSnapshot
from investorch.storage import list_sessions as list_session_metadata

from .session_queries import (
    MAX_RESPONSE_CHARS,
    SessionReadError,
    _list_result,
    _serialized_chars,
    _validate_limit,
    read_session,
)


class SessionReadOperations:
    """Read current Root metadata and durable history without changing Session state."""

    def __init__(
        self,
        *,
        config: AppConfig,
        journal: SessionJournal,
        get_runtime_snapshot: Callable[[str], RuntimeSessionSnapshot],
    ) -> None:
        self._config = config
        self._journal = journal
        self._get_runtime_snapshot = get_runtime_snapshot

    async def list_sessions(
        self, query: str | None = None, include_archived: bool = False, limit: int = 50, offset: int = 0
    ) -> dict[str, object]:
        """Return complete metadata rows; offset pages reflect the current, unfrozen listing."""
        _validate_limit(limit)
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        if query is not None and not isinstance(query, str):
            raise ValueError("query must be a string or null")
        if type(include_archived) is not bool:
            raise ValueError("include_archived must be a boolean")
        records = await asyncio.to_thread(
            list_session_metadata, self._config.sessions_db, include_archived=include_archived
        )
        needle = query.casefold() if query and query.strip() else ""
        records = [record for record in records if needle in (record.title or "").casefold()]
        records.sort(key=lambda record: record.session_id)
        records.sort(key=lambda record: record.updated_at, reverse=True)
        sessions: list[dict[str, object]] = []
        for record in records[offset : offset + limit]:
            snapshot = self._get_runtime_snapshot(record.session_id)
            status = snapshot.run_phase or (
                "pending" if snapshot.queued_count > 0 or snapshot.pending_steer_count > 0 else "idle"
            )
            item: dict[str, object] = {
                "session_id": record.session_id,
                "title": record.title,
                "archived": record.archived_at is not None,
                "branch_from_session_id": record.branch_from_session_id,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
                "status": status,
            }
            candidate = _list_result([*sessions, item], offset, len(records))
            if _serialized_chars(candidate) > MAX_RESPONSE_CHARS:
                if not sessions:
                    raise SessionReadError("response_too_large", "Session metadata exceeds the response budget.")
                break
            sessions.append(item)
        return _list_result(sessions, offset, len(records))

    async def read_session(
        self,
        session_id: str,
        view: Literal["conversation", "events"] = "conversation",
        before_seq: int | None = None,
        snapshot_seq: int | None = None,
        limit: int = 50,
    ) -> dict[str, object]:
        return await read_session(
            session_id,
            sessions_db=self._config.sessions_db,
            journal=self._journal,
            view=view,
            before_seq=before_seq,
            snapshot_seq=snapshot_seq,
            limit=limit,
        )
