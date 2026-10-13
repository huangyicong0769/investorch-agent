from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast
from urllib.parse import urlsplit

from investorch.images import IMAGE_DETAILS, RENDERABLE_MIME_TYPES, input_origin_fields, input_origin_from_record
from investorch.journal import SessionJournal
from investorch.journal.reader import JournalInvalidError, JournalSnapshot, JournalUnavailableError
from investorch.journal.semantics import JournalInconsistentError, interpret_steers
from investorch.storage import get_session
from investorch.storage import list_sessions as list_session_metadata

if TYPE_CHECKING:
    from investorch.runtime import AgentRuntime

MAX_PAGE_SIZE = 100
MAX_RESPONSE_CHARS = 32000
MAX_FIELD_CHARS = 16000


class SessionReadError(RuntimeError):
    """An actionable Session read failure with safe, path-free Tool feedback."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


async def list_sessions(
    *,
    sessions_db: str | Path,
    runtime: AgentRuntime,
    query: str | None = None,
    include_archived: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    """Return complete metadata rows; offset pages reflect the current, unfrozen listing."""
    _validate_limit(limit)
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    if query is not None and not isinstance(query, str):
        raise ValueError("query must be a string or null")
    if type(include_archived) is not bool:
        raise ValueError("include_archived must be a boolean")
    records = await asyncio.to_thread(list_session_metadata, sessions_db, include_archived=include_archived)
    needle = query.casefold() if query and query.strip() else ""
    records = [record for record in records if needle in (record.title or "").casefold()]
    records.sort(key=lambda record: record.session_id)
    records.sort(key=lambda record: record.updated_at, reverse=True)
    sessions: list[dict[str, object]] = []
    for record in records[offset : offset + limit]:
        snapshot = runtime.session_snapshot(record.session_id)
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
    session_id: str,
    *,
    sessions_db: str | Path,
    journal: SessionJournal,
    view: Literal["conversation", "events"] = "conversation",
    before_seq: int | None = None,
    snapshot_seq: int | None = None,
    limit: int = 50,
) -> dict[str, object]:
    """Read exact-ID history only; reuse snapshot_seq and the exclusive cursor on older pages."""
    _validate_limit(limit)
    if (
        not isinstance(session_id, str)
        or not session_id
        or session_id in {".", ".."}
        or any(character in session_id for character in ("/", "\\", "\0"))
        or Path(session_id).name != session_id
    ):
        raise ValueError("session_id must be an exact, non-empty filename-safe Session ID")
    if not isinstance(view, str) or view not in {"conversation", "events"}:
        raise ValueError("view must be conversation or events")
    if before_seq is not None and (type(before_seq) is not int or before_seq < 1):
        raise ValueError("before_seq must be a positive integer or null")
    if snapshot_seq is not None and (type(snapshot_seq) is not int or snapshot_seq < 0):
        raise ValueError("snapshot_seq must be a non-negative integer or null")
    await _require_session(sessions_db, session_id)
    try:
        result = await journal.read_snapshot(
            session_id,
            partial(
                _read_history_snapshot,
                session_id=session_id,
                view=view,
                before_seq=before_seq,
                snapshot_seq=snapshot_seq,
                limit=limit,
            ),
        )
    except JournalInconsistentError as exc:
        raise SessionReadError("journal_inconsistent", "Steer disposition evidence is inconsistent.") from exc
    except (JournalInvalidError, UnicodeError) as exc:
        raise SessionReadError("journal_invalid", "Committed Session history is malformed or incomplete.") from exc
    except (JournalUnavailableError, OSError) as exc:
        await _require_session(sessions_db, session_id)
        raise SessionReadError("journal_unavailable", "Session history is unavailable; retry the read.") from exc
    await _require_session(sessions_db, session_id)
    return result


async def _require_session(sessions_db: str | Path, session_id: str) -> None:
    if await asyncio.to_thread(get_session, sessions_db, session_id) is None:
        raise SessionReadError("session_not_found", "The exact Session ID does not exist in this Root.")


def _validate_limit(limit: int) -> None:
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be an integer between 1 and {MAX_PAGE_SIZE}")


def _serialized_chars(value: object) -> int:
    # Agents SDK stringifies ordinary dict Tool results before sending them to the model.
    return max(len(json.dumps(value)), len(str(value)))


def _list_result(sessions: list[dict[str, object]], offset: int, total: int) -> dict[str, object]:
    has_more = offset + len(sessions) < total
    return {"sessions": sessions, "has_more": has_more, "next_offset": offset + len(sessions) if has_more else None}


def _read_history_snapshot(
    snapshot: JournalSnapshot,
    *,
    session_id: str,
    view: Literal["conversation", "events"],
    before_seq: int | None,
    snapshot_seq: int | None,
    limit: int,
) -> dict[str, object]:
    head = 0

    def scan() -> Iterator[dict[str, object]]:
        nonlocal head
        for record in snapshot.iter_records(through_seq=snapshot_seq):
            head = cast(int, record["seq"])
            yield record

    facts = interpret_steers(scan(), through_seq=snapshot_seq)
    if snapshot_seq is not None and head != snapshot_seq:
        raise ValueError("snapshot_seq must identify an existing committed Journal sequence, or 0 for empty history")
    if before_seq is not None and before_seq > head + 1:
        raise ValueError("before_seq must not exceed snapshot_seq + 1")
    records: deque[dict[str, object] | SessionReadError] = deque(maxlen=limit)
    eligible_count = 0
    for record in snapshot.iter_records(through_seq=head):
        seq = cast(int, record["seq"])
        if before_seq is not None and seq >= before_seq:
            continue
        item = record if view == "events" else _conversation_record(record, facts.activated)
        if item is not None:
            eligible_count += 1
            if view == "events":
                records.append(item if _serialized_chars(item) <= MAX_RESPONSE_CHARS else _raw_budget_error())
            else:
                try:
                    records.append(_bound_conversation_record(item, session_id, head))
                except SessionReadError as error:
                    records.append(error)
    selected: list[dict[str, object]] = []
    for item in reversed(records):
        if isinstance(item, SessionReadError):
            if view == "events" or not selected:
                raise item
            break
        candidate = [item, *selected]
        if _serialized_chars(_history_result(session_id, view, head, candidate, eligible_count)) > MAX_RESPONSE_CHARS:
            if view == "events":
                raise _raw_budget_error()
            if not selected:
                raise SessionReadError("response_too_large", "Conversation metadata exceeds the response budget.")
            break
        selected = candidate
    result = _history_result(session_id, view, head, selected, eligible_count)
    if _serialized_chars(result) > MAX_RESPONSE_CHARS:
        raise SessionReadError("response_too_large", "History metadata exceeds the response budget.")
    return result


def _history_result(
    session_id: str, view: str, head: int, records: list[dict[str, object]], eligible_count: int
) -> dict[str, object]:
    return {
        "session_id": session_id,
        "view": view,
        "snapshot_seq": head,
        "records": records,
        "has_older": eligible_count > len(records),
        "next_before_seq": records[0]["seq"] if records else None,
        "omitted_fields": (
            sum(len(cast(list, record.get("omitted_fields", []))) for record in records)
            if view == "conversation"
            else 0
        ),
    }


def _raw_budget_error() -> SessionReadError:
    return SessionReadError(
        "response_too_large", "Raw Events exceed the response budget; reduce limit or use conversation."
    )


def _bound_conversation_record(item: dict[str, object], session_id: str, head: int) -> dict[str, object]:
    def omit(field: str, value: str) -> None:
        del item[field]
        item["omitted_fields"] = [{"field": field, "reason": "size_limit", "original_chars": len(value)}]

    for field in ("text", "arguments", "output"):
        value = item.get(field)
        if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
            omit(field, value)
    if _serialized_chars(_history_result(session_id, "conversation", head, [item], 1)) > MAX_RESPONSE_CHARS:
        for field in ("text", "arguments", "output"):
            value = item.get(field)
            if isinstance(value, str):
                omit(field, value)
    if _serialized_chars(_history_result(session_id, "conversation", head, [item], 1)) > MAX_RESPONSE_CHARS:
        raise SessionReadError("response_too_large", "Conversation metadata exceeds the response budget.")
    return item


def _conversation_record(record: dict[str, object], activated: frozenset[int]) -> dict[str, object] | None:
    event_type = cast(str, record["type"])
    if event_type == "user_steer" and record["seq"] not in activated:
        return None
    types = {
        "user_message": "user",
        "user_steer": "user",
        "assistant_message": "assistant",
        "tool_called": "tool_call",
        "tool_output": "tool_result",
    }
    if event_type not in types:
        return None
    timestamp = record.get("timestamp")
    if not isinstance(timestamp, str):
        raise JournalInvalidError("Visible Journal record has no timestamp")
    item: dict[str, object] = {"seq": record["seq"], "timestamp": timestamp, "type": types[event_type]}
    if event_type in {"user_message", "user_steer"}:
        try:
            item.update(input_origin_fields(input_origin_from_record(record)))
        except (TypeError, ValueError) as exc:
            raise JournalInvalidError("Visible Journal record has invalid input provenance") from exc
    if event_type == "tool_called":
        name, arguments = record.get("name"), record.get("arguments")
        if not isinstance(name, str) or not name or (arguments is not None and not isinstance(arguments, str)):
            raise JournalInvalidError("Visible Tool call has invalid content")
        item.update(name=name, arguments=arguments)
    else:
        field = "output" if event_type == "tool_output" else "text"
        if not isinstance(record.get(field), str):
            raise JournalInvalidError("Visible Journal record has invalid content")
        item[field] = record[field]
    images = record.get("images", [])
    if not isinstance(images, list) or any(not isinstance(image, dict) for image in images):
        raise JournalInvalidError("Visible Journal record has invalid image metadata")
    if images:
        item["images"] = [_image_metadata(image) for image in images]
        item["image_count"] = len(images)
    return item


def _image_metadata(image: dict[str, object]) -> dict[str, object]:
    filename = image.get("filename")
    if isinstance(filename, str):
        try:
            parsed = urlsplit(filename.lstrip())
            filename = None if parsed.scheme == "data" else parsed.path.replace("\\", "/").rsplit("/", 1)[-1]
        except ValueError:
            filename = None
    else:
        filename = None
    return {
        "filename": filename,
        "media_type": image.get("media_type") if image.get("media_type") in RENDERABLE_MIME_TYPES else None,
        "detail": image.get("detail") if image.get("detail") in IMAGE_DETAILS else None,
    }
