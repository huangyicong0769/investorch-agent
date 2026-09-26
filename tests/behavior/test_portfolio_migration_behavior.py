import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from investorch.portfolio import PortfolioSchemaError, init_portfolio_storage
from investorch.portfolio.schema import LegacyPortfolioMigrationRequiredError
from tests.support.portfolio_migration import load_legacy_portfolio_fixture, portfolio_semantic_snapshot


@pytest.mark.parametrize("version", [1, 2])
def test_non_live_history_upgrades_without_changing_economics(tmp_path, version):
    db = tmp_path / "portfolio.db"
    load_legacy_portfolio_fixture(db, version)
    before = portfolio_semantic_snapshot(db)
    init_portfolio_storage(db)
    init_portfolio_storage(db)
    assert portfolio_semantic_snapshot(db) == before
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (6,)
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]


@pytest.mark.parametrize("version", [3, 4, 5])
def test_startup_requires_explicit_legacy_retirement(tmp_path, version):
    db = tmp_path / "portfolio.db"
    load_legacy_portfolio_fixture(db, version)
    before = db.read_bytes()
    with pytest.raises(LegacyPortfolioMigrationRequiredError, match="investorch migrate portfolio"):
        init_portfolio_storage(db)
    assert db.read_bytes() == before


@pytest.mark.parametrize(
    "sql",
    [
        "DROP INDEX portfolio_account_cash_unallocated",
        "CREATE TABLE surprise (value TEXT)",
        "UPDATE portfolio_cash SET amount = '999'",
    ],
)
def test_claimed_v6_rejects_malformed_shape_or_projection(tmp_path, sql):
    db = tmp_path / "portfolio.db"
    load_legacy_portfolio_fixture(db, 2)
    init_portfolio_storage(db)
    with sqlite3.connect(db) as connection:
        connection.execute(sql)
    before = db.read_bytes()
    with pytest.raises(PortfolioSchemaError):
        init_portfolio_storage(db)
    assert db.read_bytes() == before


def test_v1_last_migration_statement_failure_rolls_back_every_change(tmp_path, monkeypatch):
    db = tmp_path / "portfolio.db"
    load_legacy_portfolio_fixture(db, 1)
    before = db.read_bytes()
    real_connect = sqlite3.connect

    class FailingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql == "PRAGMA user_version = 6":
                raise sqlite3.OperationalError("injected final statement failure")
            return super().execute(sql, parameters)

    def connect(path, *args, **kwargs):
        if Path(path) == db:
            kwargs["factory"] = FailingConnection
        return real_connect(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        init_portfolio_storage(db)
    assert db.read_bytes() == before
