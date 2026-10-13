import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from investorch.application.session_reads import SessionReadError, SessionReadOperations
from investorch.storage import archive_session, create_session, set_session_branch_from, set_session_title
from tests.support.runtime import make_runtime_harness


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
