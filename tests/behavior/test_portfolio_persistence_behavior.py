from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from investorch.portfolio import (
    Portfolio,
    PortfolioAlreadyExistsError,
    PortfolioConflictError,
    PortfolioNotFoundError,
    PortfolioStatus,
    StrategyBinding,
    create_portfolio,
    get_portfolio,
    init_portfolio_storage,
    list_portfolios,
    update_portfolio_metadata,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "portfolio.db"
    init_portfolio_storage(db_path)
    return db_path


def test_portfolio_metadata_round_trips_through_sqlite(tmp_path: Path) -> None:
    db_path = make_db(tmp_path)
    portfolio = Portfolio(
        "portfolio-1",
        "Core",
        "CNY",
        NOW,
        NOW + timedelta(hours=1),
        description="Long-term holdings",
        strategy_binding=StrategyBinding(
            "strategies/value.py",
            {"lookback": 20, "filters": {"markets": ["XSHG", "XSHE"], "enabled": True}},
        ),
    )

    create_portfolio(db_path, portfolio)

    assert get_portfolio(db_path, portfolio.id) == portfolio


def test_portfolio_listing_excludes_archived_by_default_and_is_deterministic(tmp_path: Path) -> None:
    db_path = make_db(tmp_path)
    older = Portfolio("older", "Older", "CNY", NOW, NOW)
    newer = Portfolio("newer", "Newer", "USD", NOW, NOW + timedelta(days=1), description=None)
    archived = Portfolio("archived", "Archived", "CNY", NOW, NOW + timedelta(days=2), status=PortfolioStatus.ARCHIVED)
    for portfolio in (older, archived, newer):
        create_portfolio(db_path, portfolio)

    assert list_portfolios(db_path) == [newer, older]
    assert list_portfolios(db_path, include_archived=True) == [archived, newer, older]


def test_portfolio_metadata_update_changes_only_mutable_fields(tmp_path: Path) -> None:
    db_path = make_db(tmp_path)
    original = Portfolio("portfolio-1", "Core", "CNY", NOW, NOW)
    create_portfolio(db_path, original)
    updated = replace(
        original,
        name="Renamed",
        description="Updated",
        status=PortfolioStatus.ARCHIVED,
        strategy_binding=StrategyBinding("strategies/new.py", {"threshold": 0.1}),
        updated_at=NOW + timedelta(days=1),
    )

    update_portfolio_metadata(db_path, updated)

    assert get_portfolio(db_path, original.id) == updated

    with pytest.raises(PortfolioConflictError, match="base_currency"):
        update_portfolio_metadata(db_path, replace(updated, base_currency="USD"))
    with pytest.raises(PortfolioConflictError, match="created_at"):
        update_portfolio_metadata(db_path, replace(updated, created_at=NOW + timedelta(seconds=1)))


def test_portfolio_create_and_update_fail_with_typed_identity_errors(tmp_path: Path) -> None:
    db_path = make_db(tmp_path)
    portfolio = Portfolio("portfolio-1", "Core", "CNY", NOW, NOW)
    create_portfolio(db_path, portfolio)

    with pytest.raises(PortfolioAlreadyExistsError):
        create_portfolio(db_path, portfolio)
    with pytest.raises(PortfolioNotFoundError):
        update_portfolio_metadata(db_path, replace(portfolio, id="missing"))

    assert get_portfolio(db_path, "missing") is None
