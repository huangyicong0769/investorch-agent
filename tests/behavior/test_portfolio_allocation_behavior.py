from datetime import UTC, datetime
from decimal import Decimal

import pytest

from investorch.application.portfolios import PortfolioOperations
from investorch.portfolio import (
    Broker,
    BrokerAccount,
    InstrumentId,
    OpeningPosition,
    create_broker,
    create_broker_account,
    get_portfolio_state_with_attribution,
    init_portfolio_storage,
)
from tests.support.config import make_test_config


@pytest.mark.asyncio
async def test_assign_unallocated_assets_moves_all_assets_without_changing_aggregate(tmp_path):
    config = make_test_config(tmp_path)
    init_portfolio_storage(config.portfolio_db)
    ops = PortfolioOperations(config=config)
    now = datetime.now(UTC)
    create_broker(config.portfolio_db, Broker("b", "qmt", "Broker", now, now))
    create_broker_account(config.portfolio_db, BrokerAccount("a", "b", "external", "Trading", "stock", now, now))
    p = await ops.create(name="Portfolio", base_currency="CNY")
    await ops.initialize(
        p.id,
        cash=Decimal("-50"),
        positions=[
            OpeningPosition(InstrumentId("600000", "XSHG"), Decimal(100), Decimal(1000)),
            OpeningPosition(InstrumentId("000001", "XSHE"), Decimal(20), None),
        ],
        source="test",
    )
    before = await ops.get_state(p.id)
    old_ledger = await ops.list_ledger(p.id)
    moved = await ops.assign_unallocated_assets(p.id, "a")
    after = get_portfolio_state_with_attribution(config.portfolio_db, p.id)
    assert after.aggregate == before
    assert after.accounts["a"].holdings == before.holdings
    assert after.accounts["a"].cash == before.cash
    assert after.accounts[None].holdings == {}
    assert all(amount == 0 for amount in after.accounts[None].cash.values())
    assert (await ops.list_ledger(p.id))[: len(old_ledger)] == old_ledger
    assert len(moved.entries) == 6
    assert len({entry.operation_id for entry in moved.entries}) == 1


async def _unallocated_portfolio(tmp_path):
    config = make_test_config(tmp_path)
    ops = PortfolioOperations(config=config)
    now = datetime.now(UTC)
    create_broker(config.portfolio_db, Broker("b", "qmt", "Broker", now, now))
    create_broker_account(config.portfolio_db, BrokerAccount("a", "b", "external", "Trading", "stock", now, now))
    p = await ops.create(name="Portfolio", base_currency="CNY")
    await ops.initialize(
        p.id,
        cash=Decimal(1000),
        positions=[
            OpeningPosition(InstrumentId("600000", "XSHG"), Decimal(100), Decimal(1000)),
        ],
        source="test",
    )
    return config, ops, p


async def test_allocation_database_failure_rolls_back_both_transfer_legs(tmp_path):
    import sqlite3

    from investorch.portfolio import PortfolioConflictError

    config, ops, p = await _unallocated_portfolio(tmp_path)
    before = get_portfolio_state_with_attribution(config.portfolio_db, p.id)
    ledger = await ops.list_ledger(p.id)
    with sqlite3.connect(config.portfolio_db) as connection:
        connection.executescript("""
            CREATE TRIGGER fail_incoming_allocation BEFORE INSERT ON portfolio_ledger
            WHEN NEW.broker_account_id = 'a'
            BEGIN SELECT RAISE(ABORT, 'injected transaction failure'); END;
        """)
    with pytest.raises(PortfolioConflictError, match="injected transaction failure"):
        await ops.assign_unallocated_assets(p.id, "a")
    assert await ops.list_ledger(p.id) == ledger
    assert get_portfolio_state_with_attribution(config.portfolio_db, p.id) == before


async def test_allocation_is_noop_after_assets_have_already_been_assigned(tmp_path):
    config, ops, p = await _unallocated_portfolio(tmp_path)
    await ops.assign_unallocated_assets(p.id, "a")
    ledger = await ops.list_ledger(p.id)
    state = get_portfolio_state_with_attribution(config.portfolio_db, p.id)
    again = await ops.assign_unallocated_assets(p.id, "a")
    assert again.entries == ()
    assert await ops.list_ledger(p.id) == ledger
    assert get_portfolio_state_with_attribution(config.portfolio_db, p.id) == state


async def test_allocation_rejects_future_effective_state_without_inventing_transfer_time(tmp_path):
    from datetime import timedelta

    from investorch.portfolio import PortfolioConflictError

    _config, ops, p = await _unallocated_portfolio(tmp_path)
    await ops.record_cash_flow(
        p.id, amount=Decimal(20), effective_at=datetime.now(UTC) + timedelta(days=1), source="test"
    )
    ledger = await ops.list_ledger(p.id)
    with pytest.raises(PortfolioConflictError, match="future-effective"):
        await ops.assign_unallocated_assets(p.id, "a")
    assert await ops.list_ledger(p.id) == ledger


async def test_missing_target_account_leaves_unallocated_state_unchanged(tmp_path):
    from investorch.portfolio import PortfolioConflictError

    config, ops, p = await _unallocated_portfolio(tmp_path)
    before = get_portfolio_state_with_attribution(config.portfolio_db, p.id)
    with pytest.raises(PortfolioConflictError, match="BrokerAccount not found"):
        await ops.assign_unallocated_assets(p.id, "missing")
    assert get_portfolio_state_with_attribution(config.portfolio_db, p.id) == before


async def test_concurrent_allocation_has_one_economic_operation(tmp_path):
    import asyncio

    config, ops, p = await _unallocated_portfolio(tmp_path)
    other = PortfolioOperations(config=config)
    before = await ops.get_state(p.id)
    first, second = await asyncio.gather(
        ops.assign_unallocated_assets(p.id, "a"),
        other.assign_unallocated_assets(p.id, "a"),
    )
    assert sorted([len(first.entries), len(second.entries)]) == [0, 4]
    assert await ops.get_state(p.id) == before
