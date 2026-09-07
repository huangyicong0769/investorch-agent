"""The sole execution-node business boundary used by REST and MCP."""

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from investorch_qmt.config import AppPaths

from .domain import ControlSession, ExecutionError
from .storage import RuntimeStorage


class ExecutionNodeService:
    def __init__(
        self,
        paths: AppPaths,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        lease_timeout_seconds: int = 10,
    ):
        if type(lease_timeout_seconds) is not int or lease_timeout_seconds <= 0:
            raise ValueError("lease timeout must be a positive integer")
        self.paths = paths
        self.storage = RuntimeStorage(paths.runtime_db)
        self._clock = clock
        self._lease = lease_timeout_seconds
        self._lock = threading.RLock()
        self._session: ControlSession | None = None
        self._sync: dict[str, str] = {}

    def _invalidate(self):
        self._session = None
        self._sync.clear()

    def _current(self):
        if self._session is not None and self._clock() >= self._session.expires_at:
            self._invalidate()
        return self._session

    def _require_control(self, session_id):
        current = self._current()
        if current is None or current.session_id != session_id:
            raise ExecutionError("STALE_CONTROL_SESSION", "Current control session is required.")

    def open_control_session(self) -> dict:
        with self._lock:
            self._invalidate()
            self._session = ControlSession(str(uuid4()), self._clock() + timedelta(seconds=self._lease))
            return {"session_id": self._session.session_id, "lease_timeout_seconds": self._lease}

    def renew_control_session(self, session_id: str, reconciled_deployments: list | None = None) -> dict:
        with self._lock:
            self._require_control(session_id)
            self._session = ControlSession(session_id, self._clock() + timedelta(seconds=self._lease))
            return {
                "session_id": session_id,
                "expires_at": self._session.expires_at.isoformat(),
                "lease_timeout_seconds": self._lease,
            }

    def close_control_session(self, session_id: str) -> dict:
        with self._lock:
            if self._current() is not None and self._session.session_id == session_id:
                self._invalidate()
            return {"status": "closed"}

    def _summary(self, db, row) -> dict:
        pending = db.execute(
            "SELECT COUNT(*) FROM outbox WHERE deployment_id=? AND status='PENDING'", (row["deployment_id"],)
        ).fetchone()[0]
        return {
            "deployment_id": row["deployment_id"],
            "portfolio_id": row["portfolio_id"],
            "broker_account_id": row["broker_account_id"],
            "status": row["status"],
            "acked_core_sequence": row["acked_core_sequence"],
            "pending_fact_count": pending,
            "portfolio_sync": self._sync.get(row["deployment_id"], "UNKNOWN"),
            "failure_reason": row["failure_reason"],
        }

    def get_node_status(self) -> dict:
        with self._lock, self.storage.transaction() as db:
            current = self._current()
            return {
                "service": {"status": "ready"},
                "qmt": {"status": "not_connected"},
                "control": {
                    "status": "AVAILABLE" if current else "UNAVAILABLE",
                    "lease_expires_at": current.expires_at.isoformat() if current else None,
                },
                "deployments": [
                    self._summary(db, row)
                    for row in db.execute("SELECT * FROM remote_deployments ORDER BY staged_at, deployment_id")
                ],
            }
