from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from agents import set_tracing_disabled
from fastapi import FastAPI

from qmt_agent.application import open_application_host
from qmt_agent.config import AppConfig, load_config
from qmt_agent.initializer import initialize
from qmt_agent.log import configure_logging
from qmt_agent.runtime import ApprovalRequest

from .connections import WebConnectionHub, websocket_router
from .errors import install_error_handlers
from .events import WebEventBridge
from .routes import APPLICATION_VERSION, router

logger = logging.getLogger(__name__)

WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 1334


async def _approval_unavailable(_request: ApprovalRequest, _review_reason: str | None) -> bool:
    raise RuntimeError("Browser approval handling is not available")


def create_web_app(config: AppConfig) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info("Web application lifespan starting")
        connections = WebConnectionHub()
        events = WebEventBridge(connections)
        app.state.connections = connections
        app.state.events = events
        try:
            async with open_application_host(
                config,
                manual_approval_handler=_approval_unavailable,
                callbacks=events.application_callbacks(),
            ) as host:
                app.state.host = host
                logger.info("Web application host ready")
                try:
                    yield
                finally:
                    app.state.host = None
                    await connections.aclose()
        finally:
            await connections.aclose()
            app.state.connections = None
            app.state.events = None
            logger.info("Web application lifespan stopped")

    app = FastAPI(title="QMT Agent", version=APPLICATION_VERSION, lifespan=lifespan)
    app.state.host = None
    app.state.connections = None
    app.state.events = None
    install_error_handlers(app)
    app.include_router(router)
    app.include_router(websocket_router)
    return app


def run_web(*, port: int = DEFAULT_WEB_PORT) -> None:
    config = load_config()
    set_tracing_disabled(not config["observability.sdk_tracing_enabled"])
    initialized = initialize(config)
    configure_logging(config)
    logger.info("QMT Agent Web started")

    try:
        if initialized:
            logger.info("First initialization completed at %s", config.root)
            print(
                f"QMT Agent initialized at {config.root}\n"
                f"Please configure required secrets in {config.root_config_path} before starting QMT Agent Web."
            )
            return

        url = f"http://{WEB_HOST}:{port}"
        logger.info("Starting local Web server at %s", url)
        print(f"QMT Agent Web: {url}")
        uvicorn.run(create_web_app(config), host=WEB_HOST, port=port, access_log=False)
    finally:
        logger.info("QMT Agent Web stopped")
