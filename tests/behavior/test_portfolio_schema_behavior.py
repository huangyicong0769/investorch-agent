from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from investorch.portfolio import (
    LATEST_SCHEMA_VERSION,
    PortfolioSchemaError,
    UnsupportedPortfolioSchemaError,
    init_portfolio_storage,
)


def test_new_portfolio_database_uses_latest_schema_idempotently(tmp_path: Path) -> None:
    db_path = tmp_path / "portfolio.db"

    init_portfolio_storage(db_path)
    init_portfolio_storage(db_path)

    with sqlite3.connect(db_path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table' "
                "AND name IN ('portfolios', 'portfolio_ledger', 'portfolio_holdings', 'portfolio_cash')"
            )
        }

    assert version == LATEST_SCHEMA_VERSION
    assert tables == {"portfolios", "portfolio_ledger", "portfolio_holdings", "portfolio_cash"}


def test_newer_portfolio_schema_fails_closed(tmp_path: Path) -> None:
    db_path = tmp_path / "portfolio.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(f"PRAGMA user_version = {LATEST_SCHEMA_VERSION + 1}")

    with pytest.raises(UnsupportedPortfolioSchemaError):
        init_portfolio_storage(db_path)


def test_unversioned_non_empty_database_fails_closed(tmp_path: Path) -> None:
    db_path = tmp_path / "portfolio.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE unrelated_data (value TEXT)")

    with pytest.raises(PortfolioSchemaError, match="unversioned non-empty"):
        init_portfolio_storage(db_path)
