from __future__ import annotations

import signal
from importlib.metadata import version
from types import FrameType

import uvicorn
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from mcp_types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from investorch_qmt.auth import BearerAuthMiddleware
from investorch_qmt.config import AppPaths, QMTConfig
from investorch_qmt.log import close_logging, configure_logging

_SERVICE_NAME = "investorch-qmt"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ServiceError(RuntimeError):
    """The companion service cannot start or run."""


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

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(config),
            host=config.server.host,
            port=config.server.port,
            access_log=False,
            log_config=None,
        )
    )
    previous_break_handler = None
    if hasattr(signal, "SIGBREAK"):
        previous_break_handler = signal.signal(signal.SIGBREAK, _raise_keyboard_interrupt)
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


def _raise_keyboard_interrupt(_: int, __: FrameType | None) -> None:
    raise KeyboardInterrupt
