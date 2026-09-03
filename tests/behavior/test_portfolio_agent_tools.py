from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from agents.tool import FunctionTool
from agents.tool_context import ToolContext

from investorch.application import PortfolioOperations
from investorch.context import AgentContext, ExecutionState
from investorch.portfolio import (
    CashAdjustment,
    CashFlow,
    Income,
    InstrumentId,
    OpeningPosition,
    PositionAdjustment,
    Trade,
    TradeSide,
)
from investorch.tools import (
    adjust_portfolio_cash,
    adjust_portfolio_position,
    archive_portfolio,
    create_portfolio,
    get_portfolio,
    get_portfolio_ledger,
    initialize_portfolio,
    list_portfolios,
    record_portfolio_cash_flow,
    record_portfolio_income,
    record_portfolio_trade,
    restore_portfolio,
    update_portfolio,
)
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


async def test_approved_lifecycle_tools_preserve_update_and_clear_semantics(tmp_path: Path) -> None:
    context = make_tool_context(tmp_path)
    mutations = (
        create_portfolio,
        update_portfolio,
        archive_portfolio,
        restore_portfolio,
        initialize_portfolio,
    )
    assert all(tool.needs_approval is True for tool in mutations)
    assert all(
        field not in create_portfolio.params_json_schema["properties"]
        for field in ("source", "external_ref", "operation_id", "entry_id", "sequence", "recorded_at")
    )

    created = await invoke(create_portfolio, context, name="Core", base_currency="CNY")
    assert isinstance(created, dict)
    portfolio_id = created["portfolio"]["portfolio_id"]

    configured = await invoke(
        update_portfolio,
        context,
        portfolio_id=portfolio_id,
        name="Renamed",
        description="Long-term",
        strategy_source_path="strategies/value.py",
        strategy_parameters_json='{"lookback":20}',
    )
    unchanged = await invoke(update_portfolio, context, portfolio_id=portfolio_id)
    cleared = await invoke(
        update_portfolio,
        context,
        portfolio_id=portfolio_id,
        clear_description=True,
        clear_strategy_binding=True,
    )

    assert isinstance(configured, dict)
    assert configured["portfolio"]["name"] == "Renamed"
    assert configured["portfolio"]["description"] == "Long-term"
    assert configured["portfolio"]["strategy_binding"] == {
        "source_path": "strategies/value.py",
        "parameters": {"lookback": 20},
    }
    assert isinstance(unchanged, dict)
    assert unchanged["portfolio"] == configured["portfolio"]
    assert isinstance(cleared, dict)
    assert cleared["portfolio"]["description"] is None
    assert cleared["portfolio"]["strategy_binding"] is None

    archived = await invoke(archive_portfolio, context, portfolio_id=portfolio_id)
    restored = await invoke(restore_portfolio, context, portfolio_id=portfolio_id)
    assert isinstance(archived, dict)
    assert archived["portfolio"]["status"] == "ARCHIVED"
    assert isinstance(restored, dict)
    assert restored["portfolio"]["status"] == "ACTIVE"


async def test_initialize_tool_uses_strict_exact_inputs_and_agent_audit_metadata(tmp_path: Path) -> None:
    context = make_tool_context(tmp_path)
    portfolio = await context.context.portfolios.create(name="Core", base_currency="CNY")
    effective_at = "2026-09-03T10:30:00+08:00"

    result = await invoke(
        initialize_portfolio,
        context,
        portfolio_id=portfolio.id,
        cash="0.1",
        positions=[
            {"code": "600519", "market": "XSHG", "quantity": "100", "total_cost": "152345.00"},
            {"code": "000001", "market": "XSHE", "quantity": "0.03", "total_cost": None},
        ],
        effective_at=effective_at,
    )

    assert initialize_portfolio.strict_json_schema is True
    assert isinstance(result, dict)
    assert result["states"][portfolio.id]["cash"] == {"CNY": "0.1"}
    assert result["states"][portfolio.id]["holdings"][0]["total_cost"] is None
    assert result["states"][portfolio.id]["holdings"][1]["total_cost"] == "152345.00"
    ledger = await context.context.portfolios.list_ledger(portfolio.id)
    assert {entry.source for entry in ledger} == {"agent"}
    assert {entry.external_ref for entry in ledger} == {None}
    assert {entry.effective_at.isoformat() for entry in ledger} == {effective_at}

    naive = await invoke(
        initialize_portfolio,
        context,
        portfolio_id=portfolio.id,
        cash="1",
        positions=None,
        effective_at="2026-09-03T10:30:00",
    )
    assert isinstance(naive, str)
    assert "timezone-aware" in naive


async def test_approved_economic_tools_record_exact_agent_facts(tmp_path: Path) -> None:
    context = make_tool_context(tmp_path)
    portfolio = await context.context.portfolios.create(name="Core", base_currency="CNY")
    mutations = (
        record_portfolio_trade,
        record_portfolio_cash_flow,
        record_portfolio_income,
        adjust_portfolio_position,
        adjust_portfolio_cash,
    )
    assert all(tool.needs_approval is True for tool in mutations)

    trade = await invoke(
        record_portfolio_trade,
        context,
        portfolio_id=portfolio.id,
        code="600519",
        market="XSHG",
        side="BUY",
        quantity="0.03",
        price="1523.45",
        commission="0.01",
    )
    cash_flow = await invoke(record_portfolio_cash_flow, context, portfolio_id=portfolio.id, amount="2000.03")
    income = await invoke(
        record_portfolio_income,
        context,
        portfolio_id=portfolio.id,
        gross_amount="10.03",
        tax="0.01",
        code="600519",
        market="XSHG",
    )
    position = await invoke(
        adjust_portfolio_position,
        context,
        portfolio_id=portfolio.id,
        code="600519",
        market="XSHG",
        resulting_quantity="0.03",
        resulting_total_cost=None,
        reason="broker reconciliation",
    )
    cash = await invoke(
        adjust_portfolio_cash,
        context,
        portfolio_id=portfolio.id,
        resulting_amount="1964.3255",
        reason="broker reconciliation",
        effective_at="2026-09-03T15:00:00+08:00",
    )

    assert all(isinstance(result, dict) for result in (trade, cash_flow, income, position, cash))
    ledger = await context.context.portfolios.list_ledger(portfolio.id)
    assert {entry.source for entry in ledger} == {"agent"}
    assert {entry.external_ref for entry in ledger} == {None}
    assert [entry.payload for entry in ledger] == [
        Trade(STOCK, TradeSide.BUY, Decimal("0.03"), Decimal("1523.45"), Decimal("0.01")),
        CashFlow("CNY", Decimal("2000.03")),
        Income("CNY", Decimal("10.03"), Decimal("0.01"), Decimal("0"), STOCK),
        PositionAdjustment(STOCK, Decimal("0.03"), None, "broker reconciliation"),
        CashAdjustment("CNY", Decimal("1964.3255"), "broker reconciliation"),
    ]


async def test_economic_tools_reject_ambiguous_or_inexact_inputs(tmp_path: Path) -> None:
    context = make_tool_context(tmp_path)
    portfolio = await context.context.portfolios.create(name="Core", base_currency="CNY")

    partial_instrument = await invoke(
        record_portfolio_income,
        context,
        portfolio_id=portfolio.id,
        gross_amount="1",
        code="600519",
    )
    malformed_decimal = await invoke(
        record_portfolio_cash_flow,
        context,
        portfolio_id=portfolio.id,
        amount="not-a-decimal",
    )
    naive_time = await invoke(
        adjust_portfolio_cash,
        context,
        portfolio_id=portfolio.id,
        resulting_amount="1",
        reason="reconcile",
        effective_at="2026-09-03T15:00:00",
    )

    assert isinstance(partial_instrument, str)
    assert "code and market" in partial_instrument
    assert isinstance(malformed_decimal, str)
    assert "valid decimal string" in malformed_decimal
    assert isinstance(naive_time, str)
    assert "timezone-aware" in naive_time
    assert await context.context.portfolios.list_ledger(portfolio.id) == []
