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
        runtime_factory=lambda notify, gate: RuntimeSupervisor(notify, gate, worker_target=controlled_child),
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
