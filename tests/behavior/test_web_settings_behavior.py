from pathlib import Path

import pytest

from investorch.config import load_config
from tests.support.web import open_test_web


async def test_theme_color_applies_without_restart_persists_and_resets(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        original = (await web.client.get("/api/bootstrap")).json()["web_config"]["theme_color"]
        for color in ("blue", "violet", "rose", "amber", "neutral", "green"):
            response = await web.client.patch("/api/config", json={"changes": {"web.theme_color": color}})
            assert response.status_code == 200
            assert response.json()["requires_restart"] is False
            assert (await web.client.get("/api/bootstrap")).json()["web_config"]["theme_color"] == color
            assert load_config(root=web.runtime.config.root)["web.theme_color"] == color

        before = web.runtime.config.root_config_path.read_bytes()
        invalid = await web.client.patch("/api/config", json={"changes": {"web.theme_color": "unknown"}})
        assert invalid.status_code == 422
        assert web.runtime.config.root_config_path.read_bytes() == before

        reset = await web.client.post("/api/config/reset", json={"keys": ["web.theme_color"]})
        assert reset.status_code == 200
        assert reset.json()["requires_restart"] is False
        assert (await web.client.get("/api/bootstrap")).json()["web_config"]["theme_color"] == original
        assert load_config(root=web.runtime.config.root)["web.theme_color"] == original


async def test_config_updates_return_changed_keys_and_timezone_restart_status(tmp_path: Path) -> None:
    async with open_test_web(tmp_path) as web:
        config = web.runtime.config
        original_timezone = config["runtime.default_timezone"]
        changes = {"runtime.default_timezone": "UTC", "runtime.max_turns": 42}
        response = await web.client.patch("/api/config", json={"changes": changes})
        assert response.status_code == 200
        result = response.json()
        assert result["updated"] == list(changes)
        assert result["requires_restart"] is True
        assert result["fields"]["runtime.default_timezone"] == {"applied": False, "requires_restart": True}
        assert config["runtime.default_timezone"] == original_timezone
        assert config["runtime.max_turns"] == 42
        saved = (await web.client.get("/api/config")).json()
        assert saved["values"]["runtime"]["default_timezone"] == "UTC"
        assert load_config(root=config.root)["runtime.default_timezone"] == "UTC"

        reset = await web.client.post("/api/config/reset", json={"keys": list(changes)})
        assert reset.status_code == 200
        assert reset.json()["updated"] == list(changes)
        assert reset.json()["requires_restart"] is True
        assert load_config(root=config.root)["runtime.default_timezone"] == original_timezone


@pytest.mark.parametrize("key", ["paths.root", "paths.workspace", "paths.state", "paths"])
@pytest.mark.parametrize("reset", [False, True])
async def test_settings_cannot_relocate_data_or_reset_paths(tmp_path: Path, key: str, reset: bool) -> None:
    async with open_test_web(tmp_path) as web:
        config = web.runtime.config
        before = config.root_config_path.read_bytes()
        original = config.public()
        if reset:
            response = await web.client.post("/api/config/reset", json={"keys": ["runtime.max_turns", key]})
        else:
            value = {"workspace": "new-workspace", "state": "new-state"} if key == "paths" else "new-directory"
            response = await web.client.patch("/api/config", json={"changes": {"runtime.max_turns": 42, key: value}})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "offline_migration_required"
        assert config.root_config_path.read_bytes() == before
        assert config.public() == original
        assert not (config.root / "new-directory").exists()
