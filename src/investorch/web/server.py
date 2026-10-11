from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import uvicorn
from agents import set_tracing_disabled
from fastapi import FastAPI

from investorch.application import open_application_host
from investorch.config import AppConfig, ConfigError, load_config
from investorch.config_schema import AppConfigSchema
from investorch.diagnostics import doctor_checks
from investorch.initializer import initialize
from investorch.log import configure_logging

from .approvals import WebApprovalBroker
from .assets import install_webui_routes
from .config_routes import router as config_router
from .connections import WebConnectionHub, websocket_router
from .errors import APIError, install_error_handlers
from .events import WebEventBridge
from .mcp_routes import router as mcp_router
from .routes import APPLICATION_VERSION, router
from .security import install_http_security
from .setup_routes import router as setup_router
from .skill_routes import router as skill_router
from .system_routes import router as system_router

logger = logging.getLogger(__name__)
WEB_HOST = "127.0.0.1"


def _recovery_reason(config: AppConfig) -> str | None:
    for check in doctor_checks(config.root):
        if check["code"] not in {"config", "sessions", "portfolio_schema", "filesystem"}:
            continue
        if check["status"] == "error":
            return f"{check['summary']}. {check['remediation']} Run investorch doctor for details."
        if check["code"] == "portfolio_schema" and check.get("source_schema_version") in (3, 4, 5):
            return "This Portfolio database requires explicit migration. Stop InvestOrch, back up offline, then run investorch migrate portfolio."
    return None


def create_web_app(
    config: AppConfig | None = None, *, root: Path | None = None, recovery_error: str | None = None
) -> FastAPI:
    instance_root = config.root if config is not None else (root or Path.home() / ".investorch").resolve()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        connections = WebConnectionHub(
            queue_capacity=AppConfigSchema().web.connection_queue_capacity
            if config is None
            else config["web.connection_queue_capacity"]
        )
        events = WebEventBridge(connections)
        broker = WebApprovalBroker(events)
        app.state.connections = connections
        app.state.events = events
        app.state.approval_broker = broker
        startup_lock = asyncio.Lock()
        async with AsyncExitStack() as stack:

            async def start_host() -> None:
                async with startup_lock:
                    if app.state.host is not None:
                        return
                    if app.state.recovery_error:
                        raise APIError(409, "recovery_required", app.state.recovery_error)
                    try:
                        configured = await asyncio.to_thread(load_config, instance_root)
                    except (ConfigError, OSError):
                        raise APIError(
                            409, "recovery_required", "Configuration cannot be loaded. Run investorch doctor."
                        ) from None
                    missing = configured.readiness()
                    if missing:
                        raise APIError(
                            422,
                            "setup_incomplete",
                            "Complete the required model settings and credentials.",
                            details={"missing": missing},
                        )
                    app.state.config = configured
                    try:
                        host = await stack.enter_async_context(
                            open_application_host(
                                configured,
                                manual_approval_handler=broker.request,
                                callbacks=events.application_callbacks(),
                                create_initial_session=False,
                            )
                        )
                    except Exception as exc:
                        logger.error("Application host startup failed (%s)", type(exc).__name__)
                        app.state.startup_error = "The application could not start. Saved configuration was retained; inspect System Status and retry."
                        raise APIError(503, "host_start_failed", app.state.startup_error) from None
                    app.state.host = host
                    app.state.startup_error = None

            app.state.start_host = start_host
            try:
                if not app.state.recovery_error:
                    try:
                        loaded = app.state.config or await asyncio.to_thread(load_config, instance_root, migrate=False)
                        reason = await asyncio.to_thread(_recovery_reason, loaded)
                        if reason:
                            app.state.recovery_error = reason
                        else:
                            loaded = await asyncio.to_thread(load_config, instance_root)
                            await asyncio.to_thread(initialize, loaded)
                            app.state.config = loaded
                            set_tracing_disabled(not loaded["observability.sdk_tracing_enabled"])
                            configure_logging(loaded)
                            if not loaded.readiness():
                                await start_host()
                    except APIError:
                        pass  # Keep the saved setup editable after a failed Host start.
                    except Exception as exc:
                        logger.error("Instance initialization failed (%s)", type(exc).__name__)
                        app.state.recovery_error = (
                            "The instance cannot be initialized safely. Preserve its files and run investorch doctor."
                        )
                yield
            finally:
                app.state.host = None
                await connections.aclose()
                broker.close()
        app.state.connections = None
        app.state.events = None
        app.state.approval_broker = None

    app = FastAPI(title="InvestOrch Agent", version=APPLICATION_VERSION, lifespan=lifespan)
    app.state.config = config
    app.state.root = instance_root
    app.state.recovery_error = recovery_error
    app.state.startup_error = None
    app.state.host = None
    app.state.connections = None
    app.state.events = None
    app.state.approval_broker = None
    install_error_handlers(app)
    install_http_security(app)
    app.include_router(router)
    app.include_router(config_router)
    app.include_router(setup_router)
    app.include_router(system_router)
    app.include_router(mcp_router)
    app.include_router(skill_router)
    app.include_router(websocket_router)
    install_webui_routes(app)
    return app


def run_web(*, port: int | None = None) -> None:
    root = Path.home() / ".investorch"
    try:
        config = load_config(root=root, migrate=False)
        recovery_error = None
    except (ConfigError, OSError):
        config = None
        recovery_error = "Configuration cannot be loaded. Preserve investorch.toml and run investorch doctor."
    effective_port = (
        port if port is not None else config["web.default_port"] if config else AppConfigSchema().web.default_port
    )
    print(f"InvestOrch Agent Web: http://{WEB_HOST}:{effective_port}")
    uvicorn.run(
        create_web_app(config, root=root, recovery_error=recovery_error),
        host=WEB_HOST,
        port=effective_port,
        access_log=False,
    )
