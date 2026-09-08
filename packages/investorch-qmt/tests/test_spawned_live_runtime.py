import json
import os
import time
from datetime import UTC, datetime
from datetime import time as daytime
from pathlib import Path
from unittest.mock import patch

import pytest
from test_execution_service import stage_body
from test_rqalpha_live_events import SH, Clock
from test_rqalpha_live_runtime import Market, bundle  # noqa: F401

from investorch_qmt.config import default_paths
from investorch_qmt.execution.domain import ExecutionError
from investorch_qmt.execution.service import ExecutionNodeService
from investorch_qmt.runtime.supervisor import RuntimeSupervisor


class SessionClock(Clock):
    def __init__(self, directory):
        super().__init__(datetime(2026, 9, 8, 9, 29, tzinfo=SH))
        self.directory = directory

    def wait(self, seconds, stopped):
        if not (self.directory / "advance").exists() or (self.directory / "observed.json").exists():
            stopped.wait(0.005)
            return
        for boundary in (daytime(9, 30), daytime(14, 57), daytime(15)):
            next_time = datetime.combine(self.current.date(), boundary, SH)
            if next_time > self.current:
                self.current = next_time
                return
        stopped.wait(0.005)


class ClosingMarket(Market):
    def close(self):
        super().close()
        (self.clock.directory / "market-closed").touch()


def production_worker_with_external_fixtures(spec, pipe):
    from investorch_qmt.rqalpha_live import runtime
    from investorch_qmt.runtime.worker import worker_main

    directory = Path(spec.deployment_dir)
    clock = SessionClock(directory)
    # Only filesystem home, market API and clock differ from the production child.
    with (
        patch.object(Path, "home", return_value=directory.parents[1]),
        patch.object(runtime, "WallClock", return_value=clock),
        patch.object(runtime, "XtDataAdapter", return_value=ClosingMarket(clock)),
    ):
        worker_main(spec, pipe)


def actual_runtime_factory(notify, gate):
    return RuntimeSupervisor(notify, gate, worker_target=production_worker_with_external_fixtures)


def test_service_spawns_production_worker_validates_artifacts_runs_engine_and_stops(bundle, tmp_path):  # noqa: F811
    directory = tmp_path / "deployments" / "deployment-a"
    observed = directory / "observed.json"
    source = b"""
import json
import os
from rqalpha.core.events import EVENT
from rqalpha.environment import Environment

def init(context):
    context.trades = []
    env = Environment.get_instance()
    env.event_bus.add_listener(EVENT.TRADE, lambda event: context.trades.append(event.trade))
    env.event_bus.add_listener(EVENT.POST_SETTLEMENT, lambda event: save(context))
    subscribe('600519.XSHG')

def handle_bar(context, bars):
    context.order = order_shares('600519.XSHG', 100)

def save(context):
    with open(context.investorch_parameters['output'] + '.tmp', 'w') as target:
        json.dump(dict(pid=os.getpid(), status=context.order.status.name,
                       reason=context.order.message, trades=len(context.trades),
                       cash=context.portfolio.cash,
                       quantity=context.portfolio.get_positions()[0].quantity), target)
    os.replace(context.investorch_parameters['output'] + '.tmp', context.investorch_parameters['output'])
"""
    body = stage_body(source=source)
    body["manifest"]["strategy_parameters"] = {"output": str(observed)}
    body["bootstrap"].update(cash="100000", positions=[{"code": "600519", "market": "XSHG", "quantity": "100"}])
    service = ExecutionNodeService(
        default_paths(tmp_path), clock=lambda: datetime(2026, 9, 8, tzinfo=UTC), runtime_factory=actual_runtime_factory
    )
    try:
        session = service.open_control_session()["session_id"]
        service.stage_deployment("deployment-a", body, session)
        service.renew_control_session(session, [{"deployment_id": "deployment-a", "acked_core_sequence": 12}])
        running = service.start_live_strategy("portfolio-a", session)
        assert running["status"] == "RUNNING"
        assert running["market_data"] == "CONNECTED"
        (directory / "advance").touch()
        deadline = time.monotonic() + 10
        while not observed.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        result = json.loads(observed.read_text())
        assert result.pop("pid") != os.getpid()
        assert result == dict(
            status="REJECTED", reason="TRADING_BACKEND_NOT_READY", trades=0, cash=100000, quantity=100
        )
        assert service.get_next_pending_fact(session) is None
        assert service.stop_live_strategy("portfolio-a", session)["status"] == "STOPPED"
        assert (directory / "market-closed").exists()
    finally:
        service.close()


def test_spawned_production_worker_rejects_changed_staged_source_before_ready(tmp_path):
    service = ExecutionNodeService(default_paths(tmp_path), runtime_factory=actual_runtime_factory)
    try:
        session = service.open_control_session()["session_id"]
        service.stage_deployment("deployment-a", stage_body(), session)
        service.renew_control_session(session, [{"deployment_id": "deployment-a", "acked_core_sequence": 12}])
        (tmp_path / "deployments" / "deployment-a" / "strategy.py").write_text("# changed after staging")
        with pytest.raises(ExecutionError, match="ARTIFACT_INVALID"):
            service.start_live_strategy("portfolio-a", session)
        status = service.get_portfolio_runtime_status("portfolio-a")
        assert status["status"] == "FAILED"
        assert status["worker_phase"] == "FAILED"
    finally:
        service.close()
