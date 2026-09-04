from __future__ import annotations

from importlib.metadata import version

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from mcp_types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from investorch_qmt.auth import BearerAuthMiddleware
from investorch_qmt.config import QMTConfig

_SERVICE_NAME = "investorch-qmt"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def create_mcp_server(transport_security: TransportSecuritySettings) -> MCPServer:
    server = MCPServer(name=_SERVICE_NAME, version=version(_SERVICE_NAME))
    security = TransportSecurityMiddleware(transport_security)

    @server.custom_route("/healthz", methods=["GET"])
    async def health(request: Request) -> Response:
        rejected = await security.validate_request(request)
        if rejected is not None:
            return rejected
        return JSONResponse(
            {
                "status": "ok",
                "service": _SERVICE_NAME,
                "version": version(_SERVICE_NAME),
                "mcp": {"status": "ready"},
            }
        )

    @server.tool(
        description="Report companion readiness and the truthful QMT connectivity state.",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    def get_status() -> dict[str, dict[str, str]]:
        return {
            "service": {"name": _SERVICE_NAME, "version": version(_SERVICE_NAME), "status": "ready"},
            "qmt": {"status": "not_connected", "reason": "QMT backend is not connected."},
        }

    return server


def create_app(config: QMTConfig) -> ASGIApp:
    transport_security = _transport_security(config)
    server = create_mcp_server(transport_security)
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        host=config.server.host,
        transport_security=transport_security,
    )
    return BearerAuthMiddleware(app, config.auth.token)


def _transport_security(config: QMTConfig) -> TransportSecuritySettings:
    if config.server.host in _LOOPBACK_HOSTS:
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        )

    allowed_hosts = list(config.server.allowed_hosts)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=[origin for host in allowed_hosts for origin in (f"http://{host}", f"https://{host}")],
    )
