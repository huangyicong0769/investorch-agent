import asyncio
import json
from dataclasses import replace

import httpx2
import pytest
from agents import Agent, ModelSettings, Runner, function_tool
from openai import AsyncOpenAI

from investorch.application import ApplicationCallbacks, open_application_host
from investorch.journal import read_session_journal
from investorch.llm.clients import ModelConnectionClients
from investorch.llm.deepseek import DeepSeekResponseError
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


@pytest.mark.parametrize("completed_with_refusal", [False, True])
async def test_safety_refusal_discards_the_streamed_answer_and_accounts_usage(
    tmp_path, monkeypatch, completed_with_refusal
):
    config = make_test_config(
        tmp_path,
        {
            "models": {"test": {"type": "deepseek"}},
            "agents": {"main": {"name": "deepseek-flash", "safety_check": "high"}},
        },
    )
    pending = message("unreviewed draft")
    refusal = {**pending, "content": [{"type": "refusal", "refusal": "private-review-detail"}]}
    payload = (
        response([refusal], title="A blocked title")
        if completed_with_refusal
        else response([], status="incomplete", incomplete_details={"reason": "content_filter"})
    )
    payload["usage"]["output_tokens_details"]["safety_tokens"] = 5
    payload["usage"]["server_tool_use"] = {"tool_search": 1}

    def handle(request):
        assert json.loads(request.content)["safety_check"] == {"level": "high"}
        return sse(
            payload,
            {
                "type": "response.output_text.delta",
                "item_id": pending["id"],
                "output_index": 0,
                "content_index": 0,
                "delta": "unreviewed draft",
                "logprobs": [],
            },
            {"type": "response.output_item.done", "output_index": 0, "item": pending},
        )

    install_deepseek_http(monkeypatch, handle)
    async with open_application_host(config, manual_approval_handler=approve, enable_activity=False) as host:
        session_id = await host.sessions.create()
        with pytest.raises(DeepSeekResponseError, match="withheld the answer"):
            await host.runtime.start_run(session_id, "Review this request", run_options()).task
        assert not get_session_title(config.sessions_db, session_id)
        records = read_session_journal(config.session_journal_dir, session_id)
        (ended,) = [record for record in records if record["type"] == "run_ended"]
        assert ended["status"] == "failed"
        assert ended["main_usage"]["tool_search"] == 1
        assert ended["main_usage"]["safety_output_tokens"] == 5
        assert ended["main_usage"]["total_tokens"] == 18
        assert "unreviewed draft" not in repr(records)
        assert "private-review-detail" not in repr(records)


async def test_nonstream_title_and_input_safety_are_handled_by_the_model(tmp_path):
    config = make_test_config(
        tmp_path,
        {
            "models": {"test": {"type": "deepseek"}},
            "agents": {"main": {"name": "deepseek-flash", "safety_check": "low"}},
        },
    )
    rejected = False

    def handle(request):
        body = json.loads(request.content)
        assert body["safety_check"] == {"level": "low"}
        assert body["title"] == {"enabled": True}
        if rejected:
            return httpx2.Response(400, json={"error": {"message": "Content Exists Risk: private-review-detail"}})
        return httpx2.Response(200, json=response([message()], title="Non-streamed title"))

    clients = ModelConnectionClients(
        config,
        None,
        client_factory=lambda **kwargs: AsyncOpenAI(
            **kwargs,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
        ),
    )
    try:
        agent = Agent(
            name="Main",
            model=clients.resolve(config.agent_model("main")),
            model_settings=ModelSettings(extra_body={"title": {"enabled": True}}),
        )
        result = await Runner.run(agent, "Hello", run_config={"tracing_disabled": True})
        assert result.raw_responses[-1].title == "Non-streamed title"
        rejected = True
        with pytest.raises(DeepSeekResponseError, match="input safety review") as error:
            await Runner.run(agent, "Reject this", run_config={"tracing_disabled": True})
        assert "private-review-detail" not in str(error.value)
    finally:
        await clients.close()


async def test_first_deepseek_title_is_committed_while_the_run_is_active(tmp_path, monkeypatch):
    config = make_test_config(
        tmp_path, {"models": {"test": {"type": "deepseek"}}, "agents": {"main": {"name": "deepseek-flash"}}}
    )
    requests = []
    title_visible, release = asyncio.Event(), asyncio.Event()
    call = {
        "id": "fc_calc",
        "call_id": "call_calc",
        "type": "function_call",
        "name": "calculate",
        "arguments": '{"expression":"2 + 2"}',
        "status": "completed",
    }

    async def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            payload = response([call], title="Intermediate title")
        else:
            await release.wait()
            payload = response([message()], title="Final title")
        return sse(payload) if body.get("stream") else httpx2.Response(200, json=payload)

    install_deepseek_http(monkeypatch, handle)

    def present(_session_id, presentation):
        if presentation.session_title == "Intermediate title":
            title_visible.set()

    async with open_application_host(
        config,
        manual_approval_handler=approve,
        enable_activity=False,
        callbacks=ApplicationCallbacks(handle_presentation_state=present),
    ) as host:
        session_id = await host.sessions.create()
        run = host.runtime.start_run(session_id, "Calculate 2 + 2", run_options())
        try:
            await asyncio.wait_for(title_visible.wait(), 3)
            assert not run.task.done()
            assert get_session_title(config.sessions_db, session_id) == "Intermediate title"
        finally:
            release.set()
        result = await run.task
        assert len(requests) == 2
        assert requests[0].get("title") == {"enabled": True}
        assert "title" not in requests[1]
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
                {"type": "response.web_search_call.in_progress", "output_index": 0, "item_id": call["id"]},
                {"type": "response.web_search_call.searching", "output_index": 1, "item_id": failed_search["id"]},
                {"type": "response.web_search_call.searching", "output_index": 0, "item_id": call["id"]},
                {"type": "response.output_item.done", "output_index": 1, "item": failed_search},
                {"type": "response.web_search_call.completed", "output_index": 0, "item_id": call["id"]},
                {"type": "response.output_item.done", "output_index": 0, "item": call},
            )
        elif last.get("content") == [{"type": "input_text", "text": "What did it say?"}]:
            payload = response([{**failed_search, "id": "ws_later_failed_search", "kind": "deep_search"}, message()])
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
        replay = requests[-1]["input"]
        summary_index = next(
            i for i, item in enumerate(replay) if str(item.get("content")).endswith("Earlier conversation summary")
        )
        assert replay.index(call) < summary_index
        assert failed_search in replay
        web_steps = [
            record for record in read_session_journal(config.session_journal_dir, fork) if record.get("hosted_status")
        ]
        assert [(record["name"], record["hosted_status"]) for record in web_steps] == [
            ("URL reading", "in_progress"),
            ("Web search", "in_progress"),
            ("Web search", "searching"),
            ("URL reading", "searching"),
            ("Web search", "failed"),
            ("URL reading", "completed"),
            ("Web search", "failed"),
        ]
        read_steps = [record for record in web_steps if record["name"] == "URL reading"]
        assert [record["hosted_call_id"] for record in read_steps] == [call["id"]] * 3
        assert json.loads(read_steps[-1]["arguments"]) == {"kind": "read_link", "action": call["action"]}


@pytest.mark.parametrize(
    ("provider", "name", "switches", "effort", "local", "hosted"),
    [
        ("deepseek", "deepseek-flash", {}, "high", True, ["deepseek_web_search", "deepseek_read_url"]),
        ("deepseek", "deepseek-v4-flash", {"web_search": False}, "none", False, ["deepseek_read_url"]),
        ("deepseek", "deepseek-flash", {"safety_check": "high"}, "none", True, []),
        ("deepseek", "deepseek-flash", {"web_search": False, "read_url": False}, "high", True, []),
        ("deepseek", "deepseek-v4-pro", {}, "high", True, []),
        ("openai_compatible", "deepseek-flash", {}, "high", True, []),
    ],
)
async def test_responses_declares_native_tools_alongside_local_tools(
    tmp_path, provider, name, switches, effort, local, hosted
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
            tools=[lookup] if local else [],
            model_settings=ModelSettings(
                reasoning={"effort": effort}, extra_body={"title": {"enabled": True}} if effort == "none" else None
            ),
        )
        result = await Runner.run(agent, "Test", run_config={"tracing_disabled": True})
    finally:
        await clients.close()

    assert result.final_output == "完成"
    (request,) = requests
    assert request["model"] == name
    assert request["reasoning"]["effort"] == effort
    assert request.get("tool_choice") != "none"
    assert [tool["type"] for tool in request["tools"]] == (["function"] if local else []) + hosted
    if local:
        assert request["tools"][0]["name"] == "lookup"
    if effort == "none":
        assert ("title" in request) is not local
        assert "safety_check" not in request
    assert "previous_response_id" not in request
