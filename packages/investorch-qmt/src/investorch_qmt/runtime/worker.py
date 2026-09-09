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


def worker_main(spec: WorkerLaunchSpec, pipe):
    import threading

    from .protocol import WorkerControl

    control = WorkerControl(history_through=spec.history_through)
    reader = threading.Thread(target=control.receive, args=(pipe,), daemon=True, name="runtime-control")
    reader.start()

    def report(phase, reason=None, market_data=None):
        pipe.send({"phase": phase, "reason": reason, "market_data": market_data})

    try:
        report("STARTING")
        artifacts = load_artifacts(spec)
        from investorch_qmt.rqalpha_live.runtime import run_live

        run_live(artifacts, control, report)
        report("STOPPED")
    except RuntimeFailure as exc:
        pipe.send({"phase": "FAILED", "reason": exc.code, "message": exc.message, "retryable": exc.retryable})
    except BaseException as exc:
        pipe.send({"phase": "FAILED", "reason": "WORKER_FAILED", "message": str(exc), "retryable": False})
    finally:
        control.stopped.set()
        pipe.close()
