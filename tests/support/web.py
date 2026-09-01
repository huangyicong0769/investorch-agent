from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

import httpx
from agents import Agent

from qmt_agent.application import ApprovalCoordinator, ApplicationHost
from qmt_agent.application.presentation_state import SessionPresentationStore
from qmt_agent.application.sessions import SessionOperations
from qmt_agent.context import AppState
from qmt_agent.web.approvals import WebApprovalBroker
from qmt_agent.web.connections import WebConnectionHub
from qmt_agent.web.events import WebEventBridge
from qmt_agent.web.server import create_web_app
from tests.support.runtime import RuntimeHarness, make_runtime_harness


@dataclass(slots=True)
class WebHarness:
    runtime: RuntimeHarness
    host: ApplicationHost
    client: httpx.AsyncClient


@asynccontextmanager
async def open_test_web(tmp_path: Path) -> AsyncIterator[WebHarness]:
    runtime = make_runtime_harness(tmp_path)
    presentation_state = SessionPresentationStore()
    sessions = SessionOperations(
        config=runtime.config,
        runtime=runtime.runtime,
        journal=runtime.journal,
        presentation_state=presentation_state,
    )

    async def approve_manually(_request: object, _reason: str | None) -> bool:
        return True

    approvals = ApprovalCoordinator(
        config=runtime.config,
        permission_agent=Agent(name="Unused Permission Agent", instructions="Unused in Web contract tests."),
        journal=runtime.journal,
        manual_handler=approve_manually,
    )
    state = AppState(
        config=runtime.config,
        execution=runtime.execution,
        selected_session_id="web-sessionless",
        main_reasoning_effort="none",
        permission_mode="manual",
    )
    host = ApplicationHost(
        config=runtime.config,
        state=state,
        execution=state.execution,
        journal=runtime.journal,
        runtime=runtime.runtime,
        sessions=sessions,
        approvals=approvals,
        activity=None,
        presentation_state=presentation_state,
        session_lifecycle_lock=asyncio.Lock(),
        initial_session_id=None,
    )
    connections = WebConnectionHub()
    broker = WebApprovalBroker(WebEventBridge(connections))
    app = create_web_app(runtime.config)
    app.state.host = host
    app.state.approval_broker = broker
    app.state.connections = connections
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    try:
        yield WebHarness(runtime=runtime, host=host, client=client)
    finally:
        await client.aclose()
        broker.close()
        await connections.aclose()
        await runtime.runtime.aclose()
