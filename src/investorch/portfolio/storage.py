from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from investorch.portfolio.domain import (
    Broker,
    BrokerAccount,
    HoldingState,
    InstrumentId,
    LedgerEntry,
    LedgerEntryType,
    Portfolio,
    PortfolioAccountState,
    PortfolioState,
    PortfolioStateWithAttribution,
    PortfolioStatus,
    StrategyBinding,
)
from investorch.portfolio.ledger import project_portfolio_with_attribution
from investorch.portfolio.schema import (
    PortfolioAlreadyExistsError,
    PortfolioConflictError,
    PortfolioDataError,
    PortfolioNotFoundError,
    PortfolioSequenceConflictError,
)
from investorch.portfolio.serialization import (
    deserialize_ledger_payload,
    deserialize_strategy_parameters,
    serialize_ledger_payload,
    serialize_strategy_parameters,
)


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


def append_ledger_operation(
    db_path: str | Path,
    entries: Iterable[LedgerEntry],
) -> dict[str, PortfolioState]:
    """Append one atomic Ledger operation and replace all affected projections."""
    proposed = tuple(entries)
    if not proposed:
        raise PortfolioConflictError("Ledger operation must contain at least one entry")
    if any(not isinstance(entry, LedgerEntry) for entry in proposed):
        raise PortfolioConflictError("Ledger operation must contain only LedgerEntry values")
    if len({entry.operation_id for entry in proposed}) != 1:
        raise PortfolioConflictError("Ledger operation entries must share one operation_id")

    portfolio_ids = sorted({entry.portfolio_id for entry in proposed})
    with closing(_connect(db_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            portfolios: dict[str, Portfolio] = {}
            projected_states: dict[str, PortfolioStateWithAttribution] = {}
            for portfolio_id in portfolio_ids:
                portfolio = _get_portfolio(connection, portfolio_id)
                if portfolio is None:
                    raise PortfolioNotFoundError(f"Portfolio not found: {portfolio_id}")
                portfolios[portfolio_id] = portfolio

            _validate_entry_id_conflicts(connection, proposed)
            for portfolio_id, portfolio in portfolios.items():
                existing = _list_ledger_entries(connection, portfolio_id)
                additions = [entry for entry in proposed if entry.portfolio_id == portfolio_id]
                _validate_sequence_conflicts(existing, additions)
                projected_states[portfolio_id] = project_portfolio_with_attribution(portfolio, [*existing, *additions])

            _insert_ledger_entries(connection, proposed)
            for state in projected_states.values():
                _replace_attributed_projection(connection, state)
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise PortfolioConflictError(f"Ledger operation conflicts with persisted data: {exc}") from exc
        except BaseException:
            connection.rollback()
            raise

    return {portfolio_id: state.aggregate for portfolio_id, state in projected_states.items()}


def list_ledger_entries(db_path: str | Path, portfolio_id: str) -> list[LedgerEntry]:
    """Load typed Ledger entries in append/audit sequence order."""
    with closing(_connect(db_path)) as connection:
        return _list_ledger_entries(connection, portfolio_id)


def get_portfolio_state(db_path: str | Path, portfolio_id: str) -> PortfolioState:
    """Read aggregate Holdings and Cash at one consistent SQLite point."""
    with closing(_connect(db_path)) as connection:
        connection.execute("BEGIN")
        return _get_portfolio_state(connection, portfolio_id)


def _get_portfolio_state(connection: sqlite3.Connection, portfolio_id: str) -> PortfolioState:
    if _get_portfolio(connection, portfolio_id) is None:
        raise PortfolioNotFoundError(f"Portfolio not found: {portfolio_id}")
    try:
        holdings = {}
        for row in connection.execute(
            """
            SELECT instrument_code, market, quantity, total_cost
            FROM portfolio_holdings
            WHERE portfolio_id = ?
            ORDER BY instrument_code, market
            """,
            (portfolio_id,),
        ):
            instrument = InstrumentId(row["instrument_code"], row["market"])
            holdings[instrument] = HoldingState(
                instrument,
                Decimal(row["quantity"]),
                None if row["total_cost"] is None else Decimal(row["total_cost"]),
            )
        cash = {
            row["currency"]: Decimal(row["amount"])
            for row in connection.execute(
                "SELECT currency, amount FROM portfolio_cash WHERE portfolio_id = ? ORDER BY currency",
                (portfolio_id,),
            )
        }
        return PortfolioState(portfolio_id, holdings, cash)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PortfolioDataError(f"invalid persisted projection for Portfolio {portfolio_id}: {exc}") from exc


def rebuild_portfolio_projection(db_path: str | Path, portfolio_id: str) -> PortfolioState:
    """Rebuild and persist one Portfolio projection from its complete Ledger."""
    with closing(_connect(db_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            portfolio = _get_portfolio(connection, portfolio_id)
            if portfolio is None:
                raise PortfolioNotFoundError(f"Portfolio not found: {portfolio_id}")
            state = project_portfolio_with_attribution(portfolio, _list_ledger_entries(connection, portfolio_id))
            _replace_attributed_projection(connection, state)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return state.aggregate


def _connect(db_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _get_portfolio(connection: sqlite3.Connection, portfolio_id: str) -> Portfolio | None:
    row = connection.execute("SELECT * FROM portfolios WHERE portfolio_id = ?", (portfolio_id,)).fetchone()
    return None if row is None else _row_to_portfolio(row)


def _list_ledger_entries(connection: sqlite3.Connection, portfolio_id: str) -> list[LedgerEntry]:
    rows = connection.execute(
        "SELECT * FROM portfolio_ledger WHERE portfolio_id = ? ORDER BY sequence ASC",
        (portfolio_id,),
    )
    return [_row_to_ledger_entry(row) for row in rows]


def _row_to_ledger_entry(row: sqlite3.Row) -> LedgerEntry:
    entry_id = row["entry_id"]
    portfolio_id = row["portfolio_id"]
    entry_type_value = row["entry_type"]
    try:
        entry_type = LedgerEntryType(entry_type_value)
        return LedgerEntry(
            entry_id=entry_id,
            operation_id=row["operation_id"],
            portfolio_id=portfolio_id,
            sequence=row["sequence"],
            entry_type=entry_type,
            effective_at=datetime.fromisoformat(row["effective_at"]),
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
            source=row["source"],
            external_ref=row["external_ref"],
            broker_account_id=row["broker_account_id"],
            payload=deserialize_ledger_payload(entry_type, row["payload_json"], entry_id=entry_id),
        )
    except PortfolioDataError as exc:
        raise PortfolioDataError(
            f"invalid persisted Ledger entry for Portfolio {portfolio_id}: {entry_id} ({entry_type_value}): {exc}"
        ) from exc
    except (TypeError, ValueError) as exc:
        raise PortfolioDataError(
            f"invalid persisted Ledger entry {entry_id} ({entry_type_value}) for Portfolio {portfolio_id}: {exc}"
        ) from exc


def _insert_ledger_entries(connection: sqlite3.Connection, entries: tuple[LedgerEntry, ...]) -> None:
    connection.executemany(
        """
        INSERT INTO portfolio_ledger (
            entry_id, portfolio_id, operation_id, sequence, entry_type,
            effective_at, recorded_at, source, external_ref, payload_json, broker_account_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                entry.entry_id,
                entry.portfolio_id,
                entry.operation_id,
                entry.sequence,
                entry.entry_type.value,
                entry.effective_at.isoformat(),
                entry.recorded_at.isoformat(),
                entry.source,
                entry.external_ref,
                serialize_ledger_payload(entry.payload),
                entry.broker_account_id,
            )
            for entry in entries
        ],
    )


def _validate_entry_id_conflicts(connection: sqlite3.Connection, entries: tuple[LedgerEntry, ...]) -> None:
    entry_ids = [entry.entry_id for entry in entries]
    if len(set(entry_ids)) != len(entry_ids):
        raise PortfolioConflictError("duplicate entry_id within Ledger operation")
    for entry_id in entry_ids:
        if connection.execute("SELECT 1 FROM portfolio_ledger WHERE entry_id = ?", (entry_id,)).fetchone():
            raise PortfolioConflictError(f"entry_id already exists: {entry_id}")


def _validate_sequence_conflicts(existing: list[LedgerEntry], additions: list[LedgerEntry]) -> None:
    existing_sequences = {entry.sequence for entry in existing}
    addition_sequences = [entry.sequence for entry in additions]
    if len(set(addition_sequences)) != len(addition_sequences):
        raise PortfolioConflictError("duplicate sequence within Portfolio Ledger operation")
    if existing_sequences:
        maximum = max(existing_sequences)
        invalid = [sequence for sequence in addition_sequences if sequence <= maximum]
        if invalid:
            raise PortfolioSequenceConflictError(
                f"Ledger sequence must be greater than persisted maximum {maximum}: {min(invalid)}"
            )


def _replace_projection(connection: sqlite3.Connection, state: PortfolioState) -> None:
    connection.execute("DELETE FROM portfolio_holdings WHERE portfolio_id = ?", (state.portfolio_id,))
    connection.executemany(
        """
        INSERT INTO portfolio_holdings (
            portfolio_id, instrument_code, market, quantity, total_cost
        ) VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                state.portfolio_id,
                instrument.code,
                instrument.market,
                str(holding.quantity),
                None if holding.total_cost is None else str(holding.total_cost),
            )
            for instrument, holding in sorted(state.holdings.items(), key=lambda item: (item[0].code, item[0].market))
        ],
    )
    connection.execute("DELETE FROM portfolio_cash WHERE portfolio_id = ?", (state.portfolio_id,))
    connection.executemany(
        "INSERT INTO portfolio_cash (portfolio_id, currency, amount) VALUES (?, ?, ?)",
        [(state.portfolio_id, currency, str(amount)) for currency, amount in sorted(state.cash.items())],
    )


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


def create_broker(db_path: str | Path, broker: Broker) -> None:
    """Register broker identity metadata, without mirroring broker assets."""
    broker = Broker(
        broker.broker_id, broker.provider, broker.display_name, broker.created_at, broker.updated_at, broker.metadata
    )
    with closing(_connect(db_path)) as connection:
        try:
            connection.execute(
                "INSERT INTO brokers VALUES (?, ?, ?, ?, ?, ?)",
                (
                    broker.broker_id,
                    broker.provider,
                    broker.display_name,
                    json.dumps(broker.metadata, allow_nan=False),
                    broker.created_at.isoformat(),
                    broker.updated_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise PortfolioConflictError(f"Broker identity conflict: {broker.broker_id}") from exc


def get_broker(db_path: str | Path, broker_id: str) -> Broker | None:
    with closing(_connect(db_path)) as connection:
        row = connection.execute("SELECT * FROM brokers WHERE broker_id = ?", (broker_id,)).fetchone()
        return None if row is None else _row_to_broker(row)


def list_brokers(db_path: str | Path) -> list[Broker]:
    with closing(_connect(db_path)) as connection:
        return [_row_to_broker(row) for row in connection.execute("SELECT * FROM brokers ORDER BY broker_id")]


def _row_to_broker(row: sqlite3.Row) -> Broker:
    try:
        return Broker(
            row["broker_id"],
            row["provider"],
            row["display_name"],
            datetime.fromisoformat(row["created_at"]),
            datetime.fromisoformat(row["updated_at"]),
            json.loads(row["metadata_json"]),
        )
    except (ValueError, TypeError) as exc:
        raise PortfolioDataError(f"invalid persisted Broker {row['broker_id']}: {exc}") from exc


def create_broker_account(db_path: str | Path, account: BrokerAccount) -> None:
    """Register a broker-scoped external account identity."""
    account = BrokerAccount(
        account.broker_account_id,
        account.broker_id,
        account.external_account_id,
        account.display_name,
        account.account_type,
        account.created_at,
        account.updated_at,
        account.metadata,
    )
    with closing(_connect(db_path)) as connection:
        try:
            connection.execute(
                "INSERT INTO broker_accounts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    account.broker_account_id,
                    account.broker_id,
                    account.external_account_id,
                    account.display_name,
                    account.account_type,
                    json.dumps(account.metadata, allow_nan=False),
                    account.created_at.isoformat(),
                    account.updated_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise PortfolioConflictError(
                f"BrokerAccount identity conflict or missing Broker: {account.broker_account_id}"
            ) from exc


def get_broker_account(db_path: str | Path, broker_account_id: str) -> BrokerAccount | None:
    with closing(_connect(db_path)) as connection:
        row = connection.execute(
            "SELECT * FROM broker_accounts WHERE broker_account_id = ?", (broker_account_id,)
        ).fetchone()
        return None if row is None else _row_to_broker_account(row)


def list_broker_accounts(db_path: str | Path, *, broker_id: str | None = None) -> list[BrokerAccount]:
    query = "SELECT * FROM broker_accounts"
    parameters = () if broker_id is None else (broker_id,)
    if broker_id is not None:
        query += " WHERE broker_id = ?"
    with closing(_connect(db_path)) as connection:
        return [
            _row_to_broker_account(row) for row in connection.execute(query + " ORDER BY broker_account_id", parameters)
        ]


def _row_to_broker_account(row: sqlite3.Row) -> BrokerAccount:
    try:
        return BrokerAccount(
            row["broker_account_id"],
            row["broker_id"],
            row["external_account_id"],
            row["display_name"],
            row["account_type"],
            datetime.fromisoformat(row["created_at"]),
            datetime.fromisoformat(row["updated_at"]),
            json.loads(row["metadata_json"]),
        )
    except (ValueError, TypeError) as exc:
        raise PortfolioDataError(f"invalid persisted BrokerAccount {row['broker_account_id']}: {exc}") from exc


def _replace_attributed_projection(connection: sqlite3.Connection, state: PortfolioStateWithAttribution) -> None:
    _replace_projection(connection, state.aggregate)
    portfolio_id = state.aggregate.portfolio_id
    connection.execute("DELETE FROM portfolio_account_holdings WHERE portfolio_id = ?", (portfolio_id,))
    connection.execute("DELETE FROM portfolio_account_cash WHERE portfolio_id = ?", (portfolio_id,))
    for account_id, account in state.accounts.items():
        connection.executemany(
            "INSERT INTO portfolio_account_holdings VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    portfolio_id,
                    account_id,
                    instrument.code,
                    instrument.market,
                    str(holding.quantity),
                    None if holding.total_cost is None else str(holding.total_cost),
                )
                for instrument, holding in account.holdings.items()
            ],
        )
        connection.executemany(
            "INSERT INTO portfolio_account_cash VALUES (?, ?, ?, ?)",
            [(portfolio_id, account_id, currency, str(amount)) for currency, amount in account.cash.items()],
        )


def get_portfolio_state_with_attribution(db_path: str | Path, portfolio_id: str) -> PortfolioStateWithAttribution:
    """Read aggregate and every account location in one SQLite read transaction."""
    with closing(_connect(db_path)) as connection:
        connection.execute("BEGIN")
        return _get_portfolio_state_with_attribution(connection, portfolio_id)


def _get_portfolio_state_with_attribution(
    connection: sqlite3.Connection,
    portfolio_id: str,
) -> PortfolioStateWithAttribution:
    aggregate = _get_portfolio_state(connection, portfolio_id)
    slices: dict[str | None, tuple[dict, dict]] = {}
    try:
        for row in connection.execute(
            "SELECT * FROM portfolio_account_holdings WHERE portfolio_id = ? ORDER BY broker_account_id, instrument_code, market",
            (portfolio_id,),
        ):
            holdings, _ = slices.setdefault(row["broker_account_id"], ({}, {}))
            instrument = InstrumentId(row["instrument_code"], row["market"])
            holdings[instrument] = HoldingState(
                instrument, Decimal(row["quantity"]), None if row["total_cost"] is None else Decimal(row["total_cost"])
            )
        for row in connection.execute(
            "SELECT * FROM portfolio_account_cash WHERE portfolio_id = ? ORDER BY broker_account_id, currency",
            (portfolio_id,),
        ):
            _, cash = slices.setdefault(row["broker_account_id"], ({}, {}))
            cash[row["currency"]] = Decimal(row["amount"])
        accounts = {
            account_id: PortfolioAccountState(portfolio_id, account_id, holdings, cash)
            for account_id, (holdings, cash) in slices.items()
        }
        return PortfolioStateWithAttribution(aggregate, accounts)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise PortfolioDataError(f"invalid persisted account projection for Portfolio {portfolio_id}: {exc}") from exc


def get_broker_account_portfolio_state(
    db_path: str | Path,
    portfolio_id: str,
    broker_account_id: str | None,
) -> PortfolioAccountState:
    state = get_portfolio_state_with_attribution(db_path, portfolio_id)
    return state.accounts.get(broker_account_id, PortfolioAccountState(portfolio_id, broker_account_id, {}, {}))
