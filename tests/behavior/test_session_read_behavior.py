import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from investorch.application.session_reads import SessionReadError, SessionReadOperations
from investorch.images import ImageContent, UserInput
from investorch.journal import read_session_journal
from investorch.output import AssistantMessage, ToolCalled, ToolOutput
from investorch.storage import archive_session, create_session, set_session_branch_from, set_session_title
from tests.support.runtime import make_runtime_harness
from tests.support.session_history import record_mixed_history


@pytest.mark.asyncio
async def test_list_enumerates_root_sessions_with_stable_ties_title_filter_and_archive_option(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    reads = SessionReadOperations(
        config=harness.config, journal=harness.journal, get_runtime_snapshot=harness.runtime.session_snapshot
    )
    assert await reads.list_sessions() == {"sessions": [], "has_more": False, "next_offset": None}
    for index in reversed(range(55)):
        session_id = f"session-{index:02}"
        create_session(harness.config.sessions_db, session_id)
        set_session_title(harness.config.sessions_db, session_id, f"Straße {index}")
    set_session_branch_from(harness.config.sessions_db, "session-02", "session-01")
    archive_session(harness.config.sessions_db, "session-54")
    with sqlite3.connect(harness.config.sessions_db) as connection:
        connection.execute("UPDATE agent_sessions SET updated_at = '2026-10-13 00:00:00'")

    first = await reads.list_sessions()
    last = await reads.list_sessions(offset=first["next_offset"])
    archived = await reads.list_sessions(query="STRASSE", include_archived=True, offset=54)

    assert [item["session_id"] for item in first["sessions"]] == [f"session-{i:02}" for i in range(50)]
    assert first["has_more"] is True
    assert first["next_offset"] == 50
    assert [item["session_id"] for item in last["sessions"]] == ["session-50", "session-51", "session-52", "session-53"]
    assert last["has_more"] is False
    assert last["next_offset"] is None
    assert first["sessions"][2] == {
        "session_id": "session-02",
        "title": "Straße 2",
        "branch_from_session_id": "session-01",
        "archived": False,
        "created_at": first["sessions"][2]["created_at"],
        "updated_at": "2026-10-13 00:00:00",
        "status": "idle",
    }
    assert archived["sessions"][0]["archived"] is True
    assert (await reads.list_sessions(query="session-02"))["sessions"] == []
    assert await reads.list_sessions(query=" \t") == first
    await harness.runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phase", "queued", "steers", "status"),
    [
        ("running", 2, 3, "running"),
        ("waiting_approval", 0, 0, "waiting_approval"),
        ("stopping", 0, 0, "stopping"),
        (None, 2, 0, "pending"),
        (None, 0, 1, "pending"),
        (None, 0, 0, "idle"),
    ],
)
async def test_list_projects_only_current_status_and_keeps_archive_independent(
    tmp_path: Path, phase: str | None, queued: int, steers: int, status: str
) -> None:
    harness = make_runtime_harness(tmp_path)
    create_session(harness.config.sessions_db, "a")
    archive_session(harness.config.sessions_db, "a")
    snapshot = replace(
        harness.runtime.session_snapshot("a"),
        run_phase=phase,
        run_id="current" if phase else None,
        queued_count=queued,
        pending_steer_count=steers,
        queue_paused=True,
    )
    reads = SessionReadOperations(
        config=harness.config, journal=harness.journal, get_runtime_snapshot=lambda _: snapshot
    )

    result = await reads.list_sessions(include_archived=True)

    assert result["sessions"][0]["status"] == status
    assert result["sessions"][0]["archived"] is True
    assert set(result["sessions"][0]) == {
        "session_id",
        "title",
        "archived",
        "branch_from_session_id",
        "created_at",
        "updated_at",
        "status",
    }
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_list_budget_shortens_pages_without_losing_any_session(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    reads = SessionReadOperations(
        config=harness.config, journal=harness.journal, get_runtime_snapshot=harness.runtime.session_snapshot
    )
    for index in range(40):
        session_id = f"session-{index:02}"
        create_session(harness.config.sessions_db, session_id)
        set_session_title(harness.config.sessions_db, session_id, "a" * 1000)
    seen = []
    offset = 0
    while True:
        page = await reads.list_sessions(limit=100, offset=offset)
        assert len(json.dumps(page)) <= 32000
        seen.extend(item["session_id"] for item in page["sessions"])
        if not page["has_more"]:
            break
        offset = page["next_offset"]
    assert len(seen) == len(set(seen)) == 40
    set_session_title(harness.config.sessions_db, seen[0], "a" * 32000)
    with pytest.raises(SessionReadError, match="response_too_large"):
        await reads.list_sessions(query="a" * 32000)
    await harness.runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"offset": -1},
        {"offset": False},
        {"query": 1},
        {"include_archived": 1},
    ],
)
async def test_list_rejects_invalid_arguments(tmp_path: Path, arguments: dict[str, object]) -> None:
    harness = make_runtime_harness(tmp_path)
    reads = SessionReadOperations(
        config=harness.config, journal=harness.journal, get_runtime_snapshot=harness.runtime.session_snapshot
    )
    with pytest.raises(ValueError):
        await reads.list_sessions(**arguments)
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_read_projects_flat_effective_history_with_original_identity_and_sanitized_images(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    create_session(harness.config.sessions_db, "archived")
    await record_mixed_history(harness.journal, "archived")
    archive_session(harness.config.sessions_db, "archived")
    reads = SessionReadOperations(
        config=harness.config, journal=harness.journal, get_runtime_snapshot=harness.runtime.session_snapshot
    )
    raw = read_session_journal(harness.config.session_journal_dir, "archived")

    result = await reads.read_session("archived")

    assert set(result) == {
        "session_id",
        "view",
        "snapshot_seq",
        "records",
        "has_older",
        "next_before_seq",
        "omitted_fields",
    }
    assert result["view"] == "conversation"
    assert result["snapshot_seq"] == 16
    assert [(record["seq"], record["type"]) for record in result["records"]] == [
        (1, "user"),
        (2, "assistant"),
        (4, "tool_call"),
        (5, "tool_result"),
        (7, "user"),
        (14, "user"),
        (15, "assistant"),
    ]
    assert [record["timestamp"] for record in result["records"]] == [
        raw[i - 1]["timestamp"] for i in [1, 2, 4, 5, 7, 14, 15]
    ]
    assert result["records"][0]["images"] == [{"filename": "input.png", "media_type": "image/png", "detail": "auto"}]
    assert result["records"][0]["image_count"] == 1
    assert "image_url" not in json.dumps(result)
    assert "base64" not in json.dumps(result)
    assert "credential" not in json.dumps(result)
    assert result["has_older"] is False
    assert result["next_before_seq"] == 1
    assert (await reads.read_session("archived", view="events"))["records"] == raw
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_visible_paging_looks_ahead_for_activation_and_keeps_the_same_snapshot_after_appends(
    tmp_path: Path,
) -> None:
    harness = make_runtime_harness(tmp_path)
    create_session(harness.config.sessions_db, "a")
    await record_mixed_history(harness.journal, "a")
    reads = SessionReadOperations(
        config=harness.config, journal=harness.journal, get_runtime_snapshot=harness.runtime.session_snapshot
    )

    latest = await reads.read_session("a", limit=3)
    await harness.journal.record_user_steers_activated("a", "run-b", (16,))
    await harness.journal.record_output("a", AssistantMessage("new message"))
    middle = await reads.read_session(
        "a", limit=3, before_seq=latest["next_before_seq"], snapshot_seq=latest["snapshot_seq"]
    )
    oldest = await reads.read_session(
        "a", limit=3, before_seq=middle["next_before_seq"], snapshot_seq=latest["snapshot_seq"]
    )
    unproven = await reads.read_session("a", before_seq=8, snapshot_seq=10)
    frozen = await reads.read_session("a", snapshot_seq=16)
    fresh = await reads.read_session("a")

    assert [record["seq"] for record in latest["records"]] == [7, 14, 15]
    assert latest["next_before_seq"] == 7
    assert [record["seq"] for record in middle["records"]] == [2, 4, 5]
    assert middle["records"][-1] == {
        "seq": 5,
        "timestamp": middle["records"][-1]["timestamp"],
        "type": "tool_result",
        "output": "3",
    }
    assert [record["seq"] for record in oldest["records"]] == [1]
    assert oldest["has_older"] is False
    assert [record["seq"] for record in unproven["records"]] == [1, 2, 4, 5]
    assert frozen["records"][-1]["seq"] == 15
    assert [record["seq"] for record in fresh["records"]][-3:] == [15, 16, 18]
    assert fresh["snapshot_seq"] == 18
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_empty_history_is_distinct_from_an_unknown_exact_id_and_empty_snapshot_stays_empty(
    tmp_path: Path,
) -> None:
    harness = make_runtime_harness(tmp_path)
    create_session(harness.config.sessions_db, "session-exact")
    reads = SessionReadOperations(
        config=harness.config, journal=harness.journal, get_runtime_snapshot=harness.runtime.session_snapshot
    )
    expected = {
        "session_id": "session-exact",
        "view": "conversation",
        "snapshot_seq": 0,
        "records": [],
        "has_older": False,
        "next_before_seq": None,
        "omitted_fields": 0,
    }
    assert await reads.read_session("session-exact") == expected
    with pytest.raises(SessionReadError, match="session_not_found"):
        await reads.read_session("session")
    await harness.journal.record_user_message("session-exact", "later")
    assert await reads.read_session("session-exact", snapshot_seq=0) == expected
    empty = await reads.read_session("session-exact", before_seq=1)
    assert empty["records"] == []
    assert empty["has_older"] is False
    assert empty["next_before_seq"] is None
    await harness.runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"session_id": "../a"},
        {"session_id": ""},
        {"session_id": 1},
        {"view": "summary"},
        {"limit": 0},
        {"limit": 101},
        {"before_seq": 0},
        {"before_seq": True},
        {"snapshot_seq": -1},
        {"snapshot_seq": True},
        {"snapshot_seq": 2},
        {"snapshot_seq": 1, "before_seq": 3},
    ],
)
async def test_read_rejects_invalid_arguments_and_impossible_boundaries(
    tmp_path: Path, arguments: dict[str, object]
) -> None:
    harness = make_runtime_harness(tmp_path)
    create_session(harness.config.sessions_db, "a")
    await harness.journal.record_user_message("a", "first")
    reads = SessionReadOperations(
        config=harness.config, journal=harness.journal, get_runtime_snapshot=harness.runtime.session_snapshot
    )
    with pytest.raises(ValueError):
        await reads.read_session(**{"session_id": "a", **arguments})
    await harness.runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        "not-json\n",
        '{"seq":1,"type":"user_message"}',
        '{"seq":true,"type":"user_message"}\n',
        '{"seq":1,"type":1}\n',
        '{"seq":2,"type":"user_message"}\n{"seq":1,"type":"user_message"}\n',
    ],
)
async def test_corrupt_history_has_explicit_safe_feedback(tmp_path: Path, content: str) -> None:
    harness = make_runtime_harness(tmp_path)
    create_session(harness.config.sessions_db, "a")
    (harness.config.session_journal_dir / "a.jsonl").write_text(content, encoding="utf-8")
    reads = SessionReadOperations(
        config=harness.config, journal=harness.journal, get_runtime_snapshot=harness.runtime.session_snapshot
    )
    with pytest.raises(SessionReadError, match="journal_invalid") as error:
        await reads.read_session("a")
    assert str(harness.config.root) not in str(error.value)
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_conflicting_disposition_and_delete_fence_return_explicit_errors(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    create_session(harness.config.sessions_db, "a")
    seq = await harness.journal.record_user_steer("a", "run-a", "instruction")
    await harness.journal.record_user_steers_activated("a", "run-a", (seq,))
    await harness.journal.record_user_steers_discarded("a", "run-a", (seq,))
    reads = SessionReadOperations(
        config=harness.config, journal=harness.journal, get_runtime_snapshot=harness.runtime.session_snapshot
    )
    with pytest.raises(SessionReadError, match="journal_inconsistent"):
        await reads.read_session("a")
    await harness.journal.prepare_session_delete("a")
    with pytest.raises(SessionReadError, match="journal_unavailable"):
        await reads.read_session("a")
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_encoded_budget_retains_complete_newest_suffix_and_pagination_recovers_the_rest(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    create_session(harness.config.sessions_db, "a")
    reads = SessionReadOperations(
        config=harness.config, journal=harness.journal, get_runtime_snapshot=harness.runtime.session_snapshot
    )
    for suffix in ["oldest", "middle", "newest"]:
        await harness.journal.record_user_message("a", '"' * 10000 + suffix)

    page = await reads.read_session("a", limit=100)
    middle = await reads.read_session("a", before_seq=page["next_before_seq"], snapshot_seq=page["snapshot_seq"])
    oldest = await reads.read_session("a", before_seq=middle["next_before_seq"], snapshot_seq=page["snapshot_seq"])

    assert [record["seq"] for record in page["records"]] == [3]
    assert page["has_older"] is True
    assert page["omitted_fields"] == 0
    assert page["records"][0]["text"] == '"' * 10000 + "newest"
    assert middle["records"][0]["seq"] == 2
    assert oldest["records"][0]["seq"] == 1
    assert oldest["has_older"] is False
    assert all(len(json.dumps(value)) <= 32000 for value in [page, middle, oldest])
    with pytest.raises(SessionReadError, match="response_too_large"):
        await reads.read_session("a", view="events")
    assert (await reads.read_session("a", view="events", limit=1))["records"][0]["text"] == '"' * 10000 + "newest"
    await harness.runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["text", "arguments", "output"])
async def test_single_oversized_field_has_explicit_original_length_without_trapping_the_cursor(
    tmp_path: Path, field: str
) -> None:
    harness = make_runtime_harness(tmp_path)
    create_session(harness.config.sessions_db, "a")
    await harness.journal.record_user_message("a", "older input")
    if field == "text":
        await harness.journal.record_user_message("a", "x" * 280000)
    else:
        event = ToolOutput("x" * 280000) if field == "output" else ToolCalled("large-tool", "x" * 280000)
        await harness.journal.record_output("a", event)
    reads = SessionReadOperations(
        config=harness.config, journal=harness.journal, get_runtime_snapshot=harness.runtime.session_snapshot
    )

    page = await reads.read_session("a", limit=1)

    assert page["omitted_fields"] == 1
    assert page["records"][0]["seq"] == 2
    assert field not in page["records"][0]
    assert page["records"][0]["omitted_fields"] == [{"field": field, "reason": "size_limit", "original_chars": 280000}]
    assert page["has_older"] is True
    older = await reads.read_session("a", before_seq=page["next_before_seq"], snapshot_seq=page["snapshot_seq"])
    assert older["records"][0]["text"] == "older input"
    with pytest.raises(SessionReadError, match="response_too_large"):
        await reads.read_session("a", view="events", limit=1)
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_giant_image_payload_is_metadata_only_and_encoded_unicode_can_require_omission(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)
    create_session(harness.config.sessions_db, "a")
    image = ImageContent("data:image/png;base64," + "A" * 200000, filename="https://example.test/file.png?token=secret")
    await harness.journal.record_user_message("a", UserInput("", (image,)))
    await harness.journal.record_user_message("a", "雪" * 8000)
    reads = SessionReadOperations(
        config=harness.config, journal=harness.journal, get_runtime_snapshot=harness.runtime.session_snapshot
    )

    page = await reads.read_session("a")

    assert len(json.dumps(page)) <= 32000
    assert page["records"][0]["text"] == ""
    assert page["records"][0]["images"][0]["filename"] == "file.png"
    assert page["records"][1]["omitted_fields"] == [{"field": "text", "reason": "size_limit", "original_chars": 8000}]
    assert "image_url" not in json.dumps(page)
    assert "base64" not in json.dumps(page)
    assert "token" not in json.dumps(page)
    with pytest.raises(SessionReadError, match="response_too_large"):
        await reads.read_session("a", view="events", before_seq=2)
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_oversized_metadata_fails_explicitly_and_old_large_raw_records_do_not_poison_narrow_pages(
    tmp_path: Path,
) -> None:
    harness = make_runtime_harness(tmp_path)
    create_session(harness.config.sessions_db, "a")
    await harness.journal.record_output("a", ToolCalled("x" * 40000))
    await harness.journal.record_output("a", AssistantMessage("newer"))
    reads = SessionReadOperations(
        config=harness.config, journal=harness.journal, get_runtime_snapshot=harness.runtime.session_snapshot
    )

    latest = await reads.read_session("a", limit=1)
    assert latest["records"][0]["seq"] == 2
    assert (await reads.read_session("a", view="events", limit=1))["records"][0]["text"] == "newer"
    with pytest.raises(SessionReadError, match="response_too_large"):
        await reads.read_session("a", before_seq=2)
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_raw_events_preserve_unrecognized_fields_without_interpreting_them_as_output_markers(
    tmp_path: Path,
) -> None:
    harness = make_runtime_harness(tmp_path)
    create_session(harness.config.sessions_db, "a")
    event = {
        "seq": 1,
        "type": "reasoning",
        "timestamp": "old",
        "text": "raw",
        "omitted_fields": 1337,
        "extra": {"a": [None, False]},
    }
    (harness.config.session_journal_dir / "a.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    reads = SessionReadOperations(
        config=harness.config, journal=harness.journal, get_runtime_snapshot=harness.runtime.session_snapshot
    )

    result = await reads.read_session("a", view="events")

    assert result["records"] == [event]
    assert result["omitted_fields"] == 0
    await harness.runtime.aclose()
