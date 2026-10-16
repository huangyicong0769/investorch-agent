from pathlib import Path
from types import SimpleNamespace

import pytest

from investorch.web import setup_routes
from tests.support.web import open_test_web


@pytest.mark.parametrize("connection_type", ["openai_compatible", "chatgpt"])
async def test_saved_connection_probe_uses_selected_auth_and_a_completed_stream(
    tmp_path: Path, monkeypatch, connection_type: str
) -> None:
    calls = []

    class Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def __aiter__(self):
            yield SimpleNamespace(type="response.completed")

    class Client:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.responses = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def create(self, **kwargs):
            calls.append(kwargs)
            return Stream()

    async def token(_self, credential):
        assert credential == "personal"
        return "private-token"

    from investorch.llm.oauth import ChatGPTAuth

    monkeypatch.setattr(setup_routes, "AsyncOpenAI", Client)
    monkeypatch.setattr(ChatGPTAuth, "access_token", token)
    async with open_test_web(tmp_path) as web:
        connection_id = "test"
        if connection_type == "chatgpt":
            connection_id = "personal"
            result = await web.client.patch(
                "/api/config", json={"changes": {"models.personal": {"type": "chatgpt", "credentials": "personal"}}}
            )
            assert result.status_code == 200
        response = await web.client.post(
            "/api/setup/test-connection",
            json={"connection": connection_id, "name": "chosen-model", "reasoning_effort": "high"},
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert calls[1]["model"] == "chosen-model"
        assert calls[1]["store"] is False
        assert calls[1]["stream"] is True
        assert "max_output_tokens" not in calls[1]
        if connection_type == "chatgpt":
            assert calls[0]["base_url"] == "https://api.openai.com/v1"
            assert calls[0]["api_key"] == "private-token"
        assert "private-token" not in response.text
