from __future__ import annotations

from pathlib import Path

import pytest
from agents import Agent, SQLiteSession
from agents.testing import ScriptedModel, assistant_message

from investorch.agents import AgentLoop, ApprovalOutcome, TokenUsage
from investorch.application import PortfolioOperations
from investorch.context import AgentContext, ExecutionState
from investorch.journal import read_session_journal
from investorch.output import OutputEvent
from investorch.runtime.control import RunControl
from investorch.storage import create_session, set_session_title
from tests.support.config import make_test_config
from tests.support.runtime import make_runtime_harness, run_options


@pytest.mark.asyncio
async def test_agent_loop_keeps_application_context_separate_from_visible_user_input(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    create_session(config.sessions_db, "session-a")
    set_session_title(config.sessions_db, "session-a", "Test")
    model = ScriptedModel(((assistant_message("done"),),))
    agent = Agent[AgentContext](name="Main", instructions="test", model=model)
    unused_agent = Agent(name="Unused", instructions="test", model=ScriptedModel())
    loop = AgentLoop(agent, unused_agent, unused_agent, config, PortfolioOperations(config=config))
    session = SQLiteSession("session-a", config.sessions_db)

    async def approval_handler(_head_seq: int | None, _tool_name: str, _arguments: str | None) -> ApprovalOutcome:
        return ApprovalOutcome(approved=True, usage=TokenUsage())

    async def output_handler(_event: OutputEvent) -> None:
        pass

    try:
        await loop.run(
            "Why is the cost unknown?",
            session,
            ExecutionState(workspace_root=config.workspace_dir),
            run_id="run-a",
            session_id="session-a",
            reasoning_effort="none",
            approval_handler=approval_handler,
            output_handler=output_handler,
            run_control=RunControl("session-a", "run-a", lambda: None),
            application_instruction="The selected Portfolio ID is portfolio-a. This establishes identity only.",
        )
        history = await session.get_items()
    finally:
        session.close()

    assert history[0] == {
        "role": "developer",
        "content": "The selected Portfolio ID is portfolio-a. This establishes identity only.",
    }
    assert history[1] == {"role": "user", "content": "Why is the cost unknown?"}
    model.assert_complete()


@pytest.mark.asyncio
async def test_runtime_journals_only_the_user_authored_part_of_contextual_input(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)

    harness.runtime.start_contextual_run(
        "session-a",
        "Why is the cost unknown?",
        "The selected Portfolio ID is portfolio-a. This establishes identity only.",
        run_options(),
    )
    await harness.agent_loop.wait_until_started("session-a")

    records = read_session_journal(harness.config.session_journal_dir, "session-a")
    assert [(record["type"], record.get("text")) for record in records] == [
        ("user_message", "Why is the cost unknown?")
    ]

    harness.agent_loop.complete("session-a")
    await harness.wait_for_run_ended("session-a")
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_application_only_run_creates_no_user_authorization_evidence(tmp_path: Path) -> None:
    harness = make_runtime_harness(tmp_path)

    harness.runtime.start_application_run(
        "session-a",
        "Guide the user through creating a Portfolio.",
        run_options(),
    )
    await harness.agent_loop.wait_until_started("session-a")

    assert await harness.journal.session_exists("session-a") is False

    harness.agent_loop.complete("session-a")
    await harness.wait_for_run_ended("session-a")
    await harness.runtime.aclose()


@pytest.mark.asyncio
async def test_agent_loop_snapshots_mcp_servers_for_each_run(tmp_path: Path, monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from agents.mcp import MCPServerStreamableHttp
    from mcp.types import Tool

    config = make_test_config(tmp_path)
    create_session(config.sessions_db, "session-a")
    set_session_title(config.sessions_db, "session-a", "Test")
    server = MCPServerStreamableHttp(params={"url": "http://unused/mcp"}, name="qmt")
    monkeypatch.setattr(
        server,
        "list_tools",
        AsyncMock(
            return_value=[
                Tool(name="get_status", inputSchema={"type": "object", "properties": {}}),
            ]
        ),
    )
    current_servers = []
    captured_agents = []
    from investorch.agents import loop as loop_module

    original_run_streamed = loop_module.Runner.run_streamed

    def capture_run(agent, *args, **kwargs):
        captured_agents.append(agent)
        return original_run_streamed(agent, *args, **kwargs)

    monkeypatch.setattr(loop_module.Runner, "run_streamed", capture_run)
    model = ScriptedModel(((assistant_message("first"),), (assistant_message("second"),)))
    agent = Agent[AgentContext](name="Main", instructions="test", model=model)
    unused = Agent(name="Unused", model=ScriptedModel())
    loop = AgentLoop(
        agent,
        unused,
        unused,
        config,
        PortfolioOperations(config=config),
        mcp_servers_provider=lambda: current_servers,
    )
    session = SQLiteSession("session-a", config.sessions_db)

    async def approve(*_args):
        return ApprovalOutcome(approved=True, usage=TokenUsage())

    async def output(_event):
        pass

    try:
        for run_id in ("first", "second"):
            await loop.run(
                "status",
                session,
                ExecutionState(workspace_root=config.workspace_dir),
                run_id=run_id,
                session_id="session-a",
                reasoning_effort="none",
                approval_handler=approve,
                output_handler=output,
                run_control=RunControl("session-a", run_id, lambda: None),
            )
            if run_id == "first":
                current_servers.append(server)
    finally:
        session.close()

    assert captured_agents[0].mcp_servers == []
    assert captured_agents[1].mcp_servers == [server]
    assert agent.mcp_servers == []
    assert [tool.name for tool in model.calls[0].tools] == []
    assert [tool.name for tool in model.calls[1].tools] == ["get_status"]
