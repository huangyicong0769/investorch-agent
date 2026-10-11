from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from agents.tool_context import ToolContext
from fastapi import FastAPI

from investorch.config import REDACTED
from investorch.context import AgentContext
from investorch.mcp import (
    MCPServerExistsError,
    MCPServerNotFoundError,
    configure_mcp_server_config,
    public_mcp_server_config,
    read_mcp_server_configs,
)
from investorch.tools.mcp import list_mcp_servers
from investorch.web.errors import install_error_handlers
from tests.support.web import open_test_web


def test_public_server_masks_credentials_and_preserves_secret_references() -> None:
    server = {
        "name": "research",
        "transport": "streamable_http",
        "url": "https://user:password@part@host.test/mcp?token=url-token&key=${URL_KEY}#fragment-token",
        "headers": {"Authorization": "Bearer literal-token", "X-Key": "${HEADER_KEY}"},
    }
    public = public_mcp_server_config(server)
    assert public["url"] == f"https://{REDACTED}@host.test/mcp?token={REDACTED}&key=${{URL_KEY}}#{REDACTED}"
    assert public["headers"] == {"Authorization": REDACTED, "X-Key": "${HEADER_KEY}"}
    assert server["headers"]["Authorization"] == "Bearer literal-token"


def test_explicit_create_and_update_preserve_neighbors_and_hidden_values(tmp_path: Path) -> None:
    path = tmp_path / "mcp.toml"
    first = configure_mcp_server_config(
        path,
        "first",
        url="https://first.test/mcp?token=secret",
        headers={"Authorization": "secret"},
        operation="create",
    )
    configure_mcp_server_config(path, "neighbor", url="https://neighbor.test", operation="create")
    with pytest.raises(MCPServerExistsError):
        configure_mcp_server_config(path, "first", url="https://replacement.test", operation="create")
    with pytest.raises(MCPServerNotFoundError):
        configure_mcp_server_config(path, "missing", url="https://replacement.test", operation="update")
    configure_mcp_server_config(path, "first", enabled=False, operation="update")
    servers = read_mcp_server_configs(path)
    assert servers[0] == {**first, "enabled": False}
    assert servers[1]["url"] == "https://neighbor.test"


@pytest.mark.parametrize(
    "changes",
    [
        {"url": "https://host.test?token=<redacted>"},
        {"headers": {"Authorization": "<redacted>"}},
        {"transport": "stdio"},
        {"timeout": float("inf")},
        {"timeout": float("nan")},
        {"timeout": True},
    ],
)
def test_invalid_updates_do_not_modify_mcp_file(tmp_path: Path, changes: dict) -> None:
    path = tmp_path / "mcp.toml"
    configure_mcp_server_config(path, "first", url="https://first.test/mcp")
    before = path.read_bytes()
    with pytest.raises((ValueError, TypeError, NotImplementedError)):
        configure_mcp_server_config(path, "first", **changes)
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_mcp_http_management_round_trip_preserves_hidden_credentials(tmp_path: Path) -> None:
    from investorch.web.mcp_routes import router

    async with open_test_web(tmp_path) as web:
        app = FastAPI()
        app.state.host = web.host
        install_error_handlers(app)
        app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/integrations/mcp")).json()["servers"] == []
            request = {
                "name": "research",
                "url": "https://user:password@host.test/mcp?token=url-token",
                "headers": {"Authorization": "header-token", "X-Key": "${TOKEN}"},
            }
            response = await client.post("/api/integrations/mcp", json=request)
            assert response.status_code == 201
            assert all(secret not in response.text for secret in ("password", "url-token", "header-token"))
            response = await client.patch("/api/integrations/mcp/research", json={"enabled": False})
            assert response.status_code == 200
            saved = read_mcp_server_configs(web.host.config.mcp_config_path)[0]
            assert saved["url"] == request["url"]
            assert saved["headers"] == request["headers"]
            listed = (await client.get("/api/integrations/mcp")).json()["servers"][0]
            assert listed["enabled"] is False
            assert listed["headers"]["Authorization"] == REDACTED
            context = ToolContext(
                context=AgentContext(
                    config=web.host.config,
                    execution=web.host.execution,
                    session_id="mcp-test",
                    run_id="mcp-test",
                    portfolios=web.host.portfolios,
                ),
                tool_name="list_mcp_servers",
                tool_call_id="mcp-list",
                tool_arguments="{}",
            )
            agent_result = await list_mcp_servers.on_invoke_tool(context, "{}")
            assert agent_result["servers"][0]["name"] == "research"
            assert agent_result["servers"][0]["enabled"] is False
            assert all(secret not in str(agent_result) for secret in ("password", "url-token", "header-token"))
            assert (await client.post("/api/integrations/mcp", json=request)).status_code == 409
            assert (await client.patch("/api/integrations/mcp/missing", json={"enabled": True})).status_code == 404
            assert (
                await client.request("DELETE", "/api/integrations/mcp/research", json={"confirm": False})
            ).status_code == 400
            response = await client.request("DELETE", "/api/integrations/mcp/research", json={"confirm": True})
            assert response.json()["removed"] is True
            assert read_mcp_server_configs(web.host.config.mcp_config_path) == []


@pytest.mark.asyncio
async def test_mcp_http_errors_never_echo_supplied_or_stored_credentials(tmp_path: Path) -> None:
    from investorch.web.mcp_routes import router

    async with open_test_web(tmp_path) as web:
        app = FastAPI()
        app.state.host = web.host
        install_error_handlers(app)
        app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/integrations/mcp",
                json={
                    "name": "bad",
                    "url": "https://host.test?token=url-token",
                    "transport": "secret-invalid-transport",
                    "headers": {"Authorization": "header-token"},
                },
            )
            assert response.status_code == 422
            assert all(
                value not in response.text for value in ("url-token", "header-token", "secret-invalid-transport")
            )
            response = await client.post(
                "/api/integrations/mcp",
                json={
                    "name": "bad",
                    "url": "https://host.test?token=url-token",
                    "timeout": "header-token",
                },
            )
            assert response.status_code == 422
            assert "header-token" not in response.text
            web.host.config.mcp_config_path.write_text('[[servers]]\nname="bad"\nurl="broken-literal-secret\n')
            response = await client.get("/api/integrations/mcp")
            assert response.status_code == 422
            assert "broken-literal-secret" not in response.text
