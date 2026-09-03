from __future__ import annotations

import pytest

from investorch.application import (
    PortfolioAlreadyActiveError,
    PortfolioAlreadyArchivedError,
    PortfolioArchivedError,
    PortfolioOperations,
)
from investorch.portfolio import (
    PortfolioNotFoundError,
    PortfolioStatus,
    StrategyBinding,
)
from tests.support.config import make_test_config


async def test_portfolio_application_creates_and_reads_durable_portfolios(tmp_path) -> None:
    config = make_test_config(tmp_path)
    operations = PortfolioOperations(config=config)
    binding = StrategyBinding("strategies/value.py", {"lookback": 20})

    created = await operations.create(
        name="Core",
        base_currency="CNY",
        description="Long-term holdings",
        strategy_binding=binding,
    )

    assert created.id
    assert created.status is PortfolioStatus.ACTIVE
    assert created.base_currency == "CNY"
    assert created.description == "Long-term holdings"
    assert created.strategy_binding == binding
    assert created.created_at == created.updated_at
    assert await operations.get(created.id) == created
    assert await operations.list() == [created]
    assert (await operations.get_state(created.id)).portfolio_id == created.id
    assert await operations.list_ledger(created.id) == []


async def test_portfolio_application_distinguishes_missing_portfolio_from_empty_ledger(tmp_path) -> None:
    operations = PortfolioOperations(config=make_test_config(tmp_path))

    with pytest.raises(PortfolioNotFoundError, match="missing"):
        await operations.get("missing")
    with pytest.raises(PortfolioNotFoundError, match="missing"):
        await operations.list_ledger("missing")


async def test_active_portfolio_metadata_can_be_changed_and_cleared(tmp_path) -> None:
    operations = PortfolioOperations(config=make_test_config(tmp_path))
    portfolio = await operations.create(
        name="Core",
        base_currency="CNY",
        description="Initial",
        strategy_binding=StrategyBinding("strategies/value.py", {"lookback": 20}),
    )

    updated = await operations.update_metadata(
        portfolio.id,
        name="Renamed",
        description=None,
        strategy_binding=None,
    )

    assert updated.name == "Renamed"
    assert updated.description is None
    assert updated.strategy_binding is None
    assert updated.updated_at >= portfolio.updated_at
    assert await operations.get(portfolio.id) == updated
    assert await operations.update_metadata(portfolio.id) == updated


async def test_archive_freezes_metadata_until_restore(tmp_path) -> None:
    operations = PortfolioOperations(config=make_test_config(tmp_path))
    portfolio = await operations.create(name="Core", base_currency="CNY")

    archived = await operations.archive(portfolio.id)

    assert archived.status is PortfolioStatus.ARCHIVED
    assert await operations.list() == []
    assert await operations.list(include_archived=True) == [archived]
    assert await operations.list_ledger(portfolio.id) == []
    with pytest.raises(PortfolioAlreadyArchivedError):
        await operations.archive(portfolio.id)
    with pytest.raises(PortfolioArchivedError):
        await operations.update_metadata(portfolio.id, name="Blocked")
    assert (await operations.get(portfolio.id)).name == "Core"

    restored = await operations.restore(portfolio.id)

    assert restored.status is PortfolioStatus.ACTIVE
    assert await operations.list() == [restored]
    with pytest.raises(PortfolioAlreadyActiveError):
        await operations.restore(portfolio.id)
