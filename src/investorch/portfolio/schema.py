from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

LATEST_SCHEMA_VERSION = 5


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


def init_portfolio_storage(db_path: str | Path) -> None:
    """Create or validate the dedicated Portfolio database schema."""
    with closing(sqlite3.connect(db_path, isolation_level=None)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        version = connection.execute("PRAGMA user_version").fetchone()[0]

        if version > LATEST_SCHEMA_VERSION:
            raise UnsupportedPortfolioSchemaError(
                f"Portfolio schema version {version} is newer than supported version {LATEST_SCHEMA_VERSION}"
            )
        if version == LATEST_SCHEMA_VERSION:
            return
        if version == 0:
            if _has_user_tables(connection):
                raise PortfolioSchemaError("refusing to initialize an unversioned non-empty database")
            _create_v1_schema(connection)
            _migrate_to_latest(connection, 1)
            return
        _migrate_to_latest(connection, version)


def _has_user_tables(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
    ).fetchone()
    return row is not None


def _create_v1_schema(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            """
            BEGIN IMMEDIATE;

            CREATE TABLE portfolios (
                portfolio_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL,
                base_currency TEXT NOT NULL,
                strategy_source_path TEXT,
                strategy_parameters_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK (status IN ('ACTIVE', 'ARCHIVED')),
                CHECK (
                    (strategy_source_path IS NULL AND strategy_parameters_json IS NULL)
                    OR
                    (strategy_source_path IS NOT NULL AND strategy_parameters_json IS NOT NULL)
                )
            );

            CREATE TABLE portfolio_ledger (
                entry_id TEXT PRIMARY KEY,
                portfolio_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                entry_type TEXT NOT NULL,
                effective_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                source TEXT NOT NULL,
                external_ref TEXT,
                payload_json TEXT NOT NULL,
                FOREIGN KEY (portfolio_id) REFERENCES portfolios(portfolio_id),
                UNIQUE (portfolio_id, sequence),
                CHECK (sequence > 0),
                CHECK (
                    entry_type IN (
                        'OPENING_POSITION',
                        'OPENING_CASH',
                        'TRADE',
                        'CASH_FLOW',
                        'INCOME',
                        'TRANSFER',
                        'ADJUSTMENT',
                        'VOID'
                    )
                )
            );

            CREATE TABLE portfolio_holdings (
                portfolio_id TEXT NOT NULL,
                instrument_code TEXT NOT NULL,
                market TEXT NOT NULL,
                quantity TEXT NOT NULL,
                total_cost TEXT,
                PRIMARY KEY (portfolio_id, instrument_code, market),
                FOREIGN KEY (portfolio_id) REFERENCES portfolios(portfolio_id)
            );

            CREATE TABLE portfolio_cash (
                portfolio_id TEXT NOT NULL,
                currency TEXT NOT NULL,
                amount TEXT NOT NULL,
                PRIMARY KEY (portfolio_id, currency),
                FOREIGN KEY (portfolio_id) REFERENCES portfolios(portfolio_id)
            );
            """
        )
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _migrate_v1_to_v2(connection: sqlite3.Connection, from_version: int) -> None:
    if from_version != 1:
        raise UnsupportedPortfolioSchemaError(
            f"Portfolio schema version {from_version} has no supported migration to version {LATEST_SCHEMA_VERSION}"
        )
    try:
        connection.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE brokers (
                broker_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                display_name TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE broker_accounts (
                broker_account_id TEXT PRIMARY KEY,
                broker_id TEXT NOT NULL REFERENCES brokers(broker_id),
                external_account_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                account_type TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (broker_id, external_account_id)
            );
            ALTER TABLE portfolio_ledger ADD COLUMN broker_account_id TEXT
                REFERENCES broker_accounts(broker_account_id);
            CREATE TABLE portfolio_account_holdings (
                portfolio_id TEXT NOT NULL REFERENCES portfolios(portfolio_id),
                broker_account_id TEXT REFERENCES broker_accounts(broker_account_id),
                instrument_code TEXT NOT NULL,
                market TEXT NOT NULL,
                quantity TEXT NOT NULL,
                total_cost TEXT
            );
            CREATE UNIQUE INDEX portfolio_account_holdings_allocated
                ON portfolio_account_holdings(portfolio_id, broker_account_id, instrument_code, market)
                WHERE broker_account_id IS NOT NULL;
            CREATE UNIQUE INDEX portfolio_account_holdings_unallocated
                ON portfolio_account_holdings(portfolio_id, instrument_code, market)
                WHERE broker_account_id IS NULL;
            CREATE TABLE portfolio_account_cash (
                portfolio_id TEXT NOT NULL REFERENCES portfolios(portfolio_id),
                broker_account_id TEXT REFERENCES broker_accounts(broker_account_id),
                currency TEXT NOT NULL,
                amount TEXT NOT NULL
            );
            CREATE UNIQUE INDEX portfolio_account_cash_allocated
                ON portfolio_account_cash(portfolio_id, broker_account_id, currency)
                WHERE broker_account_id IS NOT NULL;
            CREATE UNIQUE INDEX portfolio_account_cash_unallocated
                ON portfolio_account_cash(portfolio_id, currency)
                WHERE broker_account_id IS NULL;
            INSERT INTO portfolio_account_holdings
                SELECT portfolio_id, NULL, instrument_code, market, quantity, total_cost
                FROM portfolio_holdings;
            INSERT INTO portfolio_account_cash
                SELECT portfolio_id, NULL, currency, amount FROM portfolio_cash;
        """)
        connection.execute("PRAGMA user_version = 2")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _migrate_v2_to_v3(connection: sqlite3.Connection, from_version: int) -> None:
    if from_version != 2:
        raise UnsupportedPortfolioSchemaError(f"No migration from Portfolio schema {from_version}")
    try:
        connection.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE live_deployments (
                deployment_id TEXT PRIMARY KEY,
                portfolio_id TEXT NOT NULL REFERENCES portfolios(portfolio_id),
                broker_account_id TEXT NOT NULL REFERENCES broker_accounts(broker_account_id),
                strategy_source_path TEXT NOT NULL,
                strategy_sha256 TEXT NOT NULL,
                strategy_parameters_json TEXT NOT NULL,
                strategy_artifact_relpath TEXT NOT NULL,
                rqalpha_version TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('PREPARED','ACTIVE','STOPPED','FAILED')),
                bootstrap_ledger_sequence INTEGER,
                created_at TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                failure_reason TEXT
            );
        """)
        connection.execute("PRAGMA user_version = 3")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _migrate_v3_to_v4(connection: sqlite3.Connection, from_version: int) -> None:
    if from_version != 3:
        raise UnsupportedPortfolioSchemaError(f"No migration from Portfolio schema {from_version}")
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("""CREATE UNIQUE INDEX live_deployment_active_portfolio
            ON live_deployments(portfolio_id) WHERE status = 'ACTIVE'""")
        connection.execute("PRAGMA user_version = 4")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _migrate_to_latest(connection: sqlite3.Connection, from_version: int) -> None:
    if from_version == 1:
        _migrate_v1_to_v2(connection, from_version)
        from_version = 2
    if from_version == 2:
        _migrate_v2_to_v3(connection, from_version)
        from_version = 3
    if from_version == 3:
        _migrate_v3_to_v4(connection, from_version)
        from_version = 4
    if from_version != 4:
        raise UnsupportedPortfolioSchemaError(f"No migration from Portfolio schema {from_version}")
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("""CREATE UNIQUE INDEX live_trade_external_identity
            ON portfolio_ledger(external_ref)
            WHERE source = 'live_execution' AND external_ref IS NOT NULL""")
        connection.execute("PRAGMA user_version = 5")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
