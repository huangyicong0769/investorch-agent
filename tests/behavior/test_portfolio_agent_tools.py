from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from agents.tool import FunctionTool
from agents.tool_context import ToolContext

from investorch.application import PortfolioOperations
from investorch.context import AgentContext, ExecutionState
from investorch.portfolio import InstrumentId, OpeningPosition
from investorch.tools import get_portfolio, get_portfolio_ledger, list_portfolios
from tests.support.config import make_test_config

STOCK = InstrumentId("600519", "XSHG")
UNKNOWN_STOCK = InstrumentId("000001", "XSHE")


def make_tool_context(tmp_path: Path) -> ToolContext[AgentContext]:
    config = make_test_config(tmp_path)
    portfolios = PortfolioOperations(config=config)
    return ToolContext(
        context=AgentContext(
            config=config,
            execution=ExecutionState(workspace_root=config.workspace_dir),
            session_id="session-a",
            run_id="run-a",
            portfolios=portfolios,
        ),
        tool_name="portfolio-behavior-test",
        tool_call_id="call-a",
        tool_arguments="{}",
    )


async def invoke(tool: FunctionTool, context: ToolContext[AgentContext], **arguments: object) -> object:
    return await tool.on_invoke_tool(context, json.dumps(arguments))


async def test_list_portfolios_is_read_only_and_filters_archived_metadata(tmp_path: Path) -> None:
    context = make_tool_context(tmp_path)

    assert list_portfolios.needs_approval is False
    assert await invoke(list_portfolios, context) == {"portfolios": []}

    active = await context.context.portfolios.create(name="Core", base_currency="CNY")
    archived = await context.context.portfolios.create(name="Old", base_currency="USD")
    await context.context.portfolios.archive(archived.id)

    active_result = await invoke(list_portfolios, context)
    all_result = await invoke(list_portfolios, context, include_archived=True)

    assert isinstance(active_result, dict)
    assert [item["portfolio_id"] for item in active_result["portfolios"]] == [active.id]
    assert isinstance(all_result, dict)
    assert {item["portfolio_id"] for item in all_result["portfolios"]} == {active.id, archived.id}
    assert all("holdings" not in item for item in all_result["portfolios"])


async def test_get_portfolio_returns_exact_deterministic_logical_state(tmp_path: Path) -> None:
    context = make_tool_context(tmp_path)
    portfolio = await context.context.portfolios.create(name="Core", base_currency="CNY")
    await context.context.portfolios.initialize(
        portfolio.id,
        cash=Decimal("1000.03"),
        positions=(
            OpeningPosition(STOCK, Decimal("100"), Decimal("152345.00")),
            OpeningPosition(UNKNOWN_STOCK, Decimal("0.1"), None),
        ),
        source="import",
    )

    result = await invoke(get_portfolio, context, portfolio_id=portfolio.id)

    assert isinstance(result, dict)
    assert result["portfolio"]["portfolio_id"] == portfolio.id
    assert result["state"]["cash"] == {"CNY": "1000.03"}
    assert result["state"]["holdings"] == [
        {
            "instrument": {"code": "000001", "market": "XSHE"},
            "quantity": "0.1",
            "total_cost": None,
            "average_cost": None,
        },
        {
            "instrument": {"code": "600519", "market": "XSHG"},
            "quantity": "100",
            "total_cost": "152345.00",
            "average_cost": "1523.45",
        },
    ]

    missing = await invoke(get_portfolio, context, portfolio_id="missing")
    assert isinstance(missing, str)
    assert "Portfolio not found: missing" in missing


async def test_get_portfolio_ledger_returns_newest_entries_with_explicit_truncation(tmp_path: Path) -> None:
    context = make_tool_context(tmp_path)
    portfolio = await context.context.portfolios.create(name="Core", base_currency="CNY")
    for amount in ("0.1", "0.03", "1523.45"):
        await context.context.portfolios.record_cash_flow(portfolio.id, amount=Decimal(amount), source="import")

    result = await invoke(get_portfolio_ledger, context, portfolio_id=portfolio.id, limit=2)
    default_result = await invoke(get_portfolio_ledger, context, portfolio_id=portfolio.id)

    assert isinstance(result, dict)
    assert result["portfolio_id"] == portfolio.id
    assert result["total"] == 3
    assert result["returned"] == 2
    assert result["has_older"] is True
    assert [entry["sequence"] for entry in result["entries"]] == [2, 3]
    assert [entry["payload"]["amount"] for entry in result["entries"]] == ["0.03", "1523.45"]
    assert isinstance(default_result, dict)
    assert default_result["returned"] == 3
    assert default_result["has_older"] is False

    invalid = await invoke(get_portfolio_ledger, context, portfolio_id=portfolio.id, limit=201)
    assert isinstance(invalid, str)
    assert "limit must be between 1 and 200" in invalid
