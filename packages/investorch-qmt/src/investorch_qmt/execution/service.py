"""The sole execution-node business boundary used by REST and MCP."""

import json
import os
import shutil
import tempfile
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
            if self._current() is not None:
                raise ExecutionError(
                    "CONTROL_SESSION_BUSY",
                    "Another Core currently owns the execution-node control lease.",
                    retryable=True,
                )
            self._session = ControlSession(str(uuid4()), self._clock() + timedelta(seconds=self._lease))
            return {"session_id": self._session.session_id, "lease_timeout_seconds": self._lease}

    def renew_control_session(self, session_id: str, reconciled_deployments: list | None = None) -> dict:
        with self._lock:
            self._require_control(session_id)
            if reconciled_deployments is not None:
                from .contracts import fields, invalid, sequence, text

                if type(reconciled_deployments) is not list:
                    invalid("reconciled_deployments must be an array.")
                seen = set()
                for assertion in reconciled_deployments:
                    fields(assertion, {"deployment_id", "acked_core_sequence"})
                    identity = text(assertion["deployment_id"])
                    sequence(assertion["acked_core_sequence"])
                    if identity in seen:
                        invalid("Reconciliation identities must be unique.")
                    seen.add(identity)
                with self.storage.transaction() as db:
                    for assertion in reconciled_deployments:
                        identity = assertion["deployment_id"]
                        row = db.execute(
                            "SELECT * FROM remote_deployments WHERE deployment_id=?", (identity,)
                        ).fetchone()
                        if row is None:
                            raise ExecutionError("DEPLOYMENT_NOT_FOUND", "Deployment does not exist.", status=404)
                        summary = self._summary(db, row)
                        if (
                            summary["pending_fact_count"]
                            or summary["acked_core_sequence"] != assertion["acked_core_sequence"]
                        ):
                            self._sync[identity] = "DESYNCED"
                            raise ExecutionError(
                                "SEQUENCE_CONFLICT", "Reconciliation requires no pending facts and an equal cursor."
                            )
                    for identity in seen:
                        self._sync[identity] = "SYNCED"
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

    def stage_deployment(self, deployment_id: str, body: dict, session_id: str) -> dict:
        from .contracts import parse_stage

        with self._lock:
            self._require_control(session_id)
            source, manifest_json, bootstrap_json, snapshot = parse_stage(deployment_id, body)
            installed = False
            temporary = None
            final = self.paths.deployments / deployment_id
            try:
                with self.storage.transaction() as db:
                    existing = db.execute(
                        "SELECT * FROM remote_deployments WHERE deployment_id=?", (deployment_id,)
                    ).fetchone()
                    if existing is not None:
                        if existing["manifest_json"] != manifest_json or existing["bootstrap_json"] != bootstrap_json:
                            raise ExecutionError(
                                "DEPLOYMENT_CONFLICT", "Deployment identity already has different content."
                            )
                        if not (final / "strategy.py").is_file() or (final / "strategy.py").read_bytes() != source:
                            raise ExecutionError("DEPLOYMENT_CONFLICT", "Persisted artifact is missing or differs.")
                        return self._summary(db, existing)
                    current = db.execute(
                        "SELECT 1 FROM remote_deployments WHERE portfolio_id=? AND status IN ('STAGED','RUNNING')",
                        (snapshot.portfolio_id,),
                    ).fetchone()
                    if current:
                        raise ExecutionError(
                            "PORTFOLIO_DEPLOYMENT_CONFLICT", "Portfolio already has a current deployment."
                        )
                    self.paths.deployments.mkdir(parents=True, exist_ok=True)
                    if final.exists():
                        raise ExecutionError("DEPLOYMENT_CONFLICT", "An unregistered artifact already exists.")
                    temporary = Path(tempfile.mkdtemp(prefix=".stage-", dir=self.paths.deployments))
                    for name, content in (
                        ("strategy.py", source),
                        ("manifest.json", manifest_json.encode()),
                        ("bootstrap.json", bootstrap_json.encode()),
                    ):
                        with (temporary / name).open("wb") as file:
                            file.write(content)
                            file.flush()
                            os.fsync(file.fileno())
                    os.rename(temporary, final)
                    temporary = None
                    installed = True
                    self._require_control(session_id)
                    now = self._clock().isoformat()
                    db.execute(
                        """INSERT INTO remote_deployments (
                        deployment_id,portfolio_id,broker_account_id,strategy_sha256,strategy_artifact_relpath,
                        manifest_json,bootstrap_json,bootstrap_schema_version,bootstrap_ledger_sequence,
                        acked_core_sequence,status,staged_at,updated_at
                        ) VALUES (?,?,?,?,?,?,?,1,?,?,'STAGED',?,?)""",
                        (
                            deployment_id,
                            snapshot.portfolio_id,
                            snapshot.broker_account_id,
                            body["manifest"]["strategy_sha256"],
                            f"{deployment_id}/strategy.py",
                            manifest_json,
                            bootstrap_json,
                            snapshot.ledger_sequence,
                            snapshot.ledger_sequence,
                            now,
                            now,
                        ),
                    )
                    row = db.execute(
                        "SELECT * FROM remote_deployments WHERE deployment_id=?", (deployment_id,)
                    ).fetchone()
                    result = self._summary(db, row)
                return result
            except BaseException:
                if installed:
                    shutil.rmtree(final)
                raise
            finally:
                if temporary is not None:
                    shutil.rmtree(temporary)

    @staticmethod
    def _fact(row) -> dict:
        return {
            "fact_id": row["fact_id"],
            "queue_sequence": row["queue_sequence"],
            "deployment_id": row["deployment_id"],
            "fact_type": row["fact_type"],
            "external_fact_id": row["external_fact_id"],
            "payload": json.loads(row["payload_json"]),
        }

    def enqueue_trade_fact(self, payload: dict) -> dict:
        from .contracts import parse_trade

        payload_json = parse_trade(payload)
        with self._lock, self.storage.transaction() as db:
            existing = db.execute(
                "SELECT * FROM outbox WHERE deployment_id=? AND fact_type='TRADE_V1' AND external_fact_id=?",
                (payload["deployment_id"], payload["broker_trade_id"]),
            ).fetchone()
            if existing is not None:
                if existing["payload_json"] != payload_json:
                    raise ExecutionError("FACT_ACK_CONFLICT", "Broker trade identity has different content.")
                return self._fact(existing)
            deployment = db.execute(
                "SELECT * FROM remote_deployments WHERE deployment_id=?", (payload["deployment_id"],)
            ).fetchone()
            if deployment is None:
                raise ExecutionError("DEPLOYMENT_NOT_FOUND", "Deployment does not exist.", status=404)
            fact_id = str(uuid4())
            db.execute(
                """INSERT INTO outbox (fact_id,deployment_id,fact_type,external_fact_id,payload_json,status,created_at)
                VALUES (?,?,'TRADE_V1',?,?,'PENDING',?)""",
                (
                    fact_id,
                    payload["deployment_id"],
                    payload["broker_trade_id"],
                    payload_json,
                    self._clock().isoformat(),
                ),
            )
            self._sync[payload["deployment_id"]] = "COMMIT_PENDING"
            return self._fact(db.execute("SELECT * FROM outbox WHERE fact_id=?", (fact_id,)).fetchone())

    def get_next_pending_fact(self, session_id: str) -> dict | None:
        with self._lock, self.storage.transaction() as db:
            self._require_control(session_id)
            row = db.execute("SELECT * FROM outbox WHERE status='PENDING' ORDER BY queue_sequence LIMIT 1").fetchone()
            return self._fact(row) if row else None

    def ack_fact(self, fact_id: str, committed_ledger_sequence: int, session_id: str) -> dict:
        from .contracts import sequence

        with self._lock, self.storage.transaction() as db:
            self._require_control(session_id)
            sequence(committed_ledger_sequence)
            row = db.execute("SELECT * FROM outbox WHERE fact_id=?", (fact_id,)).fetchone()
            if row is None:
                raise ExecutionError("FACT_NOT_FOUND", "Fact does not exist.", status=404)
            if row["status"] == "ACKED":
                if row["committed_ledger_sequence"] != committed_ledger_sequence:
                    raise ExecutionError("SEQUENCE_CONFLICT", "Acknowledged sequence cannot change.")
            else:
                oldest = db.execute(
                    "SELECT fact_id FROM outbox WHERE status='PENDING' ORDER BY queue_sequence LIMIT 1"
                ).fetchone()[0]
                if oldest != fact_id:
                    raise ExecutionError("FACT_ACK_CONFLICT", "Only the oldest pending fact can be acknowledged.")
                cursor = db.execute(
                    "SELECT acked_core_sequence FROM remote_deployments WHERE deployment_id=?", (row["deployment_id"],)
                ).fetchone()[0]
                if committed_ledger_sequence != cursor + 1:
                    self._sync[row["deployment_id"]] = "DESYNCED"
                    raise ExecutionError("SEQUENCE_CONFLICT", "Committed sequence must advance exactly by one.")
                now = self._clock().isoformat()
                db.execute(
                    "UPDATE outbox SET status='ACKED',acked_at=?,committed_ledger_sequence=? WHERE fact_id=?",
                    (now, committed_ledger_sequence, fact_id),
                )
                db.execute(
                    "UPDATE remote_deployments SET acked_core_sequence=?,updated_at=? WHERE deployment_id=?",
                    (committed_ledger_sequence, now, row["deployment_id"]),
                )
            return {"fact_id": fact_id, "status": "ACKED", "committed_ledger_sequence": committed_ledger_sequence}

    def _portfolio_row(self, db, portfolio_id):
        from .contracts import text

        text(portfolio_id)
        row = db.execute(
            """SELECT * FROM remote_deployments WHERE portfolio_id=?
            ORDER BY CASE WHEN status IN ('STAGED','RUNNING') THEN 0 ELSE 1 END,
            staged_at DESC, rowid DESC LIMIT 1""",
            (portfolio_id,),
        ).fetchone()
        if row is None:
            raise ExecutionError("DEPLOYMENT_NOT_FOUND", "Portfolio has no remote deployment.", status=404)
        return row

    def get_portfolio_runtime_status(self, portfolio_id: str) -> dict:
        with self._lock, self.storage.transaction() as db:
            self._current()
            return self._summary(db, self._portfolio_row(db, portfolio_id))

    def start_live_strategy(self, portfolio_id: str) -> dict:
        with self._lock, self.storage.transaction() as db:
            row = self._portfolio_row(db, portfolio_id)
            if row["status"] != "STAGED":
                raise ExecutionError("DEPLOYMENT_CONFLICT", "Only a staged deployment can start.")
            if self._current() is None:
                raise ExecutionError("STALE_CONTROL_SESSION", "Current Core control authority is unavailable.")
            if self._summary(db, row)["portfolio_sync"] != "SYNCED":
                raise ExecutionError("PORTFOLIO_NOT_SYNCED", "Core reconciliation is required before start.")
            raise ExecutionError("BACKEND_NOT_READY", "The production live backend is not implemented.")

    def stop_live_strategy(self, portfolio_id: str) -> dict:
        with self._lock, self.storage.transaction() as db:
            row = self._portfolio_row(db, portfolio_id)
            if row["status"] == "RUNNING":
                raise ExecutionError("BACKEND_NOT_READY", "No production runtime stop controller is implemented.")
            if row["status"] == "STAGED":
                now = self._clock().isoformat()
                db.execute(
                    "UPDATE remote_deployments SET status='STOPPED',updated_at=?,ended_at=? WHERE deployment_id=?",
                    (now, now, row["deployment_id"]),
                )
                row = db.execute(
                    "SELECT * FROM remote_deployments WHERE deployment_id=?", (row["deployment_id"],)
                ).fetchone()
            return self._summary(db, row)
