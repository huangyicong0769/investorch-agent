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
        if command["command"] == "SET_HISTORY":
            continue
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


def crash_after_ready(spec, pipe):
    pipe.send({"phase": "READY", "market_data": "CONNECTED"})
    pipe.recv()
    os._exit(9)


def test_durable_writer_failure_completes_start_and_reaps_child():
    def unavailable_writer(identity, event):
        if event["phase"] == "READY":
            raise OSError("durable writer unavailable")

    supervisor = RuntimeSupervisor(unavailable_writer, lambda _: (True, None), worker_target=controlled_child)
    try:
        result = supervisor.begin_start(spec()).result(timeout=5)
        assert result["phase"] == "FAILED"
        assert result["reason"] == "WORKER_FAILED"
    finally:
        supervisor.close()


def test_start_is_rejected_while_stop_is_pending():
    from investorch_qmt.execution.domain import ExecutionError

    supervisor = RuntimeSupervisor(lambda *_: None, lambda _: (True, None), worker_target=controlled_child)
    try:
        supervisor.begin_start(spec()).result(timeout=5)
        stopping = supervisor.begin_stop("deployment-a")
        with pytest.raises(ExecutionError, match="RUNTIME_STOPPING"):
            supervisor.begin_start(spec())
        assert stopping.result(timeout=5)["phase"] == "STOPPED"
    finally:
        supervisor.close()


def test_unexpected_running_child_exit_is_terminal():
    supervisor = RuntimeSupervisor(lambda *_: None, lambda _: (True, None), worker_target=crash_after_ready)
    try:
        assert supervisor.begin_start(spec()).result(timeout=5)["phase"] == "READY"
        deadline = time.monotonic() + 5
        while supervisor.snapshot("deployment-a")["phase"] != "FAILED" and time.monotonic() < deadline:
            time.sleep(0.01)
        result = supervisor.snapshot("deployment-a")
        assert result["phase"] == "FAILED"
        assert result["reason"] == "WORKER_FAILED"
    finally:
        supervisor.close()


def unresponsive_stop_child(spec, pipe):
    pipe.send({"phase": "READY", "market_data": "CONNECTED"})
    while True:
        time.sleep(1)


def test_forced_stop_remains_distinguishable_from_graceful_acknowledgement():
    supervisor = RuntimeSupervisor(
        lambda *_: None, lambda _: (True, None), worker_target=unresponsive_stop_child, shutdown_timeout=0.1
    )
    try:
        assert supervisor.begin_start(spec()).result(timeout=5)["phase"] == "READY"
        result = supervisor.begin_stop("deployment-a").result(timeout=5)
        assert result["phase"] == "STOPPED"
        assert result["reason"] == "STOP_TIMEOUT"
    finally:
        supervisor.close()


def history_observer_child(spec, pipe):
    pipe.send({"phase": "READY", "history": spec.history_through.isoformat()})
    while True:
        command = pipe.recv()
        if command["command"] == "STOP":
            pipe.send({"phase": "STOPPED"})
            return
        if command["command"] == "SET_HISTORY":
            pipe.send({"phase": "RUNNING", "history": command["fresh_through"]})


def test_spawn_delivers_initial_and_updated_history_without_changing_gate():
    import threading
    from dataclasses import replace
    from datetime import date

    observed = []
    updated = threading.Event()
    history = {"status": "READY", "fresh_through": "2026-09-07"}

    def notify(identity, event):
        observed.append(event)
        if event.get("history") == "2026-09-08":
            updated.set()

    supervisor = RuntimeSupervisor(
        notify,
        lambda _: (True, None),
        history_snapshot=lambda: dict(history),
        worker_target=history_observer_child,
    )
    try:
        ready = supervisor.begin_start(replace(spec(), history_through=date(2026, 9, 7))).result(timeout=5)
        assert ready["history"] == "2026-09-07"
        history.update(status="NOT_READY")
        history.update(status="READY", fresh_through="2026-09-08")
        assert updated.wait(5)
        assert supervisor.snapshot("deployment-a")["phase"] == "RUNNING"
        assert not any(event["phase"] in {"PAUSED", "FAILED"} for event in observed)
    finally:
        supervisor.close()
