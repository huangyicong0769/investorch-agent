import time

import pytest
from test_execution_service import stage_body
from test_runtime_supervisor import controlled_child

from investorch_qmt.config import default_paths
from investorch_qmt.execution.domain import ExecutionError
from investorch_qmt.execution.service import ExecutionNodeService
from investorch_qmt.runtime.supervisor import RuntimeSupervisor


def service_with_worker(tmp_path):
    service = ExecutionNodeService(
        default_paths(tmp_path),
        runtime_factory=lambda notify, gate, **kwargs: RuntimeSupervisor(
            notify, gate, worker_target=controlled_child, **kwargs
        ),
    )
    session = service.open_control_session()["session_id"]
    service.stage_deployment("deployment-a", stage_body(), session)
    service.renew_control_session(session, [{"deployment_id": "deployment-a", "acked_core_sequence": 12}])
    return service, session


def test_real_spawn_ready_updates_durable_status_and_stop(tmp_path):
    service, session = service_with_worker(tmp_path)
    try:
        result = service.start_live_strategy("portfolio-a", session)
        assert result["status"] == "RUNNING"
        assert result["market_data"] == "CONNECTED"
        assert result["trading"]["reason"] == "TRADING_BACKEND_NOT_READY"
        assert service.get_next_pending_fact(session) is None
        assert service.stop_live_strategy("portfolio-a", session)["status"] == "STOPPED"
        with pytest.raises(ExecutionError, match="DEPLOYMENT_CONFLICT"):
            service.start_live_strategy("portfolio-a", session)
    finally:
        service.close()


def test_closed_control_pauses_worker_while_durable_ownership_remains(tmp_path):
    service, session = service_with_worker(tmp_path)
    try:
        service.start_live_strategy("portfolio-a", session)
        service.close_control_session(session)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = service.get_portfolio_runtime_status("portfolio-a")
            if result["worker_phase"] == "PAUSED":
                break
            time.sleep(0.01)
        assert result["status"] == "RUNNING"
        assert result["worker_phase"] == "PAUSED"
        assert result["control_authority"] == "UNAVAILABLE"
        recovered = service.open_control_session()["session_id"]
        service.renew_control_session(recovered, [{"deployment_id": "deployment-a", "acked_core_sequence": 12}])
        assert service.stop_live_strategy("portfolio-a", recovered)["status"] == "STOPPED"
    finally:
        service.close()


def test_companion_restart_marks_ghost_running_failed(tmp_path):
    service, _session = service_with_worker(tmp_path)
    # A prior process died; only its last durable RUNNING record remains.
    with service.storage.transaction() as db:
        db.execute("UPDATE remote_deployments SET status='RUNNING'")
    service.close()
    restarted = ExecutionNodeService(default_paths(tmp_path))
    try:
        result = restarted.get_portfolio_runtime_status("portfolio-a")
        assert result["status"] == "FAILED"
        assert result["failure_reason"] == "RUNTIME_LOST_ON_COMPANION_RESTART"
        assert result["worker_phase"] == "FAILED"
    finally:
        restarted.close()


def test_node_reports_installed_market_distribution_without_claiming_connection(tmp_path):
    from importlib.metadata import PackageNotFoundError, version

    try:
        installed = version("xtquant")
    except PackageNotFoundError:
        installed = None
    service = ExecutionNodeService(default_paths(tmp_path))
    try:
        market = service.get_node_status()["market_data"]
        assert market["status"] == "DISCONNECTED"
        assert market["xtquant_version"] == installed
    finally:
        service.close()


class HistoryState:
    def __init__(self, **kwargs):
        self.started = False
        self.closed = False
        self.state = {
            "status": "READY",
            "provider": "xtdata",
            "native_through": "2026-09-04",
            "fresh_through": "2026-09-07",
            "target_through": "2026-09-07",
        }

    def start(self):
        self.started = True

    def close(self):
        self.closed = True

    def snapshot(self):
        return dict(self.state)


def initial_history_child(spec, pipe):
    from datetime import date

    assert spec.history_through == date(2026, 9, 7)
    controlled_child(spec, pipe)


def test_history_lifecycle_status_and_worker_gate_are_independent(tmp_path):
    history = HistoryState()
    service = ExecutionNodeService(
        default_paths(tmp_path),
        history_manager=history,
        runtime_factory=lambda notify, gate, **kwargs: RuntimeSupervisor(
            notify, gate, worker_target=initial_history_child, **kwargs
        ),
    )
    assert not history.started
    try:
        service.start_maintenance()
        assert history.started
        session = service.open_control_session()["session_id"]
        service.stage_deployment("deployment-a", stage_body(), session)
        service.renew_control_session(session, [{"deployment_id": "deployment-a", "acked_core_sequence": 12}])
        service.start_live_strategy("portfolio-a", session)
        history.state.update(status="NOT_READY", target_through="2026-09-08", reason="HISTORY_SYNC_FAILED")
        status = service.get_node_status()
        assert status["historical_data"] == history.state
        assert status["control"]["status"] == "AVAILABLE"
        assert status["deployments"][0]["status"] == "RUNNING"
        assert status["deployments"][0]["control_authority"] == "AVAILABLE"
        assert status["deployments"][0]["market_data"] == "CONNECTED"
        assert service.stop_live_strategy("portfolio-a", session)["status"] == "STOPPED"
    finally:
        service.close()
    assert history.closed
