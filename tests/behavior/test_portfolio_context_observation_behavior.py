from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path

import pytest
from agents import Agent, SQLiteSession
from agents.testing import ScriptedModel, assistant_message, function_call
from agents.tool import FunctionTool

from investorch.agents import AgentLoop, ApprovalOutcome, TokenUsage
from investorch.application import PortfolioOperations
from investorch.application.portfolio_context import PortfolioContextOperations, PortfolioToolSucceededEvent
from investorch.config import AppConfig
from investorch.context import AgentContext, ExecutionState
from investorch.output import OutputEvent
from investorch.runtime.control import RunControl
from investorch.storage import create_session, set_session_title
from investorch.tools import (
    create_portfolio,
    get_portfolio,
    get_portfolio_ledger,
    list_portfolios,
    transfer_portfolio_cash,
)
from tests.support.config import make_test_config


async def run_scripted_tool(
    *,
    config: AppConfig,
    portfolios: PortfolioOperations,
    context: PortfolioContextOperations,
    session_id: str,
    tool: FunctionTool,
    arguments: Mapping[str, object],
    approve: bool = True,
) -> None:
    model = ScriptedModel(
        (
            (function_call(tool.name, arguments, call_id="call-a"),),
            (assistant_message("done"),),
        )
    )
    agent = Agent[AgentContext](name="Main", instructions="test", model=model, tools=[tool])
    unused_agent = Agent(name="Unused", instructions="test", model=ScriptedModel())
    loop = AgentLoop(
        agent,
        unused_agent,
        unused_agent,
        config,
        portfolios,
        successful_tool_handler=context.observe_successful_tool,
    )
    session = SQLiteSession(session_id, config.sessions_db)

    async def approval_handler(_user_input: str, _tool_name: str, _arguments: str | None) -> ApprovalOutcome:
        return ApprovalOutcome(approved=approve, usage=TokenUsage())

    async def output_handler(_event: OutputEvent) -> None:
        pass

    try:
        await loop.run(
            "test request",
            session,
            ExecutionState(workspace_root=config.workspace_dir),
            run_id="run-a",
            session_id=session_id,
            reasoning_effort="none",
            approval_handler=approval_handler,
            output_handler=output_handler,
            run_control=RunControl(session_id, "run-a", lambda: None),
        )
    finally:
        session.close()
    model.assert_complete()


def create_titled_session(config: AppConfig, session_id: str) -> None:
    create_session(config.sessions_db, session_id)
    set_session_title(config.sessions_db, session_id, "Test")


@pytest.mark.asyncio
async def test_successful_portfolio_tools_publish_typed_read_and_mutation_events(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    portfolios = PortfolioOperations(config=config)
    events: list[PortfolioToolSucceededEvent] = []

    async def capture(event: PortfolioToolSucceededEvent) -> None:
        events.append(event)

    context = PortfolioContextOperations(config=config, succeeded_handler=capture)
    portfolio = await portfolios.create(name="Core", base_currency="CNY")
    create_titled_session(config, "read")
    await run_scripted_tool(
        config=config,
        portfolios=portfolios,
        context=context,
        session_id="read",
        tool=get_portfolio,
        arguments={"portfolio_id": portfolio.id},
    )
    create_titled_session(config, "create")
    await run_scripted_tool(
        config=config,
        portfolios=portfolios,
        context=context,
        session_id="create",
        tool=create_portfolio,
        arguments={"name": "Growth", "base_currency": "USD"},
    )

    assert events[0] == PortfolioToolSucceededEvent(
        session_id="read",
        run_id="run-a",
        portfolio_ids=(portfolio.id,),
        mutated=False,
    )
    growth = next(item for item in await portfolios.list() if item.name == "Growth")
    assert events[1].session_id == "create"
    assert events[1].run_id == "run-a"
    assert events[1].portfolio_ids == (growth.id,)
    assert events[1].mutated is True


@pytest.mark.asyncio
async def test_only_successful_structured_portfolio_reads_establish_relation(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    portfolios = PortfolioOperations(config=config)
    context = PortfolioContextOperations(config=config)
    portfolio = await portfolios.create(name="Core", base_currency="CNY")

    create_titled_session(config, "success")
    await run_scripted_tool(
        config=config,
        portfolios=portfolios,
        context=context,
        session_id="success",
        tool=get_portfolio,
        arguments={"portfolio_id": portfolio.id},
    )
    assert await context.related_ids("success") == (portfolio.id,)

    create_titled_session(config, "ledger")
    await run_scripted_tool(
        config=config,
        portfolios=portfolios,
        context=context,
        session_id="ledger",
        tool=get_portfolio_ledger,
        arguments={"portfolio_id": portfolio.id},
    )
    assert await context.related_ids("ledger") == (portfolio.id,)

    create_titled_session(config, "failure")
    await run_scripted_tool(
        config=config,
        portfolios=portfolios,
        context=context,
        session_id="failure",
        tool=get_portfolio,
        arguments={"portfolio_id": "missing"},
    )
    assert await context.related_ids("failure") == ()

    create_titled_session(config, "browse")
    await run_scripted_tool(
        config=config,
        portfolios=portfolios,
        context=context,
        session_id="browse",
        tool=list_portfolios,
        arguments={},
    )
    assert await context.related_ids("browse") == ()


@pytest.mark.asyncio
async def test_approved_tool_resume_relates_created_portfolio_but_rejection_does_not(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    portfolios = PortfolioOperations(config=config)
    context = PortfolioContextOperations(config=config)

    create_titled_session(config, "approved")
    await run_scripted_tool(
        config=config,
        portfolios=portfolios,
        context=context,
        session_id="approved",
        tool=create_portfolio,
        arguments={"name": "Created", "base_currency": "CNY"},
    )
    created = await portfolios.list()
    assert len(created) == 1
    assert await context.related_ids("approved") == (created[0].id,)

    create_titled_session(config, "rejected")
    await run_scripted_tool(
        config=config,
        portfolios=portfolios,
        context=context,
        session_id="rejected",
        tool=create_portfolio,
        arguments={"name": "Rejected", "base_currency": "CNY"},
        approve=False,
    )
    assert [portfolio.name for portfolio in await portfolios.list()] == ["Created"]
    assert await context.related_ids("rejected") == ()


@pytest.mark.asyncio
async def test_successful_transfer_relates_source_then_destination(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    portfolios = PortfolioOperations(config=config)
    context = PortfolioContextOperations(config=config)
    source = await portfolios.create(name="Source", base_currency="CNY")
    destination = await portfolios.create(name="Destination", base_currency="CNY")
    await portfolios.initialize(source.id, cash=Decimal("100"), source="test")
    create_titled_session(config, "transfer")

    await run_scripted_tool(
        config=config,
        portfolios=portfolios,
        context=context,
        session_id="transfer",
        tool=transfer_portfolio_cash,
        arguments={
            "source_portfolio_id": source.id,
            "destination_portfolio_id": destination.id,
            "amount": "25",
        },
    )

    assert await context.related_ids("transfer") == (source.id, destination.id)
