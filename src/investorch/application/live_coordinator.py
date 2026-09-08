"""Application-owned execution-node lifecycle and canonical Ledger reconciliation."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
from contextlib import suppress
from pathlib import Path

from investorch.config import AppConfig
from investorch.live.domain import LiveDeployment, LiveDeploymentStatus, LiveExecutionError
from investorch.portfolio.storage import get_portfolio, list_ledger_entries
from investorch.qmt.client import QMTClient
from investorch.qmt.contracts import TradeFact
from investorch.qmt.errors import QMTError, QMTProtocolError, QMTRejectedError

from .live import LiveExecutionOperations

logger = logging.getLogger(__name__)


class _Desynchronized(LiveExecutionError):
    pass


class LiveDeploymentCoordinator:
    def __init__(self, *, config: AppConfig, client: QMTClient | None) -> None:
        self._config = config
        self._client = client
        self._live = LiveExecutionOperations(config=config)
        self._lock = asyncio.Lock()
        self._session_id: str | None = None
        self._lease_seconds = 10.0
        self._node: dict | None = None
        self._available = False
        self._sync: dict[str, str] = {}
        self._reasons: dict[str, str] = {}
        self._worker: asyncio.Task | None = None
        self._heartbeat: asyncio.Task | None = None
        self._closed = False

    async def _active(self) -> list[LiveDeployment]:
        return [d for d in await self._live.list_deployments() if d.status is LiveDeploymentStatus.ACTIVE]

    async def _head(self, portfolio_id: str) -> int:
        entries = await asyncio.to_thread(list_ledger_entries, self._config.portfolio_db, portfolio_id)
        return entries[-1].sequence if entries else 0

    async def _connect(self) -> dict:
        if self._client is None:
            raise QMTError("QMT execution node is not configured")
        node = await self._client.get_node_status()
        if self._session_id is None:
            session = await self._client.open_control_session()
            self._session_id = session["session_id"]
            self._lease_seconds = session["lease_timeout_seconds"]
        self._node = node
        self._available = True
        return node

    async def deploy_live_strategy(self, portfolio_id: str, broker_account_id: str) -> dict:
        async with self._lock:
            active = next((d for d in await self._active() if d.portfolio_id == portfolio_id), None)
            if active is not None:
                if active.broker_account_id != broker_account_id:
                    raise LiveExecutionError("Portfolio ACTIVE deployment uses another BrokerAccount")
                await self._recover_safely()
                result = self._deploy_result(active)
            else:
                prepared = await self._live.prepare_deployment(portfolio_id, broker_account_id)
                try:
                    await self._connect()
                except QMTError as exc:
                    await self._live.fail_deployment(prepared.deployment_id, str(exc))
                    self._unavailable()
                    return {"status": "failed", "reason": str(exc), "retry_safe": True}
                active = await self._live.activate_deployment(prepared.deployment_id)
                try:
                    body = await self._stage_body(active)
                    self._start_heartbeat()
                    assert self._client is not None and self._session_id is not None
                    await self._client.stage_deployment(self._session_id, active.deployment_id, body)
                except QMTRejectedError as exc:
                    if (
                        exc.code in {"INVALID_REQUEST", "INVALID_BOOTSTRAP", "UNSUPPORTED_SCHEMA", "HASH_MISMATCH"}
                        and not exc.retryable
                        and exc.status_code < 500
                    ):
                        await self._live.fail_deployment(active.deployment_id, str(exc))
                        return {"status": "failed", "reason": str(exc), "retry_safe": True}
                    self._record_error(exc, [active])
                except (QMTError, _Desynchronized, OSError, ValueError) as exc:
                    self._record_error(exc, [active])
                else:
                    await self._recover_safely()
                result = self._deploy_result(active)
        self._ensure_worker()
        return result

    def _deploy_result(self, deployment: LiveDeployment) -> dict:
        sync = self._sync.get(deployment.portfolio_id, "UNKNOWN")
        return {
            "status": "staged" if sync == "SYNCED" else "desynced" if sync == "DESYNCED" else "unknown",
            "portfolio_id": deployment.portfolio_id,
            "deployment_id": deployment.deployment_id,
            "retry_safe": True,
            "sync": sync,
            "reason": self._reasons.get(deployment.portfolio_id),
        }

    async def _stage_body(self, deployment: LiveDeployment) -> dict:
        artifact = self._config.state_dir / deployment.strategy_artifact_relpath
        source = await asyncio.to_thread(artifact.read_bytes)
        if hashlib.sha256(source).hexdigest() != deployment.strategy_sha256:
            raise _Desynchronized("Frozen strategy artifact no longer matches its recorded hash")
        bootstrap_path = artifact.with_name("bootstrap.json")
        if bootstrap_path.exists():
            bootstrap = json.loads(await asyncio.to_thread(bootstrap_path.read_text, encoding="utf-8"))
        else:
            if await self._head(deployment.portfolio_id) != deployment.bootstrap_ledger_sequence:
                raise _Desynchronized("Original Bootstrap is missing after the canonical Ledger advanced")
            bootstrap = (await self._live.build_bootstrap_snapshot(deployment.deployment_id)).to_wire()
            await asyncio.to_thread(self._save_bootstrap, bootstrap_path, bootstrap)
        if (
            not isinstance(bootstrap, dict)
            or bootstrap.get("deployment_id") != deployment.deployment_id
            or bootstrap.get("portfolio_id") != deployment.portfolio_id
            or bootstrap.get("broker_account_id") != deployment.broker_account_id
            or bootstrap.get("ledger_sequence") != deployment.bootstrap_ledger_sequence
        ):
            raise _Desynchronized("Frozen Bootstrap identity or sequence does not match the deployment")
        manifest = {
            "deployment_id": deployment.deployment_id,
            "portfolio_id": deployment.portfolio_id,
            "broker_account_id": deployment.broker_account_id,
            "strategy_source_path": deployment.strategy_source_path,
            "strategy_sha256": deployment.strategy_sha256,
            "strategy_parameters": deployment.strategy_parameters,
            "rqalpha_version": deployment.rqalpha_version,
            "created_at": deployment.created_at.isoformat(),
        }
        return {
            "manifest": manifest,
            "strategy_source_base64": base64.b64encode(source).decode("ascii"),
            "bootstrap": bootstrap,
        }

    @staticmethod
    def _save_bootstrap(path: Path, bootstrap: dict) -> None:
        temporary = path.with_suffix(".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(bootstrap, stream, ensure_ascii=False, allow_nan=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _unavailable(self) -> None:
        self._available = False
        self._session_id = None
        for portfolio_id in self._sync:
            self._sync[portfolio_id] = "UNKNOWN"

    def _record_error(self, exc: Exception, active: list[LiveDeployment]) -> None:
        desynced = isinstance(exc, _Desynchronized) or (
            isinstance(exc, QMTRejectedError)
            and exc.code
            in {"DEPLOYMENT_CONFLICT", "PORTFOLIO_DEPLOYMENT_CONFLICT", "SEQUENCE_CONFLICT", "FACT_ACK_CONFLICT"}
        )
        if not desynced:
            self._unavailable()
        for deployment in active:
            self._sync[deployment.portfolio_id] = "DESYNCED" if desynced else "UNKNOWN"
            self._reasons[deployment.portfolio_id] = str(exc)

    async def _recover_safely(self) -> bool:
        active = await self._active()
        try:
            await self._recover(active)
            return True
        except (QMTError, LiveExecutionError, OSError, ValueError) as exc:
            self._record_error(exc, active)
            return False

    async def _recover(self, active: list[LiveDeployment]) -> None:
        node = await self._connect()
        if not active:
            return
        self._start_heartbeat()
        assert self._client is not None and self._session_id is not None
        session_id = self._session_id
        by_id = {d.deployment_id: d for d in active}
        by_portfolio = {d.portfolio_id: d for d in active}
        for remote in node["deployments"]:
            local = by_portfolio.get(remote["portfolio_id"])
            if (
                local is not None
                and (remote["status"] in {"STAGED", "RUNNING"} or remote["pending_fact_count"])
                and (
                    remote["deployment_id"] != local.deployment_id
                    or remote["broker_account_id"] != local.broker_account_id
                )
            ):
                raise _Desynchronized("Execution node and Core deployment identity differ")
            if local is None and (remote["status"] in {"STAGED", "RUNNING"} or remote["pending_fact_count"]):
                raise _Desynchronized("Execution node has an orphan deployment or pending fact")
        remotes = {d["deployment_id"]: d for d in node["deployments"]}
        for deployment in active:
            if deployment.deployment_id not in remotes:
                remotes[deployment.deployment_id] = await self._client.stage_deployment(
                    session_id, deployment.deployment_id, await self._stage_body(deployment)
                )
        while True:
            if self._session_id != session_id:
                raise QMTProtocolError("Control session changed during recovery")
            fact = await self._client.get_next_fact(session_id)
            if fact is None:
                break
            deployment = by_id.get(fact["deployment_id"])
            if deployment is None:
                raise _Desynchronized("Oldest pending fact has no matching ACTIVE Core deployment")
            self._sync[deployment.portfolio_id] = "COMMIT_PENDING"
            trade = TradeFact.parse(fact["payload"])
            try:
                entry = await self._live.ingest_live_trade(
                    trade.deployment_id,
                    broker_trade_id=trade.broker_trade_id,
                    instrument=trade.instrument,
                    side=trade.side,
                    quantity=trade.quantity,
                    price=trade.price,
                    commission=trade.commission,
                    tax=trade.tax,
                    other_fee=trade.other_fee,
                    effective_at=trade.effective_at,
                )
            except (ValueError, LiveExecutionError) as exc:
                raise _Desynchronized(str(exc)) from exc
            expected = remotes[deployment.deployment_id]["acked_core_sequence"] + 1
            if entry.sequence != expected:
                raise _Desynchronized("Canonical Ledger entry does not continue the remote ACK cursor")
            await self._client.ack_fact(session_id, fact["fact_id"], entry.sequence)
            remotes[deployment.deployment_id]["acked_core_sequence"] = entry.sequence
        node = await self._client.get_node_status()
        remotes = {d["deployment_id"]: d for d in node["deployments"]}
        reconciled = []
        for deployment in active:
            remote = remotes.get(deployment.deployment_id)
            head = await self._head(deployment.portfolio_id)
            if (
                remote is None
                or remote["broker_account_id"] != deployment.broker_account_id
                or remote["portfolio_id"] != deployment.portfolio_id
            ):
                raise _Desynchronized("Deployment disappeared or changed during reconciliation")
            if remote["pending_fact_count"]:
                self._sync[deployment.portfolio_id] = "COMMIT_PENDING"
                return
            if remote["acked_core_sequence"] != head:
                raise _Desynchronized("Empty remote outbox cursor differs from canonical Ledger head")
            reconciled.append({"deployment_id": deployment.deployment_id, "acked_core_sequence": head})
        await self._client.renew_control_session(session_id, reconciled)
        node = await self._client.get_node_status()
        remotes = {d["deployment_id"]: d for d in node["deployments"]}
        if self._session_id != session_id:
            raise QMTProtocolError("Control session changed during final reconciliation")
        for deployment in active:
            remote = remotes.get(deployment.deployment_id)
            if remote is None or remote["acked_core_sequence"] != await self._head(deployment.portfolio_id):
                raise _Desynchronized("Remote cursor changed during final reconciliation")
            if remote["pending_fact_count"]:
                self._sync[deployment.portfolio_id] = "COMMIT_PENDING"
                return
        for deployment in active:
            if self._session_id != session_id:
                raise QMTProtocolError("Control session changed before terminal reconciliation")
            remote = remotes[deployment.deployment_id]
            self._sync[deployment.portfolio_id] = "SYNCED"
            self._reasons.pop(deployment.portfolio_id, None)
            if remote["status"] == "STOPPED":
                await self._live.stop_deployment(deployment.deployment_id)
            elif remote["status"] == "FAILED":
                await self._live.fail_deployment(
                    deployment.deployment_id, remote.get("failure_reason") or "Execution node reported FAILED"
                )
        self._node = node
        self._available = True

    async def ensure_connected_now(self) -> bool:
        async with self._lock:
            result = await self._recover_safely()
            if not await self._active():
                await self._release_session()
        if await self._active():
            self._ensure_worker()
        return result

    async def start(self) -> None:
        if await self._active():
            await self.ensure_connected_now()

    def _ensure_worker(self) -> None:
        if not self._closed and (self._worker is None or self._worker.done()):
            self._worker = asyncio.create_task(self._run(), name="qmt-active-recovery")

    async def _run(self) -> None:
        delays = (1, 2, 5, 10, 30)
        failures = 0
        while not self._closed and await self._active():
            await asyncio.sleep(delays[min(failures, len(delays) - 1)])
            async with self._lock:
                if not await self._active():
                    break
                success = await self._recover_safely()
            failures = 0 if success else failures + 1
        async with self._lock:
            if not await self._active():
                await self._release_session()

    def _start_heartbeat(self) -> None:
        if not self._closed and (self._heartbeat is None or self._heartbeat.done()):
            self._heartbeat = asyncio.create_task(self._renew_loop(), name="qmt-control-heartbeat")

    async def _renew_loop(self) -> None:
        while not self._closed and self._session_id is not None and await self._active():
            session_id = self._session_id
            await asyncio.sleep(self._lease_seconds / 3)
            if session_id != self._session_id or not await self._active():
                continue
            try:
                assert self._client is not None
                await self._client.renew_control_session(session_id)
            except QMTError:
                if session_id == self._session_id:
                    self._unavailable()
                return

    async def get_live_status(self, portfolio_id: str | None = None) -> dict:
        async with self._lock:
            try:
                if self._client is not None:
                    self._node = await self._client.get_node_status()
                    self._available = True
            except QMTError:
                self._unavailable()
            deployments = await self._live.list_deployments(portfolio_id)
            latest = {d.portfolio_id: d for d in deployments}
            latest.update({d.portfolio_id: d for d in deployments if d.status is LiveDeploymentStatus.ACTIVE})
            if portfolio_id is not None:
                return await self._status_for(portfolio_id, latest.get(portfolio_id))
            ids = set(latest)
            if self._node is not None:
                ids.update(d["portfolio_id"] for d in self._node["deployments"])
            return {"portfolios": [await self._status_for(pid, latest.get(pid)) for pid in sorted(ids)]}

    async def _status_for(self, portfolio_id: str, deployment: LiveDeployment | None) -> dict:
        portfolio = await asyncio.to_thread(get_portfolio, self._config.portfolio_db, portfolio_id)
        candidates = [d for d in (self._node or {}).get("deployments", []) if d["portfolio_id"] == portfolio_id]
        remote = next(
            (d for d in candidates if deployment is not None and d["deployment_id"] == deployment.deployment_id), None
        )
        current = next((d for d in candidates if d["status"] in {"STAGED", "RUNNING"} or d["pending_fact_count"]), None)
        remote = current or remote or (candidates[-1] if candidates else None)
        sync = self._sync.get(portfolio_id, "UNKNOWN")
        if not self._available:
            sync = "UNKNOWN"
        elif remote is not None:
            if (
                deployment is None
                or remote["deployment_id"] != deployment.deployment_id
                or remote["broker_account_id"] != deployment.broker_account_id
            ) or (deployment.status is not LiveDeploymentStatus.ACTIVE and remote["status"] in {"STAGED", "RUNNING"}):
                sync = "DESYNCED"
            elif remote["pending_fact_count"] and sync != "DESYNCED":
                sync = "COMMIT_PENDING"
            elif remote["acked_core_sequence"] != await self._head(portfolio_id):
                sync = "DESYNCED"
        elif deployment is not None and deployment.status is LiveDeploymentStatus.ACTIVE:
            sync = "UNKNOWN"
        if (
            sync == "SYNCED"
            and deployment is not None
            and deployment.status is LiveDeploymentStatus.ACTIVE
            and ((self._node or {}).get("control", {}).get("status") != "AVAILABLE" or self._session_id is None)
        ):
            sync = "UNKNOWN"
        return {
            "portfolio": {"portfolio_id": portfolio_id, "name": portfolio.name if portfolio else None},
            "core": {
                "deployment_id": deployment.deployment_id if deployment else None,
                "deployment_status": deployment.status.value if deployment else None,
                "strategy_source_path": deployment.strategy_source_path if deployment else None,
                "strategy_sha256": deployment.strategy_sha256 if deployment else None,
                "broker_account_id": deployment.broker_account_id if deployment else None,
                "ledger_sequence": await self._head(portfolio_id),
            },
            "node": {
                "availability": "AVAILABLE" if self._available else "UNAVAILABLE",
                "remote_status": remote["status"] if remote and self._available else None,
                "acked_core_sequence": remote["acked_core_sequence"] if remote and self._available else None,
                "pending_fact_count": remote["pending_fact_count"] if remote and self._available else None,
            },
            "sync": sync,
            "sync_reason": self._reasons.get(portfolio_id),
            "qmt": (self._node or {}).get("qmt", {"status": "not_connected"}),
            "capabilities": {"can_start": False, "reason": "BACKEND_NOT_READY"},
        }

    async def _release_session(self) -> None:
        session_id = self._session_id
        self._session_id = None
        if self._client is not None and session_id is not None:
            with suppress(QMTError, TimeoutError):
                await asyncio.wait_for(self._client.close_control_session(session_id), timeout=1)

    async def close(self) -> None:
        self._closed = True
        tasks = [task for task in (self._worker, self._heartbeat) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._client is not None:
            await self._release_session()
            await self._client.close()
        self._session_id = None
