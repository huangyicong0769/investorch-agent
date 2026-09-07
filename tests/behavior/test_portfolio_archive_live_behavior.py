from dataclasses import replace
from datetime import UTC, datetime

import pytest

from investorch.application.live import LiveExecutionOperations
from investorch.application.portfolios import PortfolioOperations
from investorch.live.domain import LiveExecutionError
from investorch.portfolio.domain import Broker, BrokerAccount, PortfolioStatus, StrategyBinding
from investorch.portfolio.schema import PortfolioConflictError
from investorch.portfolio.storage import create_broker, create_broker_account, update_portfolio_metadata
from tests.support.config import make_test_config


@pytest.fixture
async def prepared_portfolio(tmp_path):
    config = make_test_config(tmp_path)
    config.workspace_dir.mkdir(parents=True, exist_ok=True)
    (config.workspace_dir / "strategy.py").write_text("def init(context):\n    pass\n")
    now = datetime.now(UTC)
    create_broker(config.portfolio_db, Broker("broker", "qmt", "Broker", now, now))
    create_broker_account(
        config.portfolio_db, BrokerAccount("account", "broker", "external", "Account", "stock", now, now)
    )
    portfolios = PortfolioOperations(config=config)
    portfolio = await portfolios.create(
        name="Portfolio", base_currency="CNY", strategy_binding=StrategyBinding("strategy.py")
    )
    live = LiveExecutionOperations(config=config)
    deployment = await live.prepare_deployment(portfolio.id, "account")
    return config, portfolios, portfolio, live, deployment


@pytest.mark.parametrize("direct_storage", [False, True])
async def test_active_deployment_blocks_archive_without_changing_either_record(prepared_portfolio, direct_storage):
    config, portfolios, portfolio, live, prepared = prepared_portfolio
    active = await live.activate_deployment(prepared.deployment_id)

    with pytest.raises(PortfolioConflictError, match="ACTIVE live deployment"):
        if direct_storage:
            update_portfolio_metadata(config.portfolio_db, replace(portfolio, status=PortfolioStatus.ARCHIVED))
        else:
            await portfolios.archive(portfolio.id)

    assert await portfolios.get(portfolio.id) == portfolio
    assert await live.get_deployment(active.deployment_id) == active


async def test_prepared_allows_archive_but_subsequent_activation_preserves_prepared(prepared_portfolio):
    _config, portfolios, portfolio, live, prepared = prepared_portfolio
    archived = await portfolios.archive(portfolio.id)
    assert archived.status is PortfolioStatus.ARCHIVED
    assert await live.get_deployment(prepared.deployment_id) == prepared

    with pytest.raises(LiveExecutionError):
        await live.activate_deployment(prepared.deployment_id)

    assert await portfolios.get(portfolio.id) == archived
    assert await live.get_deployment(prepared.deployment_id) == prepared


@pytest.mark.parametrize("terminal_status", ["STOPPED", "FAILED"])
async def test_terminal_deployment_allows_archive(prepared_portfolio, terminal_status):
    _config, portfolios, portfolio, live, prepared = prepared_portfolio
    await live.activate_deployment(prepared.deployment_id)
    if terminal_status == "STOPPED":
        terminal = await live.stop_deployment(prepared.deployment_id)
    else:
        terminal = await live.fail_deployment(prepared.deployment_id, "runtime unavailable")

    archived = await portfolios.archive(portfolio.id)

    assert archived.status is PortfolioStatus.ARCHIVED
    assert await portfolios.get(portfolio.id) == archived
    assert await live.get_deployment(prepared.deployment_id) == terminal


async def test_active_deployment_allows_metadata_edits_and_retains_frozen_binding(prepared_portfolio):
    _config, portfolios, portfolio, live, prepared = prepared_portfolio
    active = await live.activate_deployment(prepared.deployment_id)
    binding = StrategyBinding("strategy.py", {"threshold": 2})

    updated = await portfolios.update_metadata(
        portfolio.id, name="Renamed", description="Updated description", strategy_binding=binding
    )

    assert updated.name == "Renamed"
    assert updated.description == "Updated description"
    assert updated.strategy_binding == binding
    assert updated.status is PortfolioStatus.ACTIVE
    assert await portfolios.get(portfolio.id) == updated
    assert await live.get_deployment(prepared.deployment_id) == active
