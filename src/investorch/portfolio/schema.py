from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

LATEST_SCHEMA_VERSION = 1


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
            _create_latest_schema(connection)
            return
        _migrate_to_latest(connection, version)


def _has_user_tables(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
    ).fetchone()
    return row is not None


def _create_latest_schema(connection: sqlite3.Connection) -> None:
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
        connection.execute(f"PRAGMA user_version = {LATEST_SCHEMA_VERSION}")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _migrate_to_latest(connection: sqlite3.Connection, from_version: int) -> None:
    del connection
    raise UnsupportedPortfolioSchemaError(
        f"Portfolio schema version {from_version} has no supported migration to version {LATEST_SCHEMA_VERSION}"
    )
