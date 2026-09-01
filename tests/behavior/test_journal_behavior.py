from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from investorch.journal import SessionJournal, read_session_journal, read_session_journal_page


def make_journal(directory: Path) -> SessionJournal:
    return SessionJournal(directory, ZoneInfo("UTC"))


@pytest.mark.asyncio
async def test_appends_have_increasing_sequences_and_preserve_event_order(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)

    first = await journal.record_user_message("session-a", "hello")
    second = await journal.record_user_steer("session-a", "run-a", "more")
    third = await journal.record_approval("session-a", "run-a", "approval-a", "edit", None, True)

    records = read_session_journal(tmp_path, "session-a")
    assert (first, second, third) == (1, 2, 3)
    assert [record["seq"] for record in records] == [1, 2, 3]
    assert [record["type"] for record in records] == ["user_message", "user_steer", "approval"]


@pytest.mark.asyncio
async def test_reopened_journal_continues_the_durable_sequence(tmp_path: Path) -> None:
    first_instance = make_journal(tmp_path)
    await first_instance.record_user_message("session-a", "one")
    await first_instance.record_user_message("session-a", "two")

    third = await make_journal(tmp_path).record_user_message("session-a", "three")

    assert third == 3


@pytest.mark.asyncio
async def test_history_before_seq_is_exclusive_and_pages_remain_ascending(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    for index in range(1, 6):
        await journal.record_user_message("session-a", str(index))

    latest = read_session_journal_page(tmp_path, "session-a", limit=2)
    middle = read_session_journal_page(tmp_path, "session-a", before_seq=4, limit=2)
    oldest = read_session_journal_page(tmp_path, "session-a", before_seq=2, limit=2)

    assert [record["seq"] for record in latest.records] == [4, 5]
    assert latest.has_older is True
    assert [record["seq"] for record in middle.records] == [2, 3]
    assert middle.has_older is True
    assert [record["seq"] for record in oldest.records] == [1]
    assert oldest.has_older is False


@pytest.mark.asyncio
async def test_page_before_first_record_has_empty_boundaries(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    await journal.record_user_message("session-a", "one")

    page = read_session_journal_page(tmp_path, "session-a", before_seq=1)

    assert page.records == ()
    assert page.oldest_seq is None
    assert page.newest_seq is None
    assert page.has_older is False


@pytest.mark.parametrize(
    "damaged_content",
    [
        '{"seq":1,"type":"user_message"}\nnot-json\n',
        '{"seq":2,"type":"user_message"}\n{"seq":1,"type":"user_message"}\n',
    ],
)
def test_corrupt_journal_fails_closed(tmp_path: Path, damaged_content: str) -> None:
    (tmp_path / "session-a.jsonl").write_text(damaged_content, encoding="utf-8")

    with pytest.raises(RuntimeError):
        read_session_journal(tmp_path, "session-a")


def test_incomplete_final_line_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "session-a.jsonl").write_text('{"seq":1,"type":"user_message"}', encoding="utf-8")

    with pytest.raises(RuntimeError, match="incomplete"):
        read_session_journal(tmp_path, "session-a")


@pytest.mark.asyncio
async def test_clone_is_a_stable_snapshot_and_both_sessions_then_evolve_independently(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    await journal.record_user_message("source", "one")
    await journal.record_user_message("source", "two")

    cloned = await journal.clone_session("source", "target")
    source_at_clone = read_session_journal(tmp_path, "source")

    await journal.record_user_message("source", "source-only")
    target_next_seq = await journal.record_user_message("target", "target-only")

    assert cloned is True
    assert source_at_clone == read_session_journal(tmp_path, "target")[:2]
    assert [record["text"] for record in read_session_journal(tmp_path, "source")] == ["one", "two", "source-only"]
    assert [record["text"] for record in read_session_journal(tmp_path, "target")] == ["one", "two", "target-only"]
    assert target_next_seq == 3


@pytest.mark.asyncio
async def test_delete_fence_blocks_late_writes_until_cancelled(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    await journal.record_user_message("session-a", "one")
    await journal.prepare_session_delete("session-a")

    with pytest.raises(RuntimeError, match="deleted"):
        await journal.record_user_message("session-a", "blocked")

    await journal.cancel_session_delete("session-a")
    assert await journal.record_user_message("session-a", "allowed") == 2


@pytest.mark.asyncio
async def test_deleted_history_disappears_and_remains_fenced(tmp_path: Path) -> None:
    journal = make_journal(tmp_path)
    await journal.record_user_message("session-a", "one")

    await journal.delete_session("session-a")

    assert await journal.session_exists("session-a") is False
    with pytest.raises(FileNotFoundError):
        read_session_journal(tmp_path, "session-a")
    with pytest.raises(RuntimeError, match="deleted"):
        await journal.record_user_message("session-a", "late")
