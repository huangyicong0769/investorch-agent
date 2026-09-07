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


_BASE_TABLES_SQL = """
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
    broker_account_id TEXT REFERENCES broker_accounts(broker_account_id),
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


_BROKER_TABLES_SQL = """
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
"""


_ACCOUNT_PROJECTIONS_SQL = """
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
"""


_LIVE_DEPLOYMENTS_SQL = """
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
"""


_ACTIVE_OWNERSHIP_SQL = """
CREATE UNIQUE INDEX live_deployment_active_portfolio
            ON live_deployments(portfolio_id) WHERE status = 'ACTIVE';
"""


_LIVE_TRADE_IDENTITY_SQL = """
CREATE UNIQUE INDEX live_trade_external_identity
            ON portfolio_ledger(external_ref)
            WHERE source = 'live_execution' AND external_ref IS NOT NULL;
"""


_LEGACY_ATTRIBUTION_SQL = """
ALTER TABLE portfolio_ledger ADD COLUMN broker_account_id TEXT
    REFERENCES broker_accounts(broker_account_id);
INSERT INTO portfolio_account_holdings
    SELECT portfolio_id, NULL, instrument_code, market, quantity, total_cost
    FROM portfolio_holdings;
INSERT INTO portfolio_account_cash
    SELECT portfolio_id, NULL, currency, amount FROM portfolio_cash;
"""


def init_portfolio_storage(db_path: str | Path) -> None:
    """Create the canonical schema or upgrade an older schema atomically."""
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
            sql = (
                _BROKER_TABLES_SQL
                + _BASE_TABLES_SQL
                + _ACCOUNT_PROJECTIONS_SQL
                + _LIVE_DEPLOYMENTS_SQL
                + _ACTIVE_OWNERSHIP_SQL
                + _LIVE_TRADE_IDENTITY_SQL
            )
        elif version in (1, 2, 3, 4):
            sql = ""
            if version == 1:
                sql += _BROKER_TABLES_SQL + _ACCOUNT_PROJECTIONS_SQL + _LEGACY_ATTRIBUTION_SQL
            if version <= 2:
                sql += _LIVE_DEPLOYMENTS_SQL
            if version <= 3:
                sql += _ACTIVE_OWNERSHIP_SQL
            sql += _LIVE_TRADE_IDENTITY_SQL
        else:
            raise UnsupportedPortfolioSchemaError(f"No migration from Portfolio schema {version}")
        try:
            # A single executescript owns BEGIN through COMMIT. Calling executescript
            # again after BEGIN would implicitly commit a partly upgraded schema.
            connection.executescript(
                "BEGIN IMMEDIATE;\n" + sql + f"PRAGMA user_version = {LATEST_SCHEMA_VERSION};\nCOMMIT;"
            )
        except BaseException:
            connection.rollback()
            raise


def _has_user_tables(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
    ).fetchone()
    return row is not None
