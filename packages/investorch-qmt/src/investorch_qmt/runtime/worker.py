"""Child entry point. Artifacts are the only durable input to a worker."""

import base64
import json
from pathlib import Path

from investorch_qmt.execution.contracts import parse_stage

from .model import RuntimeArtifacts, RuntimeFailure, WorkerLaunchSpec


def load_artifacts(spec: WorkerLaunchSpec) -> RuntimeArtifacts:
    directory = Path(spec.deployment_dir)
    try:
        source = (directory / "strategy.py").read_bytes()
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        bootstrap = json.loads((directory / "bootstrap.json").read_text(encoding="utf-8"))
        _, _, _, snapshot = parse_stage(
            spec.deployment_id,
            {"manifest": manifest, "bootstrap": bootstrap, "strategy_source_base64": base64.b64encode(source).decode()},
        )
        if (
            snapshot.portfolio_id != spec.portfolio_id
            or snapshot.broker_account_id != spec.broker_account_id
            or manifest["strategy_sha256"] != spec.expected_strategy_sha256
        ):
            raise ValueError("Launch identity differs from staged artifacts.")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise RuntimeFailure("ARTIFACT_INVALID", str(exc)) from exc
    return RuntimeArtifacts(source, manifest, bootstrap, snapshot, directory)
