from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from investorch.portfolio import storage
from investorch.portfolio.domain import (
    CashTransfer,
    LedgerEntry,
    LedgerEntryType,
    PortfolioState,
    PortfolioStatus,
    PositionTransfer,
    TransferDirection,
)
from investorch.portfolio.ledger import project_portfolio_with_attribution
from investorch.portfolio.schema import PortfolioConflictError, PortfolioNotFoundError


def assign_unallocated_assets(
    db_path: str | Path,
    portfolio_id: str,
    broker_account_id: str,
) -> tuple[str, tuple[LedgerEntry, ...], PortfolioState]:
    """Move legacy assets to one account with atomic append-only transfer pairs."""
    operation_id = uuid.uuid4().hex
    with closing(storage._connect(db_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            portfolio = storage._get_portfolio(connection, portfolio_id)
            if portfolio is None:
                raise PortfolioNotFoundError(f"Portfolio not found: {portfolio_id}")
            if portfolio.status is not PortfolioStatus.ACTIVE:
                raise PortfolioConflictError("Asset allocation requires an active Portfolio")
            storage._require_no_active_live_deployment(connection, portfolio_id)
            if (
                connection.execute(
                    "SELECT 1 FROM broker_accounts WHERE broker_account_id = ?", (broker_account_id,)
                ).fetchone()
                is None
            ):
                raise PortfolioConflictError(f"BrokerAccount not found: {broker_account_id}")
            ledger = storage._list_ledger_entries(connection, portfolio_id)
            now = datetime.now(UTC)
            if any(entry.effective_at > now for entry in ledger):
                raise PortfolioConflictError(
                    "Cannot allocate assets while the Ledger contains future-effective entries"
                )
            before = project_portfolio_with_attribution(portfolio, ledger)
            unallocated = before.accounts.get(None)
            sequence = max((entry.sequence for entry in ledger), default=0)
            entries: list[LedgerEntry] = []

            def add(account_id: str | None, payload: PositionTransfer | CashTransfer) -> None:
                entries.append(
                    LedgerEntry(
                        entry_id=uuid.uuid4().hex,
                        operation_id=operation_id,
                        portfolio_id=portfolio_id,
                        sequence=sequence + len(entries) + 1,
                        entry_type=LedgerEntryType.TRANSFER,
                        effective_at=now,
                        recorded_at=now,
                        source="broker_account_allocation",
                        payload=payload,
                        broker_account_id=account_id,
                    )
                )

            if unallocated is not None:
                for instrument, holding in sorted(
                    unallocated.holdings.items(), key=lambda item: (item[0].code, item[0].market)
                ):
                    add(None, PositionTransfer(instrument, TransferDirection.OUT, holding.quantity, holding.total_cost))
                    add(
                        broker_account_id,
                        PositionTransfer(instrument, TransferDirection.IN, holding.quantity, holding.total_cost),
                    )
                for currency, amount in sorted(unallocated.cash.items()):
                    if amount == 0:
                        continue
                    outgoing = TransferDirection.OUT if amount > 0 else TransferDirection.IN
                    incoming = TransferDirection.IN if amount > 0 else TransferDirection.OUT
                    add(None, CashTransfer(currency, outgoing, abs(amount)))
                    add(broker_account_id, CashTransfer(currency, incoming, abs(amount)))
            after = project_portfolio_with_attribution(portfolio, [*ledger, *entries])
            if after.aggregate != before.aggregate:
                raise PortfolioConflictError("Asset allocation must preserve aggregate Portfolio state")
            if entries:
                storage._insert_ledger_entries(connection, tuple(entries))
                storage._replace_attributed_projection(connection, after)
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise PortfolioConflictError(f"Asset allocation conflicts with persisted data: {exc}") from exc
        except BaseException:
            connection.rollback()
            raise
    return operation_id, tuple(entries), after.aggregate
