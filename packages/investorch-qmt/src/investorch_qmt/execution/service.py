"""The sole execution-node business boundary used by REST and MCP."""

import json
import os
import shutil
import tempfile
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from uuid import uuid4

from investorch_qmt.config import AppPaths
from investorch_qmt.history.sync import HistorySyncManager
from investorch_qmt.runtime.model import WorkerLaunchSpec
from investorch_qmt.runtime.supervisor import RuntimeSupervisor

from .domain import ControlSession, ExecutionError
from .storage import RuntimeStorage


class ExecutionNodeService:
    def __init__(
        self,
        paths: AppPaths,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        lease_timeout_seconds: int = 10,
        runtime_factory=RuntimeSupervisor,
        history_manager=None,
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
        self.history = history_manager if history_manager is not None else HistorySyncManager(clock=clock)
        self.supervisor = runtime_factory(
            self._runtime_event, self._runtime_gate, history_snapshot=self.history.snapshot
        )
        with self.storage.transaction() as db:
            if db.execute("SELECT 1 FROM remote_deployments WHERE status='RUNNING'").fetchone():
                now = self._clock().isoformat()
                db.execute(
                    "UPDATE remote_deployments SET status='FAILED',failure_reason=?,updated_at=?,ended_at=? WHERE status='RUNNING'",
                    ("RUNTIME_LOST_ON_COMPANION_RESTART", now, now),
                )

    def start_maintenance(self):
        self.history.start()

    def close(self):
        try:
            self.supervisor.close()
        finally:
            self.history.close()

    def _runtime_gate(self, identity):
        with self._lock:
            if self._current() is None:
                return False, "CONTROL_AUTHORITY_UNAVAILABLE"
            if self._sync.get(identity) != "SYNCED":
                return False, "PORTFOLIO_NOT_SYNCED"
            return True, None

    def _runtime_event(self, identity, event):
        phase = event["phase"]
        status = {"READY": "RUNNING", "RUNNING": "RUNNING", "PAUSED": "RUNNING", "STOPPED": "STOPPED"}.get(phase)
        if phase == "FAILED":
            status = "STAGED" if event.get("retryable") else "FAILED"
        if status is None:
            return
        with self._lock, self.storage.transaction() as db:
            now = self._clock().isoformat()
            db.execute(
                """UPDATE remote_deployments SET status=?,failure_reason=?,updated_at=?,
                ended_at=CASE WHEN ? IN ('STOPPED','FAILED') THEN ? ELSE ended_at END
                WHERE deployment_id=? AND status IN ('STAGED','RUNNING')""",
                (status, event.get("reason"), now, status, now, identity),
            )

    def _invalidate(self):
        self._session = None
        self._sync.clear()
        self.supervisor.disable_gate()

    def _set_sync(self, identity, value):
        self._sync[identity] = value
        if value != "SYNCED":
            self.supervisor.disable_gate(identity, "PORTFOLIO_NOT_SYNCED")

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
                            self._set_sync(identity, "DESYNCED")
                            raise ExecutionError(
                                "SEQUENCE_CONFLICT", "Reconciliation requires no pending facts and an equal cursor."
                            )
                    for identity in seen:
                        self._set_sync(identity, "SYNCED")
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
        worker = self.supervisor.snapshot(row["deployment_id"])
        return {
            "worker_phase": worker["phase"]
            if worker
            else (row["status"] if row["status"] in ("STOPPED", "FAILED") else None),
            "market_data": worker["market_data"] if worker else "DISCONNECTED",
            "control_authority": "AVAILABLE" if self._current() else "UNAVAILABLE",
            "trading": {"status": "NOT_READY", "available": False, "reason": "TRADING_BACKEND_NOT_READY"},
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
            try:
                xtquant_version = version("xtquant")
            except PackageNotFoundError:
                xtquant_version = None
            deployments = [
                self._summary(db, row)
                for row in db.execute("SELECT * FROM remote_deployments ORDER BY staged_at, deployment_id")
            ]
            return {
                "service": {"status": "ready"},
                "historical_data": self.history.snapshot(),
                "market_data": {
                    "backend": "xtdata",
                    "status": "CONNECTED"
                    if any(d["market_data"] == "CONNECTED" for d in deployments)
                    else "DISCONNECTED",
                    "xtquant_version": xtquant_version,
                },
                "trading": {"status": "NOT_READY", "reason": "TRADING_BACKEND_NOT_READY"},
                "control": {
                    "status": "AVAILABLE" if current else "UNAVAILABLE",
                    "lease_expires_at": current.expires_at.isoformat() if current else None,
                },
                "deployments": deployments,
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
                    self._set_sync(row["deployment_id"], "DESYNCED")
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

    def start_live_strategy(self, portfolio_id: str, session_id: str | None = None) -> dict:
        with self._lock, self.storage.transaction() as db:
            self._require_control(session_id)
            row = self._portfolio_row(db, portfolio_id)
            if row["status"] not in ("STAGED", "RUNNING"):
                raise ExecutionError("DEPLOYMENT_CONFLICT", "Only a current deployment can start.")
            if self._summary(db, row)["portfolio_sync"] != "SYNCED":
                raise ExecutionError("PORTFOLIO_NOT_SYNCED", "Core reconciliation is required before start.")
            through = self.history.snapshot().get("fresh_through")
            spec = WorkerLaunchSpec(
                row["deployment_id"],
                row["portfolio_id"],
                row["broker_account_id"],
                str(self.paths.deployments / row["deployment_id"]),
                row["strategy_sha256"],
                history_through=date.fromisoformat(through) if through is not None else None,
            )
            startup = self.supervisor.begin_start(spec)
        result = startup.result()
        if result["phase"] == "FAILED":
            raise ExecutionError(
                result["reason"],
                result.get("message", "Worker could not start."),
                retryable=result.get("retryable", False),
            )
        return self.get_portfolio_runtime_status(portfolio_id)

    def stop_live_strategy(self, portfolio_id: str, session_id: str | None = None) -> dict:
        with self._lock, self.storage.transaction() as db:
            self._require_control(session_id)
            row = self._portfolio_row(db, portfolio_id)
            shutdown = self.supervisor.begin_stop(row["deployment_id"])
            worker = self.supervisor.snapshot(row["deployment_id"])
            if row["status"] == "STAGED" and (worker is None or worker["phase"] == "FAILED"):
                now = self._clock().isoformat()
                db.execute(
                    "UPDATE remote_deployments SET status='STOPPED',updated_at=?,ended_at=? WHERE deployment_id=?",
                    (now, now, row["deployment_id"]),
                )
            if row["status"] == "RUNNING" and worker is None:
                now = self._clock().isoformat()
                db.execute(
                    "UPDATE remote_deployments SET status='FAILED',failure_reason='WORKER_FAILED',updated_at=?,ended_at=? WHERE deployment_id=?",
                    (now, now, row["deployment_id"]),
                )
        shutdown.result()
        return self.get_portfolio_runtime_status(portfolio_id)
