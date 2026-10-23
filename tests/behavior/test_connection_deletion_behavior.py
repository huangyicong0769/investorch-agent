from pathlib import Path

import pytest

from investorch.runtime import RunDefaults
from tests.support.web import open_test_web


@pytest.mark.parametrize("selection", ["draft", "persisted", "active"])
@pytest.mark.parametrize("operation", ["reset", "replace"])
async def test_connection_deletion_preserves_session_references(tmp_path: Path, selection: str, operation: str) -> None:
    async with open_test_web(
        tmp_path,
        {
            "models": {
                "other": {
                    "type": "openai_compatible",
                    "base_url": "https://other.example.test/v1",
                    "api_key_secret": "TEST_API_KEY",
                }
            }
        },
    ) as web:
        config = web.host.config
        session_id = (await web.client.post("/api/sessions", json={})).json()["session"]["session_id"]
        models = web.host.session_models
        models.update(
            session_id, model="other", name="other-model", reasoning_effort="low", context_window_tokens=32000
        )
        if selection == "persisted":
            await models.commit(session_id, models.snapshot(session_id))
        if selection == "active":
            web.host.runtime.start_application_run(session_id, "held application Run", RunDefaults("manual", "queue"))
            models.set_draft(session_id, config.agent_model("main"))
        before = config.root_config_path.read_bytes()
        if operation == "reset":
            response = await web.client.post("/api/config/reset", json={"keys": ["models.other"]})
        else:
            response = await web.client.patch(
                "/api/config", json={"changes": {"models": {"test": config.public(saved=True)["models"]["test"]}}}
            )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "connection_in_use"
        assert config.root_config_path.read_bytes() == before
        assert "other" in config.public(saved=True)["models"]


async def test_unused_connection_can_be_deleted(tmp_path: Path) -> None:
    async with open_test_web(
        tmp_path,
        {
            "models": {
                "other": {
                    "type": "openai_compatible",
                    "base_url": "https://other.example.test/v1",
                    "api_key_secret": "TEST_API_KEY",
                }
            }
        },
    ) as web:
        response = await web.client.post("/api/config/reset", json={"keys": ["models.other"]})
        assert response.status_code == 200
        assert "other" not in web.host.config.public(saved=True)["models"]
        session_id = (await web.client.post("/api/sessions", json={})).json()["session"]["session_id"]
        selected = await web.client.patch(
            f"/api/sessions/{session_id}/model",
            json={"model": "other", "name": "other-model", "reasoning_effort": "low", "context_window_tokens": 32000},
        )
        assert selected.status_code == 422
        assert (await web.client.get(f"/api/sessions/{session_id}/model")).json()["draft"] is None
