from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from investorch.live.domain import LiveDeployment, LiveDeploymentStatus, LiveExecutionError
from investorch.portfolio.domain import PortfolioStateWithAttribution, PortfolioStatus
from investorch.portfolio.storage import _connect, _get_portfolio, _get_portfolio_state_with_attribution


def require_live_eligible(
    connection: sqlite3.Connection, portfolio_id: str, broker_account_id: str
) -> PortfolioStateWithAttribution:
    portfolio = _get_portfolio(connection, portfolio_id)
    if portfolio is None or portfolio.status is not PortfolioStatus.ACTIVE:
        raise LiveExecutionError("Live deployment requires an active Portfolio")
    if (
        connection.execute("SELECT 1 FROM broker_accounts WHERE broker_account_id = ?", (broker_account_id,)).fetchone()
        is None
    ):
        raise LiveExecutionError("BrokerAccount does not exist")
    state = _get_portfolio_state_with_attribution(connection, portfolio_id)
    for account_id, account in state.accounts.items():
        if account_id != broker_account_id and (
            any(h.quantity != 0 for h in account.holdings.values())
            or any(amount != 0 for amount in account.cash.values())
        ):
            raise LiveExecutionError("multi-account / unallocated Portfolio live execution is not supported in 0.2.0")
    return state


def create_live_deployment(db_path: str | Path, deployment: LiveDeployment) -> None:
    if deployment.status is not LiveDeploymentStatus.PREPARED:
        raise LiveExecutionError("New deployment must be PREPARED")
    with closing(_connect(db_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            require_live_eligible(connection, deployment.portfolio_id, deployment.broker_account_id)
            connection.execute(
                """INSERT INTO live_deployments (
                    deployment_id, portfolio_id, broker_account_id, strategy_source_path,
                    strategy_sha256, strategy_parameters_json, strategy_artifact_relpath,
                    rqalpha_version, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    deployment.deployment_id,
                    deployment.portfolio_id,
                    deployment.broker_account_id,
                    deployment.strategy_source_path,
                    deployment.strategy_sha256,
                    deployment.strategy_parameters_json,
                    deployment.strategy_artifact_relpath,
                    deployment.rqalpha_version,
                    deployment.status.value,
                    deployment.created_at.isoformat(),
                ),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def _row_to_deployment(row: sqlite3.Row) -> LiveDeployment:
    data = dict(row)
    data["status"] = LiveDeploymentStatus(data["status"])
    for field in ("created_at", "started_at", "ended_at"):
        data[field] = None if data[field] is None else datetime.fromisoformat(data[field])
    return LiveDeployment(**data)


def get_live_deployment(db_path: str | Path, deployment_id: str) -> LiveDeployment | None:
    with closing(_connect(db_path)) as connection:
        row = connection.execute("SELECT * FROM live_deployments WHERE deployment_id = ?", (deployment_id,)).fetchone()
        return None if row is None else _row_to_deployment(row)


def list_live_deployments(db_path: str | Path, portfolio_id: str | None = None) -> list[LiveDeployment]:
    query = "SELECT * FROM live_deployments"
    parameters = ()
    if portfolio_id is not None:
        query += " WHERE portfolio_id = ?"
        parameters = (portfolio_id,)
    query += " ORDER BY created_at, deployment_id"
    with closing(_connect(db_path)) as connection:
        return [_row_to_deployment(row) for row in connection.execute(query, parameters)]


def transition_live_deployment(
    db_path: str | Path,
    deployment_id: str,
    status: LiveDeploymentStatus,
    *,
    failure_reason: str | None = None,
) -> LiveDeployment:
    from datetime import UTC

    transitions = {
        LiveDeploymentStatus.PREPARED: {LiveDeploymentStatus.ACTIVE, LiveDeploymentStatus.FAILED},
        LiveDeploymentStatus.ACTIVE: {LiveDeploymentStatus.STOPPED, LiveDeploymentStatus.FAILED},
    }
    with closing(_connect(db_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT * FROM live_deployments WHERE deployment_id = ?", (deployment_id,)
            ).fetchone()
            if row is None:
                raise LiveExecutionError(f"Deployment not found: {deployment_id}")
            deployment = _row_to_deployment(row)
            if status not in transitions.get(deployment.status, set()):
                raise LiveExecutionError(f"Invalid deployment transition: {deployment.status} -> {status}")
            now = datetime.now(UTC).isoformat()
            if status is LiveDeploymentStatus.ACTIVE:
                require_live_eligible(connection, deployment.portfolio_id, deployment.broker_account_id)
                head = connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) FROM portfolio_ledger WHERE portfolio_id = ?",
                    (deployment.portfolio_id,),
                ).fetchone()[0]
                connection.execute(
                    "UPDATE live_deployments SET status = ?, started_at = ?, bootstrap_ledger_sequence = ? WHERE deployment_id = ?",
                    (status.value, now, head, deployment_id),
                )
            else:
                if status is LiveDeploymentStatus.FAILED and (
                    not isinstance(failure_reason, str) or not failure_reason.strip()
                ):
                    raise LiveExecutionError("Failed deployment requires a failure reason")
                connection.execute(
                    "UPDATE live_deployments SET status = ?, ended_at = ?, failure_reason = ? WHERE deployment_id = ?",
                    (status.value, now, failure_reason, deployment_id),
                )
            result = _row_to_deployment(
                connection.execute(
                    "SELECT * FROM live_deployments WHERE deployment_id = ?", (deployment_id,)
                ).fetchone()
            )
            connection.commit()
            return result
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise LiveExecutionError("Portfolio already has an ACTIVE live deployment") from exc
        except BaseException:
            connection.rollback()
            raise


def get_active_live_deployment(db_path: str | Path, portfolio_id: str) -> LiveDeployment | None:
    with closing(_connect(db_path)) as connection:
        row = connection.execute(
            "SELECT * FROM live_deployments WHERE portfolio_id = ? AND status = 'ACTIVE'", (portfolio_id,)
        ).fetchone()
        return None if row is None else _row_to_deployment(row)
