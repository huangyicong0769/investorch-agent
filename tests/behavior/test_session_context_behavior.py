from datetime import UTC, datetime

from investorch.storage import create_spawn_session
from tests.support.runtime import run_options
from tests.support.web import open_test_web


async def test_context_lists_direct_children_sources_and_only_proven_run_outcomes(tmp_path):
    async with open_test_web(tmp_path) as web:
        root = await web.host.sessions.create()
        for child, parent in (("child", root), ("grandchild", "child"), ("idle", root)):
            create_spawn_session(
                web.host.config.sessions_db, child, parent_session_id=parent, parent_run_id="run", tool_call_id=child
            )
        now = datetime.now(UTC)
        await web.host.journal.record_run_ended("child", "child-run", "completed", now, now)
        response = await web.client.get(f"/api/sessions/{root}/context")
        assert response.status_code == 200
        children = {item["session"]["session_id"]: item["status"] for item in response.json()["children"]}
        assert children == {"child": "completed", "idle": "idle"}
        child = (await web.client.get("/api/sessions/child/context")).json()
        assert child["parent"]["session_id"] == root
        assert child["fork_source"] is None
        await web.host.journal.record_user_message("child", "new interrupted input")
        assert (await web.client.get("/api/sessions/child/context")).json()["status"] == "idle"


async def test_context_prefers_live_phase_and_degrades_invalid_history_without_breaking_chat(tmp_path):
    async with open_test_web(tmp_path) as web:
        root = await web.host.sessions.create()
        create_spawn_session(
            web.host.config.sessions_db, "child", parent_session_id=root, parent_run_id="run", tool_call_id="call"
        )
        web.host.runtime.start_run("child", "running", run_options())
        await web.runtime.agent_loop.wait_until_started("child")
        context = (await web.client.get(f"/api/sessions/{root}/context")).json()
        assert context["children"][0]["status"] == "running"
        (web.host.config.session_journal_dir / f"{root}.jsonl").write_text("broken\n")
        degraded = (await web.client.get(f"/api/sessions/{root}/context")).json()
        assert degraded["errors"] == [{"session_id": root, "code": "history_unavailable"}]
        assert str(web.host.config.root) not in str(degraded)
        assert (await web.client.get(f"/api/sessions/{root}/state")).status_code == 200
