from datetime import UTC, datetime
from decimal import Decimal

import pytest

from investorch.application.portfolios import PortfolioOperations
from investorch.portfolio import (
    Broker,
    BrokerAccount,
    InstrumentId,
    OpeningPosition,
    TradeSide,
    create_broker,
    create_broker_account,
    get_portfolio_state_with_attribution,
)
from tests.support.config import make_test_config

STOCK = InstrumentId("600519", "XSHG")


async def _portfolio(tmp_path, *, account="a", cash=Decimal(1000), quantity=Decimal(100)):
    config = make_test_config(tmp_path)
    ops = PortfolioOperations(config=config)
    now = datetime.now(UTC)
    create_broker(config.portfolio_db, Broker("broker", "qmt", "Broker", now, now))
    for identifier in ("a", "b"):
        create_broker_account(
            config.portfolio_db, BrokerAccount(identifier, "broker", identifier, identifier, "stock", now, now)
        )
    p = await ops.create(name="Portfolio", base_currency="CNY")
    await ops.initialize(
        p.id,
        cash=cash,
        positions=[OpeningPosition(STOCK, quantity, quantity * Decimal(10))] if quantity else [],
        source="test",
    )
    if account is not None:
        await ops.assign_unallocated_assets(p.id, account)
    return config, ops, p


async def test_ordinary_sell_consumes_the_allocated_account(tmp_path):
    config, ops, p = await _portfolio(tmp_path)
    before = get_portfolio_state_with_attribution(config.portfolio_db, p.id)
    result = await ops.record_trade(
        p.id, instrument=STOCK, side=TradeSide.SELL, quantity=Decimal(10), price=Decimal(12), source="manual"
    )
    state = get_portfolio_state_with_attribution(config.portfolio_db, p.id)
    assert result.entries[0].broker_account_id == "a"
    assert state.accounts["a"].holdings[STOCK].quantity == Decimal(90)
    assert state.accounts[None] == before.accounts[None]
    assert state.aggregate.holdings[STOCK].quantity == Decimal(90)
    assert state.aggregate.cash == {"CNY": Decimal(1120)}


async def _mutate(ops, portfolio_id, kind):
    if kind in ("buy", "sell"):
        return await ops.record_trade(
            portfolio_id,
            instrument=STOCK,
            side=TradeSide.BUY if kind == "buy" else TradeSide.SELL,
            quantity=Decimal(10),
            price=Decimal(12),
            source="manual",
        )
    if kind == "cash":
        return await ops.record_cash_flow(portfolio_id, amount=Decimal(100), source="manual")
    if kind == "income":
        return await ops.record_income(portfolio_id, gross_amount=Decimal(100), source="manual")
    if kind == "adjust_cash":
        return await ops.adjust_cash(portfolio_id, resulting_amount=Decimal(900), reason="fix", source="manual")
    return await ops.adjust_position(
        portfolio_id,
        instrument=STOCK,
        resulting_quantity=Decimal(50),
        resulting_total_cost=Decimal(500),
        reason="fix",
        source="manual",
    )


@pytest.mark.parametrize("account", ["a", None])
@pytest.mark.parametrize(
    ("kind", "cash", "quantity"),
    [
        ("buy", 880, 110),
        ("sell", 1120, 90),
        ("cash", 1100, 100),
        ("income", 1100, 100),
        ("adjust_cash", 900, 100),
        ("adjust_position", 1000, 50),
    ],
)
async def test_ordinary_operations_use_unique_location(tmp_path, account, kind, cash, quantity):
    config, ops, p = await _portfolio(tmp_path, account=account)
    before = get_portfolio_state_with_attribution(config.portfolio_db, p.id)
    result = await _mutate(ops, p.id, kind)
    state = get_portfolio_state_with_attribution(config.portfolio_db, p.id)
    assert result.entries[0].broker_account_id == account
    assert state.accounts[account].cash == {"CNY": Decimal(cash)}
    assert state.accounts[account].holdings[STOCK].quantity == Decimal(quantity)
    assert state.aggregate.cash == {"CNY": Decimal(cash)}
    assert state.aggregate.holdings[STOCK].quantity == Decimal(quantity)
    if account is not None:
        assert state.accounts[None] == before.accounts[None]


@pytest.mark.parametrize("kind", ["cash", "adjust_cash", "adjust_position"])
async def test_empty_portfolio_keeps_unallocated_location(tmp_path, kind):
    config, ops, _p = await _portfolio(tmp_path)
    empty = await ops.create(name="Empty", base_currency="CNY")
    result = await _mutate(ops, empty.id, kind)
    assert result.entries[0].broker_account_id is None
    assert set(get_portfolio_state_with_attribution(config.portfolio_db, empty.id).accounts) == {None}


async def test_negative_cash_is_an_economic_location(tmp_path):
    config, ops, p = await _portfolio(tmp_path, cash=Decimal(-100), quantity=Decimal(0))
    result = await ops.record_cash_flow(p.id, amount=Decimal(10), source="manual")
    assert result.entries[0].broker_account_id == "a"
    assert get_portfolio_state_with_attribution(config.portfolio_db, p.id).accounts["a"].cash == {"CNY": Decimal(-90)}


async def _add_cash_location(config, ops, portfolio_id, account):
    from uuid import uuid4

    from investorch.portfolio.domain import CashFlow, LedgerEntry, LedgerEntryType
    from investorch.portfolio.storage import append_ledger_operation

    ledger = await ops.list_ledger(portfolio_id)
    now = datetime.now(UTC)
    append_ledger_operation(
        config.portfolio_db,
        [
            LedgerEntry(
                str(uuid4()),
                str(uuid4()),
                portfolio_id,
                len(ledger) + 1,
                LedgerEntryType.CASH_FLOW,
                now,
                now,
                "explicit-writer",
                CashFlow("CNY", Decimal(-1)),
                broker_account_id=account,
            )
        ],
    )


@pytest.mark.parametrize("second_account", ["b", None])
@pytest.mark.parametrize("kind", ["buy", "sell", "cash", "income", "adjust_cash", "adjust_position"])
async def test_ambiguous_portfolio_rejects_every_implicit_operation(tmp_path, second_account, kind):
    from investorch.portfolio.schema import PortfolioConflictError

    config, ops, p = await _portfolio(tmp_path)
    await _add_cash_location(config, ops, p.id, second_account)
    ledger = await ops.list_ledger(p.id)
    state = get_portfolio_state_with_attribution(config.portfolio_db, p.id)
    with pytest.raises(PortfolioConflictError, match="Portfolio economic location is ambiguous across BrokerAccounts"):
        await _mutate(ops, p.id, kind)
    assert await ops.list_ledger(p.id) == ledger
    assert get_portfolio_state_with_attribution(config.portfolio_db, p.id) == state


async def _transfer(ops, source, destination, kind):
    if kind == "cash":
        return await ops.transfer_cash(
            source_portfolio_id=source, destination_portfolio_id=destination, amount=Decimal(100), source="manual"
        )
    return await ops.transfer_position(
        source_portfolio_id=source,
        destination_portfolio_id=destination,
        instrument=STOCK,
        quantity=Decimal(10),
        transferred_cost=Decimal(100),
        source="manual",
    )


@pytest.mark.parametrize("kind", ["cash", "position"])
@pytest.mark.parametrize(
    ("source_account", "destination_account"), [("a", "b"), ("a", None), (None, "b"), (None, None)]
)
async def test_transfer_resolves_endpoints_independently(tmp_path, kind, source_account, destination_account):
    config, ops, p = await _portfolio(tmp_path, account=source_account)
    destination = await ops.create(name="Destination", base_currency="CNY")
    if destination_account is not None:
        await ops.initialize(destination.id, cash=Decimal(1), source="test")
        await ops.assign_unallocated_assets(destination.id, destination_account)
    result = await _transfer(ops, p.id, destination.id, kind)
    assert [entry.broker_account_id for entry in result.entries] == [source_account, destination_account]
    source = get_portfolio_state_with_attribution(config.portfolio_db, p.id)
    target = get_portfolio_state_with_attribution(config.portfolio_db, destination.id)
    if kind == "cash":
        assert source.accounts[source_account].cash == {"CNY": Decimal(900)}
        assert target.accounts[destination_account].cash == {"CNY": Decimal(101 if destination_account else 100)}
    else:
        assert source.accounts[source_account].holdings[STOCK].quantity == Decimal(90)
        assert target.accounts[destination_account].holdings[STOCK].quantity == Decimal(10)


@pytest.mark.parametrize("kind", ["cash", "position"])
@pytest.mark.parametrize("ambiguous_endpoint", ["source", "destination"])
@pytest.mark.parametrize("active_other", [False, True])
async def test_transfer_ambiguity_is_atomic_and_active_ownership_has_priority(
    tmp_path, kind, ambiguous_endpoint, active_other
):
    from investorch.application.live import LiveExecutionOperations
    from investorch.portfolio.domain import StrategyBinding
    from investorch.portfolio.schema import PortfolioConflictError

    config, ops, p = await _portfolio(tmp_path)
    other = await ops.create(name="Other", base_currency="CNY")
    await ops.initialize(
        other.id, cash=Decimal(1000), positions=[OpeningPosition(STOCK, Decimal(100), Decimal(1000))], source="test"
    )
    await ops.assign_unallocated_assets(other.id, "b")
    ambiguous, owned = (p, other) if ambiguous_endpoint == "source" else (other, p)
    await _add_cash_location(config, ops, ambiguous.id, None)
    if active_other:
        config.workspace_dir.mkdir(parents=True, exist_ok=True)
        (config.workspace_dir / "strategy.py").write_text("def init(context):\n    pass\n")
        await ops.update_metadata(owned.id, strategy_binding=StrategyBinding("strategy.py"))
        live = LiveExecutionOperations(config=config)
        deployment = await live.prepare_deployment(owned.id, "b" if owned.id == other.id else "a")
        await live.activate_deployment(deployment.deployment_id)
    ledgers = [await ops.list_ledger(item.id) for item in (p, other)]
    states = [get_portfolio_state_with_attribution(config.portfolio_db, item.id) for item in (p, other)]
    message = (
        "Portfolio economic mutation is blocked by ACTIVE live deployment"
        if active_other
        else "Portfolio economic location is ambiguous across BrokerAccounts"
    )
    with pytest.raises(PortfolioConflictError, match=message):
        await _transfer(ops, p.id, other.id, kind)
    assert [await ops.list_ledger(item.id) for item in (p, other)] == ledgers
    assert [get_portfolio_state_with_attribution(config.portfolio_db, item.id) for item in (p, other)] == states


@pytest.mark.parametrize("status", ["PREPARED", "STOPPED"])
async def test_nonactive_deployment_allows_allocated_sell(tmp_path, status):
    from investorch.application.live import LiveExecutionOperations
    from investorch.portfolio.domain import StrategyBinding

    config, ops, p = await _portfolio(tmp_path)
    config.workspace_dir.mkdir(parents=True, exist_ok=True)
    (config.workspace_dir / "strategy.py").write_text("def init(context):\n    pass\n")
    await ops.update_metadata(p.id, strategy_binding=StrategyBinding("strategy.py"))
    live = LiveExecutionOperations(config=config)
    deployment = await live.prepare_deployment(p.id, "a")
    if status == "STOPPED":
        await live.activate_deployment(deployment.deployment_id)
        await live.stop_deployment(deployment.deployment_id)
    result = await _mutate(ops, p.id, "sell")
    assert result.entries[0].broker_account_id == "a"
    assert get_portfolio_state_with_attribution(config.portfolio_db, p.id).aggregate.holdings[
        STOCK
    ].quantity == Decimal(90)


@pytest.mark.parametrize("kind", ["cash", "adjust_cash", "adjust_position"])
async def test_sequence_retry_resolves_new_location_and_default_effective_time(tmp_path, monkeypatch, kind):
    import asyncio

    import investorch.application.portfolios as application

    config, ops, p = await _portfolio(tmp_path, account=None)
    original_append = application.append_ledger_operation
    attempts = []

    def append_after_concurrent_allocation(db, entries):
        attempts.append(entries)
        if len(attempts) == 1:
            asyncio.run(PortfolioOperations(config=config).assign_unallocated_assets(p.id, "a"))
        return original_append(db, entries)

    monkeypatch.setattr(application, "append_ledger_operation", append_after_concurrent_allocation)
    result = await _mutate(ops, p.id, kind)
    assert len(attempts) == 2
    assert attempts[0][0].broker_account_id is None
    assert result.entries[0].broker_account_id == "a"
    state = get_portfolio_state_with_attribution(config.portfolio_db, p.id)
    assert state.aggregate.cash == {"CNY": Decimal(900 if kind == "adjust_cash" else 1100 if kind == "cash" else 1000)}
    assert state.aggregate.holdings[STOCK].quantity == Decimal(50 if kind == "adjust_position" else 100)
    assert not any(state.accounts[None].cash.values())
    assert not state.accounts[None].holdings


async def test_explicit_effective_time_is_preserved(tmp_path):
    config, ops, p = await _portfolio(tmp_path)
    effective_at = datetime.now(UTC)
    result = await ops.adjust_cash(
        p.id, resulting_amount=Decimal(900), reason="fix", source="manual", effective_at=effective_at
    )
    assert result.entries[0].effective_at == effective_at
    assert get_portfolio_state_with_attribution(config.portfolio_db, p.id).aggregate.cash == {"CNY": Decimal(900)}
