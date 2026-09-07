from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from investorch.live.contracts import BootstrapPosition, RQAlphaLiveBootstrapSnapshot
from investorch.live.domain import LiveDeploymentStatus, LiveExecutionError
from investorch.portfolio.live_storage import _row_to_deployment, require_live_eligible
from investorch.portfolio.storage import _connect, _get_portfolio


def build_bootstrap_snapshot(db_path: str | Path, deployment_id: str) -> RQAlphaLiveBootstrapSnapshot:
    """Read deployment, attributed economic state, and ledger head at one SQLite point."""
    with closing(_connect(db_path)) as connection:
        connection.execute("BEGIN")
        try:
            row = connection.execute(
                "SELECT * FROM live_deployments WHERE deployment_id = ?", (deployment_id,)
            ).fetchone()
            if row is None:
                raise LiveExecutionError(f"Deployment not found: {deployment_id}")
            deployment = _row_to_deployment(row)
            if deployment.status is not LiveDeploymentStatus.ACTIVE:
                raise LiveExecutionError("Bootstrap requires an ACTIVE deployment")
            state = require_live_eligible(connection, deployment.portfolio_id, deployment.broker_account_id)
            portfolio = _get_portfolio(connection, deployment.portfolio_id)
            if portfolio is None:
                raise LiveExecutionError("Deployment Portfolio not found")
            account = state.accounts.get(deployment.broker_account_id)
            head = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM portfolio_ledger WHERE portfolio_id = ?",
                (deployment.portfolio_id,),
            ).fetchone()[0]
            snapshot = RQAlphaLiveBootstrapSnapshot(
                deployment_id=deployment.deployment_id,
                portfolio_id=deployment.portfolio_id,
                broker_account_id=deployment.broker_account_id,
                ledger_sequence=head,
                generated_at=datetime.now(UTC),
                base_currency=portfolio.base_currency,
                cash=Decimal(0) if account is None else account.cash.get(portfolio.base_currency, Decimal(0)),
                positions=tuple(
                    BootstrapPosition(instrument.code, instrument.market, holding.quantity)
                    for instrument, holding in sorted(
                        (() if account is None else account.holdings.items()),
                        key=lambda item: (item[0].code, item[0].market),
                    )
                    if holding.quantity != 0
                ),
            )
            connection.commit()
            return snapshot
        except BaseException:
            connection.rollback()
            raise
