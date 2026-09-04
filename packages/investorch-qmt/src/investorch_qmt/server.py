from __future__ import annotations

from importlib.metadata import version

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from investorch_qmt.auth import BearerAuthMiddleware
from investorch_qmt.config import QMTConfig

_SERVICE_NAME = "investorch-qmt"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def create_mcp_server() -> MCPServer:
    server = MCPServer(name=_SERVICE_NAME, version=version(_SERVICE_NAME))

    @server.custom_route("/healthz", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "service": _SERVICE_NAME,
                "version": version(_SERVICE_NAME),
                "mcp": {"status": "ready"},
            }
        )

    return server


def create_app(config: QMTConfig) -> ASGIApp:
    server = create_mcp_server()
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        host=config.server.host,
        transport_security=_transport_security(config),
    )
    return BearerAuthMiddleware(app, config.auth.token)


def _transport_security(config: QMTConfig) -> TransportSecuritySettings | None:
    if config.server.host in _LOOPBACK_HOSTS:
        return None

    allowed_hosts = list(config.server.allowed_hosts)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=[origin for host in allowed_hosts for origin in (f"http://{host}", f"https://{host}")],
    )
