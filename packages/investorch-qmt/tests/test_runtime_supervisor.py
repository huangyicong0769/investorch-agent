import os
import time
from pathlib import Path

import pytest

from investorch_qmt.runtime.model import WorkerLaunchSpec
from investorch_qmt.runtime.supervisor import RuntimeSupervisor


def controlled_child(spec, pipe):
    mode = Path(spec.deployment_dir).name
    pipe.send({"phase": "STARTING"})
    if mode == "crash":
        os._exit(7)
    if mode != "timeout":
        pipe.send({"phase": "READY", "market_data": "CONNECTED"})
    while True:
        command = pipe.recv()
        if command["command"] == "STOP":
            pipe.send({"phase": "STOPPED"})
            return
        if mode != "timeout":
            pipe.send({"phase": "RUNNING" if command["enabled"] else "PAUSED", "market_data": "CONNECTED"})


def spec(mode="ready"):
    return WorkerLaunchSpec("deployment-a", "portfolio-a", "account-a", mode, "0" * 64)


def test_spawn_waits_for_ready_and_concurrent_start_shares_child():
    events = []
    supervisor = RuntimeSupervisor(
        lambda *args: events.append(args), lambda _: (True, None), worker_target=controlled_child
    )
    try:
        first = supervisor.begin_start(spec())
        second = supervisor.begin_start(spec())
        assert first is second
        assert first.result(timeout=5)["phase"] == "READY"
        assert supervisor.snapshot("deployment-a")["market_data"] == "CONNECTED"
        stopped = supervisor.begin_stop("deployment-a")
        assert stopped is supervisor.begin_stop("deployment-a")
        assert stopped.result(timeout=5)["phase"] == "STOPPED"
        assert [event[1]["phase"] for event in events].count("READY") == 1
    finally:
        supervisor.close()


@pytest.mark.parametrize("mode,code,retryable", [("timeout", "START_TIMEOUT", True), ("crash", "WORKER_FAILED", False)])
def test_start_failure_cleans_child_and_reports_classification(mode, code, retryable):
    supervisor = RuntimeSupervisor(
        lambda *_: None, lambda _: (True, None), worker_target=controlled_child, startup_timeout=0.5
    )
    try:
        result = supervisor.begin_start(spec(mode)).result(timeout=5)
        assert result["reason"] == code
        assert result["retryable"] is retryable
        assert supervisor.snapshot("deployment-a")["phase"] == "FAILED"
    finally:
        supervisor.close()


def test_stop_during_startup_cancels_pending_start():
    supervisor = RuntimeSupervisor(lambda *_: None, lambda _: (True, None), worker_target=controlled_child)
    try:
        starting = supervisor.begin_start(spec("timeout"))
        stopping = supervisor.begin_stop("deployment-a")
        assert stopping.result(timeout=5)["phase"] == "STOPPED"
        assert starting.result(timeout=5)["phase"] == "STOPPED"
    finally:
        supervisor.close()


def test_safety_gate_is_delivered_without_status_polling():
    gate = [True]
    phases = []
    supervisor = RuntimeSupervisor(
        lambda _, event: phases.append(event["phase"]), lambda _: (gate[0], "lease"), worker_target=controlled_child
    )
    try:
        supervisor.begin_start(spec()).result(timeout=5)
        gate[0] = False
        deadline = time.monotonic() + 5
        while "PAUSED" not in phases and time.monotonic() < deadline:
            time.sleep(0.01)
        assert "PAUSED" in phases
        gate[0] = True
        deadline = time.monotonic() + 5
        while phases[-1] != "RUNNING" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert phases[-1] == "RUNNING"
    finally:
        supervisor.close()
