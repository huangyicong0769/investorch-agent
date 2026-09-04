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

from investorch_qmt.config import load_config
from investorch_qmt.server import create_app

TOKEN = "protocol-token-with-at-least-32-characters"


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
async def test_official_client_discovers_and_calls_only_b0_status_tool(tmp_path: Path) -> None:
    app = create_app(service_config(tmp_path))

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

    assert [tool.name for tool in tools.tools] == ["get_status"]
    assert tools.tools[0].input_schema["type"] == "object"
    assert tools.tools[0].input_schema["properties"] == {}
    assert "required" not in tools.tools[0].input_schema
    assert tools.tools[0].annotations is not None
    assert tools.tools[0].annotations.read_only_hint is True
    assert result.is_error is False
    assert result.structured_content == {
        "service": {"name": "investorch-qmt", "version": version("investorch-qmt"), "status": "ready"},
        "qmt": {"status": "not_connected", "reason": "QMT backend is not connected."},
    }


@pytest.mark.asyncio
async def test_default_transport_security_rejects_unexpected_host(tmp_path: Path) -> None:
    app = create_app(service_config(tmp_path))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://unexpected.example") as client:
        response = await client.get("/healthz", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 421


@pytest.mark.asyncio
async def test_lan_transport_security_accepts_only_configured_host(tmp_path: Path) -> None:
    app = create_app(service_config(tmp_path, host="0.0.0.0", allowed_hosts=("qmt-pc:8765",)))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://qmt-pc:8765") as client:
        accepted = await client.get("/healthz", headers={"Authorization": f"Bearer {TOKEN}"})
        rejected = await client.get(
            "/healthz",
            headers={"Authorization": f"Bearer {TOKEN}", "Host": "unexpected.example"},
        )

    assert accepted.status_code == 200
    assert rejected.status_code == 421
