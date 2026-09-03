from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from investorch.portfolio.domain import Portfolio, PortfolioStatus, StrategyBinding
from investorch.portfolio.schema import (
    PortfolioAlreadyExistsError,
    PortfolioConflictError,
    PortfolioDataError,
    PortfolioNotFoundError,
)
from investorch.portfolio.serialization import deserialize_strategy_parameters, serialize_strategy_parameters


def create_portfolio(db_path: str | Path, portfolio: Portfolio) -> None:
    """Persist a new Portfolio without requiring opening Ledger entries."""
    strategy_source_path, strategy_parameters_json = _serialize_strategy_binding(portfolio.strategy_binding)
    with closing(_connect(db_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """
                INSERT INTO portfolios (
                    portfolio_id, name, description, status, base_currency,
                    strategy_source_path, strategy_parameters_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    portfolio.id,
                    portfolio.name,
                    portfolio.description,
                    portfolio.status.value,
                    portfolio.base_currency,
                    strategy_source_path,
                    strategy_parameters_json,
                    portfolio.created_at.isoformat(),
                    portfolio.updated_at.isoformat(),
                ),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise PortfolioAlreadyExistsError(f"Portfolio already exists: {portfolio.id}") from exc
        except BaseException:
            connection.rollback()
            raise


def get_portfolio(db_path: str | Path, portfolio_id: str) -> Portfolio | None:
    """Load one Portfolio's metadata without its Ledger or projections."""
    with closing(_connect(db_path)) as connection:
        return _get_portfolio(connection, portfolio_id)


def list_portfolios(db_path: str | Path, *, include_archived: bool = False) -> list[Portfolio]:
    """List Portfolio metadata in deterministic most-recently-updated order."""
    query = "SELECT * FROM portfolios"
    parameters: tuple[str, ...] = ()
    if not include_archived:
        query += " WHERE status = ?"
        parameters = (PortfolioStatus.ACTIVE.value,)
    query += " ORDER BY updated_at DESC, portfolio_id ASC"

    with closing(_connect(db_path)) as connection:
        return [_row_to_portfolio(row) for row in connection.execute(query, parameters)]


def update_portfolio_metadata(db_path: str | Path, portfolio: Portfolio) -> None:
    """Persist mutable metadata while protecting accounting identity fields."""
    strategy_source_path, strategy_parameters_json = _serialize_strategy_binding(portfolio.strategy_binding)
    with closing(_connect(db_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = _get_portfolio(connection, portfolio.id)
            if existing is None:
                raise PortfolioNotFoundError(f"Portfolio not found: {portfolio.id}")
            if existing.base_currency != portfolio.base_currency:
                raise PortfolioConflictError("base_currency cannot be changed by metadata update")
            if existing.created_at != portfolio.created_at:
                raise PortfolioConflictError("created_at cannot be changed by metadata update")

            connection.execute(
                """
                UPDATE portfolios
                SET name = ?, description = ?, status = ?, strategy_source_path = ?,
                    strategy_parameters_json = ?, updated_at = ?
                WHERE portfolio_id = ?
                """,
                (
                    portfolio.name,
                    portfolio.description,
                    portfolio.status.value,
                    strategy_source_path,
                    strategy_parameters_json,
                    portfolio.updated_at.isoformat(),
                    portfolio.id,
                ),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def _connect(db_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _get_portfolio(connection: sqlite3.Connection, portfolio_id: str) -> Portfolio | None:
    row = connection.execute("SELECT * FROM portfolios WHERE portfolio_id = ?", (portfolio_id,)).fetchone()
    return None if row is None else _row_to_portfolio(row)


def _row_to_portfolio(row: sqlite3.Row) -> Portfolio:
    portfolio_id = row["portfolio_id"]
    try:
        source_path = row["strategy_source_path"]
        parameters_json = row["strategy_parameters_json"]
        if (source_path is None) != (parameters_json is None):
            raise ValueError("StrategyBinding columns must both be null or non-null")
        strategy_binding = None
        if source_path is not None:
            strategy_binding = StrategyBinding(
                source_path,
                deserialize_strategy_parameters(parameters_json, portfolio_id=portfolio_id),
            )
        return Portfolio(
            id=portfolio_id,
            name=row["name"],
            base_currency=row["base_currency"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            description=row["description"],
            status=PortfolioStatus(row["status"]),
            strategy_binding=strategy_binding,
        )
    except PortfolioDataError:
        raise
    except (TypeError, ValueError) as exc:
        raise PortfolioDataError(f"invalid persisted Portfolio metadata for {portfolio_id}: {exc}") from exc


def _serialize_strategy_binding(binding: StrategyBinding | None) -> tuple[str | None, str | None]:
    if binding is None:
        return None, None
    return binding.source_path, serialize_strategy_parameters(binding.parameters)
