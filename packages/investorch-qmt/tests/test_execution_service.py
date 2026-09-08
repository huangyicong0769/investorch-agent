from datetime import UTC, datetime, timedelta

import pytest

from investorch_qmt.config import default_paths
from investorch_qmt.execution.domain import ExecutionError
from investorch_qmt.execution.service import ExecutionNodeService


def test_control_authority_is_fenced_expiring_and_process_local(tmp_path):
    now = [datetime(2026, 9, 8, tzinfo=UTC)]
    service = ExecutionNodeService(default_paths(tmp_path), clock=lambda: now[0])
    first = service.open_control_session()["session_id"]
    second = service.open_control_session()["session_id"]
    with pytest.raises(ExecutionError, match="STALE_CONTROL_SESSION"):
        service.renew_control_session(first)
    service.close_control_session(first)
    assert service.renew_control_session(second)["lease_timeout_seconds"] == 10
    now[0] += timedelta(seconds=11)
    assert service.get_node_status()["control"]["status"] == "UNAVAILABLE"
    with pytest.raises(ExecutionError, match="STALE_CONTROL_SESSION"):
        service.renew_control_session(second)
    reopened = ExecutionNodeService(default_paths(tmp_path))
    with pytest.raises(ExecutionError, match="STALE_CONTROL_SESSION"):
        reopened.renew_control_session(second)


def stage_body(deployment_id="deployment-a", portfolio_id="portfolio-a", source=b"# exact\r\n"):
    import base64
    import hashlib

    return {
        "manifest": {
            "deployment_id": deployment_id,
            "portfolio_id": portfolio_id,
            "broker_account_id": "account-a",
            "strategy_source_path": "strategies/a.py",
            "strategy_sha256": hashlib.sha256(source).hexdigest(),
            "strategy_parameters": {"window": 5},
            "rqalpha_version": "6.3.0",
            "created_at": "2026-09-08T00:00:00+00:00",
        },
        "strategy_source_base64": base64.b64encode(source).decode(),
        "bootstrap": {
            "schema_version": 1,
            "deployment_id": deployment_id,
            "portfolio_id": portfolio_id,
            "broker_account_id": "account-a",
            "ledger_sequence": 12,
            "generated_at": "2026-09-08T00:00:00+00:00",
            "base_currency": "CNY",
            "cash": "1000",
            "positions": [],
        },
    }


def test_stage_exact_bytes_idempotency_and_conflicts(tmp_path):
    service = ExecutionNodeService(default_paths(tmp_path))
    session = service.open_control_session()["session_id"]
    body = stage_body()
    result = service.stage_deployment("deployment-a", body, session)
    assert result["status"] == "STAGED"
    assert result["acked_core_sequence"] == 12
    assert (tmp_path / "deployments/deployment-a/strategy.py").read_bytes() == b"# exact\r\n"
    assert service.stage_deployment("deployment-a", body, session) == result
    with pytest.raises(ExecutionError, match="DEPLOYMENT_CONFLICT"):
        service.stage_deployment("deployment-a", stage_body(source=b"changed"), session)
    with pytest.raises(ExecutionError, match="PORTFOLIO_DEPLOYMENT_CONFLICT"):
        service.stage_deployment("deployment-b", stage_body("deployment-b"), session)
    assert [path.name for path in (tmp_path / "deployments").iterdir()] == ["deployment-a"]


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda b: b["manifest"].update(strategy_sha256="0" * 64), "HASH_MISMATCH"),
        (lambda b: b["bootstrap"].update(schema_version=True), "INVALID_BOOTSTRAP"),
        (lambda b: b["manifest"].update(portfolio_id="wrong"), "INVALID_REQUEST"),
        (lambda b: b.update(strategy_source_base64="?"), "INVALID_REQUEST"),
        (lambda b: b["manifest"].update(extra=1), "INVALID_REQUEST"),
        (lambda b: b["manifest"].update(strategy_parameters={"bad": float("nan")}), "INVALID_REQUEST"),
    ],
)
def test_invalid_stage_leaves_no_artifact_or_deployment(tmp_path, change, code):
    service = ExecutionNodeService(default_paths(tmp_path))
    session = service.open_control_session()["session_id"]
    body = stage_body()
    change(body)
    with pytest.raises(ExecutionError, match=code):
        service.stage_deployment("deployment-a", body, session)
    assert service.get_node_status()["deployments"] == []
    assert not (tmp_path / "deployments").exists()


def test_failed_database_insert_removes_installed_artifact(tmp_path):
    import sqlite3

    service = ExecutionNodeService(default_paths(tmp_path))
    with sqlite3.connect(tmp_path / "runtime.db") as db:
        db.execute(
            "CREATE TRIGGER reject_stage BEFORE INSERT ON remote_deployments BEGIN SELECT RAISE(ABORT,'disk fault'); END"
        )
    session = service.open_control_session()["session_id"]
    with pytest.raises(sqlite3.IntegrityError):
        service.stage_deployment("deployment-a", stage_body(), session)
    assert service.get_node_status()["deployments"] == []
    assert list((tmp_path / "deployments").iterdir()) == []
