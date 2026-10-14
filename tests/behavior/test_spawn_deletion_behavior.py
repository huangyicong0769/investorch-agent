import sqlite3
from pathlib import Path

import pytest

from investorch.application.sessions import (
    SessionDeletionCleanupError,
    SessionHasChildrenError,
    SessionHasQueuedInputsError,
)
from investorch.runtime import SessionBusyError
from investorch.storage import SessionDeletionScopeChangedError, create_spawn_session, get_session, list_sessions
from tests.support.runtime import run_options
from tests.support.sessions import make_session_harness


async def tree(harness):
    root = await harness.operations.create()
    for child, parent in (("child", root), ("grandchild", "child"), ("sibling", root)):
        create_spawn_session(
            harness.runtime.config.sessions_db, child, parent_session_id=parent, parent_run_id="run", tool_call_id=child
        )
        await harness.runtime.journal.record_user_message(child, "history")
    return root


async def test_explicit_delete_removes_the_spawn_tree_and_retains_unrelated_state(tmp_path):
    harness = make_session_harness(tmp_path)
    root = await tree(harness)
    unrelated = await harness.operations.create()
    artifact = harness.runtime.config.workspace_dir / "keep.txt"
    artifact.write_text("keep")
    deleted = await harness.operations.delete(root)
    assert set(deleted) == {root, "child", "grandchild", "sibling"}
    assert [r.session_id for r in list_sessions(harness.runtime.config.sessions_db)] == [unrelated]
    for session_id in deleted:
        assert not await harness.runtime.journal.session_exists(session_id)
        with pytest.raises(RuntimeError, match="deleted"):
            await harness.runtime.journal.record_user_message(session_id, "late")
    assert artifact.read_text() == "keep"
    await harness.runtime.runtime.aclose()


async def test_running_descendant_rejects_the_entire_delete_without_stopping_it(tmp_path):
    harness = make_session_harness(tmp_path)
    root = await tree(harness)
    harness.runtime.runtime.start_run("grandchild", "running", run_options())
    await harness.runtime.agent_loop.wait_until_started("grandchild")
    with pytest.raises(SessionBusyError):
        await harness.operations.delete(root)
    assert {r.session_id for r in list_sessions(harness.runtime.config.sessions_db)} == {
        root,
        "child",
        "grandchild",
        "sibling",
    }
    assert harness.runtime.runtime.session_snapshot("grandchild").run_phase == "running"
    await harness.runtime.journal.record_user_message("child", "still writable")
    await harness.runtime.runtime.aclose()


async def test_independent_fork_blocks_spawn_cascade(tmp_path):
    harness = make_session_harness(tmp_path)
    root = await tree(harness)
    fork = await harness.operations.fork("child")
    with pytest.raises(SessionHasChildrenError):
        await harness.operations.delete(root)
    assert get_session(harness.runtime.config.sessions_db, fork).branch_from_session_id == "child"
    await harness.runtime.runtime.aclose()


async def test_deleting_a_child_preserves_its_parent_and_sibling(tmp_path):
    harness = make_session_harness(tmp_path)
    root = await tree(harness)
    assert set(await harness.operations.delete("child")) == {"child", "grandchild"}
    assert {r.session_id for r in list_sessions(harness.runtime.config.sessions_db)} == {root, "sibling"}
    await harness.runtime.runtime.aclose()


async def test_changed_descendants_require_a_new_confirmation(tmp_path):
    harness = make_session_harness(tmp_path)
    root = await tree(harness)
    preview = await harness.operations.deletion_preview(root)
    create_spawn_session(
        harness.runtime.config.sessions_db, "later", parent_session_id=root, parent_run_id="run", tool_call_id="later"
    )
    with pytest.raises(SessionDeletionScopeChangedError):
        await harness.operations.delete(root, expected_version=preview["version"])
    assert len(list_sessions(harness.runtime.config.sessions_db)) == 5
    await harness.runtime.runtime.aclose()


async def test_queued_descendant_prevents_any_persistent_delete(tmp_path):
    harness = make_session_harness(tmp_path)
    root = await tree(harness)
    harness.runtime.runtime.start_run("child", "run", run_options("queue"))
    await harness.runtime.agent_loop.wait_until_started("child")
    await harness.runtime.runtime.submit_follow_up("child", "pending", run_options())
    harness.runtime.runtime.cancel_run("child")
    await harness.runtime.wait_for_run_ended("child")
    with pytest.raises(SessionHasQueuedInputsError):
        await harness.operations.delete(root)
    assert harness.runtime.runtime.list_queued_inputs("child")[0].text == "pending"
    assert get_session(harness.runtime.config.sessions_db, root) is not None
    await harness.runtime.runtime.aclose()


async def test_precommit_failure_rolls_back_the_whole_tree_and_releases_all_fences(tmp_path):
    harness = make_session_harness(tmp_path)
    root = await tree(harness)
    with sqlite3.connect(harness.runtime.config.sessions_db) as connection:
        connection.execute(
            "CREATE TRIGGER reject_delete BEFORE DELETE ON agent_sessions WHEN OLD.session_id = 'child' BEGIN SELECT RAISE(ABORT, 'controlled failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        await harness.operations.delete(root)
    assert {r.session_id for r in list_sessions(harness.runtime.config.sessions_db)} == {
        root,
        "child",
        "grandchild",
        "sibling",
    }
    for member in (root, "child", "grandchild", "sibling"):
        await harness.runtime.journal.record_user_message(member, "still writable")
        assert not harness.runtime.runtime.is_session_reserved(member)
    await harness.runtime.runtime.aclose()


async def test_postcommit_file_failure_reports_deleted_ids_and_can_be_cleaned_again(tmp_path, monkeypatch):
    harness = make_session_harness(tmp_path)
    root = await tree(harness)
    original_unlink = Path.unlink

    def fail_one(path, *args, **kwargs):
        if path.name == "child.jsonl":
            raise OSError("controlled file cleanup failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_one)
    with pytest.raises(SessionDeletionCleanupError) as failed:
        await harness.operations.delete(root)
    assert set(failed.value.deleted_session_ids) == {root, "child", "grandchild", "sibling"}
    assert list_sessions(harness.runtime.config.sessions_db) == []
    monkeypatch.setattr(Path, "unlink", original_unlink)
    await harness.operations.cleanup_deleted_sessions(failed.value.deleted_session_ids)
    for member in failed.value.deleted_session_ids:
        assert not await harness.runtime.journal.session_exists(member)
    await harness.runtime.runtime.aclose()
