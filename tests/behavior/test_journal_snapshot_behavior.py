import asyncio
import threading
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from investorch.journal import SessionJournal
from investorch.journal.reader import JournalInvalidError, JournalSnapshot


@pytest.mark.asyncio
async def test_frozen_prefix_can_be_scanned_twice_without_blocking_later_appends(tmp_path: Path) -> None:
    journal = SessionJournal(tmp_path, ZoneInfo("UTC"))
    await journal.record_user_message("a", "first")
    captured = threading.Event()
    release = threading.Event()

    def read(snapshot: JournalSnapshot) -> tuple[list, list]:
        first = list(snapshot.iter_records())
        captured.set()
        assert release.wait(2)
        return first, list(snapshot.iter_records())

    task = asyncio.create_task(journal.read_snapshot("a", read))
    try:
        assert await asyncio.to_thread(captured.wait, 2)
        async with asyncio.timeout(2):
            assert await journal.record_user_message("a", "later") == 2
    finally:
        release.set()
    first, second = await task
    assert [record["text"] for record in first] == ["first"]
    assert first == second
    assert [record["text"] for record in await journal.read_snapshot("a", lambda s: list(s.iter_records()))] == [
        "first",
        "later",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["delete", "truncate", "cancel"])
async def test_frozen_reader_handles_deletion_truncation_and_cancellation(tmp_path: Path, change: str) -> None:
    journal = SessionJournal(tmp_path, ZoneInfo("UTC"))
    await journal.record_user_message("a", "first")
    captured = threading.Event()
    release = threading.Event()

    def read(snapshot: JournalSnapshot) -> list:
        captured.set()
        assert release.wait(2)
        return list(snapshot.iter_records())

    task = asyncio.create_task(journal.read_snapshot("a", read))
    try:
        assert await asyncio.to_thread(captured.wait, 2)
        if change == "delete":
            await journal.delete_session("a")
        elif change == "truncate":
            (tmp_path / "a.jsonl").write_bytes(b"")
        else:
            task.cancel()
            assert await journal.record_user_message("a", "after cancellation") == 2
    finally:
        release.set()
    if change == "truncate":
        with pytest.raises(JournalInvalidError, match="truncated"):
            await task
    elif change == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(await journal.read_snapshot("a", lambda s: list(s.iter_records()))) == 2
    else:
        assert [record["text"] for record in await task] == ["first"]
        assert not (tmp_path / "a.jsonl").exists()


@pytest.mark.asyncio
async def test_live_writer_and_readers_observe_only_committed_complete_prefixes(tmp_path: Path) -> None:
    journal = SessionJournal(tmp_path, ZoneInfo("UTC"))

    async def write() -> None:
        for _ in range(10):
            await journal.record_user_message("a", "雪" * 100000)

    async def read() -> None:
        for _ in range(10):
            records = await journal.read_snapshot("a", lambda s: list(s.iter_records()))
            assert [record["seq"] for record in records] == list(range(1, len(records) + 1))
            assert all(record["text"] == "雪" * 100000 for record in records)

    await asyncio.gather(write(), read())
    assert len(await journal.read_snapshot("a", lambda s: list(s.iter_records()))) == 10


@pytest.mark.asyncio
async def test_missing_history_is_empty_but_committed_partial_lines_and_symlinks_fail_closed(tmp_path: Path) -> None:
    journal = SessionJournal(tmp_path, ZoneInfo("UTC"))
    assert await journal.read_snapshot("unused", lambda s: list(s.iter_records())) == []
    assert not (tmp_path / "unused.jsonl").exists()
    (tmp_path / "partial.jsonl").write_text('{"seq":1,"type":"user_message"}', encoding="utf-8")
    with pytest.raises(JournalInvalidError, match="incomplete"):
        await journal.read_snapshot("partial", lambda s: list(s.iter_records()))
    (tmp_path / "link.jsonl").symlink_to(tmp_path / "partial.jsonl")
    with pytest.raises(JournalInvalidError, match="regular file"):
        await journal.read_snapshot("link", lambda s: list(s.iter_records()))
