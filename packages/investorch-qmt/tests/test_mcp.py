from __future__ import annotations

import asyncio
import socket
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path

import httpx
import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from sse_starlette.sse import AppStatus

from investorch_qmt.config import default_paths, load_config
from investorch_qmt.server import create_app

TOKEN = "protocol-token-with-at-least-32-characters"


def service_market_status(tmp_path):
    from investorch_qmt.execution.service import ExecutionNodeService

    service = ExecutionNodeService(default_paths(tmp_path / "observed-version"))
    try:
        return service.get_node_status()["market_data"]
    finally:
        service.close()


def service_config(tmp_path: Path, *, host: str = "127.0.0.1", allowed_hosts: tuple[str, ...] = ()):
    path = tmp_path / "investorch-qmt.toml"
    allowed = ", ".join(f'"{item}"' for item in allowed_hosts)
    path.write_text(
        f"""
[server]
host = "{host}"
allowed_hosts = [{allowed}]

[auth]
token = "{TOKEN}"
""",
        encoding="utf-8",
    )
    return load_config(path)


@asynccontextmanager
async def running_app(app):
    # Each test starts a fresh server, whereas the SSE dependency keeps a process-wide shutdown flag.
    AppStatus.should_exit = False
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="on", log_config=None, access_log=False)
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(200):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.01)
        else:
            raise RuntimeError("test server did not start")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)


@pytest.mark.asyncio
async def test_official_client_discovers_live_controls_and_truthful_status(tmp_path: Path) -> None:
    app = create_app(service_config(tmp_path), paths=default_paths(tmp_path))

    async with (
        running_app(app) as url,
        httpx2.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}, trust_env=False) as http_client,
    ):
        transport = streamable_http_client(url, http_client=http_client)
        async with Client(transport, mode="legacy") as client:
            tools = await client.list_tools()
            result = await client.call_tool("get_status")

            assert client.server_info.name == "investorch-qmt"
            assert client.server_info.version == version("investorch-qmt")

    assert [tool.name for tool in tools.tools] == ["get_status", "start_live_strategy", "stop_live_strategy"]
    assert tools.tools[0].input_schema["type"] == "object"
    assert tools.tools[0].input_schema["properties"] == {}
    assert "required" not in tools.tools[0].input_schema
    assert tools.tools[0].annotations is not None
    assert tools.tools[0].annotations.read_only_hint is True
    assert result.is_error is False
    assert {key: result.structured_content[key] for key in ("service", "market_data", "trading")} == {
        "service": {"name": "investorch-qmt", "version": version("investorch-qmt"), "status": "ready"},
        "market_data": service_market_status(tmp_path),
        "trading": {"status": "NOT_READY", "reason": "TRADING_BACKEND_NOT_READY"},
    }


@pytest.mark.asyncio
async def test_default_transport_security_rejects_unexpected_host(tmp_path: Path) -> None:
    app = create_app(service_config(tmp_path), paths=default_paths(tmp_path))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://unexpected.example") as client:
        response = await client.get("/healthz", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 421


@pytest.mark.asyncio
async def test_lan_transport_security_accepts_only_configured_host(tmp_path: Path) -> None:
    app = create_app(
        service_config(tmp_path, host="0.0.0.0", allowed_hosts=("qmt-pc:8765",)), paths=default_paths(tmp_path)
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://qmt-pc:8765") as client:
        accepted = await client.get("/healthz", headers={"Authorization": f"Bearer {TOKEN}"})
        rejected = await client.get(
            "/healthz",
            headers={"Authorization": f"Bearer {TOKEN}", "Host": "unexpected.example"},
        )

    assert accepted.status_code == 200
    assert rejected.status_code == 421


@pytest.mark.asyncio
async def test_mcp_controls_share_the_rest_execution_state(tmp_path):
    from test_execution_service import stage_body, unavailable_runtime_factory

    from investorch_qmt.execution.service import ExecutionNodeService

    service = ExecutionNodeService(default_paths(tmp_path), runtime_factory=unavailable_runtime_factory)
    session = service.open_control_session()["session_id"]
    service.stage_deployment("deployment-a", stage_body(), session)
    service.renew_control_session(session, [{"deployment_id": "deployment-a", "acked_core_sequence": 12}])
    app = create_app(service_config(tmp_path), service=service)
    async with (
        running_app(app) as url,
        httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {TOKEN}", "X-InvestOrch-Control-Session": session}, trust_env=False
        ) as http_client,
        Client(streamable_http_client(url, http_client=http_client), mode="legacy") as client,
    ):
        started = await client.call_tool("start_live_strategy", {"portfolio_id": "portfolio-a"})
        assert started.structured_content["code"] == "MARKET_DATA_NOT_READY"
        assert service.get_portfolio_runtime_status("portfolio-a")["status"] == "STAGED"
        stopped = await client.call_tool("stop_live_strategy", {"portfolio_id": "portfolio-a"})
        assert stopped.structured_content["status"] == "STOPPED"
        status = await client.call_tool("get_status")
        assert status.structured_content["deployments"] == service.get_node_status()["deployments"]


@pytest.mark.asyncio
async def test_mcp_write_authority_is_request_local_and_never_an_agent_argument(tmp_path):
    from test_execution_service import stage_body, unavailable_runtime_factory

    from investorch_qmt.execution.service import ExecutionNodeService

    service = ExecutionNodeService(default_paths(tmp_path), runtime_factory=unavailable_runtime_factory)
    authority = service.open_control_session()["session_id"]
    service.stage_deployment("deployment-a", stage_body(), authority)
    service.renew_control_session(authority, [{"deployment_id": "deployment-a", "acked_core_sequence": 12}])
    app = create_app(service_config(tmp_path), service=service)
    async with (
        running_app(app) as url,
        httpx2.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}, trust_env=False) as bearer_http,
        httpx2.AsyncClient(
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "X-InvestOrch-Control-Session": authority,
            },
            trust_env=False,
        ) as owner_http,
        Client(streamable_http_client(url, http_client=bearer_http), mode="legacy") as bearer,
        Client(streamable_http_client(url, http_client=owner_http), mode="legacy") as owner,
    ):
        discovered = await owner.list_tools()
        for tool in discovered.tools[1:]:
            assert set(tool.input_schema["properties"]) == {"portfolio_id"}
        assert (await bearer.call_tool("get_status")).structured_content["market_data"]["status"] == "DISCONNECTED"
        for tool in ("start_live_strategy", "stop_live_strategy"):
            for portfolio in ("portfolio-a", "absent"):
                result = await bearer.call_tool(tool, {"portfolio_id": portfolio})
                assert result.structured_content["code"] == "STALE_CONTROL_SESSION"
        current, missing = await asyncio.gather(
            owner.call_tool("start_live_strategy", {"portfolio_id": "portfolio-a"}),
            bearer.call_tool("start_live_strategy", {"portfolio_id": "portfolio-a"}),
        )
        assert current.structured_content["code"] == "MARKET_DATA_NOT_READY"
        assert missing.structured_content["code"] == "STALE_CONTROL_SESSION"
        service.close_control_session(authority)
        replacement = service.open_control_session()["session_id"]
        for tool in ("start_live_strategy", "stop_live_strategy"):
            result = await owner.call_tool(tool, {"portfolio_id": "portfolio-a"})
            assert result.structured_content["code"] == "STALE_CONTROL_SESSION"
        assert service.get_portfolio_runtime_status("portfolio-a")["status"] == "STAGED"
        # The same MCP session receives current authority on each HTTP request.
        owner_http.headers["X-InvestOrch-Control-Session"] = replacement
        stopped = await owner.call_tool("stop_live_strategy", {"portfolio_id": "portfolio-a"})
        assert stopped.structured_content["status"] == "STOPPED"
