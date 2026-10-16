from pathlib import Path

from investorch.storage.sessions import get_session_model
from tests.support.web import open_test_web


async def test_session_model_edits_are_isolated_drafts_and_invalid_updates_preserve_them(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        first = (await web.client.post("/api/sessions", json={})).json()["session"]["session_id"]
        second = (await web.client.post("/api/sessions", json={})).json()["session"]["session_id"]
        path = f"/api/sessions/{first}/model"
        original = (await web.client.get(path)).json()
        assert original["persisted"] is None
        assert original["draft"] is None
        assert original["active"] is None
        changed = await web.client.patch(path, json={"reasoning_effort": "low"})
        assert changed.status_code == 200
        assert changed.json()["draft"]["reasoning_effort"] == "low"
        assert get_session_model(web.runtime.config.sessions_db, first) is None
        assert (await web.client.get(f"/api/sessions/{second}/model")).json()["effective"] == original["effective"]
        for invalid in (
            {"name": "different"},
            {"context_window_tokens": None},
            {"context_window_tokens": 0},
            {"model": "missing"},
        ):
            assert (await web.client.patch(path, json=invalid)).status_code == 422
            assert (await web.client.get(path)).json()["effective"] == changed.json()["effective"]
        assert (await web.client.patch("/api/defaults", json={"reasoning_effort": "high"})).status_code == 422
        assert (await web.client.get("/api/sessions/nonexistent/model")).status_code == 404
