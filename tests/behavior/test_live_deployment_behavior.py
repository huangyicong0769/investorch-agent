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
