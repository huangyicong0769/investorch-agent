from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

LATEST_SCHEMA_VERSION = 6


class PortfolioStorageError(Exception):
    """Base error for Portfolio persistence failures."""


class PortfolioSchemaError(PortfolioStorageError):
    """Raised when the Portfolio database schema cannot be used safely."""


class UnsupportedPortfolioSchemaError(PortfolioSchemaError):
    """Raised when the Portfolio database schema is not supported."""


class PortfolioDataError(PortfolioStorageError):
    """Raised when persisted Portfolio data violates the storage contract."""


class PortfolioNotFoundError(PortfolioStorageError):
    """Raised when a requested Portfolio does not exist."""


class PortfolioAlreadyExistsError(PortfolioStorageError):
    """Raised when a Portfolio id is already persisted."""


class PortfolioConflictError(PortfolioStorageError):
    """Raised when a Portfolio write conflicts with persisted data."""


class PortfolioSequenceConflictError(PortfolioConflictError):
    """Raised when persisted Ledger append order advanced before commit."""


class LegacyPortfolioMigrationRequiredError(PortfolioSchemaError):
    """Legacy live state requires an explicit, backed-up retirement."""


class LegacyLiveDeploymentActiveError(PortfolioSchemaError):
    """Legacy execution has not reached a terminal state."""


def init_portfolio_storage(db_path: str | Path) -> None:
    """Create v6 or migrate non-live schemas; never silently retire live state."""
    with closing(sqlite3.connect(db_path, isolation_level=None)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            version = schema_version(connection)
            validate_schema(connection, version)
            if version in (3, 4, 5):
                raise LegacyPortfolioMigrationRequiredError(
                    f"Portfolio schema v{version} requires explicit retirement; run investorch migrate portfolio"
                )
            if version == 6:
                return
            if version in (1, 2):
                # Release the lock: archive creation must precede schema mutation.
                connection.rollback()
            else:
                upgrade_schema(connection, version)
                connection.commit()
        except BaseException:
            connection.rollback()
            raise

    if version in (1, 2):
        from investorch.portfolio.migration import migrate_portfolio

        migrate_portfolio(db_path)


def schema_version(connection: sqlite3.Connection) -> int:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version not in range(LATEST_SCHEMA_VERSION + 1):
        raise UnsupportedPortfolioSchemaError(f"Unsupported Portfolio schema version {version}; supported latest is 6")
    return version


def _schema_sql(version: int) -> str:
    from investorch.portfolio._schema_sql import (
        _ACCOUNT_PROJECTIONS_SQL,
        _ACTIVE_OWNERSHIP_SQL,
        _BASE_TABLES_SQL,
        _BROKER_TABLES_SQL,
        _LIVE_DEPLOYMENTS_SQL,
        _LIVE_TRADE_IDENTITY_SQL,
    )

    if version == 0:
        return ""
    if version == 1:
        return _BASE_TABLES_SQL.replace(
            "    broker_account_id TEXT REFERENCES broker_accounts(broker_account_id),\n", ""
        )
    sql = _BROKER_TABLES_SQL + _BASE_TABLES_SQL + _ACCOUNT_PROJECTIONS_SQL
    if version in (3, 4, 5):
        sql += _LIVE_DEPLOYMENTS_SQL
    if version in (4, 5):
        sql += _ACTIVE_OWNERSHIP_SQL
    if version == 5:
        sql += _LIVE_TRADE_IDENTITY_SQL
    return sql


def _execute_sql(connection: sqlite3.Connection, sql: str) -> None:
    # Only fixed DDL constants, containing no semicolons in string literals.
    for statement in sql.split(";"):
        if statement.strip():
            connection.execute(statement)


def _normalize_sql(sql: str) -> str:
    """Ignore SQL formatting while preserving quoted values and escaped quotes."""
    import re

    return "".join(
        part if index % 2 else re.sub(r"\s+", "", part).lower()
        for index, part in enumerate(re.split(r"('(?:''|[^'])*')", sql))
    )


def _checks(sql: str) -> tuple[str, ...]:
    import re

    expressions = []
    for match in re.finditer(r"\bCHECK\s*\(", sql, re.IGNORECASE):
        start = match.end()
        depth, end = 1, start
        while depth and end < len(sql):
            depth += (sql[end] == "(") - (sql[end] == ")")
            end += 1
        expressions.append(_normalize_sql(sql[start : end - 1]))
    return tuple(sorted(expressions))


def _shape(connection: sqlite3.Connection) -> dict:
    result = {}
    for kind, name, table, sql in connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
    ):
        quoted = '"' + name.replace('"', '""') + '"'
        if kind == "table":
            columns = tuple(tuple(row) for row in connection.execute(f"PRAGMA table_xinfo({quoted})"))
            foreign_keys = tuple(
                sorted(tuple(row)[2:] for row in connection.execute(f"PRAGMA foreign_key_list({quoted})"))
            )
            indexes = []
            for row in connection.execute(f"PRAGMA index_list({quoted})"):
                index_name = '"' + row[1].replace('"', '""') + '"'
                indexes.append(
                    (
                        row[2],
                        row[3],
                        row[4],
                        tuple(tuple(r) for r in connection.execute(f"PRAGMA index_xinfo({index_name})")),
                    )
                )
            result[name] = (kind, columns, foreign_keys, tuple(sorted(indexes)), _checks(sql))
        else:
            result[name] = (kind, table, _normalize_sql(sql or ""))
    return result


def validate_schema(connection: sqlite3.Connection, version: int) -> None:
    """Reject unknown structures, including lost constraints and partial indexes."""
    actual = _shape(connection)
    if version == 0 and actual:
        raise PortfolioSchemaError("refusing to initialize an unversioned non-empty database")
    with closing(sqlite3.connect(":memory:")) as expected:
        _execute_sql(expected, _schema_sql(version))
        wanted = _shape(expected)
    if actual != wanted:
        changed = sorted(name for name in actual.keys() | wanted.keys() if actual.get(name) != wanted.get(name))
        raise PortfolioSchemaError(f"Portfolio schema v{version} shape mismatch: {', '.join(changed)}")
    validate_integrity(connection)
    if version >= 2:
        validate_projections(connection)


def validate_integrity(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise PortfolioSchemaError("Portfolio foreign_key_check failed")
    if [row[0] for row in connection.execute("PRAGMA quick_check")] != ["ok"]:
        raise PortfolioSchemaError("Portfolio quick_check failed")


def validate_projections(connection: sqlite3.Connection) -> None:
    from decimal import Decimal

    from investorch.portfolio.storage import _get_portfolio_state_with_attribution

    old_factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        for (portfolio_id,) in connection.execute("SELECT portfolio_id FROM portfolios"):
            state = _get_portfolio_state_with_attribution(connection, portfolio_id)
            cash, holdings = {}, {}
            for account in state.accounts.values():
                for currency, amount in account.cash.items():
                    cash[currency] = cash.get(currency, Decimal(0)) + amount
                for instrument, holding in account.holdings.items():
                    quantity, cost = holdings.get(instrument, (Decimal(0), Decimal(0)))
                    holdings[instrument] = (
                        quantity + holding.quantity,
                        None if cost is None or holding.total_cost is None else cost + holding.total_cost,
                    )
            aggregate = {
                instrument: (holding.quantity, holding.total_cost)
                for instrument, holding in state.aggregate.holdings.items()
            }
            if cash != state.aggregate.cash or holdings != aggregate:
                raise PortfolioSchemaError(f"Aggregate/account projection mismatch for Portfolio {portfolio_id}")
    finally:
        connection.row_factory = old_factory


def upgrade_schema(connection: sqlite3.Connection, version: int) -> None:
    """Apply only inside the caller's single explicit transaction."""
    from investorch.portfolio._schema_sql import (
        _ACCOUNT_PROJECTIONS_SQL,
        _BROKER_TABLES_SQL,
        _LEGACY_ATTRIBUTION_SQL,
    )

    if not connection.in_transaction:
        raise PortfolioSchemaError("Portfolio migration requires an explicit transaction")
    if version == 0:
        _execute_sql(connection, _schema_sql(6))
    elif version == 1:
        _execute_sql(connection, _BROKER_TABLES_SQL + _ACCOUNT_PROJECTIONS_SQL + _LEGACY_ATTRIBUTION_SQL)
    elif version in (3, 4, 5):
        if version == 5:
            connection.execute("DROP INDEX live_trade_external_identity")
        connection.execute("DROP TABLE live_deployments")
    validate_schema(connection, 6)
    connection.execute("PRAGMA user_version = 6")
