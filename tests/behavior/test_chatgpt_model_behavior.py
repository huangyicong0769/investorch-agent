from __future__ import annotations

import json
import time
from dataclasses import replace

import httpx2
import pytest
from agents import Agent, AgentHooks, ModelBehaviorError, ModelSettings, Runner, function_tool
from openai import AsyncOpenAI
from pydantic import BaseModel

from investorch.agents.prompts import MAIN_AGENT_INSTRUCTIONS
from investorch.agents.usage import run_with_usage
from investorch.application import open_application_host
from investorch.commands.handlers import dispatch_command
from investorch.commands.parser import parse_command
from investorch.journal import read_session_journal
from investorch.llm.clients import ModelConnectionClients
from investorch.llm.models import ChatGPTInferenceError, ChatGPTResponsesModel, chatgpt_tools, with_developer_prompt
from investorch.llm.oauth import ChatGPTAuth
from tests.support.config import make_test_config
from tests.support.runtime import run_options


def response(output, *, status="completed"):
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 1,
        "status": status,
        "model": "test-model",
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
    }


def events(payload, *, streamed_output=None):
    records = [{"type": "response.created", "response": response([], status="in_progress")}]
    for index, item in enumerate(streamed_output or []):
        records.append({"type": "response.output_item.done", "output_index": index, "item": item})
    records.append({"type": "response.completed", "response": payload})
    return "".join(
        f"data: {json.dumps({**record, 'sequence_number': index})}\n\n" for index, record in enumerate(records)
    ).encode()


async def test_main_chatgpt_web_search_uses_session_switch_and_records_sources(tmp_path, monkeypatch):
    config = make_test_config(
        tmp_path,
        {
            "models": {"chatgpt": {"type": "chatgpt", "credentials": "personal"}},
            "agents": {"main": {"model": "chatgpt", "name": "gpt-6.2-sol", "auto_title": False}},
        },
    )
    search = {
        "type": "web_search_call",
        "id": "ws_example",
        "status": "completed",
        "action": {"type": "search", "query": "Example Domains"},
    }
    citation = {
        "type": "url_citation",
        "start_index": 0,
        "end_index": 15,
        "url": "https://www.iana.org/help/example-domains",
        "title": "Example Domains",
    }
    tool_types = []
    requests = []

    def handle(request):
        body = json.loads(request.content)
        tool_types.append({tool["type"] for tool in body["tools"]})
        requests.append(body)
        web_enabled = "web_search" in tool_types[-1]
        answer = {
            "id": "msg_answer",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [
                {"type": "output_text", "text": "Example Domains", "annotations": [citation] if web_enabled else []}
            ],
        }
        output = [search, answer] if web_enabled else [answer]
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, content=events(response(output), streamed_output=output)
        )

    def factory(**kwargs):
        return AsyncOpenAI(
            **(kwargs | {"api_key": "test-oauth-token"}),
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
        )

    def clients(app_config, auth):
        return ModelConnectionClients(app_config, auth, client_factory=factory)

    async def approve(*_):
        return True

    monkeypatch.setattr("investorch.application.host.ModelConnectionClients", clients)
    async with open_application_host(config, manual_approval_handler=approve, enable_activity=False) as host:
        session_id = host.initial_session_id
        assert session_id is not None
        await host.runtime.start_run(session_id, "Find Example Domains", run_options()).task
        await dispatch_command(
            parse_command("/model web off"), host.state, runtime=host.runtime, sessions=host.sessions
        )
        await host.runtime.start_run(session_id, "What did you find?", run_options()).task

    assert {"namespace", "web_search"} <= tool_types[0]
    assert "web_search" not in tool_types[1]
    assert search in requests[1]["input"]
    initial = requests[0]["input"][0]
    assert initial["role"] == "developer"
    assert initial["content"].startswith(MAIN_AGENT_INSTRUCTIONS)
    for request in requests:
        assert request["input"][0] == initial
        assert request["input"].count(initial) == 1
        assert "instructions" not in request
    records = read_session_journal(config.session_journal_dir, session_id)
    assert [(event["name"], event["hosted_status"]) for event in records if event.get("hosted_call_id")] == [
        ("Web search", "completed"),
    ]
    first_answer = next(event for event in records if event["type"] == "assistant_message")
    assert first_answer["citations"][0]["url"] == citation["url"]


@pytest.mark.parametrize("output_in_completed_event", ["full", "null", "empty"])
async def test_nonstream_runner_uses_streaming_http_and_preserves_structured_output_and_usage(
    output_in_completed_event,
):
    requests = []

    class Answer(BaseModel):
        answer: str

    def handle(request):
        requests.append(json.loads(request.content))
        output = [
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": '{"answer":"ready"}', "annotations": []}],
            }
        ]
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream", "x-request-id": "req-test"},
            content=events(
                response(
                    output
                    if output_in_completed_event == "full"
                    else None
                    if output_in_completed_event == "null"
                    else []
                ),
                streamed_output=output,
            ),
        )

    async with AsyncOpenAI(
        api_key="test-oauth-token", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
    ) as client:
        agent = Agent(
            name="Review",
            model=ChatGPTResponsesModel("test-model", client),
            output_type=Answer,
            model_settings=ModelSettings(max_tokens=200, temperature=0.2, store=True, metadata={"name": "review"}),
        )
        result = await Runner.run(agent, "Review this", max_turns=1, run_config={"tracing_disabled": True})
    assert result.final_output == Answer(answer="ready")
    assert result.context_wrapper.usage.input_tokens == 11
    assert result.context_wrapper.usage.output_tokens_details.reasoning_tokens == 2
    assert requests[0]["stream"] is True
    assert requests[0]["store"] is False
    assert requests[0]["text"]["format"]["type"] == "json_schema"
    assert not ({"max_output_tokens", "temperature", "metadata"} & requests[0].keys())


async def test_namespaced_function_executes_through_sdk_and_its_result_is_replayed():
    called = []
    requests = []

    @function_tool
    def report(value: str) -> str:
        """Report a value."""
        called.append(value)
        return "recorded"

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        output = (
            [
                {
                    "id": "fc_1",
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "report",
                    "namespace": "investorch",
                    "arguments": '{"value":"approved"}',
                    "status": "completed",
                }
            ]
            if len(requests) == 1
            else [
                {
                    "id": "msg_2",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "done", "annotations": []}],
                }
            ]
        )
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=events(response(output)))

    async with AsyncOpenAI(
        api_key="test-oauth-token", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
    ) as client:
        agent = Agent(name="Main", model=ChatGPTResponsesModel("test-model", client), tools=chatgpt_tools([report]))
        result = await Runner.run(agent, "Report", run_config={"tracing_disabled": True})
        await Runner.run(
            agent,
            [*result.to_input_list(), {"role": "user", "content": "What was reported?"}],
            run_config={"tracing_disabled": True},
        )
    assert called == ["approved"]
    assert result.final_output == "done"
    assert requests[0]["tools"][0]["type"] == "namespace"
    for request in requests[1:]:
        (call,) = [item for item in request["input"] if item.get("type") == "function_call"]
        (tool_output,) = [item for item in request["input"] if item.get("type") == "function_call_output"]
        assert call["call_id"] == tool_output["call_id"] == "call_1"
        assert call["arguments"] == '{"value":"approved"}'
        assert tool_output["output"] == "recorded"
        assert "parsed_arguments" not in call
    (message,) = [item for item in requests[2]["input"] if item.get("role") == "assistant"]
    assert message["content"] == [{"type": "output_text", "text": "done", "annotations": []}]


async def test_truncated_stream_cannot_be_reported_as_success():
    def handle(request):
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=b"data: [DONE]\n\n")

    async with AsyncOpenAI(
        api_key="test-oauth-token", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
    ) as client:
        agent = Agent(name="Main", model=ChatGPTResponsesModel("test-model", client))
        with pytest.raises(ModelBehaviorError, match="completed"):
            await Runner.run(agent, "hello", run_config={"tracing_disabled": True})


async def test_streamed_runner_preserves_namespaced_approval_and_executes_only_after_approval():
    called = []
    requests = []

    @function_tool(needs_approval=True)
    def record(value: str) -> str:
        """Record an approved value."""
        called.append(value)
        return "saved"

    def handle(request):
        requests.append(json.loads(request.content))
        output = (
            [
                {
                    "id": "fc_approval",
                    "type": "function_call",
                    "call_id": "call_approval",
                    "name": "record",
                    "namespace": "investorch",
                    "arguments": '{"value":"safe"}',
                    "status": "completed",
                }
            ]
            if len(requests) == 1
            else [
                {
                    "id": "msg_after",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "saved", "annotations": []}],
                }
            ]
        )
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=events(response(output)))

    async with AsyncOpenAI(
        api_key="test-oauth-token", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
    ) as client:
        agent = Agent(name="Main", model=ChatGPTResponsesModel("test-model", client), tools=chatgpt_tools([record]))
        result = Runner.run_streamed(agent, "record safe", run_config={"tracing_disabled": True})
        async for _ in result.stream_events():
            pass
        assert called == []
        assert len(result.interruptions) == 1
        resumed = result.to_state()
        resumed.approve(result.interruptions[0])
        final = Runner.run_streamed(agent, resumed, run_config={"tracing_disabled": True})
        async for _ in final.stream_events():
            pass
    assert called == ["safe"]
    assert final.final_output == "saved"
    assert len(requests) == 2


@pytest.mark.parametrize(
    ("endpoint", "model_name", "streamed"),
    [
        ("https://api.openai.com/v1", "gpt-6.2-astra", False),
        ("https://api.openai.com/v1/", "gpt-6.2-sol", True),
        ("https://api.openai.com/v1", "gpt-6.1-sol", False),
        ("https://api.deepseek.com", "deepseek-flash", True),
        ("https://api.deepseek.com", "deepseek-v4-pro", False),
        ("https://provider.example.test/v1", "provider-model", False),
    ],
)
async def test_api_key_model_instruction_placement_and_request_parameters(tmp_path, endpoint, model_name, streamed):
    requests = []
    model_inputs = []
    config = make_test_config(
        tmp_path,
        {
            "models": {"test": {"base_url": endpoint}},
            "agents": {"title": {"model": "test", "name": model_name, "context_window_tokens": 1000000}},
        },
    )
    instructions = "Write a title for the task."
    history = [
        {"role": "developer", "content": "For this task, use English."},
        {"role": "user", "content": "hello"},
        {"role": "developer", "content": "Current Run state data: ready."},
    ]

    def handle(request):
        requests.append(json.loads(request.content))
        output = [
            {
                "id": "msg_key",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "ok", "annotations": []}],
            }
        ]
        if requests[-1].get("stream"):
            return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=events(response(output)))
        return httpx2.Response(200, json=response(output))

    def factory(**kwargs):
        return AsyncOpenAI(**kwargs, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)))

    class ModelInputHook(AgentHooks):
        async def on_llm_start(self, context, agent, system_prompt, input_items):
            model_inputs.append((system_prompt, list(input_items)))

    auth = ChatGPTAuth(config)
    clients = ModelConnectionClients(config, auth, client_factory=factory)
    try:
        model, _ = clients.for_agent("title")
        agent = Agent(
            name="Title",
            model=model,
            instructions=instructions,
            model_settings=ModelSettings(max_tokens=100, temperature=0.2),
            hooks=ModelInputHook(),
        )
        if streamed:
            run_config = with_developer_prompt({"tracing_disabled": True})
            result = Runner.run_streamed(agent, history, run_config=run_config)
            async for _ in result.stream_events():
                pass
        else:
            result = await run_with_usage(agent, history, run_config={"tracing_disabled": True})
    finally:
        await clients.close()
        await auth.close()

    assert result.final_output == "ok"
    expected = [{"role": "developer", "content": instructions}, *history]
    initial_input = history if streamed else expected
    assert result.to_input_list()[: len(initial_input)] == initial_input
    (request,) = requests
    assert request["max_output_tokens"] == 100
    assert request["temperature"] == 0.2
    assert model_inputs == [(None, expected)]
    assert request["input"] == expected
    assert "instructions" not in request


@pytest.mark.parametrize(
    ("status", "code", "guidance"),
    [
        (401, "subscription_sharing_invalid_user", "Sign in again"),
        (403, "subscription_sharing_user_not_eligible", "not eligible"),
        (403, "chatpass_v2_scope_not_authorized", "granted permissions"),
        (429, "subscription_sharing_usage_limit_exceeded", "Manage Usage"),
        (429, "usage_limit_reached", "Manage Usage"),
        (503, "subscription_sharing_usage_unavailable", "Credentials are retained"),
        (401, "private-code-with-token", "Sign in again"),
    ],
)
async def test_chatgpt_http_errors_use_safe_categories_without_retries_or_billing_fallback(
    tmp_path, status, code, guidance
):
    from investorch.llm.oauth import ChatGPTAuth

    config = make_test_config(tmp_path, {"models": {"chatgpt": {"type": "chatgpt", "credentials": "personal"}}})
    config.save_oauth_credential(
        "personal",
        {
            "client_id": "issued-client",
            "subject": "test-subject",
            "access_token": "private-oauth-token",
            "refresh_token": "private-refresh-token",
            "expires_at": time.time() + 3600,
            "scopes": ["chatgpt.tokens.use.direct"],
        },
    )
    requests = []

    def handle(request):
        requests.append(request)
        return httpx2.Response(
            status, json={"error": {"code": code, "message": "private-provider-body", "param": "private-param"}}
        )

    def factory(**kwargs):
        return AsyncOpenAI(**kwargs, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)))

    auth = ChatGPTAuth(config)
    clients = ModelConnectionClients(config, auth, client_factory=factory)
    before = config.root_config_path.read_bytes()
    try:
        selected = replace(config.agent_model("main"), model="chatgpt")
        agent = Agent(name="Main", model=clients.resolve(selected))
        with pytest.raises(ChatGPTInferenceError) as caught:
            await Runner.run(agent, "hello", run_config={"tracing_disabled": True})
        assert caught.value.status == status
        assert guidance in str(caught.value)
        assert "private-" not in str(caught.value)
        if not code.startswith("private-"):
            assert caught.value.code == code
        assert len(requests) == 1
        assert str(requests[0].url) == "https://api.openai.com/v1/responses"
        assert requests[0].headers["authorization"] == "Bearer private-oauth-token"
        assert config.root_config_path.read_bytes() == before
    finally:
        await clients.close()
        await auth.close()


async def test_streamed_plan_limit_is_safe_failure_before_forwarding_provider_body():
    payload = response([], status="failed")
    payload["error"] = {"code": "subscription_sharing_usage_limit_exceeded", "message": "private-provider-body"}
    event = {"type": "response.failed", "sequence_number": 0, "response": payload}

    def handle(request):
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, content=f"data: {json.dumps(event)}\n\n".encode()
        )

    async with AsyncOpenAI(
        api_key="private-token", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
    ) as client:
        result = Runner.run_streamed(
            Agent(name="Main", model=ChatGPTResponsesModel("test", client)),
            "hello",
            run_config={"tracing_disabled": True},
        )
        with pytest.raises(ChatGPTInferenceError) as caught:
            async for emitted in result.stream_events():
                assert "private-" not in str(emitted)
    assert caught.value.code == "subscription_sharing_usage_limit_exceeded"
    assert "Manage Usage" in str(caught.value)
    assert "private-" not in str(caught.value)


@pytest.mark.parametrize(
    ("reasoning", "expected"),
    [
        ({"effort": "high"}, {"effort": "high", "summary": "auto"}),
        ({"effort": "none"}, {"effort": "none"}),
        ({"effort": "high", "summary": "detailed"}, {"effort": "high", "summary": "detailed"}),
    ],
)
async def test_chatgpt_requests_summaries_for_enabled_reasoning(reasoning, expected):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=events(
                response(
                    [
                        {
                            "id": "msg_summary_check",
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": "Done", "annotations": []}],
                        }
                    ]
                )
            ),
        )

    settings = ModelSettings(reasoning=reasoning)
    original_reasoning = settings.reasoning.model_dump()
    async with AsyncOpenAI(
        api_key="test-oauth-token", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
    ) as client:
        agent = Agent(name="Main", model=ChatGPTResponsesModel("test-model", client), model_settings=settings)
        await Runner.run(agent, "Inspect", max_turns=1, run_config={"tracing_disabled": True})
    assert requests[0]["reasoning"] == expected
    assert settings.reasoning.model_dump() == original_reasoning
