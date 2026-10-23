"""A failed remote MCP connection leaves unrelated Agent work available."""

from __future__ import annotations

import asyncio
import json
import socket
from contextlib import asynccontextmanager
from pathlib import Path

import httpx2
import pytest
import uvicorn
from agents import Agent, SQLiteSession
from agents.mcp import MCPServerManager
from agents.testing import ScriptedModel, assistant_message, function_call
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from investorch.agents import AgentLoop, ApprovalOutcome, TokenUsage
from investorch.application.portfolios import PortfolioOperations
from investorch.context import ExecutionState
from investorch.mcp import configure_mcp_server_config, load_mcp_servers
from investorch.runtime.control import RunControl
from investorch.storage import create_session, set_session_title
from investorch.tools.base import calculate
from tests.support.config import make_test_config


@pytest.fixture(autouse=True)
def direct_local_connections(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


@asynccontextmanager
async def mcp_peer():
    app = FastAPI()
    state = {"fail_listing": True, "fail_call": False, "calls": 0, "error_message": "Connection closed"}

    @app.post("/{name}/mcp")
    async def rpc(name: str, request: Request):
        data = await request.json()
        method = data["method"]
        if "id" not in data:
            return Response(status_code=202)
        if method == "initialize":
            if name == "broken" and state.get("hold_initialize"):
                await state["release"].wait()
            if name == "broken" and state.get("fail_initialize"):
                if state["fail_initialize"] == "rpc":
                    return JSONResponse(
                        {
                            "jsonrpc": "2.0",
                            "id": data["id"],
                            "error": {"code": -32603, "message": "initialization failed"},
                        }
                    )
                return Response(status_code=503)
            result = {
                "protocolVersion": data["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": name, "version": "1"},
            }
        elif method == "tools/list":
            if name == "broken" and state.get("drop_body"):
                return Response(content=b"", headers={"content-type": "application/json", "content-length": "100"})
            if name == "broken" and state.get("hold_listing"):
                state["entered"].set()
                await state["release"].wait()
            if name == "broken" and state["fail_listing"]:
                return JSONResponse(
                    {"jsonrpc": "2.0", "id": data["id"], "error": {"code": -32000, "message": state["error_message"]}}
                )
            result = {
                "tools": [
                    {
                        "name": "lookup",
                        "description": "Read a fixture value.",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                ]
            }
        elif method == "tools/call":
            state["calls"] += 1
            if state["fail_call"]:
                return JSONResponse(
                    {"jsonrpc": "2.0", "id": data["id"], "error": {"code": -32000, "message": state["error_message"]}}
                )
            result = {"content": [{"type": "text", "text": "fixture value"}]}
        else:
            return Response(status_code=405)
        return JSONResponse({"jsonrpc": "2.0", "id": data["id"], "result": result})

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{listener.getsockname()[1]}", state
    finally:
        server.should_exit = True
        await task
        listener.close()


@asynccontextmanager
async def agent_with_mcp(tmp_path: Path, url: str, model: ScriptedModel, **manager_options):
    config = make_test_config(tmp_path)
    for name in ("broken", "healthy"):
        configure_mcp_server_config(
            config.mcp_config_path,
            name,
            url=f"{url}/{name}/mcp?token=literal-url-secret",
            cache_tools_list=True,
            headers={"Authorization": "Bearer literal-header-secret"},
        )
    servers = load_mcp_servers(config.mcp_config_path, {}, 5)
    main = Agent(
        name="Main",
        instructions="Use the requested tools.",
        model=model,
        tools=[calculate],
        mcp_servers=servers,
        mcp_config={"include_server_in_tool_names": True},
    )
    unused = Agent(name="Unused", model=ScriptedModel())
    loop = AgentLoop(main, unused, unused, config, PortfolioOperations(config=config), lambda _: model)
    create_session(config.sessions_db, "session")
    set_session_title(config.sessions_db, "session", "MCP failure isolation")
    session = SQLiteSession("session", config.sessions_db)

    async def approve(*_):
        return ApprovalOutcome(True, TokenUsage())

    async def output(_):
        pass

    async def run(run_id):
        return await loop.run(
            "Continue the requested work",
            session,
            ExecutionState(),
            run_id=run_id,
            session_id="session",
            model_config=config.agent_model("main"),
            approval_handler=approve,
            output_handler=output,
            run_control=RunControl("session", run_id, lambda: None),
        )

    try:
        async with MCPServerManager(servers, **manager_options) as manager:
            errors = manager.errors
            for server in servers:
                server.record_connection_result(errors.get(server))
            yield run, model, session, servers
    finally:
        session.close()


async def test_failed_discovery_keeps_local_and_other_mcp_tools_and_recovers_next_run(tmp_path: Path):
    model = ScriptedModel(
        (
            (function_call("calculate", {"expression": "2+3"}, call_id="local"),),
            (function_call("mcp_healthy__lookup", {}, call_id="remote"),),
            (assistant_message("Independent work completed"),),
            (function_call("mcp_broken__lookup", {}, call_id="recovered"),),
            (assistant_message("Connection recovered"),),
        )
    )
    async with mcp_peer() as (url, peer), agent_with_mcp(tmp_path, url, model) as (run, _, session, servers):
        assert (await run("first")).output == "Independent work completed"
        first_input = json.dumps(model.calls[0].input)
        assert "broken" in first_input and "MCPError: Connection closed" in first_input
        first_tools = {tool.name for tool in model.calls[0].tools}
        assert "calculate" in first_tools and "mcp_healthy__lookup" in first_tools
        assert "mcp_broken__lookup" not in first_tools
        assert servers[0].runtime_status == "unavailable"
        peer["fail_listing"] = False
        assert (await run("second")).output == "Connection recovered"
        assert servers[0].runtime_status == "available"
        assert not any(item.get("role") == "developer" for item in await session.get_items())
    model.assert_complete()


@pytest.mark.parametrize("failure", ["rpc", "read"])
async def test_failed_mcp_call_reports_the_uncertain_outcome_and_keeps_the_run_alive(
    tmp_path: Path, monkeypatch, failure: str
):
    model = ScriptedModel(
        (
            (function_call("mcp_broken__lookup", {}, call_id="remote"),),
            (function_call("calculate", {"expression": "4+5"}, call_id="local"),),
            (assistant_message("Remaining work completed"),),
        )
    )
    async with mcp_peer() as (url, peer), agent_with_mcp(tmp_path, url, model) as (run, _, session, servers):
        peer.update(fail_listing=False, fail_call=failure)
        if failure == "read":

            async def read_failure(*_, **__):
                peer["calls"] += 1
                raise httpx2.ReadError("peer closed during read")

            monkeypatch.setattr(servers[0].session, "call_tool", read_failure)
        assert (await run("first")).output == "Remaining work completed"
        assert peer["calls"] == 1
        outputs = [
            item["output"]
            for item in await session.get_items()
            if item.get("type") == "function_call_output" and item["call_id"] == "remote"
        ]
        text = str(outputs)
        detail = {"rpc": "MCPError: Connection closed", "read": "ReadError: peer closed during read"}
        assert "broken" in text and detail[failure] in text
        assert "unknown" in text.lower()
        assert servers[0].runtime_status == "unavailable"
        assert servers[0].runtime_error["operation"] == "call_tool"
    model.assert_complete()


@pytest.mark.parametrize("failure", ["http", "rpc"])
async def test_failed_initial_connection_leaves_the_other_tools_available(tmp_path: Path, failure: str):
    model = ScriptedModel(((assistant_message("Available work completed"),),))
    async with mcp_peer() as (url, peer):
        peer["fail_initialize"] = failure
        async with agent_with_mcp(tmp_path, url, model) as (run, _, _, servers):
            assert (await run("first")).output == "Available work completed"
            assert servers[0].runtime_error["operation"] == "connect"
            assert ("503" if failure == "http" else "initialization failed") in servers[0].runtime_error["message"]
            assert "mcp_healthy__lookup" in {tool.name for tool in model.calls[0].tools}
    model.assert_complete()


async def test_sdk_manager_connect_timeout_is_reported_and_other_tools_remain_available(tmp_path: Path):
    model = ScriptedModel(((assistant_message("Available work completed"),),))
    async with mcp_peer() as (url, peer):
        release = asyncio.Event()
        peer.update(hold_initialize=True, release=release)
        try:
            async with agent_with_mcp(tmp_path, url, model, connect_timeout_seconds=0.1) as (run, _, _, servers):
                assert servers[0].runtime_status == "unavailable"
                assert servers[0].runtime_error == {"operation": "connect", "message": "TimeoutError: "}
                assert (await run("first")).output == "Available work completed"
                assert "mcp_healthy__lookup" in {tool.name for tool in model.calls[0].tools}
                assert "TimeoutError" in json.dumps(model.calls[0].input)
        finally:
            release.set()
    model.assert_complete()


async def test_cancelled_discovery_is_cancelled_and_the_next_run_can_continue(tmp_path: Path):
    model = ScriptedModel(((assistant_message("Next Run completed"),),))
    async with mcp_peer() as (url, peer), agent_with_mcp(tmp_path, url, model) as (run, _, _, _):
        entered, release = asyncio.Event(), asyncio.Event()
        peer.update(hold_listing=True, entered=entered, release=release)
        task = asyncio.create_task(run("cancelled"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            peer.update(hold_listing=False, fail_listing=False)
        assert not model.calls
        assert (await run("next")).output == "Next Run completed"
    model.assert_complete()


async def test_connection_diagnostics_redact_literal_url_and_header_credentials(tmp_path: Path, caplog):
    model = ScriptedModel(((assistant_message("Independent work completed"),),))
    async with mcp_peer() as (url, peer), agent_with_mcp(tmp_path, url, model) as (run, _, _, servers):
        peer["error_message"] = (
            f"Connection closed: literal-url-secret literal-header-secret {url}?token=literal-url-secret"
        )
        assert (await run("first")).output == "Independent work completed"
        diagnostics = str(servers[0].runtime_error) + json.dumps(model.calls[0].input) + caplog.text
        assert "literal-url-secret" not in diagnostics and "literal-header-secret" not in diagnostics
        assert "Connection closed" in diagnostics and "<redacted>" in diagnostics
    model.assert_complete()


async def test_dropped_http_response_keeps_the_other_tools_available(tmp_path: Path):
    model = ScriptedModel(((assistant_message("Available work completed"),),))
    async with mcp_peer() as (url, peer), agent_with_mcp(tmp_path, url, model) as (run, _, _, servers):
        peer["drop_body"] = True
        assert (await run("first")).output == "Available work completed"
        assert servers[0].runtime_status == "unavailable"
        assert "mcp_healthy__lookup" in {tool.name for tool in model.calls[0].tools}
    model.assert_complete()


async def test_raw_mcp_http_client_failure_stays_inside_its_connection(tmp_path: Path, monkeypatch):
    model = ScriptedModel(((assistant_message("Available work completed"),),))
    async with mcp_peer() as (url, _), agent_with_mcp(tmp_path, url, model) as (run, _, _, servers):

        async def read_failure(*_, **__):
            raise httpx2.ReadError("peer closed during read")

        monkeypatch.setattr(servers[0].session, "list_tools", read_failure)
        assert (await run("first")).output == "Available work completed"
        assert "ReadError: peer closed during read" in json.dumps(model.calls[0].input)
    model.assert_complete()
