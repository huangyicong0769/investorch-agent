from __future__ import annotations

from pathlib import Path

import pytest
from agents import SQLiteSession

from investorch.application.interaction import ArchivedSessionInputError, submit_user_input
from investorch.application.sessions import (
    SessionArchivedError,
    SessionHasChildrenError,
    SessionHasQueuedInputsError,
)
from investorch.context import AppState
from investorch.journal import read_session_journal
from investorch.runtime import SessionBusyError
from investorch.storage import get_session, list_archived_sessions, list_sessions
from tests.support.runtime import run_options
from tests.support.sessions import SessionHarness, make_session_harness


async def add_sdk_item(harness: SessionHarness, session_id: str, text: str) -> None:
    session = SQLiteSession(session_id, harness.runtime.config.sessions_db)
    try:
        await session.add_items([{"role": "user", "content": text}])
    finally:
        session.close()


async def sdk_items(harness: SessionHarness, session_id: str) -> list[object]:
    session = SQLiteSession(session_id, harness.runtime.config.sessions_db)
    try:
        return await session.get_items()
    finally:
        session.close()


@pytest.mark.asyncio
async def test_create_persists_a_normal_unarchived_session(tmp_path: Path) -> None:
    harness = make_session_harness(tmp_path)

    session_id = await harness.operations.create()

    record = get_session(harness.runtime.config.sessions_db, session_id)
    assert record is not None
    assert record.archived_at is None
    assert session_id in {item.session_id for item in list_sessions(harness.runtime.config.sessions_db)}
    await harness.runtime.runtime.aclose()


@pytest.mark.asyncio
async def test_archive_and_unarchive_preserve_durable_session_state(tmp_path: Path) -> None:
    harness = make_session_harness(tmp_path)
    parent_id = await harness.operations.create()
    session_id = await harness.operations.fork(parent_id)
    await harness.operations.set_title(session_id, "Research")
    await add_sdk_item(harness, session_id, "sdk history")
    await harness.runtime.journal.record_user_message(session_id, "journal history")

    await harness.operations.archive(session_id)

    assert session_id not in {item.session_id for item in list_sessions(harness.runtime.config.sessions_db)}
    assert session_id in {item.session_id for item in list_archived_sessions(harness.runtime.config.sessions_db)}
    archived = get_session(harness.runtime.config.sessions_db, session_id)
    assert archived is not None
    assert archived.title == "Research"
    assert archived.branch_from_session_id == parent_id
    assert await sdk_items(harness, session_id) == [{"role": "user", "content": "sdk history"}]
    assert [
        record["text"] for record in read_session_journal(harness.runtime.config.session_journal_dir, session_id)
    ] == ["journal history"]

    state = AppState(
        config=harness.runtime.config,
        execution=harness.runtime.execution,
        selected_session_id=session_id,
        main_reasoning_effort="none",
        permission_mode="manual",
    )
    with pytest.raises(ArchivedSessionInputError):
        await submit_user_input(state=state, runtime=harness.runtime.runtime, session_id=session_id, text="blocked")

    await harness.operations.unarchive(session_id)

    restored = get_session(harness.runtime.config.sessions_db, session_id)
    assert restored is not None
    assert restored.archived_at is None
    assert restored.branch_from_session_id == parent_id
    assert session_id in {item.session_id for item in list_sessions(harness.runtime.config.sessions_db)}

    submission = await submit_user_input(
        state=state,
        runtime=harness.runtime.runtime,
        session_id=session_id,
        text="accepted after restore",
    )
    await harness.runtime.agent_loop.wait_until_started(session_id)
    assert submission.disposition == "run_started"
    harness.runtime.agent_loop.complete(session_id)
    await harness.runtime.wait_for_run_ended(session_id)
    await harness.runtime.runtime.aclose()


@pytest.mark.asyncio
async def test_archive_rejects_busy_or_queued_session(tmp_path: Path) -> None:
    busy = make_session_harness(tmp_path / "busy")
    busy_id = await busy.operations.create()
    busy.runtime.runtime.start_run(busy_id, "running", run_options())
    await busy.runtime.agent_loop.wait_until_started(busy_id)

    with pytest.raises(SessionBusyError):
        await busy.operations.archive(busy_id)

    busy.runtime.agent_loop.complete(busy_id)
    await busy.runtime.wait_for_run_ended(busy_id)
    await busy.runtime.runtime.aclose()

    queued = make_session_harness(tmp_path / "queued")
    queued_id = await queued.operations.create()
    queued.runtime.runtime.start_run(queued_id, "running", run_options("queue"))
    await queued.runtime.agent_loop.wait_until_started(queued_id)
    await queued.runtime.runtime.submit_follow_up(queued_id, "Q1", run_options())

    with pytest.raises(SessionHasQueuedInputsError):
        await queued.operations.archive(queued_id)

    queued.runtime.runtime.clear_queue(queued_id)
    queued.runtime.agent_loop.complete(queued_id)
    await queued.runtime.wait_for_run_ended(queued_id)
    await queued.runtime.runtime.aclose()


@pytest.mark.asyncio
async def test_title_persists_and_archived_session_cannot_be_renamed(tmp_path: Path) -> None:
    harness = make_session_harness(tmp_path)
    session_id = await harness.operations.create()

    updated = await harness.operations.set_title(session_id, "New title")

    assert updated.title == "New title"
    assert get_session(harness.runtime.config.sessions_db, session_id).title == "New title"
    await harness.operations.archive(session_id)
    with pytest.raises(SessionArchivedError):
        await harness.operations.set_title(session_id, "Forbidden")
    assert get_session(harness.runtime.config.sessions_db, session_id).title == "New title"
    await harness.runtime.runtime.aclose()


@pytest.mark.asyncio
async def test_fork_copies_stable_head_without_transient_runtime_state(tmp_path: Path) -> None:
    harness = make_session_harness(tmp_path)
    source_id = await harness.operations.create()
    await harness.operations.set_title(source_id, "Research")
    await add_sdk_item(harness, source_id, "sdk history")
    await harness.runtime.journal.record_user_message(source_id, "journal history")

    target_id = await harness.operations.fork(source_id)

    target = get_session(harness.runtime.config.sessions_db, target_id)
    assert target is not None
    assert target_id != source_id
    assert target.title == "Research (fork)"
    assert target.branch_from_session_id == source_id
    assert target.archived_at is None
    assert await sdk_items(harness, target_id) == await sdk_items(harness, source_id)
    assert read_session_journal(harness.runtime.config.session_journal_dir, target_id) == read_session_journal(
        harness.runtime.config.session_journal_dir,
        source_id,
    )
    snapshot = harness.runtime.runtime.session_snapshot(target_id)
    assert snapshot.run_id is None
    assert snapshot.queued_count == 0
    assert snapshot.pending_steer_count == 0
    assert snapshot.todos == ()
    await harness.runtime.runtime.aclose()


@pytest.mark.asyncio
async def test_forked_sessions_evolve_independently(tmp_path: Path) -> None:
    harness = make_session_harness(tmp_path)
    source_id = await harness.operations.create()
    await add_sdk_item(harness, source_id, "shared")
    await harness.runtime.journal.record_user_message(source_id, "shared")
    target_id = await harness.operations.fork(source_id)

    await add_sdk_item(harness, source_id, "source-only")
    await add_sdk_item(harness, target_id, "target-only")
    await harness.runtime.journal.record_user_message(source_id, "source-only")
    await harness.runtime.journal.record_user_message(target_id, "target-only")

    assert [item["content"] for item in await sdk_items(harness, source_id)] == ["shared", "source-only"]
    assert [item["content"] for item in await sdk_items(harness, target_id)] == ["shared", "target-only"]
    assert [item["text"] for item in read_session_journal(harness.runtime.config.session_journal_dir, source_id)] == [
        "shared",
        "source-only",
    ]
    assert [item["text"] for item in read_session_journal(harness.runtime.config.session_journal_dir, target_id)] == [
        "shared",
        "target-only",
    ]
    await harness.runtime.runtime.aclose()


@pytest.mark.asyncio
async def test_parent_with_child_cannot_be_deleted(tmp_path: Path) -> None:
    harness = make_session_harness(tmp_path)
    parent_id = await harness.operations.create()
    child_id = await harness.operations.fork(parent_id)

    with pytest.raises(SessionHasChildrenError):
        await harness.operations.delete(parent_id)

    assert get_session(harness.runtime.config.sessions_db, parent_id) is not None
    assert get_session(harness.runtime.config.sessions_db, child_id) is not None
    await harness.runtime.runtime.aclose()


@pytest.mark.asyncio
async def test_delete_removes_identity_and_history_and_blocks_late_journal_write(tmp_path: Path) -> None:
    harness = make_session_harness(tmp_path)
    session_id = await harness.operations.create()
    await harness.runtime.journal.record_user_message(session_id, "history")

    await harness.operations.delete(session_id)

    assert get_session(harness.runtime.config.sessions_db, session_id) is None
    assert session_id not in {
        record.session_id for record in list_sessions(harness.runtime.config.sessions_db, include_archived=True)
    }
    assert await harness.runtime.journal.session_exists(session_id) is False
    with pytest.raises(RuntimeError, match="deleted"):
        await harness.runtime.journal.record_user_message(session_id, "late")
    await harness.runtime.runtime.aclose()


@pytest.mark.asyncio
async def test_clear_empties_old_continuation_and_metadata_and_creates_replacement(tmp_path: Path) -> None:
    harness = make_session_harness(tmp_path)
    parent_id = await harness.operations.create()
    old_id = await harness.operations.fork(parent_id)
    await harness.operations.set_title(old_id, "Old title")
    await add_sdk_item(harness, old_id, "old continuation")

    replacement_id = await harness.operations.clear(old_id)

    old = get_session(harness.runtime.config.sessions_db, old_id)
    replacement = get_session(harness.runtime.config.sessions_db, replacement_id)
    assert replacement_id != old_id
    assert replacement is not None
    assert replacement.archived_at is None
    assert replacement.title is None
    assert replacement.branch_from_session_id is None
    assert old is None
    assert await sdk_items(harness, old_id) == []

    harness.runtime.runtime.start_run(replacement_id, "replacement is usable", run_options())
    await harness.runtime.agent_loop.wait_until_started(replacement_id)
    harness.runtime.agent_loop.complete(replacement_id)
    assert (await harness.runtime.wait_for_run_ended(replacement_id)).status == "completed"
    await harness.runtime.runtime.aclose()


@pytest.mark.asyncio
async def test_clear_rejects_archived_busy_or_queued_session(tmp_path: Path) -> None:
    harness = make_session_harness(tmp_path)

    archived_id = await harness.operations.create()
    await harness.operations.archive(archived_id)
    with pytest.raises(SessionArchivedError):
        await harness.operations.clear(archived_id)

    busy_id = await harness.operations.create()
    harness.runtime.runtime.start_run(busy_id, "running", run_options())
    await harness.runtime.agent_loop.wait_until_started(busy_id)
    with pytest.raises(SessionBusyError):
        await harness.operations.clear(busy_id)
    harness.runtime.agent_loop.complete(busy_id)
    await harness.runtime.wait_for_run_ended(busy_id)

    queued_id = await harness.operations.create()
    harness.runtime.runtime.start_run(queued_id, "running", run_options("queue"))
    await harness.runtime.agent_loop.wait_until_started(queued_id)
    await harness.runtime.runtime.submit_follow_up(queued_id, "future", run_options())
    with pytest.raises(SessionHasQueuedInputsError):
        await harness.operations.clear(queued_id)
    harness.runtime.runtime.clear_queue(queued_id)
    harness.runtime.agent_loop.complete(queued_id)
    await harness.runtime.wait_for_run_ended(queued_id)
    await harness.runtime.runtime.aclose()


@pytest.mark.asyncio
async def test_discard_unused_deletes_only_unowned_empty_session(tmp_path: Path) -> None:
    harness = make_session_harness(tmp_path)
    empty_id = await harness.operations.create()
    sdk_id = await harness.operations.create()
    journal_id = await harness.operations.create()
    titled_id = await harness.operations.create()
    parent_id = await harness.operations.create()
    child_id = await harness.operations.fork(parent_id)
    queued_id = await harness.operations.create()

    await add_sdk_item(harness, sdk_id, "used")
    await harness.runtime.journal.record_user_message(journal_id, "used")
    await harness.operations.set_title(titled_id, "Used")
    harness.runtime.runtime.start_run(queued_id, "running", run_options("queue"))
    await harness.runtime.agent_loop.wait_until_started(queued_id)
    await harness.runtime.runtime.submit_follow_up(queued_id, "future", run_options())

    assert await harness.operations.discard_if_unused(empty_id) is True
    for retained_id in (sdk_id, journal_id, titled_id, parent_id, child_id, queued_id):
        assert await harness.operations.discard_if_unused(retained_id) is False
        assert get_session(harness.runtime.config.sessions_db, retained_id) is not None
    assert get_session(harness.runtime.config.sessions_db, empty_id) is None

    harness.runtime.runtime.clear_queue(queued_id)
    harness.runtime.runtime.cancel_run(queued_id)
    assert (await harness.runtime.wait_for_run_ended(queued_id)).status == "cancelled"
    await harness.runtime.runtime.aclose()
