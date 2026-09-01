from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from agents import set_tracing_disabled
from fastapi import FastAPI

from investorch.application import open_application_host
from investorch.config import AppConfig, load_config
from investorch.initializer import initialize
from investorch.log import configure_logging

from .approvals import WebApprovalBroker
from .assets import install_webui_routes
from .connections import WebConnectionHub, websocket_router
from .errors import install_error_handlers
from .events import WebEventBridge
from .routes import APPLICATION_VERSION, router

logger = logging.getLogger(__name__)

WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 1334


def create_web_app(config: AppConfig) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info("Web application lifespan starting")
        connections = WebConnectionHub()
        events = WebEventBridge(connections)
        approval_broker = WebApprovalBroker(events)
        app.state.connections = connections
        app.state.events = events
        app.state.approval_broker = approval_broker
        try:
            async with open_application_host(
                config,
                manual_approval_handler=approval_broker.request,
                callbacks=events.application_callbacks(),
                create_initial_session=False,
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
            approval_broker.close()
            app.state.connections = None
            app.state.events = None
            app.state.approval_broker = None
            logger.info("Web application lifespan stopped")

    app = FastAPI(title="InvestOrch Agent", version=APPLICATION_VERSION, lifespan=lifespan)
    app.state.host = None
    app.state.connections = None
    app.state.events = None
    app.state.approval_broker = None
    install_error_handlers(app)
    app.include_router(router)
    app.include_router(websocket_router)
    install_webui_routes(app)
    return app


def run_web(*, port: int = DEFAULT_WEB_PORT) -> None:
    config = load_config()
    set_tracing_disabled(not config["observability.sdk_tracing_enabled"])
    initialized = initialize(config)
    configure_logging(config)
    logger.info("InvestOrch Agent Web started")

    try:
        if initialized:
            logger.info("First initialization completed at %s", config.root)
            print(
                f"InvestOrch Agent initialized at {config.root}\n"
                f"Please configure required secrets in {config.root_config_path} before starting InvestOrch Agent Web."
            )
            return

        url = f"http://{WEB_HOST}:{port}"
        logger.info("Starting local Web server at %s", url)
        print(f"InvestOrch Agent Web: {url}")
        uvicorn.run(create_web_app(config), host=WEB_HOST, port=port, access_log=False)
    finally:
        logger.info("InvestOrch Agent Web stopped")
