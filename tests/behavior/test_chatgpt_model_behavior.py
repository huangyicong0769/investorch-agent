from __future__ import annotations

import json

import httpx2
from openai import AsyncOpenAI

from investorch.agents.prompts import MAIN_AGENT_INSTRUCTIONS
from investorch.application import open_application_host
from investorch.commands.handlers import dispatch_command
from investorch.commands.parser import parse_command
from investorch.journal import read_session_journal
from investorch.llm.clients import ModelConnectionClients
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
