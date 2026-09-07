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
