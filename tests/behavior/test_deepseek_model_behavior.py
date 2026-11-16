import asyncio
import json
from dataclasses import replace

import httpx2
import pytest
from agents import Agent, ModelSettings, Runner, function_tool
from openai import AsyncOpenAI

from investorch.application import open_application_host
from investorch.journal import read_session_journal
from investorch.llm.clients import ModelConnectionClients
from investorch.output import consume_run_events, serialize_output_event
from investorch.storage import get_session_title
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


def install_deepseek_http(monkeypatch, handle):
    def clients(app_config, auth):
        return ModelConnectionClients(
            app_config,
            auth,
            client_factory=lambda **kwargs: AsyncOpenAI(
                **kwargs, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
            ),
        )

    monkeypatch.setattr("investorch.application.host.ModelConnectionClients", clients)


async def approve(*_):
    return True


async def test_first_deepseek_run_uses_first_native_title_without_auxiliary_request(tmp_path, monkeypatch):
    config = make_test_config(
        tmp_path, {"models": {"test": {"type": "deepseek"}}, "agents": {"main": {"name": "deepseek-flash"}}}
    )
    requests = []
    call = {
        "id": "fc_calc",
        "call_id": "call_calc",
        "type": "function_call",
        "name": "calculate",
        "arguments": '{"expression":"2 + 2"}',
        "status": "completed",
    }

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            payload = response([call], title="Intermediate title")
        else:
            payload = response([message()], title="Final title")
        return sse(payload) if body.get("stream") else httpx2.Response(200, json=payload)

    install_deepseek_http(monkeypatch, handle)
    async with open_application_host(config, manual_approval_handler=approve, enable_activity=False) as host:
        session_id = await host.sessions.create()
        result = await host.runtime.start_run(session_id, "Calculate 2 + 2", run_options()).task
        assert len(requests) == 2
        assert all(body.get("title") == {"enabled": True} for body in requests)
        assert any(item.get("type") == "function_call_output" for item in requests[1]["input"])
        assert result.auxiliary_usage.requests == 0
        assert get_session_title(config.sessions_db, session_id) == "Intermediate title"
        await host.runtime.start_run(session_id, "Thanks", run_options()).task
        assert "title" not in requests[-1]


async def test_native_title_does_not_overwrite_a_manual_rename(tmp_path, monkeypatch):
    config = make_test_config(
        tmp_path, {"models": {"test": {"type": "deepseek"}}, "agents": {"main": {"name": "deepseek-flash"}}}
    )
    started, release = asyncio.Event(), asyncio.Event()

    async def handle(_request):
        started.set()
        await release.wait()
        return sse(response([message()], title="Automatic title"))

    install_deepseek_http(monkeypatch, handle)
    async with open_application_host(config, manual_approval_handler=approve, enable_activity=False) as host:
        session_id = await host.sessions.create()
        run = host.runtime.start_run(session_id, "Start", run_options())
        try:
            await asyncio.wait_for(started.wait(), 2)
            await host.sessions.set_title(session_id, "Manual title")
        finally:
            release.set()
        await run.task
        assert get_session_title(config.sessions_db, session_id) == "Manual title"


async def test_streamed_citations_are_positioned_by_code_point_and_kept_once(tmp_path):
    config = make_test_config(tmp_path, {"models": {"test": {"type": "deepseek"}}})
    text = "研究🙂值得关注。"
    citation = {
        "type": "url_citation",
        "start_index": 0,
        "end_index": 3,
        "url": "https://example.org/results",
        "title": "Results",
    }
    answer = message(text, annotations=[citation])
    position = {"output_index": 0, "content_index": 0, "item_id": answer["id"]}

    def handle(_):
        return sse(
            response([answer]),
            {"type": "response.output_text.delta", "delta": text, "logprobs": [], **position},
            {
                "type": "response.output_text.annotation.added",
                "annotation": citation,
                "annotation_index": 0,
                **position,
            },
            {"type": "response.output_item.done", "output_index": 0, "item": answer},
        )

    clients = ModelConnectionClients(
        config,
        None,
        client_factory=lambda **kwargs: AsyncOpenAI(
            **kwargs, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
        ),
    )
    output = []

    async def capture(event):
        output.append(serialize_output_event(event))

    try:
        result = Runner.run_streamed(
            Agent(name="Main", model=clients.resolve(config.agent_model("main"))),
            "Search",
            run_config={"tracing_disabled": True},
        )
        await consume_run_events(result, capture, "Main", deepseek=True)
    finally:
        await clients.close()

    (final,) = [event for event in output if event["type"] == "assistant_message"]
    assert final["text"] == text
    assert final["display_text"] == "研究🙂 [1](<https://example.org/results>)值得关注。"
    assert final["citations"] == [{key: value for key, value in citation.items() if key != "type"}]


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
    failed_search = {
        "type": "web_search_call",
        "id": "ws_failed_search",
        "kind": "deep_search",
        "status": "failed",
        "action": {"type": "search", "queries": ["Example"]},
        "error": {"code": "unavailable"},
    }
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        last = body["input"][-1]
        if body["model"] == "compact-model":
            payload = response([message("Earlier conversation summary")])
        elif last.get("content") == [{"type": "input_text", "text": "Please read https://example.org/"}]:
            payload = response([call, failed_search, message()])
            payload["usage"]["server_tool_use"] = {"tool_search": 3, "tool_fetch": 1, "tool_image_search": 2}
            return sse(
                payload,
                {"type": "response.output_item.added", "output_index": 0, "item": {**call, "status": "in_progress"}},
                {
                    "type": "response.output_item.added",
                    "output_index": 1,
                    "item": {**failed_search, "status": "in_progress"},
                },
                {"type": "response.output_item.done", "output_index": 1, "item": failed_search},
                {"type": "response.output_item.done", "output_index": 0, "item": call},
            )
        else:
            payload = response([message()])
        return sse(payload) if body.get("stream") else httpx2.Response(200, json=payload)

    install_deepseek_http(monkeypatch, handle)
    async with open_application_host(config, manual_approval_handler=approve, enable_activity=False) as host:
        session_id = await host.sessions.create()
        await host.sessions.set_title(session_id, "Web test")
        await host.runtime.start_run(session_id, "Warm up", run_options()).task
        used = await host.runtime.start_run(session_id, "Please read https://example.org/", run_options()).task
        assert (used.main_usage.tool_search, used.main_usage.tool_fetch, used.main_usage.tool_image_search) == (3, 1, 2)
        assert used.main_usage.total_tokens == 18
        assert host.presentation_state.get(session_id).main_usage.tool_search == 3
        assert any(
            entry.get("run_id") and (entry.get("main_usage") or {}).get("tool_search") == 3
            for entry in read_session_journal(config.session_journal_dir, session_id)
        )
        await host.runtime.start_run(session_id, "What did it say?", run_options()).task
        assert call in requests[-1]["input"]
        assert "previous_response_id" not in requests[-1]
        assert (await host.sessions.compact(session_id)).changed
        fork = await host.sessions.fork(session_id)

    async with open_application_host(config, manual_approval_handler=approve, enable_activity=False) as host:
        await host.runtime.start_run(fork, "Read it again", run_options()).task
        assert call in requests[-1]["input"]
        assert failed_search in requests[-1]["input"]
        web_steps = [
            record for record in read_session_journal(config.session_journal_dir, fork) if record.get("hosted_status")
        ]
        assert [(record["name"], record["hosted_status"]) for record in web_steps] == [
            ("DeepSeek web search", "failed"),
            ("DeepSeek URL reading", "completed"),
        ]
        assert json.loads(web_steps[1]["arguments"]) == {"kind": "read_link", "action": call["action"]}


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
