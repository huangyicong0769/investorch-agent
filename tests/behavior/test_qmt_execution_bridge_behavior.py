"""Exercise the real HTTP boundary with a separately installed companion process."""

import json

import httpx
import pytest
from agents.mcp import MCPServerStreamableHttp

from investorch.application.live_coordinator import LiveDeploymentCoordinator
from investorch.mcp import ControlSessionAuth
from investorch.qmt.client import QMTClient
from tests.behavior.test_live_deployment_behavior import setup_live
from tests.support.qmt_companion import companion_node


@pytest.mark.parametrize("lose_response", ["", "ack"])
async def test_real_node_stages_drains_trade_and_stops_through_mcp(tmp_path, monkeypatch, lose_response):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    config, portfolios, portfolio, live = await setup_live(tmp_path)
    async with companion_node(tmp_path, lose_response=lose_response) as profile:
        coordinator = LiveDeploymentCoordinator(config=config, client=QMTClient(profile))
        try:
            deployed = await coordinator.deploy_live_strategy(portfolio.id, "account")
            assert deployed["status"] == "staged", deployed
            status = await coordinator.get_live_status(portfolio.id)
            assert status["sync"] == "SYNCED", status
            deployment = (await live.list_deployments(portfolio.id))[0]
            payload = {
                "schema_version": 1,
                "deployment_id": deployment.deployment_id,
                "broker_trade_id": "bridge-trade-1",
                "instrument": {"code": "600519", "market": "XSHG"},
                "side": "BUY",
                "quantity": "100",
                "price": "10.50",
                "commission": "1.00",
                "tax": "0",
                "other_fee": "0",
                "effective_at": "2026-09-08T01:23:45+08:00",
            }
            async with httpx.AsyncClient(headers=dict(profile.headers)) as http:
                response = await http.post(profile.mcp_url.removesuffix("/mcp") + "/__test/enqueue", json=payload)
                assert response.status_code == 200, response.text
            original_authority = coordinator.control_session_id
            delivered = await coordinator.ensure_connected_now()
            if lose_response == "ack":
                assert delivered is False
                assert coordinator.control_session_id is None
                assert await coordinator.ensure_connected_now()
                assert coordinator.control_session_id == original_authority
            else:
                assert delivered is True
            ledger = await portfolios.list_ledger(portfolio.id)
            assert len(ledger) == 1
            assert ledger[0].broker_account_id == "account"
            assert ledger[0].sequence == 1
            status = await coordinator.get_live_status(portfolio.id)
            assert status["sync"] == "SYNCED", status
            async with QMTClient(profile) as client:
                node = await client.get_node_status()
            assert node["deployments"][0]["acked_core_sequence"] == 1
            assert node["deployments"][0]["pending_fact_count"] == 0
            async with MCPServerStreamableHttp(
                name="node",
                params={
                    "url": profile.mcp_url,
                    "headers": dict(profile.headers),
                    "auth": ControlSessionAuth(lambda: coordinator.control_session_id),
                },
            ) as mcp:
                started = await mcp.call_tool("start_live_strategy", {"portfolio_id": portfolio.id})
                assert started.structured_content["code"] == "BACKEND_NOT_READY"
                stopped = await mcp.call_tool("stop_live_strategy", {"portfolio_id": portfolio.id})
                assert stopped.is_error is False
            assert await coordinator.ensure_connected_now()
            assert (await live.get_deployment(deployment.deployment_id)).status.value == "STOPPED"
            assert len(await portfolios.list_ledger(portfolio.id)) == 1
        finally:
            await coordinator.close()


async def test_restart_restages_exact_frozen_deployment_on_empty_replacement_node(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    config, _portfolios, portfolio, live = await setup_live(tmp_path)
    async with companion_node(tmp_path / "first") as profile:
        coordinator = LiveDeploymentCoordinator(config=config, client=QMTClient(profile))
        try:
            assert (await coordinator.deploy_live_strategy(portfolio.id, "account"))["status"] == "staged"
            deployment = (await live.list_deployments(portfolio.id))[0]
            staged = tmp_path / "first/node/deployments" / deployment.deployment_id
            original_source = (staged / "strategy.py").read_bytes()
            original_bootstrap = json.loads((staged / "bootstrap.json").read_text())
        finally:
            await coordinator.close()
    (config.workspace_dir / "strategy.py").write_text("changed workspace after freeze")
    async with companion_node(tmp_path / "second") as profile:
        restarted = LiveDeploymentCoordinator(config=config, client=QMTClient(profile))
        try:
            assert await restarted.ensure_connected_now()
            staged = tmp_path / "second/node/deployments" / deployment.deployment_id
            assert (staged / "strategy.py").read_bytes() == original_source
            assert json.loads((staged / "bootstrap.json").read_text()) == original_bootstrap
            assert len(await live.list_deployments(portfolio.id)) == 1
            assert (await restarted.get_live_status(portfolio.id))["sync"] == "SYNCED"
        finally:
            await restarted.close()


async def test_lost_stage_response_retries_one_exact_remote_deployment(tmp_path, monkeypatch):
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    config, _portfolios, portfolio, live = await setup_live(tmp_path)
    async with companion_node(tmp_path / "lost-stage", lose_response="stage") as profile:
        coordinator = LiveDeploymentCoordinator(config=config, client=QMTClient(profile))
        try:
            result = await coordinator.deploy_live_strategy(portfolio.id, "account")
            assert result["status"] == "unknown", result
            assert coordinator.control_session_id is None
            active = (await live.list_deployments(portfolio.id))[0]
            assert active.status.value == "ACTIVE"
            (config.workspace_dir / "strategy.py").write_text("workspace changed")
            retry = await coordinator.deploy_live_strategy(portfolio.id, "account")
            assert retry["status"] == "staged", retry
            assert len(await live.list_deployments(portfolio.id)) == 1
            async with QMTClient(profile) as client:
                node = await client.get_node_status()
            assert len(node["deployments"]) == 1
            assert node["deployments"][0]["deployment_id"] == active.deployment_id
        finally:
            await coordinator.close()
