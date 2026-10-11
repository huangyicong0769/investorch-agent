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
