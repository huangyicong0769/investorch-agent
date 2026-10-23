from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
from agents import Agent

from investorch.application import (
    ApplicationHost,
    ApprovalCoordinator,
    PortfolioOperations,
    PortfolioSessionWorkflows,
)
from investorch.application.presentation_state import SessionPresentationStore
from investorch.application.sessions import SessionOperations
from investorch.application.skill_sessions import SkillSessionWorkflows
from investorch.application.skills import SkillOperations
from investorch.context import AppState
from investorch.llm.oauth import ChatGPTAuth
from investorch.web.approvals import WebApprovalBroker
from investorch.web.connections import WebConnectionHub
from investorch.web.events import WebEventBridge
from investorch.web.server import create_web_app
from tests.support.runtime import RuntimeHarness, make_runtime_harness


@dataclass(slots=True)
class WebHarness:
    runtime: RuntimeHarness
    host: ApplicationHost
    client: httpx.AsyncClient


@asynccontextmanager
async def open_test_web(
    tmp_path: Path,
    config_overrides: dict[str, dict[str, object]] | None = None,
    *,
    raise_app_exceptions: bool = True,
) -> AsyncIterator[WebHarness]:
    runtime = make_runtime_harness(tmp_path, config_overrides=config_overrides)
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
        session_models=runtime.runtime.session_models,
        permission_mode="manual",
    )
    portfolios = PortfolioOperations(config=runtime.config)
    skills = SkillOperations(config=runtime.config)
    host = ApplicationHost(
        config=runtime.config,
        state=state,
        execution=state.execution,
        journal=runtime.journal,
        runtime=runtime.runtime,
        sessions=sessions,
        portfolios=portfolios,
        skills=skills,
        skill_sessions=SkillSessionWorkflows(state=state, runtime=runtime.runtime, sessions=sessions, skills=skills),
        portfolio_sessions=PortfolioSessionWorkflows(
            state=state,
            runtime=runtime.runtime,
            sessions=sessions,
            portfolios=portfolios,
        ),
        approvals=approvals,
        activity=None,
        presentation_state=presentation_state,
        initial_session_id=None,
    )
    connections = WebConnectionHub(queue_capacity=runtime.config["web.connection_queue_capacity"])
    broker = WebApprovalBroker(WebEventBridge(connections))
    app = create_web_app(runtime.config)
    app.state.chatgpt_auth = ChatGPTAuth(runtime.config)
    app.state.host = host
    app.state.approval_broker = broker
    app.state.connections = connections
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions),
        base_url="http://127.0.0.1",
    )
    try:
        yield WebHarness(runtime=runtime, host=host, client=client)
    finally:
        await client.aclose()
        await app.state.chatgpt_auth.close()
        broker.close()
        await connections.aclose()
        await runtime.runtime.aclose()
