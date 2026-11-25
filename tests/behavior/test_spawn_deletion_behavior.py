import sqlite3
from pathlib import Path

import pytest

from investorch.storage import create_spawn_session, get_session, list_sessions
from tests.support.runtime import run_options
from tests.support.web import open_test_web


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
