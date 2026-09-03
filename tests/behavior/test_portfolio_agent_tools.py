from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from agents import Agent, ModelSettings
from agents.tool import FunctionTool
from agents.tool_context import ToolContext

from investorch.agents import AgentLoop
from investorch.agents.main import create_agent
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
    correct_portfolio_entry,
    create_portfolio,
    get_portfolio,
    get_portfolio_ledger,
    initialize_portfolio,
    list_portfolios,
    record_portfolio_cash_flow,
    record_portfolio_income,
    record_portfolio_trade,
    restore_portfolio,
    transfer_portfolio_cash,
    transfer_portfolio_position,
    update_portfolio,
)
from tests.support.config import make_test_config
from tests.support.web import open_test_web

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
    unattributed_income = await invoke(
        record_portfolio_income,
        context,
        portfolio_id=portfolio.id,
        gross_amount="0.03",
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

    assert all(isinstance(result, dict) for result in (trade, cash_flow, income, unattributed_income, position, cash))
    ledger = await context.context.portfolios.list_ledger(portfolio.id)
    assert {entry.source for entry in ledger} == {"agent"}
    assert {entry.external_ref for entry in ledger} == {None}
    assert [entry.payload for entry in ledger] == [
        Trade(STOCK, TradeSide.BUY, Decimal("0.03"), Decimal("1523.45"), Decimal("0.01")),
        CashFlow("CNY", Decimal("2000.03")),
        Income("CNY", Decimal("10.03"), Decimal("0.01"), Decimal("0"), STOCK),
        Income("CNY", Decimal("0.03"), Decimal("0"), Decimal("0"), None),
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
    malformed_time = await invoke(
        record_portfolio_cash_flow,
        context,
        portfolio_id=portfolio.id,
        amount="1",
        effective_at="not-a-timestamp",
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
    assert isinstance(malformed_time, str)
    assert "timezone-aware" in malformed_time
    assert isinstance(naive_time, str)
    assert "timezone-aware" in naive_time
    assert await context.context.portfolios.list_ledger(portfolio.id) == []


async def test_correction_tool_uses_a_strict_discriminated_replacement(tmp_path: Path) -> None:
    context = make_tool_context(tmp_path)
    portfolio = await context.context.portfolios.create(name="Core", base_currency="CNY")
    original = await context.context.portfolios.record_cash_flow(
        portfolio.id,
        amount=Decimal("10.03"),
        source="import",
    )

    result = await invoke(
        correct_portfolio_entry,
        context,
        portfolio_id=portfolio.id,
        target_entry_id=original.entries[0].entry_id,
        reason="amount was wrong",
        replacement={"type": "cash_flow", "amount": "12.03"},
    )

    replacement_schema = correct_portfolio_entry.params_json_schema["properties"]["replacement"]
    assert correct_portfolio_entry.needs_approval is True
    assert correct_portfolio_entry.strict_json_schema is True
    assert "discriminator" in replacement_schema
    assert isinstance(result, dict)
    assert result["states"][portfolio.id]["cash"] == {"CNY": "12.03"}
    ledger = await context.context.portfolios.list_ledger(portfolio.id)
    assert [entry.entry_type.value for entry in ledger] == ["CASH_FLOW", "VOID", "CASH_FLOW"]
    assert ledger[-1].payload == CashFlow("CNY", Decimal("12.03"))
    assert {entry.source for entry in ledger[1:]} == {"agent"}
    assert {entry.external_ref for entry in ledger[1:]} == {None}

    invalid = await invoke(
        correct_portfolio_entry,
        context,
        portfolio_id=portfolio.id,
        target_entry_id=ledger[-1].entry_id,
        reason="invalid shape",
        replacement={"type": "cash_flow", "amount": "1", "unexpected": "field"},
    )
    assert isinstance(invalid, str)
    assert "Invalid JSON input" in invalid
    invalid_type = await invoke(
        correct_portfolio_entry,
        context,
        portfolio_id=portfolio.id,
        target_entry_id=ledger[-1].entry_id,
        reason="invalid type",
        replacement={"type": "transfer", "amount": "1"},
    )
    assert isinstance(invalid_type, str)
    assert "Invalid JSON input" in invalid_type


async def test_correction_tool_rejects_one_sided_transfer_correction(tmp_path: Path) -> None:
    context = make_tool_context(tmp_path)
    source = await context.context.portfolios.create(name="Source", base_currency="CNY")
    destination = await context.context.portfolios.create(name="Destination", base_currency="CNY")
    await context.context.portfolios.initialize(source.id, cash=Decimal("100"), source="import")
    transfer = await context.context.portfolios.transfer_cash(
        source_portfolio_id=source.id,
        destination_portfolio_id=destination.id,
        amount=Decimal("10"),
        source="import",
    )

    result = await invoke(
        correct_portfolio_entry,
        context,
        portfolio_id=source.id,
        target_entry_id=next(entry.entry_id for entry in transfer.entries if entry.portfolio_id == source.id),
        reason="wrong transfer",
        replacement={"type": "cash_flow", "amount": "10"},
    )

    assert isinstance(result, str)
    assert "Direct Agent correction of Portfolio TRANSFER entries is not supported" in result
    assert len(await context.context.portfolios.list_ledger(source.id)) == 2
    assert len(await context.context.portfolios.list_ledger(destination.id)) == 1


async def test_approved_transfer_tools_mutate_both_portfolios_atomically(tmp_path: Path) -> None:
    context = make_tool_context(tmp_path)
    source = await context.context.portfolios.create(name="Source", base_currency="CNY")
    destination = await context.context.portfolios.create(name="Destination", base_currency="CNY")
    await context.context.portfolios.initialize(
        source.id,
        cash=Decimal("100.03"),
        positions=(
            OpeningPosition(STOCK, Decimal("10"), Decimal("15234.50")),
            OpeningPosition(UNKNOWN_STOCK, Decimal("1"), None),
        ),
        source="import",
    )

    known_cost_position = await invoke(
        transfer_portfolio_position,
        context,
        source_portfolio_id=source.id,
        destination_portfolio_id=destination.id,
        code="600519",
        market="XSHG",
        quantity="0.03",
        transferred_cost="45.7035",
    )
    unknown_cost_position = await invoke(
        transfer_portfolio_position,
        context,
        source_portfolio_id=source.id,
        destination_portfolio_id=destination.id,
        code="000001",
        market="XSHE",
        quantity="0.03",
        transferred_cost=None,
    )
    cash = await invoke(
        transfer_portfolio_cash,
        context,
        source_portfolio_id=source.id,
        destination_portfolio_id=destination.id,
        amount="10.03",
        effective_at="2026-09-03T15:00:00+08:00",
    )

    assert transfer_portfolio_position.needs_approval is True
    assert transfer_portfolio_cash.needs_approval is True
    assert isinstance(known_cost_position, dict)
    assert {entry["portfolio_id"] for entry in known_cost_position["entries"]} == {source.id, destination.id}
    assert isinstance(unknown_cost_position, dict)
    assert isinstance(cash, dict)
    assert cash["states"][source.id]["cash"] == {"CNY": "90.00"}
    assert cash["states"][destination.id]["cash"] == {"CNY": "10.03"}
    source_ledger = await context.context.portfolios.list_ledger(source.id)
    destination_ledger = await context.context.portfolios.list_ledger(destination.id)
    assert source_ledger[-3].operation_id == destination_ledger[0].operation_id == known_cost_position["operation_id"]
    assert source_ledger[-2].operation_id == destination_ledger[1].operation_id == unknown_cost_position["operation_id"]
    assert source_ledger[-1].operation_id == destination_ledger[-1].operation_id == cash["operation_id"]
    assert destination_ledger[0].payload.transferred_cost == Decimal("45.7035")
    assert destination_ledger[1].payload.transferred_cost is None
    assert {entry.source for entry in destination_ledger} == {"agent"}


async def test_cash_transfer_rejects_cross_currency_without_partial_ledger_writes(tmp_path: Path) -> None:
    context = make_tool_context(tmp_path)
    source = await context.context.portfolios.create(name="Source", base_currency="CNY")
    destination = await context.context.portfolios.create(name="Destination", base_currency="USD")

    result = await invoke(
        transfer_portfolio_cash,
        context,
        source_portfolio_id=source.id,
        destination_portfolio_id=destination.id,
        amount="10.03",
    )

    assert isinstance(result, str)
    assert "base currency" in result
    assert await context.context.portfolios.list_ledger(source.id) == []
    assert await context.context.portfolios.list_ledger(destination.id) == []


def test_main_agent_registers_complete_portfolio_tool_surface(tmp_path: Path) -> None:
    agent = create_agent(None, ModelSettings(), make_test_config(tmp_path))  # type: ignore[arg-type]
    portfolio_tools = {
        tool.name: tool
        for tool in agent.tools
        if isinstance(tool, FunctionTool) and ("portfolio" in tool.name or tool.name == "list_portfolios")
    }
    read_names = {"list_portfolios", "get_portfolio", "get_portfolio_ledger"}
    mutation_names = {
        "create_portfolio",
        "update_portfolio",
        "archive_portfolio",
        "restore_portfolio",
        "initialize_portfolio",
        "record_portfolio_trade",
        "record_portfolio_cash_flow",
        "record_portfolio_income",
        "adjust_portfolio_position",
        "adjust_portfolio_cash",
        "correct_portfolio_entry",
        "transfer_portfolio_position",
        "transfer_portfolio_cash",
    }

    assert set(portfolio_tools) == read_names | mutation_names
    assert all(portfolio_tools[name].needs_approval is False for name in read_names)
    assert all(portfolio_tools[name].needs_approval is True for name in mutation_names)


async def test_run_created_agent_context_uses_host_portfolio_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with open_test_web(tmp_path) as harness:
        portfolio = await harness.host.portfolios.create(name="Core", base_currency="CNY")
        agent_loop = AgentLoop(
            Agent(name="Main", instructions="test"),
            Agent(name="Title", instructions="test"),
            Agent(name="Compaction", instructions="test"),
            harness.host.config,
            harness.host.portfolios,
        )
        captured: dict[str, AgentContext] = {}

        def stop_at_sdk_boundary(*_args: object, context: AgentContext, **_kwargs: object) -> None:
            captured["context"] = context
            raise RuntimeError("controlled SDK boundary")

        monkeypatch.setattr("investorch.agents.loop.Runner.run_streamed", stop_at_sdk_boundary)

        async def unused_handler(*_args: object) -> None:
            raise AssertionError("handler should not be reached")

        with pytest.raises(RuntimeError, match="controlled SDK boundary"):
            await agent_loop.run(
                "read the portfolio",
                None,  # type: ignore[arg-type]
                harness.host.execution,
                run_id="run-a",
                session_id="session-a",
                reasoning_effort="none",
                approval_handler=unused_handler,  # type: ignore[arg-type]
                output_handler=unused_handler,
                run_control=None,  # type: ignore[arg-type]
            )

        context = ToolContext(
            context=captured["context"],
            tool_name="portfolio-behavior-test",
            tool_call_id="call-a",
            tool_arguments="{}",
        )

        result = await invoke(get_portfolio, context, portfolio_id=portfolio.id)

    assert isinstance(result, dict)
    assert result["portfolio"]["portfolio_id"] == portfolio.id
