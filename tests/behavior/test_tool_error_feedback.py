"""Tool failures retain corrective feedback without leaking validation inputs."""

from __future__ import annotations

import json
from pathlib import Path

from agents import RunContextWrapper

from investorch.context import AgentContext
from investorch.tools.base import calculate
from investorch.tools.errors import tool
from tests.behavior.test_workspace_capabilities import make_tool_context


async def test_argument_error_identifies_invalid_json_without_echoing_input(tmp_path: Path) -> None:
    context = make_tool_context(tmp_path)
    result = await calculate.on_invoke_tool(context, json.dumps({"expression": {"secret": "private-input-canary"}}))

    assert isinstance(result, str)
    assert "Invalid JSON input" in result
    assert "private-input-canary" not in result


async def test_corrective_feedback_redacts_configured_credentials(tmp_path: Path) -> None:
    context = make_tool_context(tmp_path, {"secrets": {"CANARY": "private_credential_canary"}})
    result = await calculate.on_invoke_tool(context, json.dumps({"expression": "private_credential_canary(1)"}))

    assert isinstance(result, str)
    assert "Unsupported function" in result and "<redacted>" in result
    assert "private_credential_canary" not in result


async def test_unexpected_tool_exception_does_not_expose_its_details(tmp_path: Path) -> None:
    @tool
    def failing_tool(context: RunContextWrapper[AgentContext]) -> str:
        raise RuntimeError("provider payload: private-input-canary")

    result = await failing_tool.on_invoke_tool(make_tool_context(tmp_path), "{}")

    assert isinstance(result, str)
    assert "System Status" in result
    assert "provider payload" not in result and "private-input-canary" not in result
