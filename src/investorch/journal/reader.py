import json
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast


@dataclass(frozen=True, slots=True)
class JournalPage:
    records: tuple[dict[str, object], ...]
    has_older: bool
    oldest_seq: int | None
    newest_seq: int | None


def read_session_journal(
    directory: Path,
    session_id: str,
) -> list[dict[str, object]]:
    path = _session_path(directory, session_id)
    return list(_iter_session_journal_records(path))


def read_session_journal_page(
    directory: Path,
    session_id: str,
    *,
    before_seq: int | None = None,
    limit: int = 200,
) -> JournalPage:
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    if before_seq is not None and (type(before_seq) is not int or before_seq < 1):
        raise ValueError("before_seq must be a positive integer or None")

    eligible: deque[dict[str, object]] = deque(maxlen=limit)
    eligible_count = 0
    path = _session_path(directory, session_id)
    for record in _iter_session_journal_records(path):
        seq = cast(int, record["seq"])
        if before_seq is None or seq < before_seq:
            eligible_count += 1
            eligible.append(record)

    records = tuple(eligible)
    return JournalPage(
        records=records,
        has_older=eligible_count > len(records),
        oldest_seq=cast(int, records[0]["seq"]) if records else None,
        newest_seq=cast(int, records[-1]["seq"]) if records else None,
    )


def _iter_session_journal_records(path: Path) -> Iterator[dict[str, object]]:

    if path.is_symlink():
        raise RuntimeError(f"Session journal is not a regular file: {path}")
    if not path.exists():
        raise FileNotFoundError(path)
    if not path.is_file():
        raise RuntimeError(f"Session journal is not a regular file: {path}")

    previous_seq = 0

    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.endswith("\n"):
                raise RuntimeError(f"Session journal has an incomplete line {line_number}: {path}")

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Session journal has invalid JSON on line {line_number}: {path}") from exc

            if not isinstance(record, dict):
                raise RuntimeError(f"Session journal line {line_number} is not an object: {path}")

            seq = record.get("seq")
            if type(seq) is not int or seq < 1:
                raise RuntimeError(f"Session journal has an invalid sequence on line {line_number}: {path}")
            if seq <= previous_seq:
                raise RuntimeError(f"Session journal sequence is not increasing on line {line_number}: {path}")

            event_type = record.get("type")
            if not isinstance(event_type, str):
                raise RuntimeError(f"Session journal has an invalid type on line {line_number}: {path}")

            previous_seq = seq
            yield record


def _session_path(directory: Path, session_id: str) -> Path:
    if (
        not session_id
        or session_id in {".", ".."}
        or "/" in session_id
        or "\\" in session_id
        or Path(session_id).name != session_id
    ):
        raise ValueError("session_id must be a non-empty filename-safe value")

    return directory / f"{session_id}.jsonl"
