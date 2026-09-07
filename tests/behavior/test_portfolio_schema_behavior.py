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
                "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    assert version == LATEST_SCHEMA_VERSION
    assert tables == {
        "portfolios",
        "portfolio_ledger",
        "portfolio_holdings",
        "portfolio_cash",
        "brokers",
        "broker_accounts",
        "portfolio_account_holdings",
        "portfolio_account_cash",
        "live_deployments",
    }


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


def test_late_migration_failure_preserves_entire_v1_schema_and_data(tmp_path: Path) -> None:
    db_path = tmp_path / "portfolio.db"
    fixture = Path(__file__).parent / "fixtures" / "portfolio_schema_v1.sql"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(fixture.read_text())
        connection.execute(
            "INSERT INTO portfolios VALUES ('p', 'Legacy', NULL, 'ACTIVE', 'CNY', NULL, NULL, '2026-09-07', '2026-09-07')"
        )
        connection.execute("INSERT INTO portfolio_cash VALUES ('p', 'CNY', '123.45')")
        # An existing conflicting index forces the last required DDL statement to fail.
        connection.execute("CREATE INDEX live_trade_external_identity ON portfolio_ledger(entry_id)")
        before_schema = connection.execute("SELECT type, name, sql FROM sqlite_schema ORDER BY type, name").fetchall()
        before_cash = connection.execute("SELECT * FROM portfolio_cash").fetchall()
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        init_portfolio_storage(db_path)
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert (
            connection.execute("SELECT type, name, sql FROM sqlite_schema ORDER BY type, name").fetchall()
            == before_schema
        )
        assert connection.execute("SELECT * FROM portfolio_cash").fetchall() == before_cash


@pytest.mark.parametrize("old_version", [2, 3, 4])
def test_supported_attributed_schemas_upgrade_preserving_account_state(tmp_path: Path, old_version: int) -> None:
    db_path = tmp_path / "portfolio.db"
    init_portfolio_storage(db_path)
    with sqlite3.connect(db_path) as connection:
        # Remove only features absent from the historical version under test.
        connection.execute("DROP INDEX live_trade_external_identity")
        if old_version <= 3:
            connection.execute("DROP INDEX live_deployment_active_portfolio")
        if old_version == 2:
            connection.execute("DROP TABLE live_deployments")
        connection.execute(f"PRAGMA user_version = {old_version}")
        connection.execute("INSERT INTO brokers VALUES ('b', 'qmt', 'Broker', '{}', '2026-09-07', '2026-09-07')")
        connection.execute(
            "INSERT INTO broker_accounts VALUES ('a', 'b', 'external', 'Trading', 'stock', '{}', '2026-09-07', '2026-09-07')"
        )
        connection.execute(
            "INSERT INTO portfolios VALUES ('p', 'Existing', NULL, 'ACTIVE', 'CNY', NULL, NULL, '2026-09-07', '2026-09-07')"
        )
        connection.execute("INSERT INTO portfolio_cash VALUES ('p', 'CNY', '123.45')")
        connection.execute("INSERT INTO portfolio_account_cash VALUES ('p', 'a', 'CNY', '123.45')")
    init_portfolio_storage(db_path)
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == LATEST_SCHEMA_VERSION
        assert connection.execute("SELECT * FROM portfolio_account_cash").fetchall() == [("p", "a", "CNY", "123.45")]
        indexes = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type = 'index'")}
        assert {"live_trade_external_identity", "live_deployment_active_portfolio"} <= indexes
