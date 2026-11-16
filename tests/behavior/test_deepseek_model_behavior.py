import json
from dataclasses import replace

import httpx2
import pytest
from agents import Agent, ModelSettings, Runner, function_tool
from openai import AsyncOpenAI

from investorch.llm.clients import ModelConnectionClients
from tests.support.config import make_test_config


def message(text="完成", *, annotations=()):
    return {
        "type": "message",
        "id": "msg_answer",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": list(annotations)}],
    }


def response(output, **fields):
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 1,
        "status": "completed",
        "model": "deepseek-flash",
        "output": output,
        "parallel_tool_calls": True,
        "tools": [],
        "tool_choice": "auto",
        "usage": {
            "input_tokens": 11,
            "output_tokens": 7,
            "total_tokens": 18,
            "input_tokens_details": {"cached_tokens": 3},
            "output_tokens_details": {"reasoning_tokens": 2},
        },
        **fields,
    }


@pytest.mark.parametrize(
    ("provider", "name", "switches", "effort", "hosted"),
    [
        ("deepseek", "deepseek-flash", {}, "high", ["deepseek_web_search", "deepseek_read_url"]),
        ("deepseek", "deepseek-v4-flash", {"web_search": False}, "none", ["deepseek_read_url"]),
        ("deepseek", "deepseek-flash", {"web_search": False, "read_url": False}, "high", []),
        ("deepseek", "deepseek-v4-pro", {}, "high", []),
        ("openai_compatible", "deepseek-flash", {}, "high", []),
    ],
)
async def test_responses_declares_native_tools_alongside_local_tools(
    tmp_path, provider, name, switches, effort, hosted
):
    config = make_test_config(tmp_path, {"models": {"test": {"type": provider}}})
    requests = []

    @function_tool
    def lookup() -> str:
        """Read a test fixture."""
        return "fixture"

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json=response([message()]))

    clients = ModelConnectionClients(
        config,
        None,
        client_factory=lambda **kwargs: AsyncOpenAI(
            **kwargs, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
        ),
    )
    selected = replace(config.agent_model("main"), name=name, reasoning_effort=effort, **switches)
    try:
        agent = Agent(
            name="Main",
            model=clients.resolve(selected),
            tools=[lookup],
            model_settings=ModelSettings(reasoning={"effort": effort}),
        )
        result = await Runner.run(agent, "Test", run_config={"tracing_disabled": True})
    finally:
        await clients.close()

    assert result.final_output == "完成"
    (request,) = requests
    assert request["model"] == name
    assert request["reasoning"]["effort"] == effort
    assert request.get("tool_choice") != "none"
    assert [tool["type"] for tool in request["tools"]] == ["function", *hosted]
    assert request["tools"][0]["name"] == "lookup"
    assert "previous_response_id" not in request
