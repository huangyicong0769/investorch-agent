import json
from dataclasses import replace

import pytest
from test_execution_service import stage_body

from investorch_qmt.config import default_paths
from investorch_qmt.execution.service import ExecutionNodeService
from investorch_qmt.runtime.model import RuntimeFailure, WorkerLaunchSpec
from investorch_qmt.runtime.worker import load_artifacts


def staged_spec(tmp_path):
    service = ExecutionNodeService(default_paths(tmp_path))
    session = service.open_control_session()["session_id"]
    body = stage_body(source=b"def init(context):\n    pass\n")
    service.stage_deployment("deployment-a", body, session)
    return WorkerLaunchSpec(
        "deployment-a",
        "portfolio-a",
        "account-a",
        str(tmp_path / "deployments/deployment-a"),
        body["manifest"]["strategy_sha256"],
    )


def test_worker_revalidates_frozen_artifacts_without_database(tmp_path):
    spec = staged_spec(tmp_path)
    (tmp_path / "runtime.db").unlink()
    artifacts = load_artifacts(spec)
    assert artifacts.manifest["strategy_parameters"] == {"window": 5}
    assert artifacts.snapshot.cash == "1000" or str(artifacts.snapshot.cash) == "1000"
    assert artifacts.source == b"def init(context):\n    pass\n"
    assert not (tmp_path / "runtime.db").exists()


@pytest.mark.parametrize("target", ["strategy.py", "manifest.json", "bootstrap.json", "identity"])
def test_worker_rejects_tampered_staged_artifacts(tmp_path, target):
    spec = staged_spec(tmp_path)
    directory = tmp_path / "deployments/deployment-a"
    if target == "strategy.py":
        (directory / target).write_text("print('changed')")
    elif target == "identity":
        spec = replace(spec, broker_account_id="other-account")
    else:
        payload = json.loads((directory / target).read_text())
        payload["portfolio_id"] = "other-portfolio"
        (directory / target).write_text(json.dumps(payload))
    with pytest.raises(RuntimeFailure, match="ARTIFACT_INVALID"):
        load_artifacts(spec)
