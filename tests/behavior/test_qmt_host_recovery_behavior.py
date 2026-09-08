"""Real host/SDK/companion acceptance with a controllable network availability boundary."""

import asyncio
import socket
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import uvicorn
from agents import ModelSettings
from agents.testing import ScriptedModel, assistant_message, function_call
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from investorch.application.host import open_application_host
from investorch.application.live import LiveExecutionOperations
from investorch.mcp import configure_mcp_server_config
from investorch.portfolio.domain import Broker, BrokerAccount, StrategyBinding
from investorch.portfolio.storage import create_broker, create_broker_account
from investorch.runtime import RunOptions
from investorch.storage import set_session_title
from tests.behavior.test_qmt_execution_bridge_behavior import companion_node
from tests.support.config import make_test_config


class AvailabilityGate:
    """Only fail/forward HTTP: no substitute MCP protocol or execution-node behavior."""

    def __init__(self, upstream):
        self.upstream = upstream
        self.offline = True
        self.mcp_offline = False
        self.requests = []
        self.http = httpx.AsyncClient(timeout=None, trust_env=False)

    async def __call__(self, scope, receive, send):
        request = Request(scope, receive)
        self.requests.append(request.url.path)
        if self.offline or (self.mcp_offline and request.url.path == "/mcp"):
            await Response(status_code=503)(scope, receive, send)
            return
        headers = {key: value for key, value in request.headers.items() if key.lower() != "host"}
        outgoing = self.http.build_request(
            request.method, self.upstream + request.url.path, headers=headers, content=await request.body()
        )
        result = await self.http.send(outgoing, stream=True)
        response = StreamingResponse(
            result.aiter_raw(),
            status_code=result.status_code,
            headers=dict(result.headers),
            background=BackgroundTask(result.aclose),
        )
        await response(scope, receive, send)


@asynccontextmanager
async def availability_gate(profile):
    gate = AvailabilityGate(profile.mcp_url.removesuffix("/mcp"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(gate, lifespan="off", log_level="error", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        yield gate, url
    finally:
        server.should_exit = True
        await gate.http.aclose()
        await asyncio.wait_for(task, 10)
        sock.close()


def host_config(tmp_path, profile, url):
    config = make_test_config(
        tmp_path,
        {"qmt": {"mcp_server": "node"}, "backtest": {"use_cnequity": False}},
    )
    configure_mcp_server_config(
        config.mcp_config_path,
        "node",
        url=url + "/mcp",
        headers=dict(profile.headers),
        timeout=1,
        require_approval=["start_live_strategy", "stop_live_strategy"],
    )
    return config


def external_seams(monkeypatch, model):
    async def no_command_sandbox(_execution, _workspace):
        # No test executes shell commands; the platform sandbox is outside this contract.
        pass

    monkeypatch.setattr("investorch.application.host.start_execution", no_command_sandbox)
    monkeypatch.setattr(
        "investorch.application.host.create_model",
        lambda _config, role: (model if role == "main" else ScriptedModel(), ModelSettings()),
    )
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


async def reject_approval(_request, _reason):
    raise AssertionError("Read-only status/research must not request approval")


async def test_offline_host_recovers_next_sdk_run_without_mutating_inflight_run(tmp_path, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    controls = {"mcp_node__get_status", "mcp_node__start_live_strategy", "mcp_node__stop_live_strategy"}

    async def pause_research(call):
        assert controls.isdisjoint(tool.name for tool in call.tools)
        entered.set()
        await release.wait()
        return [function_call("list_broker_accounts", {}, call_id="research")]

    def finish_research(call):
        # Second model turn of the SAME run, after successful reconnect.
        assert controls.isdisjoint(tool.name for tool in call.tools)
        return [assistant_message("research usable")]

    def request_status(call):
        assert controls.issubset(tool.name for tool in call.tools), [tool.name for tool in call.tools]
        return [function_call("mcp_node__get_status", {}, call_id="node-status")]

    def verify_status(call):
        outputs = [item for item in call.input if item.get("type") == "function_call_output"]
        assert any("not_connected" in str(item["output"]) for item in outputs)
        return [assistant_message("node reachable; QMT not connected")]

    model = ScriptedModel(
        [
            {"responder": pause_research},
            {"responder": finish_research},
            {"responder": request_status},
            {"responder": verify_status},
        ]
    )
    external_seams(monkeypatch, model)
    async with companion_node(tmp_path / "companion") as profile, availability_gate(profile) as (gate, url):
        config = host_config(tmp_path / "core", profile, url)
        async with open_application_host(
            config, manual_approval_handler=reject_approval, enable_activity=False
        ) as host:
            session_id = host.initial_session_id
            set_session_title(config.sessions_db, session_id, "Recovery acceptance")
            portfolio = await host.portfolios.create(name="Offline research", base_currency="CNY")
            assert portfolio.name == "Offline research"
            initial_requests = list(gate.requests)
            assert initial_requests and set(initial_requests) == {"/mcp"}
            # Observe idle for longer than initial MCP connection timeout.
            await asyncio.sleep(1.1)
            assert gate.requests == initial_requests
            options = RunOptions("none", "manual", "queue")
            inflight = host.runtime.start_run(session_id, "research", options)
            try:
                await asyncio.wait_for(entered.wait(), 5)
                gate.offline = False
                await host.live_coordinator.get_live_status()
                assert gate.requests.count("/mcp") > initial_requests.count("/mcp")
                release.set()
                assert (await asyncio.wait_for(inflight.task, 10)).output == "research usable"
                next_run = host.runtime.start_run(session_id, "node status", options)
                assert (await asyncio.wait_for(next_run.task, 10)).output == "node reachable; QMT not connected"
                model.assert_complete()
            finally:
                release.set()


async def test_rest_stage_survives_mcp_reconnect_failure_in_real_host(tmp_path, monkeypatch):
    external_seams(monkeypatch, ScriptedModel())
    async with companion_node(tmp_path / "companion") as profile, availability_gate(profile) as (gate, url):
        gate.offline = False
        gate.mcp_offline = True
        config = host_config(tmp_path / "core", profile, url)
        config.workspace_dir.mkdir(parents=True, exist_ok=True)
        (config.workspace_dir / "strategy.py").write_text("def init(context):\n    pass\n")
        now = datetime.now(UTC)
        create_broker(config.portfolio_db, Broker("broker", "qmt", "Broker", now, now))
        create_broker_account(
            config.portfolio_db, BrokerAccount("account", "broker", "external", "Account", "stock", now, now)
        )
        async with open_application_host(
            config, manual_approval_handler=reject_approval, enable_activity=False, create_initial_session=False
        ) as host:
            portfolio = await host.portfolios.create(
                name="Live", base_currency="CNY", strategy_binding=StrategyBinding("strategy.py")
            )
            startup_attempts = gate.requests.count("/mcp")
            result = await host.live_coordinator.deploy_live_strategy(portfolio.id, "account")
            assert result["status"] == "staged", result
            assert gate.requests.count("/mcp") > startup_attempts
            deployments = await LiveExecutionOperations(config=config).list_deployments(portfolio.id)
            assert len(deployments) == 1
            assert deployments[0].status.value == "ACTIVE"
            async with httpx.AsyncClient(headers=dict(profile.headers), trust_env=False) as http:
                remote = await http.get(profile.rest_base_url + "/node/status")
            remote_deployments = remote.json()["deployments"]
            assert len(remote_deployments) == 1
            assert remote_deployments[0]["status"] == "STAGED"
            assert remote_deployments[0]["deployment_id"] == deployments[0].deployment_id
