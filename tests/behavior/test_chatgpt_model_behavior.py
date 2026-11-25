from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing

import httpx2
from openai import AsyncOpenAI

from investorch.agents.prompts import MAIN_AGENT_INSTRUCTIONS
from investorch.application import open_application_host
from investorch.commands.handlers import dispatch_command
from investorch.commands.parser import parse_command
from investorch.config import load_config
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


async def test_main_replays_reasoning_from_the_selected_connection_after_switches_and_fork(tmp_path, monkeypatch):
    config = make_test_config(
        tmp_path,
        {
            "models": {
                "first": {"type": "chatgpt", "credentials": "personal-first"},
                "second": {"type": "chatgpt", "credentials": "personal-second"},
            },
            "agents": {"main": {"model": "first", "name": "gpt-6.2-sol", "auto_title": False}},
        },
    )
    requests = []
    held_request: int | None = None
    request_started = asyncio.Event()
    release_response = asyncio.Event()

    async def handle(request):
        requests.append(json.loads(request.content))
        number = len(requests)
        if number == held_request:
            request_started.set()
            await release_response.wait()
        reasoning_key = "shared" if number in (1, 3) else str(number)
        output = [
            {
                "type": "reasoning",
                "id": f"rs_{reasoning_key}",
                "summary": [],
                "encrypted_content": f"opaque-{reasoning_key}",
            },
            {
                "type": "message",
                "id": f"msg_{number}",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": f"Answer {number}", "annotations": []}],
            },
        ]
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

    def reasoning_sent() -> list[str]:
        return [item["encrypted_content"] for item in requests[-1]["input"] if item.get("type") == "reasoning"]

    monkeypatch.setattr("investorch.application.host.ModelConnectionClients", clients)
    async with open_application_host(config, manual_approval_handler=approve, enable_activity=False) as host:
        session_id = host.initial_session_id
        assert session_id is not None
        await host.runtime.start_run(session_id, "Start", run_options()).task
        await host.runtime.start_run(session_id, "Continue on the first connection", run_options()).task
        assert reasoning_sent() == ["opaque-shared"]
        with closing(sqlite3.connect(config.sessions_db)) as database:
            first_history = database.execute(
                "SELECT id, message_data FROM agent_messages WHERE session_id = ? ORDER BY id", (session_id,)
            ).fetchall()

        host.session_models.update(
            session_id, model="second", name="gpt-6.2-sol", reasoning_effort="max", context_window_tokens=1000000
        )
        await host.runtime.start_run(session_id, "Switch connection", run_options()).task
        assert reasoning_sent() == []
        await host.runtime.start_run(session_id, "Continue on the second connection", run_options()).task
        assert reasoning_sent() == ["opaque-shared"]
        with closing(sqlite3.connect(config.sessions_db)) as database:
            history = database.execute(
                "SELECT id, message_data FROM agent_messages WHERE session_id = ? ORDER BY id", (session_id,)
            ).fetchall()
        assert history[: len(first_history)] == first_history

        fork = await host.sessions.fork(session_id)
        await host.runtime.start_run(fork, "Continue in the fork", run_options()).task
        assert reasoning_sent() == ["opaque-shared", "opaque-4"]
        host.session_models.update(
            fork, model="first", name="gpt-6.2-sol", reasoning_effort="max", context_window_tokens=1000000
        )
        await host.runtime.start_run(fork, "Switch back to the first connection", run_options()).task
        assert reasoning_sent() == ["opaque-shared", "opaque-2"]

        legacy = await host.sessions.fork(session_id)
        with closing(sqlite3.connect(config.sessions_db)) as database, database:
            database.execute("UPDATE agent_messages SET source_connection = NULL WHERE session_id = ?", (legacy,))
        await host.runtime.start_run(legacy, "Resume a Session with unknown history sources", run_options()).task
        assert reasoning_sent() == []
        await host.runtime.start_run(legacy, "Continue on the recorded connection", run_options()).task
        assert reasoning_sent() == ["opaque-7"]

    config.update("models.second.credentials", "personal-replacement")
    updated_config = load_config(root=config.root)
    async with open_application_host(updated_config, manual_approval_handler=approve, enable_activity=False) as host:
        await host.runtime.start_run(session_id, "Continue with replacement credentials", run_options()).task
        assert reasoning_sent() == []
        await host.runtime.start_run(session_id, "Continue on the replacement connection", run_options()).task
        assert reasoning_sent() == ["opaque-9"]

        async with open_application_host(
            updated_config, manual_approval_handler=approve, enable_activity=False, create_initial_session=False
        ) as other:
            concurrent_session = await host.sessions.create()
            other.session_models.update(
                concurrent_session,
                model="second",
                name="gpt-6.2-sol",
                reasoning_effort="max",
                context_window_tokens=1000000,
            )
            held_request = len(requests) + 1
            original = host.runtime.start_run(concurrent_session, "Hold the first connection", run_options())
            try:
                await asyncio.wait_for(request_started.wait(), timeout=5)
                await other.runtime.start_run(concurrent_session, "Run the second connection", run_options()).task
            finally:
                release_response.set()
                await original.task
            await other.runtime.start_run(concurrent_session, "Continue the second connection", run_options()).task
            assert reasoning_sent() == [f"opaque-{held_request + 1}"]
