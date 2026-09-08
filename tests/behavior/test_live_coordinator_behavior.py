from decimal import Decimal

import httpx
import pytest

from investorch.application.live_coordinator import LiveDeploymentCoordinator
from investorch.live.domain import LiveExecutionError
from investorch.qmt.client import QMTClient
from investorch.qmt.config import QMTConnectionProfile
from tests.behavior.test_live_deployment_behavior import setup_live


def client_for(handler):
    return QMTClient(
        QMTConnectionProfile("node", "http://node/mcp", "http://node/api/v1", {}, 1),
        transport=httpx.MockTransport(handler),
    )


async def test_unreachable_preflight_releases_prepared_owner(tmp_path):
    config, portfolios, p, live = await setup_live(tmp_path)

    def unavailable(request):
        raise httpx.ConnectError("offline", request=request)

    coordinator = LiveDeploymentCoordinator(config=config, client=client_for(unavailable))
    try:
        result = await coordinator.deploy_live_strategy(p.id, "account")
        assert result["status"] == "failed"
        deployments = await live.list_deployments(p.id)
        assert len(deployments) == 1
        assert deployments[0].status.value == "FAILED"
        await portfolios.record_cash_flow(p.id, amount=Decimal(1), source="manual")
    finally:
        await coordinator.close()


class WireNode:
    """HTTP boundary fixture for ambiguous responses; canonical state remains real SQLite."""

    def __init__(self):
        self.deployments = {}
        self.stage_bodies = []
        self.facts = []
        self.acks = []
        self.session = 0
        self.renewed = 0
        self.closed = []
        self.lose_stage = False
        self.lose_ack = None
        self.reject_stage = None
        self.offline = False
        self.sequence_offset = 0

    def enqueue(self, deployment_id, *, trade_id="t1", quantity="1", side="BUY"):
        from datetime import UTC, datetime

        payload = {
            "schema_version": 1,
            "deployment_id": deployment_id,
            "broker_trade_id": trade_id,
            "instrument": {"code": "600519", "market": "XSHG"},
            "side": side,
            "quantity": quantity,
            "price": "10",
            "commission": "0",
            "tax": "0",
            "other_fee": "0",
            "effective_at": datetime.now(UTC).isoformat(),
        }
        self.facts.append(
            {
                "fact_id": trade_id,
                "queue_sequence": len(self.acks) + len(self.facts) + 1,
                "deployment_id": deployment_id,
                "external_fact_id": trade_id,
                "fact_type": "TRADE_V1",
                "payload": payload,
            }
        )
        self.deployments[deployment_id]["pending_fact_count"] += 1

    def __call__(self, request):
        import json
        from datetime import UTC, datetime, timedelta

        if self.offline:
            raise httpx.ConnectError("offline", request=request)
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        if path.endswith("/node/status"):
            return httpx.Response(
                200,
                json={
                    "service": {"status": "ready"},
                    "qmt": {"status": "not_connected"},
                    "control": {"status": "AVAILABLE", "lease_expires_at": None},
                    "deployments": list(self.deployments.values()),
                },
            )
        if path.endswith("/control-sessions"):
            self.session += 1
            return httpx.Response(200, json={"session_id": str(self.session), "lease_timeout_seconds": 10})
        if "/control-sessions/" in path:
            if request.method == "DELETE":
                self.closed.append(path.rsplit("/", 1)[-1])
                return httpx.Response(200, json={"status": "closed"})
            self.renewed += 1
            for item in (body or {}).get("reconciled_deployments", []):
                self.deployments[item["deployment_id"]]["portfolio_sync"] = "SYNCED"
            return httpx.Response(
                200,
                json={
                    "session_id": str(self.session),
                    "lease_timeout_seconds": 10,
                    "expires_at": (datetime.now(UTC) + timedelta(seconds=10)).isoformat(),
                },
            )
        if "/deployments/" in path:
            if self.reject_stage:
                return httpx.Response(422, json={"code": self.reject_stage, "message": "rejected", "retryable": False})
            self.stage_bodies.append(body)
            manifest = body["manifest"]
            deployment_id = manifest["deployment_id"]
            self.deployments.setdefault(
                deployment_id,
                {
                    "deployment_id": deployment_id,
                    "portfolio_id": manifest["portfolio_id"],
                    "broker_account_id": manifest["broker_account_id"],
                    "status": "STAGED",
                    "acked_core_sequence": body["bootstrap"]["ledger_sequence"] + self.sequence_offset,
                    "pending_fact_count": 0,
                    "portfolio_sync": "UNKNOWN",
                    "failure_reason": None,
                },
            )
            if self.lose_stage:
                self.lose_stage = False
                raise httpx.ReadTimeout("stage response lost", request=request)
            return httpx.Response(200, json=self.deployments[deployment_id])
        if path.endswith("/facts/next"):
            return httpx.Response(200, json={"fact": self.facts[0] if self.facts else None})
        if path.endswith("/ack"):
            if self.lose_ack == "before":
                self.lose_ack = None
                raise httpx.ReadTimeout("ACK not received", request=request)
            fact = self.facts.pop(0)
            self.acks.append(body["committed_ledger_sequence"])
            self.deployments[fact["deployment_id"]]["acked_core_sequence"] = body["committed_ledger_sequence"]
            self.deployments[fact["deployment_id"]]["pending_fact_count"] -= 1
            if self.lose_ack == "after":
                self.lose_ack = None
                raise httpx.ReadTimeout("ACK response lost", request=request)
            return httpx.Response(
                200,
                json={
                    "fact_id": fact["fact_id"],
                    "status": "ACKED",
                    "committed_ledger_sequence": body["committed_ledger_sequence"],
                },
            )
        raise AssertionError(path)


async def test_lost_stage_response_keeps_owner_and_reuses_frozen_deployment(tmp_path):
    from investorch.portfolio.domain import StrategyBinding

    config, portfolios, p, live = await setup_live(tmp_path)
    node = WireNode()
    node.lose_stage = True
    coordinator = LiveDeploymentCoordinator(config=config, client=client_for(node))
    try:
        first = await coordinator.deploy_live_strategy(p.id, "account")
        assert first["status"] == "unknown"
        assert (await live.list_deployments(p.id))[0].status.value == "ACTIVE"
        original = node.stage_bodies[0]
        node.deployments.clear()  # Lost remote runtime storage; exact local freeze must survive restart.
    finally:
        await coordinator.close()
    (config.workspace_dir / "strategy.py").write_text("changed source")
    await portfolios.update_metadata(p.id, strategy_binding=StrategyBinding("strategy.py", {"new": True}))
    restarted = LiveDeploymentCoordinator(config=config, client=client_for(node))
    try:
        result = await restarted.deploy_live_strategy(p.id, "account")
        assert result["status"] == "staged"
        assert result["deployment_id"] == first["deployment_id"]
        assert len(await live.list_deployments(p.id)) == 1
        assert node.stage_bodies[-1] == original
        assert (await restarted.get_live_status(p.id))["capabilities"] == {
            "can_start": False,
            "reason": "BACKEND_NOT_READY",
        }
    finally:
        await restarted.close()


@pytest.mark.parametrize("loss", ["before", "after"])
async def test_lost_ack_recovers_without_duplicate_canonical_trade(tmp_path, loss):
    config, portfolios, p, _live = await setup_live(tmp_path)
    node = WireNode()
    coordinator = LiveDeploymentCoordinator(config=config, client=client_for(node))
    try:
        deployed = await coordinator.deploy_live_strategy(p.id, "account")
        node.enqueue(deployed["deployment_id"])
        node.lose_ack = loss
        assert await coordinator.ensure_connected_now() is False
        assert len(await portfolios.list_ledger(p.id)) == 1
        assert await coordinator.ensure_connected_now() is True
        assert len(await portfolios.list_ledger(p.id)) == 1
        assert node.acks == [1]
        assert (await coordinator.get_live_status(p.id))["sync"] == "SYNCED"
    finally:
        await coordinator.close()


async def test_terminal_waits_for_pending_ack_then_allows_new_deployment(tmp_path):
    config, portfolios, p, live = await setup_live(tmp_path)
    node = WireNode()
    coordinator = LiveDeploymentCoordinator(config=config, client=client_for(node))
    try:
        first = await coordinator.deploy_live_strategy(p.id, "account")
        node.enqueue(first["deployment_id"])
        node.deployments[first["deployment_id"]]["status"] = "STOPPED"
        node.lose_ack = "before"
        assert await coordinator.ensure_connected_now() is False
        assert (await live.get_deployment(first["deployment_id"])).status.value == "ACTIVE"
        assert await coordinator.ensure_connected_now() is True
        assert (await live.get_deployment(first["deployment_id"])).status.value == "STOPPED"
        second = await coordinator.deploy_live_strategy(p.id, "account")
        assert second["status"] == "staged"
        assert second["deployment_id"] != first["deployment_id"]
        status = await coordinator.get_live_status(p.id)
        assert status["core"]["deployment_id"] == second["deployment_id"]
        assert status["node"]["remote_status"] == "STAGED"
        assert status["sync"] == "SYNCED"
        assert len(await portfolios.list_ledger(p.id)) == 1
    finally:
        await coordinator.close()


async def test_invalid_economic_fact_stays_pending_and_desynchronized(tmp_path):
    config, portfolios, p, live = await setup_live(tmp_path)
    node = WireNode()
    coordinator = LiveDeploymentCoordinator(config=config, client=client_for(node))
    try:
        result = await coordinator.deploy_live_strategy(p.id, "account")
        node.enqueue(result["deployment_id"], side="SELL")
        assert await coordinator.ensure_connected_now() is False
        status = await coordinator.get_live_status(p.id)
        assert status["sync"] == "DESYNCED"
        assert len(node.facts) == 1
        assert node.acks == []
        assert await portfolios.list_ledger(p.id) == []
        assert (await live.get_deployment(result["deployment_id"])).status.value == "ACTIVE"
    finally:
        await coordinator.close()


async def test_definitive_stage_validation_rejection_releases_owner(tmp_path):
    config, _portfolios, p, live = await setup_live(tmp_path)
    node = WireNode()
    node.reject_stage = "HASH_MISMATCH"
    coordinator = LiveDeploymentCoordinator(config=config, client=client_for(node))
    try:
        result = await coordinator.deploy_live_strategy(p.id, "account")
        assert result["status"] == "failed"
        assert (await live.list_deployments(p.id))[0].status.value == "FAILED"
        assert node.deployments == {}
    finally:
        await coordinator.close()


async def test_node_global_fifo_uses_each_portfolio_cursor(tmp_path):
    from investorch.portfolio.domain import StrategyBinding

    config, portfolios, p, _live = await setup_live(tmp_path)
    other = await portfolios.create(name="Other", base_currency="CNY", strategy_binding=StrategyBinding("strategy.py"))
    await portfolios.initialize(other.id, cash=Decimal(100), source="opening")
    await portfolios.assign_unallocated_assets(other.id, "account")
    other_head = len(await portfolios.list_ledger(other.id))
    node = WireNode()
    coordinator = LiveDeploymentCoordinator(config=config, client=client_for(node))
    try:
        first = await coordinator.deploy_live_strategy(p.id, "account")
        second = await coordinator.deploy_live_strategy(other.id, "account")
        node.enqueue(second["deployment_id"], trade_id="other-1")
        node.enqueue(first["deployment_id"], trade_id="first-1")
        node.enqueue(second["deployment_id"], trade_id="other-2")
        assert await coordinator.ensure_connected_now() is True
        assert node.acks == [other_head + 1, 1, other_head + 2]
        assert len(await portfolios.list_ledger(p.id)) == 1
        assert len(await portfolios.list_ledger(other.id)) == other_head + 2
    finally:
        await coordinator.close()


async def test_identity_conflict_never_adopts_or_refreezes(tmp_path):
    config, _portfolios, p, live = await setup_live(tmp_path)
    node = WireNode()
    coordinator = LiveDeploymentCoordinator(config=config, client=client_for(node))
    try:
        first = await coordinator.deploy_live_strategy(p.id, "account")
        original = node.deployments.pop(first["deployment_id"])
        node.deployments["other"] = {**original, "deployment_id": "other"}
        result = await coordinator.deploy_live_strategy(p.id, "account")
        assert result["status"] == "desynced"
        assert len(await live.list_deployments(p.id)) == 1
        assert len(node.stage_bodies) == 1
        with pytest.raises(LiveExecutionError, match="another BrokerAccount"):
            await coordinator.deploy_live_strategy(p.id, "different-account")
    finally:
        await coordinator.close()


async def test_empty_outbox_head_mismatch_is_desynchronized(tmp_path):
    config, _portfolios, p, live = await setup_live(tmp_path)
    node = WireNode()
    node.sequence_offset = 2
    coordinator = LiveDeploymentCoordinator(config=config, client=client_for(node))
    try:
        result = await coordinator.deploy_live_strategy(p.id, "account")
        assert result["status"] == "desynced"
        assert node.acks == []
        assert (await live.get_deployment(result["deployment_id"])).status.value == "ACTIVE"
    finally:
        await coordinator.close()


async def test_idle_start_has_no_node_requests_or_recovery_loop(tmp_path):
    config, _portfolios, _p, _live = await setup_live(tmp_path)
    requests = []
    node = WireNode()

    def handle(request):
        requests.append(request)
        return node(request)

    coordinator = LiveDeploymentCoordinator(config=config, client=client_for(handle))
    try:
        await coordinator.start()
        assert requests == []
        assert (await coordinator.get_live_status())["portfolios"] == []
        assert [r.url.path for r in requests] == ["/api/v1/node/status"]
    finally:
        await coordinator.close()


async def test_heartbeat_renews_while_fact_request_is_blocked(tmp_path):
    import asyncio

    config, _portfolios, p, _live = await setup_live(tmp_path)
    node = WireNode()
    entered, release, heartbeat = asyncio.Event(), asyncio.Event(), asyncio.Event()
    block = False

    async def handle(request):
        if block and request.url.path.endswith("/facts/next"):
            entered.set()
            await release.wait()
        response = node(request)
        if request.url.path.endswith("/control-sessions"):
            return httpx.Response(200, json={**response.json(), "lease_timeout_seconds": 0.03})
        if block and request.url.path.endswith("/renew") and not request.content:
            heartbeat.set()
        return response

    coordinator = LiveDeploymentCoordinator(config=config, client=client_for(handle))
    try:
        await coordinator.deploy_live_strategy(p.id, "account")
        block = True
        recovery = asyncio.create_task(coordinator.ensure_connected_now())
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(heartbeat.wait(), 1)
        release.set()
        assert await recovery is True
    finally:
        release.set()
        await coordinator.close()


async def test_status_does_not_reuse_synced_observation_when_node_is_offline(tmp_path):
    config, _portfolios, p, live = await setup_live(tmp_path)
    node = WireNode()
    coordinator = LiveDeploymentCoordinator(config=config, client=client_for(node))
    try:
        result = await coordinator.deploy_live_strategy(p.id, "account")
        node.offline = True
        status = await coordinator.get_live_status(p.id)
        assert status["sync"] == "UNKNOWN"
        assert status["node"]["availability"] == "UNAVAILABLE"
        assert (await live.get_deployment(result["deployment_id"])).status.value == "ACTIVE"
    finally:
        await coordinator.close()


async def test_desynchronization_explains_the_blocking_condition(tmp_path):
    config, _portfolios, p, _live = await setup_live(tmp_path)
    node = WireNode()
    node.sequence_offset = 1
    coordinator = LiveDeploymentCoordinator(config=config, client=client_for(node))
    try:
        result = await coordinator.deploy_live_strategy(p.id, "account")
        assert "cursor" in result["reason"]
        status = await coordinator.get_live_status(p.id)
        assert status["sync_reason"] == result["reason"]
    finally:
        await coordinator.close()


async def test_application_host_opens_without_a_qmt_configuration(tmp_path, monkeypatch):
    from investorch.application.host import open_application_host
    from tests.support.config import make_test_config

    config = make_test_config(tmp_path, {"secrets": {"DEEPSEEK_API_KEY": "unused-test-key"}})

    async def initialize_command_sandbox(_execution, _workspace):
        # This host contract does not execute commands or depend on the macOS sandbox.
        pass

    monkeypatch.setattr("investorch.application.host.start_execution", initialize_command_sandbox)

    async def approve(_request, _reason):
        return False

    async with open_application_host(
        config, manual_approval_handler=approve, create_initial_session=False, enable_activity=False
    ) as host:
        assert host.live_coordinator is not None
        assert (await host.live_coordinator.get_live_status())["portfolios"] == []


async def test_session_renewal_failure_keeps_active_and_foreground_recovers(tmp_path):
    import asyncio

    config, _portfolios, p, live = await setup_live(tmp_path)
    node = WireNode()
    failed = asyncio.Event()
    fail_renew = False

    async def handle(request):
        nonlocal fail_renew
        if fail_renew and request.url.path.endswith("/renew") and not request.content:
            fail_renew = False
            failed.set()
            return httpx.Response(409, json={"code": "STALE_CONTROL_SESSION", "message": "expired", "retryable": True})
        response = node(request)
        if request.url.path.endswith("/control-sessions"):
            return httpx.Response(200, json={**response.json(), "lease_timeout_seconds": 0.03})
        return response

    coordinator = LiveDeploymentCoordinator(config=config, client=client_for(handle))
    try:
        result = await coordinator.deploy_live_strategy(p.id, "account")
        first_session = node.session
        fail_renew = True
        await asyncio.wait_for(failed.wait(), 1)
        assert (await coordinator.get_live_status(p.id))["sync"] == "UNKNOWN"
        assert (await live.get_deployment(result["deployment_id"])).status.value == "ACTIVE"
        assert await coordinator.ensure_connected_now() is True
        assert node.session == first_session + 1
        assert (await coordinator.get_live_status(p.id))["sync"] == "SYNCED"
        node.deployments[result["deployment_id"]]["status"] = "STOPPED"
        assert await coordinator.ensure_connected_now() is True
        assert node.closed == [str(node.session)]
        assert (await live.get_deployment(result["deployment_id"])).status.value == "STOPPED"
    finally:
        await coordinator.close()


async def test_missing_original_bootstrap_after_ingestion_cannot_be_rebuilt(tmp_path):
    config, portfolios, p, live = await setup_live(tmp_path)
    node = WireNode()
    coordinator = LiveDeploymentCoordinator(config=config, client=client_for(node))
    try:
        result = await coordinator.deploy_live_strategy(p.id, "account")
        node.enqueue(result["deployment_id"])
        assert await coordinator.ensure_connected_now() is True
        assert len(await portfolios.list_ledger(p.id)) == 1
        deployment = await live.get_deployment(result["deployment_id"])
        (config.state_dir / deployment.strategy_artifact_relpath).with_name("bootstrap.json").unlink()
        node.deployments.clear()
        assert await coordinator.ensure_connected_now() is False
        assert len(node.stage_bodies) == 1
        assert (await live.get_deployment(result["deployment_id"])).status.value == "ACTIVE"
        status = await coordinator.get_live_status(p.id)
        assert "Bootstrap is missing" in status["sync_reason"]
        assert status["sync"] == "DESYNCED"
    finally:
        await coordinator.close()


async def test_status_reflects_remote_session_reconciliation_invalidation(tmp_path):
    config, _portfolios, p, _live = await setup_live(tmp_path)
    node = WireNode()
    coordinator = LiveDeploymentCoordinator(config=config, client=client_for(node))
    try:
        result = await coordinator.deploy_live_strategy(p.id, "account")
        node.deployments[result["deployment_id"]]["portfolio_sync"] = "UNKNOWN"
        assert (await coordinator.get_live_status(p.id))["sync"] == "UNKNOWN"
    finally:
        await coordinator.close()


async def test_existing_deployment_retry_reports_observed_terminal_status(tmp_path):
    config, _portfolios, p, live = await setup_live(tmp_path)
    node = WireNode()
    coordinator = LiveDeploymentCoordinator(config=config, client=client_for(node))
    try:
        result = await coordinator.deploy_live_strategy(p.id, "account")
        node.deployments[result["deployment_id"]]["status"] = "STOPPED"
        repeated = await coordinator.deploy_live_strategy(p.id, "account")
        assert repeated["status"] == "stopped"
        assert repeated["deployment_id"] == result["deployment_id"]
        assert (await live.get_deployment(result["deployment_id"])).status.value == "STOPPED"
    finally:
        await coordinator.close()
