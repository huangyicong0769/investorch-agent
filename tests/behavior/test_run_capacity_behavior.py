from pathlib import Path

from tests.support.runtime import run_options
from tests.support.web import open_test_web


async def test_web_rejects_new_runs_with_a_corrective_capacity_error(tmp_path: Path) -> None:
    async with open_test_web(tmp_path, {"runtime": {"max_concurrent_runs": 1}}, raise_app_exceptions=False) as web:
        first = await web.host.sessions.create()
        other = await web.host.sessions.create()
        web.host.runtime.start_run(first, "occupy", run_options())
        await web.runtime.agent_loop.wait_until_started(first)
        response = await web.client.post(f"/api/sessions/{other}/messages", json={"text": "next"})
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "capacity_exceeded"
        assert response.json()["error"]["details"] == {"active": 1, "limit": 1}
        assert web.host.runtime.session_snapshot(other).run_id is None
