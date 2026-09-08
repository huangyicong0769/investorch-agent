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


def trade(trade_id="fill-1", deployment_id="deployment-a"):
    return {
        "schema_version": 1,
        "deployment_id": deployment_id,
        "broker_trade_id": trade_id,
        "instrument": {"code": "600519", "market": "XSHG"},
        "side": "BUY",
        "quantity": "100",
        "price": "10.50",
        "commission": "1",
        "tax": "0",
        "other_fee": "0",
        "effective_at": "2026-09-08T01:23:45+08:00",
    }


def test_durable_fifo_ack_and_idempotent_redelivery(tmp_path):
    service = ExecutionNodeService(default_paths(tmp_path))
    session = service.open_control_session()["session_id"]
    service.stage_deployment("deployment-a", stage_body(), session)
    first = service.enqueue_trade_fact(trade())
    second = service.enqueue_trade_fact(trade("fill-2"))
    assert service.enqueue_trade_fact(trade()) == first
    service = ExecutionNodeService(default_paths(tmp_path))
    session = service.open_control_session()["session_id"]
    assert service.get_next_pending_fact(session) == first
    with pytest.raises(ExecutionError, match="FACT_ACK_CONFLICT"):
        service.ack_fact(second["fact_id"], 13, session)
    with pytest.raises(ExecutionError, match="SEQUENCE_CONFLICT"):
        service.ack_fact(first["fact_id"], 14, session)
    ack = service.ack_fact(first["fact_id"], 13, session)
    assert ack == {"fact_id": first["fact_id"], "status": "ACKED", "committed_ledger_sequence": 13}
    assert service.ack_fact(first["fact_id"], 13, session) == ack
    assert service.get_next_pending_fact(session) == second
    service.ack_fact(second["fact_id"], 14, session)
    assert service.get_next_pending_fact(session) is None
    assert service.get_node_status()["deployments"][0]["acked_core_sequence"] == 14
    assert service.enqueue_trade_fact(trade()) == first


def test_ack_failure_rolls_back_fact_and_cursor(tmp_path):
    import sqlite3

    service = ExecutionNodeService(default_paths(tmp_path))
    session = service.open_control_session()["session_id"]
    service.stage_deployment("deployment-a", stage_body(), session)
    fact = service.enqueue_trade_fact(trade())
    with sqlite3.connect(tmp_path / "runtime.db") as db:
        db.execute(
            "CREATE TRIGGER reject_cursor BEFORE UPDATE OF acked_core_sequence ON remote_deployments BEGIN SELECT RAISE(ABORT,'disk fault'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        service.ack_fact(fact["fact_id"], 13, session)
    assert service.get_next_pending_fact(session) == fact
    assert service.get_node_status()["deployments"][0]["acked_core_sequence"] == 12


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(quantity="NaN"),
        lambda p: p.update(price=1),
        lambda p: p.update(quantity="0"),
        lambda p: p.update(commission="-1"),
        lambda p: p.update(schema_version=True),
        lambda p: p.update(broker_trade_id=" fill "),
        lambda p: p.update(effective_at="2026-09-08"),
        lambda p: p.update(extra=1),
        lambda p: p["instrument"].update(extra=1),
    ],
)
def test_trade_contract_rejects_invalid_payload_before_persistence(tmp_path, change):
    service = ExecutionNodeService(default_paths(tmp_path))
    payload = trade()
    change(payload)
    with pytest.raises(ExecutionError):
        service.enqueue_trade_fact(payload)


def test_stale_authority_cannot_stage_pull_or_ack(tmp_path):
    service = ExecutionNodeService(default_paths(tmp_path))
    stale = service.open_control_session()["session_id"]
    current = service.open_control_session()["session_id"]
    for operation in (
        lambda: service.stage_deployment("deployment-a", stage_body(), stale),
        lambda: service.get_next_pending_fact(stale),
        lambda: service.ack_fact("unknown", 1, stale),
    ):
        with pytest.raises(ExecutionError, match="STALE_CONTROL_SESSION"):
            operation()
    assert service.get_next_pending_fact(current) is None


def test_reconciliation_required_after_open_expiry_and_pending_delivery(tmp_path):
    service = ExecutionNodeService(default_paths(tmp_path))
    session = service.open_control_session()["session_id"]
    service.stage_deployment("deployment-a", stage_body(), session)
    with pytest.raises(ExecutionError, match="PORTFOLIO_NOT_SYNCED"):
        service.start_live_strategy("portfolio-a")
    service.renew_control_session(session, [{"deployment_id": "deployment-a", "acked_core_sequence": 12}])
    with pytest.raises(ExecutionError, match="BACKEND_NOT_READY"):
        service.start_live_strategy("portfolio-a")
    assert service.get_portfolio_runtime_status("portfolio-a")["status"] == "STAGED"
    session = service.open_control_session()["session_id"]
    assert service.get_portfolio_runtime_status("portfolio-a")["portfolio_sync"] == "UNKNOWN"
    fact = service.enqueue_trade_fact(trade())
    with pytest.raises(ExecutionError, match="SEQUENCE_CONFLICT"):
        service.renew_control_session(session, [{"deployment_id": "deployment-a", "acked_core_sequence": 12}])
    service.ack_fact(fact["fact_id"], 13, session)
    service.renew_control_session(session, [{"deployment_id": "deployment-a", "acked_core_sequence": 13}])
    assert service.get_portfolio_runtime_status("portfolio-a")["portfolio_sync"] == "SYNCED"
    assert service.stop_live_strategy("portfolio-a")["status"] == "STOPPED"
    assert service.stop_live_strategy("portfolio-a")["status"] == "STOPPED"


def test_same_broker_identity_cannot_change_payload(tmp_path):
    service = ExecutionNodeService(default_paths(tmp_path))
    session = service.open_control_session()["session_id"]
    service.stage_deployment("deployment-a", stage_body(), session)
    first = service.enqueue_trade_fact(trade())
    changed = trade()
    changed["price"] = "11"
    with pytest.raises(ExecutionError, match="FACT_ACK_CONFLICT"):
        service.enqueue_trade_fact(changed)
    assert service.get_next_pending_fact(session) == first


def test_terminal_failure_is_preserved_and_running_stop_cannot_fake_success(tmp_path):
    import sqlite3

    service = ExecutionNodeService(default_paths(tmp_path))
    session = service.open_control_session()["session_id"]
    service.stage_deployment("deployment-a", stage_body(), session)
    with sqlite3.connect(tmp_path / "runtime.db") as db:
        db.execute("UPDATE remote_deployments SET status='RUNNING'")
    with pytest.raises(ExecutionError, match="BACKEND_NOT_READY"):
        service.stop_live_strategy("portfolio-a")
    assert service.get_portfolio_runtime_status("portfolio-a")["status"] == "RUNNING"
    with sqlite3.connect(tmp_path / "runtime.db") as db:
        db.execute("UPDATE remote_deployments SET status='FAILED', failure_reason='runtime failed'")
    assert service.stop_live_strategy("portfolio-a")["status"] == "FAILED"
