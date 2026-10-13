from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

from investorch.config import AppConfig
from investorch.journal import SessionJournal
from investorch.runtime.models import RuntimeSessionSnapshot
from investorch.storage import list_sessions as list_session_metadata

MAX_PAGE_SIZE = 100
MAX_RESPONSE_CHARS = 32000
MAX_FIELD_CHARS = 16000


class SessionReadError(RuntimeError):
    """An actionable Session read failure with safe, path-free Tool feedback."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


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


def _validate_limit(limit: int) -> None:
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be an integer between 1 and {MAX_PAGE_SIZE}")


def _serialized_chars(value: object) -> int:
    return len(json.dumps(value))


def _list_result(sessions: list[dict[str, object]], offset: int, total: int) -> dict[str, object]:
    has_more = offset + len(sessions) < total
    return {"sessions": sessions, "has_more": has_more, "next_offset": offset + len(sessions) if has_more else None}
