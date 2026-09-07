import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from investorch.application.live import LiveExecutionOperations
from investorch.application.portfolios import PortfolioOperations
from investorch.live.domain import LiveExecutionError
from investorch.portfolio.domain import Broker, BrokerAccount, StrategyBinding
from investorch.portfolio.storage import create_broker, create_broker_account
from tests.support.config import make_test_config


async def test_prepared_deployment_freezes_exact_source_and_parameters(tmp_path):
    config = make_test_config(tmp_path)
    config.workspace_dir.mkdir(parents=True, exist_ok=True)
    source = config.workspace_dir / "strategy.py"
    original = b"def init(context):\r\n    context.value = 1\r\n"
    source.write_bytes(original)
    now = datetime.now(UTC)
    create_broker(config.portfolio_db, Broker("broker", "qmt", "Broker", now, now))
    create_broker_account(
        config.portfolio_db, BrokerAccount("account", "broker", "external", "Account", "stock", now, now)
    )
    portfolios = PortfolioOperations(config=config)
    portfolio = await portfolios.create(
        name="Portfolio",
        base_currency="CNY",
        strategy_binding=StrategyBinding("strategy.py", {"nested": {"x": [1, True]}}),
    )
    live = LiveExecutionOperations(config=config)
    deployment = await live.prepare_deployment(portfolio.id, "account")
    source.write_text("changed")
    await portfolios.update_metadata(portfolio.id, strategy_binding=StrategyBinding("strategy.py", {"new": 2}))
    loaded = await live.get_deployment(deployment.deployment_id)
    assert loaded == deployment
    assert loaded.status.value == "PREPARED"
    assert loaded.strategy_sha256 == hashlib.sha256(original).hexdigest()
    assert loaded.strategy_parameters == {"nested": {"x": [1, True]}}
    loaded.strategy_parameters["nested"]["x"].append(9)
    assert (await live.get_deployment(deployment.deployment_id)).strategy_parameters == {"nested": {"x": [1, True]}}
    artifact = config.state_dir / loaded.strategy_artifact_relpath
    assert artifact.read_bytes() == original
    manifest = json.loads(artifact.with_name("manifest.json").read_text())
    assert manifest["strategy_sha256"] == loaded.strategy_sha256
    assert await live.list_deployments(portfolio.id) == [loaded]


async def setup_live(tmp_path):
    config = make_test_config(tmp_path)
    config.workspace_dir.mkdir(parents=True, exist_ok=True)
    (config.workspace_dir / "strategy.py").write_text("def init(context):\n    pass\n")
    now = datetime.now(UTC)
    create_broker(config.portfolio_db, Broker("broker", "qmt", "Broker", now, now))
    create_broker_account(
        config.portfolio_db, BrokerAccount("account", "broker", "external", "Account", "stock", now, now)
    )
    portfolios = PortfolioOperations(config=config)
    p = await portfolios.create(name="P", base_currency="CNY", strategy_binding=StrategyBinding("strategy.py"))
    return config, portfolios, p, LiveExecutionOperations(config=config)


@pytest.mark.parametrize("source_path", ["missing.py", "folder", "bad.txt", "../outside.py"])
async def test_prepare_rejects_invalid_source_without_artifacts(tmp_path, source_path):
    config, portfolios, p, live = await setup_live(tmp_path)
    (config.workspace_dir / "folder").mkdir()
    (config.workspace_dir / "bad.txt").write_text("bad")
    with pytest.raises((ValueError, FileNotFoundError)):
        await portfolios.update_metadata(p.id, strategy_binding=StrategyBinding(source_path))
        await live.prepare_deployment(p.id, "account")
    assert await live.list_deployments() == []
    assert not (config.state_dir / "live" / "deployments").exists()


async def test_prepare_refuses_unallocated_assets_and_cleans_artifact(tmp_path):
    config, portfolios, p, live = await setup_live(tmp_path)
    await portfolios.initialize(p.id, cash=Decimal(100), source="test")
    with pytest.raises(LiveExecutionError, match="unallocated"):
        await live.prepare_deployment(p.id, "account")
    assert await live.list_deployments() == []
    assert list((config.state_dir / "live" / "deployments").iterdir()) == []


async def test_activation_reserves_one_owner_and_terminal_deployment_cannot_restart(tmp_path):
    config, _portfolios, p, live = await setup_live(tmp_path)
    first = await live.prepare_deployment(p.id, "account")
    second = await live.prepare_deployment(p.id, "account")
    active = await live.activate_deployment(first.deployment_id)
    assert active.status.value == "ACTIVE"
    assert active.started_at is not None
    assert active.bootstrap_ledger_sequence == 0
    with pytest.raises(LiveExecutionError):
        await live.activate_deployment(second.deployment_id)
    # Another application instance observes persistent ownership after restart.
    restarted = LiveExecutionOperations(config=config)
    assert (await restarted.get_deployment(first.deployment_id)).status.value == "ACTIVE"
    stopped = await restarted.stop_deployment(first.deployment_id)
    assert stopped.status.value == "STOPPED"
    assert stopped.ended_at is not None
    with pytest.raises(LiveExecutionError):
        await live.activate_deployment(first.deployment_id)
    assert (await live.activate_deployment(second.deployment_id)).status.value == "ACTIVE"


@pytest.mark.parametrize(
    "operation",
    [
        "trade",
        "cash",
        "income",
        "adjust_cash",
        "adjust_position",
        "correction",
        "transfer_cash_in",
        "transfer_cash_out",
        "transfer_position",
        "relocation",
    ],
)
async def test_active_owner_blocks_ordinary_economic_operations_but_allows_metadata(tmp_path, operation):
    from investorch.portfolio.domain import InstrumentId, OpeningCash, OpeningPosition, TradeSide
    from investorch.portfolio.schema import PortfolioConflictError

    _config, portfolios, p, live = await setup_live(tmp_path)
    stock = InstrumentId("600519", "XSHG")
    opening = await portfolios.initialize(
        p.id, cash=Decimal(1000), positions=[OpeningPosition(stock, Decimal(10), Decimal(100))], source="test"
    )
    await portfolios.assign_unallocated_assets(p.id, "account")
    other = await portfolios.create(name="Other", base_currency="CNY")
    prepared = await live.prepare_deployment(p.id, "account")
    active = await live.activate_deployment(prepared.deployment_id)
    assert active.bootstrap_ledger_sequence == len(await portfolios.list_ledger(p.id))
    before = await portfolios.list_ledger(p.id)
    actions = {
        "trade": lambda: portfolios.record_trade(
            p.id, instrument=stock, side=TradeSide.BUY, quantity=Decimal(1), price=Decimal(1), source="manual"
        ),
        "cash": lambda: portfolios.record_cash_flow(p.id, amount=Decimal(1), source="manual"),
        "income": lambda: portfolios.record_income(p.id, gross_amount=Decimal(1), source="manual"),
        "adjust_cash": lambda: portfolios.adjust_cash(p.id, resulting_amount=Decimal(1), reason="fix", source="manual"),
        "adjust_position": lambda: portfolios.adjust_position(
            p.id,
            instrument=stock,
            resulting_quantity=Decimal(1),
            resulting_total_cost=None,
            reason="fix",
            source="manual",
        ),
        "correction": lambda: portfolios.correct_entry(
            p.id,
            target_entry_id=opening.entries[0].entry_id,
            replacement_payload=OpeningCash("CNY", Decimal(5)),
            reason="fix",
            source="manual",
        ),
        "transfer_cash_in": lambda: portfolios.transfer_cash(
            source_portfolio_id=other.id, destination_portfolio_id=p.id, amount=Decimal(1), source="manual"
        ),
        "transfer_cash_out": lambda: portfolios.transfer_cash(
            source_portfolio_id=p.id, destination_portfolio_id=other.id, amount=Decimal(1), source="manual"
        ),
        "transfer_position": lambda: portfolios.transfer_position(
            source_portfolio_id=p.id,
            destination_portfolio_id=other.id,
            instrument=stock,
            quantity=Decimal(1),
            transferred_cost=None,
            source="manual",
        ),
        "relocation": lambda: portfolios.assign_unallocated_assets(p.id, "account"),
    }
    with pytest.raises(PortfolioConflictError, match="ACTIVE"):
        await actions[operation]()
    assert await portfolios.list_ledger(p.id) == before
    renamed = await portfolios.update_metadata(
        p.id, name="Renamed", strategy_binding=StrategyBinding("strategy.py", {"new": 2})
    )
    assert renamed.name == "Renamed"
    assert (await live.get_deployment(active.deployment_id)).strategy_parameters == {}


async def test_empty_active_portfolio_cannot_be_initialized(tmp_path):
    from investorch.portfolio.schema import PortfolioConflictError

    _config, portfolios, p, live = await setup_live(tmp_path)
    deployment = await live.prepare_deployment(p.id, "account")
    await live.activate_deployment(deployment.deployment_id)
    with pytest.raises(PortfolioConflictError, match="ACTIVE"):
        await portfolios.initialize(p.id, cash=Decimal(1), source="manual")
    await live.stop_deployment(deployment.deployment_id)
    await portfolios.initialize(p.id, cash=Decimal(1), source="manual")


async def test_active_ownership_is_database_unique_and_account_can_serve_two_portfolios(tmp_path):
    import asyncio
    import sqlite3

    config, portfolios, p, live = await setup_live(tmp_path)
    first = await live.prepare_deployment(p.id, "account")
    second = await live.prepare_deployment(p.id, "account")
    outcomes = await asyncio.gather(
        live.activate_deployment(first.deployment_id),
        live.activate_deployment(second.deployment_id),
        return_exceptions=True,
    )
    assert sum(isinstance(result, LiveExecutionError) for result in outcomes) == 1
    loser = next(d for d in await live.list_deployments(p.id) if d.status.value == "PREPARED")
    with sqlite3.connect(config.portfolio_db) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE live_deployments SET status = 'ACTIVE' WHERE deployment_id = ?", (loser.deployment_id,)
        )
    other = await portfolios.create(name="Other", base_currency="CNY", strategy_binding=StrategyBinding("strategy.py"))
    third = await live.prepare_deployment(other.id, "account")
    assert (await live.activate_deployment(third.deployment_id)).status.value == "ACTIVE"


@pytest.mark.parametrize("activate", [False, True])
async def test_failed_deployment_is_terminal(tmp_path, activate):
    _config, _portfolios, p, live = await setup_live(tmp_path)
    deployment = await live.prepare_deployment(p.id, "account")
    if activate:
        await live.activate_deployment(deployment.deployment_id)
    failed = await live.fail_deployment(deployment.deployment_id, "runtime unavailable")
    assert failed.status.value == "FAILED"
    assert failed.failure_reason == "runtime unavailable"
    with pytest.raises(LiveExecutionError):
        await live.activate_deployment(deployment.deployment_id)


async def test_activation_revalidates_assets_added_after_prepare(tmp_path):
    _config, portfolios, p, live = await setup_live(tmp_path)
    prepared = await live.prepare_deployment(p.id, "account")
    await portfolios.record_cash_flow(p.id, amount=Decimal(100), source="manual")
    with pytest.raises(LiveExecutionError, match="unallocated"):
        await live.activate_deployment(prepared.deployment_id)
    assert (await live.get_deployment(prepared.deployment_id)).status.value == "PREPARED"
