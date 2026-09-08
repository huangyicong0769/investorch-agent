"""Parent process lifecycle supervision, independent of the durable store."""

import multiprocessing
import threading
import time
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass, field

from investorch_qmt.execution.domain import ExecutionError

from .worker import worker_main


@dataclass
class WorkerHandle:
    process: object
    pipe: object
    startup: Future = field(default_factory=Future)
    shutdown: Future = field(default_factory=Future)
    phase: str = "STARTING"
    market_data: str = "CONNECTING"
    reason: str | None = None
    retryable: bool = False
    stop_requested: threading.Event = field(default_factory=threading.Event)
    monitor: threading.Thread | None = None
    gates: deque = field(default_factory=deque)


class RuntimeSupervisor:
    def __init__(self, on_event, gate, *, worker_target=worker_main, startup_timeout=30.0, shutdown_timeout=5.0):
        self._context = multiprocessing.get_context("spawn")
        self._target = worker_target
        self._on_event = on_event
        self._gate = gate
        self._startup_timeout = startup_timeout
        self._shutdown_timeout = shutdown_timeout
        self._lock = threading.RLock()
        self._workers = {}
        self._closed = False

    def snapshot(self, identity):
        with self._lock:
            handle = self._workers.get(identity)
            if handle is None:
                return None
            return {"phase": handle.phase, "market_data": handle.market_data, "reason": handle.reason}

    def begin_start(self, spec):
        with self._lock:
            if self._closed:
                raise ExecutionError("RUNTIME_STOPPING", "Execution service is shutting down.")
            existing = self._workers.get(spec.deployment_id)
            if existing:
                if existing.phase == "STOPPING" or existing.stop_requested.is_set():
                    raise ExecutionError("RUNTIME_STOPPING", "Worker is stopping.")
                if existing.phase not in ("FAILED", "STOPPED"):
                    return existing.startup
                if existing.phase == "STOPPED" or not existing.retryable:
                    raise ExecutionError("DEPLOYMENT_CONFLICT", "Terminal workers require a new deployment.")
            parent, child = self._context.Pipe(duplex=True)
            process = self._context.Process(
                target=self._target, args=(spec, child), name=f"strategy-{spec.deployment_id}"
            )
            handle = WorkerHandle(process, parent)
            self._workers[spec.deployment_id] = handle
            try:
                process.start()
            except BaseException:
                parent.close()
                child.close()
                del self._workers[spec.deployment_id]
                raise
            child.close()
            handle.monitor = threading.Thread(
                target=self._watch,
                args=(spec.deployment_id, handle),
                daemon=True,
                name=f"supervise-{spec.deployment_id}",
            )
            handle.monitor.start()
            return handle.startup

    def begin_stop(self, identity):
        with self._lock:
            handle = self._workers.get(identity)
            if handle is None:
                result = Future()
                result.set_result({"phase": "STOPPED"})
                return result
            if not handle.shutdown.done():
                handle.phase = "STOPPING"
                handle.stop_requested.set()
            return handle.shutdown

    def disable_gate(self, identity=None, reason="CONTROL_AUTHORITY_UNAVAILABLE"):
        with self._lock:
            for key, handle in self._workers.items():
                if (identity is None or identity == key) and not handle.shutdown.done():
                    handle.gates.append((False, reason))

    def _publish(self, identity, handle, event):
        with self._lock:
            handle.phase = event["phase"]
            handle.reason = event.get("reason")
            handle.retryable = event.get("retryable", False)
            if event.get("market_data"):
                handle.market_data = event["market_data"]
            elif event["phase"] in ("FAILED", "STOPPED"):
                handle.market_data = "DISCONNECTED"
        self._on_event(identity, event)

    def _watch(self, identity, handle):
        deadline = time.monotonic() + self._startup_timeout
        stop_deadline = None
        last_gate = None
        terminal = None
        ready = False
        try:
            while terminal is None:
                if handle.stop_requested.is_set() and stop_deadline is None:
                    stop_deadline = time.monotonic() + self._shutdown_timeout
                    handle.pipe.send({"command": "SET_GATE", "enabled": False, "reason": "STOP_REQUESTED"})
                    handle.pipe.send({"command": "STOP"})
                if stop_deadline is None:
                    with self._lock:
                        queued = list(handle.gates)
                        handle.gates.clear()
                    for gate in queued:
                        handle.pipe.send({"command": "SET_GATE", "enabled": gate[0], "reason": gate[1]})
                        last_gate = gate
                    gate = self._gate(identity)
                    if gate != last_gate:
                        handle.pipe.send({"command": "SET_GATE", "enabled": gate[0], "reason": gate[1]})
                        last_gate = gate
                if handle.pipe.poll(0.05):
                    event = handle.pipe.recv()
                    phase = event.get("phase")
                    if phase not in ("STARTING", "READY", "RUNNING", "PAUSED", "FAILED", "STOPPED"):
                        raise ValueError("Invalid worker lifecycle message.")
                    if phase in ("FAILED", "STOPPED"):
                        terminal = event
                    elif stop_deadline is None:
                        if phase in ("RUNNING", "PAUSED") and not ready:
                            raise ValueError("Worker executed before READY.")
                        self._publish(identity, handle, event)
                        if phase == "READY":
                            ready = True
                            if not handle.startup.done():
                                handle.startup.set_result(event)
                if stop_deadline is not None and time.monotonic() >= stop_deadline:
                    terminal = {"phase": "STOPPED", "reason": "STOP_TIMEOUT"}
                elif stop_deadline is None and not ready and time.monotonic() >= deadline:
                    terminal = {"phase": "FAILED", "reason": "START_TIMEOUT", "retryable": True}
                elif not handle.process.is_alive() and terminal is None and not handle.pipe.poll():
                    terminal = {"phase": "FAILED", "reason": "WORKER_FAILED", "retryable": False}
        except Exception as exc:
            terminal = {"phase": "FAILED", "reason": "WORKER_FAILED", "message": str(exc), "retryable": False}
        finally:
            if handle.stop_requested.is_set():
                terminal = {"phase": "STOPPED"}
            if ready and terminal.get("phase") == "FAILED":
                terminal["retryable"] = False
            handle.process.join(timeout=0.5)
            if handle.process.is_alive():
                handle.process.terminate()
                handle.process.join(timeout=self._shutdown_timeout)
            if handle.process.is_alive():
                handle.process.kill()
                handle.process.join()
            handle.pipe.close()
            handle.process.close()
            try:
                self._publish(identity, handle, terminal)
            except Exception as exc:
                if not handle.startup.done():
                    handle.startup.set_exception(exc)
                handle.shutdown.set_exception(exc)
            else:
                if not handle.startup.done():
                    handle.startup.set_result(terminal)
                handle.shutdown.set_result(terminal)

    def close(self):
        with self._lock:
            self._closed = True
            identities = list(self._workers)
        for identity in identities:
            self.begin_stop(identity)
        for identity in identities:
            handle = self._workers[identity]
            if handle.monitor:
                handle.monitor.join(timeout=self._shutdown_timeout + 2)
