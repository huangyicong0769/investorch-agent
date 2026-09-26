from datetime import UTC, datetime
from decimal import Decimal

import pytest

from investorch.application.portfolios import PortfolioOperations
from investorch.portfolio import (
    Broker,
    BrokerAccount,
    InstrumentId,
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
    await _seed(config, ops, p.id, account, cash, quantity)
    return config, ops, p


async def _seed(config, ops, portfolio_id, account, cash, quantity):
    from uuid import uuid4

    from investorch.portfolio import LedgerEntry, LedgerEntryType, OpeningCash, OpeningPosition, append_ledger_operation

    now = datetime.now(UTC)
    sequence = len(await ops.list_ledger(portfolio_id))
    payloads = [(LedgerEntryType.OPENING_CASH, OpeningCash("CNY", cash))]
    if quantity:
        payloads.append((LedgerEntryType.OPENING_POSITION, OpeningPosition(STOCK, quantity, quantity * Decimal(10))))
    operation_id = str(uuid4())
    append_ledger_operation(
        config.portfolio_db,
        [
            LedgerEntry(
                str(uuid4()),
                operation_id,
                portfolio_id,
                sequence + index,
                kind,
                now,
                now,
                "seed",
                payload,
                broker_account_id=account,
            )
            for index, (kind, payload) in enumerate(payloads, 1)
        ],
    )


async def test_ordinary_sell_consumes_the_allocated_account(tmp_path):
    config, ops, p = await _portfolio(tmp_path)
    before = get_portfolio_state_with_attribution(config.portfolio_db, p.id)
    result = await ops.record_trade(
        p.id, instrument=STOCK, side=TradeSide.SELL, quantity=Decimal(10), price=Decimal(12), source="manual"
    )
    state = get_portfolio_state_with_attribution(config.portfolio_db, p.id)
    assert result.entries[0].broker_account_id == "a"
    assert state.accounts["a"].holdings[STOCK].quantity == Decimal(90)
    assert state.accounts.get(None) == before.accounts.get(None)
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
        assert state.accounts.get(None) == before.accounts.get(None)


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
        await _seed(config, ops, destination.id, destination_account, Decimal(1), Decimal(0))
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


async def test_correction_retains_original_account_even_when_portfolio_is_ambiguous(tmp_path):
    from uuid import uuid4

    from investorch.portfolio import LedgerEntry, LedgerEntryType, Trade, append_ledger_operation

    config, ops, p = await _portfolio(tmp_path)
    ledger = await ops.list_ledger(p.id)
    now = datetime.now(UTC)
    target = LedgerEntry(
        str(uuid4()),
        str(uuid4()),
        p.id,
        len(ledger) + 1,
        LedgerEntryType.TRADE,
        now,
        now,
        "seed",
        Trade(STOCK, TradeSide.BUY, Decimal(10), Decimal(12)),
        broker_account_id="a",
    )
    append_ledger_operation(config.portfolio_db, [target])
    await _add_cash_location(config, ops, p.id, "b")
    result = await ops.correct_entry(
        p.id,
        target_entry_id=target.entry_id,
        replacement_payload=Trade(STOCK, TradeSide.BUY, Decimal(20), Decimal(12)),
        reason="quantity correction",
        source="manual",
    )
    assert [entry.broker_account_id for entry in result.entries] == ["a", "a"]
    state = get_portfolio_state_with_attribution(config.portfolio_db, p.id)
    assert state.accounts["a"].cash == {"CNY": Decimal(760)}
    assert state.accounts["a"].holdings[STOCK].quantity == Decimal(120)
    assert state.accounts["a"].holdings[STOCK].total_cost == Decimal(1240)
    assert state.aggregate.cash == {"CNY": Decimal(759)}
    assert target in await ops.list_ledger(p.id)


@pytest.mark.parametrize("kind", ["cash", "position"])
@pytest.mark.parametrize("ambiguous_endpoint", ["source", "destination"])
async def test_ambiguous_transfer_endpoint_leaves_both_portfolios_unchanged(tmp_path, kind, ambiguous_endpoint):
    from investorch.portfolio.schema import PortfolioConflictError

    config, ops, p = await _portfolio(tmp_path)
    other = await ops.create(name="Other", base_currency="CNY")
    await _seed(config, ops, other.id, "b", Decimal(1000), Decimal(100))
    ambiguous = p if ambiguous_endpoint == "source" else other
    await _add_cash_location(config, ops, ambiguous.id, None)
    before = [await ops.list_ledger(item.id) for item in (p, other)]
    with pytest.raises(PortfolioConflictError, match="economic location is ambiguous"):
        await _transfer(ops, p.id, other.id, kind)
    assert [await ops.list_ledger(item.id) for item in (p, other)] == before


@pytest.mark.parametrize("kind", ["cash", "position"])
async def test_omitted_transfer_effective_time_is_the_command_time(tmp_path, kind):
    _config, ops, p = await _portfolio(tmp_path)
    other = await ops.create(name="Other", base_currency="CNY")
    result = await _transfer(ops, p.id, other.id, kind)
    assert all(entry.effective_at == entry.recorded_at for entry in result.entries)


async def test_sequence_retry_preserves_command_time_with_real_competing_append(tmp_path, monkeypatch):
    import sqlite3
    from uuid import uuid4

    from investorch.portfolio import CashFlow, LedgerEntry, LedgerEntryType, append_ledger_operation

    config, ops, p = await _portfolio(tmp_path)
    original_connect = sqlite3.connect
    competing_time = datetime.now(UTC)
    competing_entry = LedgerEntry(
        str(uuid4()),
        str(uuid4()),
        p.id,
        3,
        LedgerEntryType.CASH_FLOW,
        competing_time,
        competing_time,
        "competing-writer",
        CashFlow("CNY", Decimal(1)),
        broker_account_id="a",
    )
    append_attempts = []
    callback_errors = []
    injecting = False

    def on_statement(statement):
        nonlocal injecting
        if statement != "BEGIN IMMEDIATE" or injecting:
            return
        append_attempts.append(statement)
        if len(append_attempts) == 1:
            injecting = True
            try:
                append_ledger_operation(config.portfolio_db, [competing_entry])
            except Exception as exc:
                callback_errors.append(exc)
            finally:
                injecting = False

    def traced_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connection.set_trace_callback(on_statement)
        return connection

    # Schedule a real competing database writer immediately before the first
    # append transaction begins; application and storage logic remain real.
    monkeypatch.setattr(sqlite3, "connect", traced_connect)
    result = await ops.record_cash_flow(p.id, amount=Decimal(10), source="manual")
    assert not callback_errors
    assert len(append_attempts) == 2
    entry = result.entries[0]
    assert entry.sequence == 4
    assert entry.effective_at == entry.recorded_at
    assert entry.broker_account_id == "a"
    assert (await ops.get_state(p.id)).cash == {"CNY": Decimal(1011)}
