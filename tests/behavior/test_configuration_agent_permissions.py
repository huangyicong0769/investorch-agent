"""Model and authorization settings remain under direct human control."""

import json
from pathlib import Path

import pytest

from investorch.tools.config import update_config
from tests.behavior.test_workspace_capabilities import make_tool_context


@pytest.mark.parametrize(
    "key",
    ["models.test.base_url", "agents.main.name", "secrets.TEST_API_KEY", "permission.mode", "auth.chatgpt_host_id"],
)
async def test_agent_cannot_modify_model_or_authorization_settings(tmp_path: Path, key: str) -> None:
    context = make_tool_context(tmp_path)
    config = context.context.config
    before = config.root_config_path.read_bytes()
    public_before = config.public()

    result = await update_config.on_invoke_tool(context, json.dumps({"key": key, "value": "changed"}))

    assert isinstance(result, str)
    assert "cannot be modified through the Agent config tool" in result
    assert config.root_config_path.read_bytes() == before
    assert config.public() == public_before
