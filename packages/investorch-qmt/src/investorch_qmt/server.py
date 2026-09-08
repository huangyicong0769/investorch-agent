from __future__ import annotations

import asyncio
import signal
import sqlite3
from contextlib import asynccontextmanager
from importlib.metadata import version

import uvicorn
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from mcp_types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from investorch_qmt.auth import BearerAuthMiddleware
from investorch_qmt.config import AppPaths, QMTConfig, default_paths
from investorch_qmt.execution.domain import ExecutionError
from investorch_qmt.execution.rest import register_routes
from investorch_qmt.execution.service import ExecutionNodeService
from investorch_qmt.log import close_logging, configure_logging

_SERVICE_NAME = "investorch-qmt"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ServiceError(RuntimeError):
    """The companion service cannot start or run."""


def create_mcp_server(transport_security: TransportSecuritySettings, service: ExecutionNodeService) -> MCPServer:
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
        description="Report companion, market-data, worker and trading readiness separately.",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    def get_status() -> dict[str, object]:
        status = service.get_node_status()
        status["service"].update(name=_SERVICE_NAME, version=version(_SERVICE_NAME))
        return status

    @server.tool(
        description="Start a Portfolio's staged strategy on real MiniQMT market data. All orders are rejected because trading is not enabled.",
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
        ),
        structured_output=True,
    )
    def start_live_strategy(portfolio_id: str, ctx: Context) -> dict[str, object]:
        try:
            return service.start_live_strategy(portfolio_id, (ctx.headers or {}).get("x-investorch-control-session"))
        except ExecutionError as exc:
            return exc.to_wire()

    @server.tool(
        description="Stop a Portfolio's live strategy worker. Requires approval in the Core MCP profile.",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False),
        structured_output=True,
    )
    def stop_live_strategy(portfolio_id: str, ctx: Context) -> dict[str, object]:
        try:
            return service.stop_live_strategy(portfolio_id, (ctx.headers or {}).get("x-investorch-control-session"))
        except ExecutionError as exc:
            return exc.to_wire()

    register_routes(server, service, security)

    return server


def create_app(
    config: QMTConfig, paths: AppPaths | None = None, service: ExecutionNodeService | None = None
) -> ASGIApp:
    transport_security = _transport_security(config)
    service = service or ExecutionNodeService(paths or default_paths())
    server = create_mcp_server(transport_security, service)
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        host=config.server.host,
        transport_security=transport_security,
    )
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        try:
            async with original_lifespan(application) as state:
                yield state
        finally:
            await asyncio.to_thread(service.close)

    app.router.lifespan_context = lifespan
    return BearerAuthMiddleware(app, config.auth.token)


def run_service(config: QMTConfig, paths: AppPaths) -> None:
    try:
        logger = configure_logging(config, paths)
    except OSError as exc:
        raise ServiceError(f"Cannot configure operational logging: {exc}") from exc

    logger.info(
        "service starting version=%s bind=%s:%d", version(_SERVICE_NAME), config.server.host, config.server.port
    )
    print(f"InvestOrch QMT {version(_SERVICE_NAME)}", flush=True)
    print(f"Listening on http://{config.server.host}:{config.server.port}", flush=True)
    if config.server.host not in _LOOPBACK_HOSTS:
        warning = (
            "LAN mode is intended for a trusted local network or private VPN. "
            "Do not expose this endpoint directly to the public Internet."
        )
        logger.warning(warning)
        print(warning, flush=True)

    try:
        app = create_app(config, paths=paths)
    except (ValueError, OSError, sqlite3.Error) as exc:
        close_logging(logger)
        raise ServiceError(f"Cannot initialize runtime storage: {exc}") from exc

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=config.server.host,
            port=config.server.port,
            access_log=False,
            log_config=None,
        )
    )
    previous_break_handler = None
    if hasattr(signal, "SIGBREAK"):
        previous_break_handler = signal.signal(signal.SIGBREAK, signal.default_int_handler)
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    except SystemExit as exc:
        if exc.code not in (None, 0):
            raise ServiceError(
                f"Cannot bind {config.server.host}:{config.server.port} or complete server startup."
            ) from None
    except OSError as exc:
        raise ServiceError(f"Cannot bind {config.server.host}:{config.server.port}: {exc}") from exc
    finally:
        if previous_break_handler is not None:
            signal.signal(signal.SIGBREAK, previous_break_handler)
        logger.info("service stopped")
        close_logging(logger)


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
