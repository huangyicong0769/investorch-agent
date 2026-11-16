import json
from dataclasses import replace

import httpx2
import pytest
from agents import Agent, ModelSettings, Runner, function_tool
from openai import AsyncOpenAI

from investorch.application import open_application_host
from investorch.llm.clients import ModelConnectionClients
from tests.support.config import make_test_config
from tests.support.runtime import run_options


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


def sse(payload, *events):
    records = [
        {"type": "response.created", "response": response([], status="in_progress")},
        *events,
        {"type": f"response.{payload['status']}", "response": payload},
    ]
    return httpx2.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content="".join(
            f"data: {json.dumps({**event, 'sequence_number': index})}\n\n" for index, event in enumerate(records)
        ),
    )


async def test_host_replays_signed_web_item_after_compact_fork_and_reopening(tmp_path, monkeypatch):
    config = make_test_config(
        tmp_path,
        {
            "models": {"test": {"type": "deepseek"}},
            "agents": {"main": {"name": "deepseek-flash"}, "compact": {"name": "compact-model"}},
            "compaction": {"auto_enabled": False},
        },
    )
    call = {
        "id": "ws_read_link",
        "type": "web_search_call",
        "kind": "read_link",
        "status": "completed",
        "action": {"type": "open_page", "url": "https://example.org/"},
        "page": {"url": "https://example.org/", "title": "Example Domain", "published_at": None},
        "signature": "fixture-provider-signature",
    }
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        last = body["input"][-1]
        if body["model"] == "compact-model":
            payload = response([message("Earlier conversation summary")])
        elif last.get("content") == [{"type": "input_text", "text": "Please read https://example.org/"}]:
            payload = response([call, message()])
        else:
            payload = response([message()])
        return sse(payload) if body.get("stream") else httpx2.Response(200, json=payload)

    def clients(app_config, auth):
        return ModelConnectionClients(
            app_config,
            auth,
            client_factory=lambda **kwargs: AsyncOpenAI(
                **kwargs, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
            ),
        )

    async def approve(*_):
        return True

    monkeypatch.setattr("investorch.application.host.ModelConnectionClients", clients)
    async with open_application_host(config, manual_approval_handler=approve, enable_activity=False) as host:
        session_id = await host.sessions.create()
        await host.sessions.set_title(session_id, "Web test")
        await host.runtime.start_run(session_id, "Warm up", run_options()).task
        await host.runtime.start_run(session_id, "Please read https://example.org/", run_options()).task
        await host.runtime.start_run(session_id, "What did it say?", run_options()).task
        assert call in requests[-1]["input"]
        assert "previous_response_id" not in requests[-1]
        assert (await host.sessions.compact(session_id)).changed
        fork = await host.sessions.fork(session_id)

    async with open_application_host(config, manual_approval_handler=approve, enable_activity=False) as host:
        await host.runtime.start_run(fork, "Read it again", run_options()).task
        assert call in requests[-1]["input"]


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
