from __future__ import annotations

import json
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from investorch.live.domain import LiveDeploymentStatus, LiveExecutionError, LiveTradeIdempotencyConflict
from investorch.portfolio.domain import LedgerEntry, LedgerEntryType, Trade
from investorch.portfolio.ledger import project_portfolio_with_attribution
from investorch.portfolio.live_storage import _row_to_deployment
from investorch.portfolio.storage import (
    _connect,
    _get_portfolio,
    _insert_ledger_entries,
    _list_ledger_entries,
    _replace_attributed_projection,
    _row_to_ledger_entry,
)


def append_live_trade_idempotently(
    db_path: str | Path,
    deployment_id: str,
    broker_trade_id: str,
    trade: Trade,
    effective_at: datetime,
) -> LedgerEntry:
    """Commit a broker fact once, or explicitly reject a conflicting/invalid fact."""
    if not isinstance(broker_trade_id, str) or not broker_trade_id.strip():
        raise LiveExecutionError("broker_trade_id must be nonempty")
    if not isinstance(trade, Trade):
        raise LiveExecutionError("live ingestion requires a Trade")
    with closing(_connect(db_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT * FROM live_deployments WHERE deployment_id = ?", (deployment_id,)
            ).fetchone()
            if row is None:
                raise LiveExecutionError(f"Deployment not found: {deployment_id}")
            deployment = _row_to_deployment(row)
            if deployment.status is LiveDeploymentStatus.PREPARED:
                raise LiveExecutionError("PREPARED deployment cannot ingest broker trades")
            identity = json.dumps(
                [deployment.broker_account_id, broker_trade_id], ensure_ascii=True, separators=(",", ":")
            )
            existing = connection.execute(
                "SELECT * FROM portfolio_ledger WHERE source = 'live_execution' AND external_ref = ?", (identity,)
            ).fetchone()
            if existing is not None:
                committed = _row_to_ledger_entry(existing)
                if (
                    committed.portfolio_id != deployment.portfolio_id
                    or committed.broker_account_id != deployment.broker_account_id
                    or committed.payload != trade
                    or committed.effective_at != effective_at
                ):
                    raise LiveTradeIdempotencyConflict(
                        "broker trade identity conflicts with its committed economic payload"
                    )
                connection.commit()
                return committed
            portfolio = _get_portfolio(connection, deployment.portfolio_id)
            ledger = _list_ledger_entries(connection, deployment.portfolio_id)
            entry = LedgerEntry(
                entry_id=uuid.uuid4().hex,
                operation_id=uuid.uuid4().hex,
                portfolio_id=deployment.portfolio_id,
                sequence=max((item.sequence for item in ledger), default=0) + 1,
                entry_type=LedgerEntryType.TRADE,
                effective_at=effective_at,
                recorded_at=datetime.now(UTC),
                source="live_execution",
                payload=trade,
                external_ref=identity,
                broker_account_id=deployment.broker_account_id,
            )
            # Preserve the canonical Ledger constraints: an invalid late fact
            # fails explicitly; durable failed-delivery handling belongs to B2/B5.
            state = project_portfolio_with_attribution(portfolio, [*ledger, entry])
            _insert_ledger_entries(connection, (entry,))
            _replace_attributed_projection(connection, state)
            connection.commit()
            return entry
        except BaseException:
            connection.rollback()
            raise
