from datetime import UTC, datetime
from decimal import Decimal

import pytest

from investorch.live.domain import LiveTradeIdempotencyConflict
from investorch.portfolio.domain import InstrumentId, TradeSide
from tests.behavior.test_live_deployment_behavior import setup_live


async def test_live_trade_delivery_is_idempotent_and_conflicting_payload_fails(tmp_path):
    _config, portfolios, p, live = await setup_live(tmp_path)
    deployment = await live.prepare_deployment(p.id, "account")
    await live.activate_deployment(deployment.deployment_id)
    trade = dict(
        broker_trade_id="execution:1",
        instrument=InstrumentId("600519", "XSHG"),
        side=TradeSide.BUY,
        quantity=Decimal(100),
        price=Decimal("10.50"),
        commission=Decimal(1),
        effective_at=datetime.now(UTC),
    )
    first = await live.ingest_live_trade(deployment.deployment_id, **trade)
    repeated = await live.ingest_live_trade(deployment.deployment_id, **trade)
    assert first == repeated
    assert first.sequence == 1
    assert first.source == "live_execution"
    assert first.broker_account_id == "account"
    assert await portfolios.list_ledger(p.id) == [first]
    assert (await portfolios.get_state(p.id)).cash == {"CNY": Decimal(-1051)}
    with pytest.raises(LiveTradeIdempotencyConflict):
        await live.ingest_live_trade(deployment.deployment_id, **(trade | {"price": Decimal(11)}))
    assert await portfolios.list_ledger(p.id) == [first]


@pytest.mark.parametrize(
    "change",
    [
        {"quantity": Decimal(2)},
        {"commission": Decimal(2)},
        {"tax": Decimal(2)},
        {"other_fee": Decimal(2)},
        {"instrument": InstrumentId("000001", "XSHE")},
        {"side": TradeSide.SELL},
        {"effective_at": datetime(2025, 1, 1, tzinfo=UTC)},
    ],
)
async def test_duplicate_identity_compares_all_typed_economic_fields(tmp_path, change):
    _config, portfolios, p, live = await setup_live(tmp_path)
    deployment = await live.prepare_deployment(p.id, "account")
    await live.activate_deployment(deployment.deployment_id)
    trade = dict(
        broker_trade_id="id",
        instrument=InstrumentId("600519", "XSHG"),
        side=TradeSide.BUY,
        quantity=Decimal(1),
        price=Decimal(10),
        effective_at=datetime.now(UTC),
    )
    first = await live.ingest_live_trade(deployment.deployment_id, **trade)
    with pytest.raises(LiveTradeIdempotencyConflict):
        await live.ingest_live_trade(deployment.deployment_id, **(trade | change))
    assert await portfolios.list_ledger(p.id) == [first]


async def test_parallel_repeated_delivery_has_one_economic_effect(tmp_path):
    import asyncio

    _config, portfolios, p, live = await setup_live(tmp_path)
    deployment = await live.prepare_deployment(p.id, "account")
    await live.activate_deployment(deployment.deployment_id)
    trade = dict(
        broker_trade_id="parallel",
        instrument=InstrumentId("600519", "XSHG"),
        side=TradeSide.BUY,
        quantity=Decimal(1),
        price=Decimal(10),
        effective_at=datetime.now(UTC),
    )
    results = await asyncio.gather(*(live.ingest_live_trade(deployment.deployment_id, **trade) for _ in range(8)))
    assert all(result == results[0] for result in results)
    assert await portfolios.list_ledger(p.id) == [results[0]]
    assert (await portfolios.get_state(p.id)).cash == {"CNY": Decimal(-10)}


async def test_trade_identity_is_account_namespaced_and_decimal_scale_is_normalized(tmp_path):
    from investorch.portfolio.domain import BrokerAccount, StrategyBinding
    from investorch.portfolio.storage import create_broker_account

    config, portfolios, p, live = await setup_live(tmp_path)
    now = datetime.now(UTC)
    create_broker_account(
        config.portfolio_db, BrokerAccount("second-account", "broker", "second", "Second", "stock", now, now)
    )
    other = await portfolios.create(name="Other", base_currency="CNY", strategy_binding=StrategyBinding("strategy.py"))
    first = await live.prepare_deployment(p.id, "account")
    second = await live.prepare_deployment(other.id, "second-account")
    await live.activate_deployment(first.deployment_id)
    await live.activate_deployment(second.deployment_id)
    trade = dict(
        broker_trade_id="same external id",
        instrument=InstrumentId("600519", "XSHG"),
        side=TradeSide.BUY,
        quantity=Decimal("1.0"),
        price=Decimal("10.00"),
        effective_at=now,
    )
    a = await live.ingest_live_trade(first.deployment_id, **trade)
    b = await live.ingest_live_trade(second.deployment_id, **trade)
    assert a.external_ref != b.external_ref
    assert a.sequence == b.sequence == 1
    assert (
        await live.ingest_live_trade(first.deployment_id, **(trade | {"price": Decimal(10), "quantity": Decimal(1)}))
        == a
    )


@pytest.mark.parametrize("terminal", ["STOPPED", "FAILED"])
async def test_terminal_deployment_accepts_valid_late_fact_but_keeps_ledger_constraints(tmp_path, terminal):
    from investorch.portfolio.domain import InsufficientPositionError

    _config, portfolios, p, live = await setup_live(tmp_path)
    deployment = await live.prepare_deployment(p.id, "account")
    await live.activate_deployment(deployment.deployment_id)
    if terminal == "STOPPED":
        await live.stop_deployment(deployment.deployment_id)
    else:
        await live.fail_deployment(deployment.deployment_id, "connection lost")
    trade = dict(
        broker_trade_id="late",
        instrument=InstrumentId("600519", "XSHG"),
        side=TradeSide.BUY,
        quantity=Decimal(1),
        price=Decimal(10),
        effective_at=datetime.now(UTC),
    )
    committed = await live.ingest_live_trade(deployment.deployment_id, **trade)
    with pytest.raises(InsufficientPositionError):
        await live.ingest_live_trade(
            deployment.deployment_id,
            **(trade | {"broker_trade_id": "invalid", "side": TradeSide.SELL, "quantity": Decimal(2)}),
        )
    assert await portfolios.list_ledger(p.id) == [committed]


async def test_prepared_and_unknown_deployment_cannot_ingest_trade(tmp_path):
    from investorch.live.domain import LiveExecutionError

    _config, _portfolios, p, live = await setup_live(tmp_path)
    deployment = await live.prepare_deployment(p.id, "account")
    trade = dict(
        broker_trade_id="id",
        instrument=InstrumentId("600519", "XSHG"),
        side=TradeSide.BUY,
        quantity=Decimal(1),
        price=Decimal(10),
        effective_at=datetime.now(UTC),
    )
    for identifier in (deployment.deployment_id, "missing"):
        with pytest.raises(LiveExecutionError):
            await live.ingest_live_trade(identifier, **trade)


async def test_manual_source_cannot_impersonate_live_ingestion_but_refs_remain_nonunique(tmp_path):
    from investorch.portfolio.schema import PortfolioConflictError

    _config, portfolios, p, _live = await setup_live(tmp_path)
    with pytest.raises(PortfolioConflictError, match="dedicated live trade ingestion"):
        await portfolios.record_cash_flow(p.id, amount=Decimal(10), source="live_execution", external_ref="id")
    await portfolios.record_cash_flow(p.id, amount=Decimal(10), source="manual", external_ref="id")
    await portfolios.record_cash_flow(p.id, amount=Decimal(10), source="manual", external_ref="id")
    assert (await portfolios.get_state(p.id)).cash == {"CNY": Decimal(20)}
