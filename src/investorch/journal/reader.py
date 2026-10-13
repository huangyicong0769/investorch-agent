from __future__ import annotations

import errno
import json
import os
import stat
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast


class JournalInvalidError(RuntimeError):
    """Raised when a captured, committed Journal prefix is not valid history."""


class JournalUnavailableError(RuntimeError):
    """Raised when lifecycle operations prevent capturing a Journal snapshot."""


@dataclass(frozen=True, slots=True)
class JournalSnapshot:
    """A read-only file descriptor and committed byte boundary, scanned outside the write lock."""

    _file: BinaryIO | None
    _byte_count: int

    @classmethod
    def capture(cls, path: Path) -> JournalSnapshot:
        if path.is_symlink():
            raise JournalInvalidError("Session journal is not a regular file")
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        except FileNotFoundError:
            return cls(None, 0)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise JournalInvalidError("Session journal is not a regular file") from exc
            raise
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise JournalInvalidError("Session journal is not a regular file")
            file = os.fdopen(descriptor, "rb")
            descriptor = -1
            return cls(file, info.st_size)
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def close(self) -> None:
        if self._file is not None:
            self._file.close()

    def iter_records(self, *, through_seq: int | None = None) -> Iterator[dict[str, object]]:
        """Replay the same prefix; later appends and bytes beyond through_seq are excluded."""
        if through_seq is not None and (type(through_seq) is not int or through_seq < 0):
            raise ValueError("through_seq must be a non-negative integer or None")
        if self._file is None or through_seq == 0:
            return
        self._file.seek(0)
        remaining = self._byte_count
        previous_seq = 0
        line_number = 0
        while remaining:
            line = self._file.readline(remaining)
            if not line:
                raise JournalInvalidError("Captured Session journal was truncated")
            remaining -= len(line)
            line_number += 1
            record = _parse_journal_line(line, previous_seq, line_number, "captured Journal")
            seq = cast(int, record["seq"])
            previous_seq = seq
            if through_seq is not None and seq > through_seq:
                return

            yield record
            if seq == through_seq:
                return


@dataclass(frozen=True, slots=True)
class JournalPage:
    records: tuple[dict[str, object], ...]
    has_older: bool
    oldest_seq: int | None
    newest_seq: int | None


def read_session_journal(
    directory: Path,
    session_id: str,
    *,
    through_seq: int | None = None,
) -> list[dict[str, object]]:
    if through_seq is not None and (type(through_seq) is not int or through_seq < 1):
        raise ValueError("through_seq must be a positive integer or None")
    path = _session_path(directory, session_id)
    return list(_iter_session_journal_records(path, through_seq=through_seq))


def read_session_journal_page(
    directory: Path,
    session_id: str,
    *,
    before_seq: int | None = None,
    limit: int,
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


def _iter_session_journal_records(
    path: Path,
    *,
    through_seq: int | None = None,
) -> Iterator[dict[str, object]]:

    if path.is_symlink():
        raise RuntimeError(f"Session journal is not a regular file: {path}")
    if not path.exists():
        raise FileNotFoundError(path)
    if not path.is_file():
        raise RuntimeError(f"Session journal is not a regular file: {path}")

    previous_seq = 0

    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            record = _parse_journal_line(line, previous_seq, line_number, str(path))
            seq = cast(int, record["seq"])
            previous_seq = seq
            if through_seq is not None and seq > through_seq:
                return
            yield record
            if seq == through_seq:
                return


def _parse_journal_line(line: str | bytes, previous_seq: int, line_number: int, location: str) -> dict[str, object]:
    complete = line.endswith("\n") if isinstance(line, str) else line.endswith(b"\n")
    if not complete:
        raise JournalInvalidError(f"Session journal has an incomplete line {line_number}: {location}")
    try:
        record = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise JournalInvalidError(f"Session journal has invalid JSON on line {line_number}: {location}") from exc
    if not isinstance(record, dict):
        raise JournalInvalidError(f"Session journal line {line_number} is not an object: {location}")
    seq = record.get("seq")
    if type(seq) is not int or seq < 1:
        raise JournalInvalidError(f"Session journal has an invalid sequence on line {line_number}: {location}")
    if seq <= previous_seq:
        raise JournalInvalidError(f"Session journal sequence is not increasing on line {line_number}: {location}")
    if not isinstance(record.get("type"), str):
        raise JournalInvalidError(f"Session journal has an invalid type on line {line_number}: {location}")
    return record


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
