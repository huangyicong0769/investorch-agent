import asyncio
import sqlite3
from pathlib import Path

import pytest

from investorch.application.sessions import (
    SessionClearReplacementError,
    SessionDeletionCleanupError,
    SessionHasChildrenError,
    SessionHasQueuedInputsError,
)
from investorch.runtime import SessionBusyError
from investorch.storage import SessionDeletionScopeChangedError, create_spawn_session, get_session, list_sessions
from tests.support.runtime import run_options
from tests.support.sessions import make_session_harness
from tests.support.web import open_test_web


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


async def test_clear_has_the_same_cascade_contract_and_creates_a_plain_replacement(tmp_path):
    harness = make_session_harness(tmp_path)
    root = await tree(harness)
    replacement = await harness.operations.clear(root, expected_session_ids=(root, "child", "grandchild", "sibling"))
    assert [r.session_id for r in list_sessions(harness.runtime.config.sessions_db)] == [replacement]
    record = get_session(harness.runtime.config.sessions_db, replacement)
    assert record.spawned_by_session_id is None and record.branch_from_session_id is None
    for member in (root, "child", "grandchild", "sibling"):
        assert not await harness.runtime.journal.session_exists(member)
    await harness.runtime.runtime.aclose()


async def test_clear_without_a_reviewed_scope_preserves_all_spawn_descendants(tmp_path):
    harness = make_session_harness(tmp_path)
    root = await tree(harness)
    with pytest.raises(SessionDeletionScopeChangedError):
        await harness.operations.clear(root)
    assert {record.session_id for record in list_sessions(harness.runtime.config.sessions_db)} == {
        root,
        "child",
        "grandchild",
        "sibling",
    }
    for member in ("child", "grandchild", "sibling"):
        assert await harness.runtime.journal.session_exists(member)
    await harness.runtime.runtime.aclose()


async def test_clear_reports_committed_ids_when_replacement_creation_fails(tmp_path, monkeypatch):
    harness = make_session_harness(tmp_path)
    root = await tree(harness)
    unrelated = await harness.operations.create()

    async def failed_create():
        raise sqlite3.OperationalError("controlled replacement failure")

    monkeypatch.setattr(harness.operations, "create", failed_create)
    with pytest.raises(SessionClearReplacementError) as failed:
        await harness.operations.clear(root, expected_session_ids=(root, "child", "grandchild", "sibling"))
    assert set(failed.value.deleted_session_ids) == {root, "child", "grandchild", "sibling"}
    assert failed.value.replacement_session_id is None
    assert [record.session_id for record in list_sessions(harness.runtime.config.sessions_db)] == [unrelated]
    for member in (root, "child", "grandchild", "sibling"):
        assert not await harness.runtime.journal.session_exists(member)
    await harness.runtime.runtime.aclose()


async def test_cancelled_clear_finishes_replacement_storage_before_releasing_lifecycle_mutation(tmp_path, monkeypatch):
    harness = make_session_harness(tmp_path)
    root = await tree(harness)
    unrelated = await harness.operations.create()
    original_create = harness.operations.create
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_create():
        started.set()
        await release.wait()
        return await original_create()

    monkeypatch.setattr(harness.operations, "create", delayed_create)
    clearing = asyncio.create_task(
        harness.operations.clear(root, expected_session_ids=(root, "child", "grandchild", "sibling"))
    )
    await started.wait()
    clearing.cancel()
    mutation = asyncio.create_task(harness.operations.set_title(unrelated, "After Clear"))
    await asyncio.sleep(0)
    assert not mutation.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await clearing
    await mutation
    records = list_sessions(harness.runtime.config.sessions_db)
    assert len(records) == 2 and unrelated in {record.session_id for record in records}
    assert not {record.session_id for record in records}.intersection((root, "child", "grandchild", "sibling"))
    replacement = next(record for record in records if record.session_id != unrelated)
    assert replacement.spawned_by_session_id is None
    await harness.runtime.runtime.aclose()


async def test_changed_descendants_require_a_new_confirmation(tmp_path):
    harness = make_session_harness(tmp_path)
    root = await tree(harness)
    preview = await harness.operations.deletion_preview(root)
    create_spawn_session(
        harness.runtime.config.sessions_db, "later", parent_session_id=root, parent_run_id="run", tool_call_id="later"
    )
    with pytest.raises(SessionDeletionScopeChangedError):
        await harness.operations.delete(
            root, expected_session_ids=tuple(member["session_id"] for member in preview["sessions"])
        )
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


@pytest.mark.parametrize("action", ["delete", "clear"])
async def test_web_requires_the_reviewed_scope_and_returns_all_deleted_ids(tmp_path, action):
    async with open_test_web(tmp_path) as web:
        root = await web.host.sessions.create()

        async def confirm(body):
            if action == "delete":
                return await web.client.request("DELETE", f"/api/sessions/{root}", json=body)
            return await web.client.post(f"/api/sessions/{root}/clear", json=body)

        create_spawn_session(
            web.host.config.sessions_db, "child", parent_session_id=root, parent_run_id="run", tool_call_id="child"
        )
        preview = (await web.client.get(f"/api/sessions/{root}/deletion-preview")).json()
        assert {s["session_id"] for s in preview["sessions"]} == {root, "child"}
        assert preview["blocked"] == []
        for body in ({"confirm": True}, {"confirm": True, "session_ids": []}, {"confirm": True, "session_ids": [root]}):
            unreviewed = await confirm(body)
            assert unreviewed.status_code == 409 and unreviewed.json()["error"]["code"] == "session_deletion_changed"
        create_spawn_session(
            web.host.config.sessions_db, "later", parent_session_id=root, parent_run_id="run", tool_call_id="later"
        )
        stale = await confirm({"confirm": True, "session_ids": [s["session_id"] for s in preview["sessions"]]})
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "session_deletion_changed"
        refreshed = (await web.client.get(f"/api/sessions/{root}/deletion-preview")).json()
        cleared = await confirm(
            {"confirm": True, "session_ids": [s["session_id"] for s in reversed(refreshed["sessions"])]}
        )
        assert cleared.status_code == 200
        assert set(cleared.json()["deleted_session_ids"]) == {root, "child", "later"}
        if action == "clear":
            replacement = get_session(web.host.config.sessions_db, cleared.json()["replacement_session_id"])
            assert list_sessions(web.host.config.sessions_db) == [replacement]
            assert replacement.spawned_by_session_id is None
        else:
            assert list_sessions(web.host.config.sessions_db) == []


async def test_web_preview_names_busy_descendants_and_does_not_stop_them(tmp_path):
    async with open_test_web(tmp_path) as web:
        root = await web.host.sessions.create()
        create_spawn_session(
            web.host.config.sessions_db, "child", parent_session_id=root, parent_run_id="run", tool_call_id="child"
        )
        web.host.runtime.start_run("child", "run", run_options())
        await web.runtime.agent_loop.wait_until_started("child")
        preview = (await web.client.get(f"/api/sessions/{root}/deletion-preview")).json()
        assert {"session_id": "child", "code": "session_busy"} in preview["blocked"]
        refused = await web.client.request(
            "DELETE",
            f"/api/sessions/{root}",
            json={"confirm": True, "session_ids": [s["session_id"] for s in preview["sessions"]]},
        )
        assert refused.status_code == 409
        assert web.host.runtime.session_snapshot("child").run_phase == "running"


async def test_web_clear_creation_failure_reports_committed_scope_and_resets_initial_selection(tmp_path, monkeypatch):
    async with open_test_web(tmp_path, raise_app_exceptions=False) as web:
        root = await web.host.sessions.create()
        for child, parent in (("child", root), ("grandchild", "child")):
            create_spawn_session(
                web.host.config.sessions_db, child, parent_session_id=parent, parent_run_id="run", tool_call_id=child
            )
            await web.host.journal.record_user_message(child, "History")
        web.host.initial_session_id = "grandchild"

        async def failed_create():
            raise sqlite3.OperationalError("controlled replacement failure")

        monkeypatch.setattr(web.host.sessions, "create", failed_create)
        response = await web.client.post(
            f"/api/sessions/{root}/clear", json={"confirm": True, "session_ids": [root, "child", "grandchild"]}
        )
        assert response.status_code == 503
        error = response.json()["error"]
        assert error["code"] == "session_clear_replacement_failed"
        assert set(error["details"]["deleted_session_ids"]) == {root, "child", "grandchild"}
        assert error["details"]["replacement_session_id"] is None
        assert error["details"]["stage"] == "replacement_creation"
        assert list_sessions(web.host.config.sessions_db) == []
        assert web.host.initial_session_id is None
        bootstrap = (await web.client.get("/api/bootstrap")).json()
        assert bootstrap["initial_session_id"] is None and bootstrap["sessions"] == []
        repeated = await web.client.post(
            f"/api/sessions/{root}/clear", json={"confirm": True, "session_ids": [root, "child", "grandchild"]}
        )
        assert repeated.status_code == 404 and list_sessions(web.host.config.sessions_db) == []


@pytest.mark.parametrize("stage", ["cleanup", "transaction"])
async def test_web_clear_distinguishes_committed_cleanup_failure_from_uncommitted_delete(tmp_path, monkeypatch, stage):
    async with open_test_web(tmp_path, raise_app_exceptions=False) as web:
        root = await web.host.sessions.create()
        create_spawn_session(
            web.host.config.sessions_db, "child", parent_session_id=root, parent_run_id="run", tool_call_id="child"
        )
        await web.host.journal.record_user_message("child", "History")
        web.host.initial_session_id = root
        original_unlink = Path.unlink
        if stage == "cleanup":

            def fail_cleanup(path, *args, **kwargs):
                if path.name == "child.jsonl":
                    raise OSError("controlled cleanup failure")
                return original_unlink(path, *args, **kwargs)

            monkeypatch.setattr(Path, "unlink", fail_cleanup)
        else:
            with sqlite3.connect(web.host.config.sessions_db) as connection:
                connection.execute(
                    "CREATE TRIGGER reject_clear BEFORE DELETE ON agent_sessions BEGIN SELECT RAISE(ABORT, 'controlled transaction failure'); END"
                )
        response = await web.client.post(
            f"/api/sessions/{root}/clear", json={"confirm": True, "session_ids": [root, "child"]}
        )
        error = response.json()["error"]
        if stage == "cleanup":
            assert response.status_code == 503 and error["code"] == "session_cleanup_failed"
            assert set(error["details"]["deleted_session_ids"]) == {root, "child"}
            assert list_sessions(web.host.config.sessions_db) == [] and web.host.initial_session_id is None
            monkeypatch.setattr(Path, "unlink", original_unlink)
            cleaned = await web.client.post(
                "/api/sessions/deletion-cleanup", json={"confirm": True, "session_ids": [root, "child"]}
            )
            assert cleaned.status_code == 200 and not await web.host.journal.session_exists("child")
        else:
            assert response.status_code == 500 and not error["details"].get("deleted_session_ids")
            assert {record.session_id for record in list_sessions(web.host.config.sessions_db)} == {root, "child"}
            assert web.host.initial_session_id == root and await web.host.journal.session_exists("child")
