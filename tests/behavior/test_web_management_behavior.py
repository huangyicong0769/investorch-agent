from pathlib import Path

import httpx
import pytest

from investorch.config import REQUIRED_MODEL_AGENTS, load_config
from investorch.web.server import create_web_app
from tests.support.web import open_test_web


async def test_settings_batch_is_safe_and_human_can_save_permission_defaults(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        response = await web.client.get("/api/config")
        assert response.status_code == 200
        assert response.json()["secrets"]["TEST_API_KEY"] == {"configured": True}
        assert "fixture-key-never-used-for-network" not in response.text
        original = web.runtime.config.root_config_path.read_bytes()
        failed = await web.client.patch(
            "/api/config", json={"changes": {"runtime.max_turns": 40, "web.default_port": 70000}}
        )
        assert failed.status_code == 422
        assert web.runtime.config.root_config_path.read_bytes() == original
        saved = await web.client.patch("/api/config", json={"changes": {"permission.mode": "manual"}})
        assert saved.status_code == 200
        assert saved.json()["requires_restart"] is True


@pytest.mark.parametrize(
    "headers",
    [
        {"origin": "https://attacker.example"},
        {"origin": "http://127.0.0.1:9000"},
        {"host": "attacker.example"},
        {"sec-fetch-site": "cross-site"},
        {"referer": "https://attacker.example/form"},
        {"content-type": "text/plain"},
    ],
)
async def test_http_write_boundary_rejects_cross_site_and_simple_content(
    tmp_path: Path, headers: dict[str, str]
) -> None:
    async with open_test_web(tmp_path) as web:
        before = web.runtime.config.root_config_path.read_bytes()
        response = await web.client.patch("/api/config", json={"changes": {"runtime.max_turns": 40}}, headers=headers)
        assert response.status_code in (403, 415)
        assert web.runtime.config.root_config_path.read_bytes() == before


async def test_same_origin_browser_can_save_and_secret_errors_never_echo_input(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        result = await web.client.patch(
            "/api/config",
            json={"changes": {"secrets.EXTRA_KEY": "private-fixture-value"}},
            headers={"origin": "http://127.0.0.1", "sec-fetch-site": "same-origin"},
        )
        assert result.status_code == 200
        assert result.headers["cache-control"] == "no-store"
        failed = await web.client.patch(
            "/api/config", json={"changes": {"secrets.EXTRA_KEY": ["private-fixture-value"]}}
        )
        assert failed.status_code == 422
        assert failed.json()["error"]["details"]["errors"][0]["path"] == "secrets.EXTRA_KEY"
        assert "private-fixture-value" not in failed.text


async def test_empty_root_setup_starts_real_host_once_in_same_http_process(tmp_path: Path) -> None:
    app = create_web_app(load_config(root=tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client,
    ):
        assert (await client.get("/api/setup/state")).json()["status"] == "setup_required"
        assert (await client.get("/api/bootstrap")).status_code == 503
        assert (await client.get("/setup")).status_code == 200
        assert (await client.get("/api/config/schema")).json()["properties"].keys() == {"models", "secrets"}
        changes = {"secrets.TEST_KEY": "offline-test-no-network"}
        for role in REQUIRED_MODEL_AGENTS:
            changes.update(
                {
                    f"models.{role}.name": "test-model",
                    f"models.{role}.base_url": "https://models.example.test/v1",
                    f"models.{role}.api_key_secret": "TEST_KEY",
                }
            )
        saved = await client.patch("/api/setup/config", json={"changes": changes})
        assert saved.status_code == 200
        assert (await client.get("/api/setup/state")).json()["missing"] == []
        completed = await client.post("/api/setup/complete", json={})
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "ready"
        host = app.state.host
        assert (await client.post("/api/setup/complete", json={})).json()["status"] == "ready"
        assert app.state.host is host
        assert (await client.get("/api/bootstrap")).status_code == 200
        assert (await client.post("/api/sessions", json={})).status_code in (200, 201)
        assert (await client.patch("/api/setup/config", json={"changes": changes})).status_code == 409


async def test_corrupt_database_enters_recovery_without_initializing_other_state(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "state").mkdir(parents=True)
    database = root / "state" / "portfolio.db"
    database.write_bytes(b"not a sqlite database")
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    app = create_web_app(load_config(root=root))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client,
    ):
        state = (await client.get("/api/setup/state")).json()
        assert state["status"] == "recovery_required"
        assert (await client.get("/api/system")).status_code == 200
        assert (await client.post("/api/setup/complete", json={})).status_code == 409
    after = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert after == before


async def test_skill_http_controls_preserve_ownership_and_session_retry_identity(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        catalog = (await web.client.get("/api/skills")).json()["skills"]
        assert len(catalog) == 6
        disabled = await web.client.patch("/api/skills/skill-creator", json={"enabled": False})
        assert disabled.status_code == 200
        assert disabled.json()["restart_required"] is True
        detail = (await web.client.get("/api/skills/skill-creator")).json()
        assert detail["status"] == "disabled"
        assert detail["content"]
        assert (await web.client.get("/api/skills/unknown")).status_code == 404
        assert (
            await web.client.request("DELETE", "/api/skills/skill-creator", json={"confirm": True})
        ).status_code == 422
        rejected = await web.client.post(
            "/api/skill-sessions", json={"request_id": "edit", "action": "edit", "name": "skill-creator"}
        )
        assert rejected.status_code == 422
        body = {"request_id": "fork-once", "action": "fork", "name": "skill-creator"}
        started = (await web.client.post("/api/skill-sessions", json=body)).json()
        duplicate = (await web.client.post("/api/skill-sessions", json=body)).json()
        assert started["started"] is True
        assert duplicate["started"] is False
        assert started["session"]["session_id"] == duplicate["session"]["session_id"]
        session_id = started["session"]["session_id"]
        await web.runtime.agent_loop.wait_until_started(session_id)
        history = (await web.client.get(f"/api/sessions/{session_id}/history")).json()
        assert any(item["type"] == "user_message" for item in history["records"])
        web.runtime.agent_loop.complete(session_id)
        await web.runtime.wait_for_run_ended(session_id)
