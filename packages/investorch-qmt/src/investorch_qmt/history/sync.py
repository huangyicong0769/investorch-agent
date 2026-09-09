"""Node-wide scheduler; maintenance readiness never controls Strategy safety gates."""

import contextlib
import multiprocessing
import threading
from datetime import date, datetime, timedelta

from .model import SHANGHAI, SyncSpec, latest_completed
from .worker import worker_main

_RETRY_SECONDS = (60, 300, 900, 1800)


class HistorySyncManager:
    def __init__(self, *, clock=None, context=None, worker_target=worker_main, shutdown_timeout=5.0):
        self._clock = clock or (lambda: datetime.now(SHANGHAI))
        self._context = context or multiprocessing.get_context("spawn")
        self._worker_target = worker_target
        self._shutdown_timeout = shutdown_timeout
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._started = False
        self._process = self._pipe = None
        self._active_target = self._launched_at = None
        self._calendar = ()
        self._native = self._fresh = self._target = None
        self._status = "NOT_READY"
        self._progress = self._reason = None
        self._retry_at = None
        self._failures = 0

    def snapshot(self):
        with self._lock:
            result = {
                "status": self._status,
                "provider": "xtdata",
                "native_through": self._native.isoformat() if self._native else None,
                "fresh_through": self._fresh.isoformat() if self._fresh else None,
                "target_through": self._target.isoformat() if self._target else None,
            }
            if self._progress is not None and self._status == "SYNCING":
                result["progress"] = dict(self._progress)
            if self._reason:
                result["reason"] = self._reason
            return result

    def start(self, *, background=True):
        with self._lock:
            if self._started or self._stop.is_set():
                return
            self._started = True
            self.poll()
            if background:
                self._thread = threading.Thread(target=self._run, daemon=True, name="history-sync")
                self._thread.start()

    def _run(self):
        while not self._stop.wait(0.1):
            self.poll()

    def poll(self):
        """Run one nonblocking scheduler iteration; clock/process seams support deterministic tests."""
        with self._lock:
            if not self._started or self._stop.is_set():
                return
            now = self._clock()
            if self._process is not None:
                try:
                    while self._pipe.poll():
                        terminal = self._receive(self._pipe.recv())
                        if terminal:
                            self._reap()
                            break
                    if self._process is not None and not self._process.is_alive() and not self._pipe.poll():
                        self._failed(now, "HISTORY_SYNC_FAILED")
                        self._reap()
                except (EOFError, OSError, ValueError, KeyError, TypeError):
                    self._failed(now, "HISTORY_SYNC_FAILED")
                    self._reap()
            if self._calendar:
                self._target = latest_completed(self._calendar, now)
            if self._process is not None:
                return
            if self._retry_at is not None and now < self._retry_at:
                return
            covered = max(self._native or date.min, self._fresh or date.min)
            if self._target is not None and covered >= self._target:
                self._status = "READY"
                return
            self._launch(now)

    def _launch(self, now):
        parent = child = process = None
        try:
            parent, child = self._context.Pipe(duplex=True)
            process = self._context.Process(
                target=self._worker_target,
                args=(SyncSpec(now, self._native, self._fresh), child),
                name="history-maintenance",
            )
            process.start()
        except Exception:
            for resource in (parent, child, process):
                if resource is not None:
                    resource.close()
            self._failed(now, "HISTORY_SYNC_FAILED")
            return
        child.close()
        self._process, self._pipe = process, parent
        self._active_target, self._launched_at = None, now
        self._status, self._progress, self._reason = "SYNCING", None, None

    def _receive(self, message):
        phase = message["phase"]
        if phase == "REFERENCE":
            native = date.fromisoformat(message["native_through"])
            calendar = tuple(date.fromisoformat(day) for day in message["calendar"])
            if not calendar or tuple(sorted(set(calendar))) != calendar:
                raise ValueError("Invalid native calendar.")
            if self._native is not None and native != self._native:
                raise ValueError("Maintenance changed the node coverage baseline.")
            self._native, self._calendar = native, calendar
            self._active_target = date.fromisoformat(message["target_through"])
            if self._active_target != latest_completed(calendar, self._launched_at):
                raise ValueError("Maintenance target differs from its launch session.")
            self._target = self._active_target
        elif phase == "PROGRESS":
            finished, total = message["finished"], message["total"]
            if type(finished) is not int or type(total) is not int or not 0 <= finished <= total:
                raise ValueError("Invalid maintenance progress.")
            self._progress = {"finished": finished, "total": total}
        elif phase == "SUCCEEDED":
            through = date.fromisoformat(message["target_through"])
            if self._active_target is None or through != self._active_target:
                raise ValueError("Unvalidated maintenance target.")
            if through > self._native:
                self._fresh = max(self._fresh or date.min, through)
            self._status, self._progress, self._reason = "READY", None, None
            self._failures, self._retry_at = 0, None
            return True
        elif phase == "FAILED":
            self._failed(self._clock(), message.get("reason", "HISTORY_SYNC_FAILED"))
            return True
        elif phase not in {"STARTING", "VALIDATING"}:
            raise ValueError("Invalid maintenance phase.")
        return False

    def _failed(self, now, reason):
        self._status, self._reason, self._progress = "NOT_READY", reason, None
        delay = _RETRY_SECONDS[min(self._failures, len(_RETRY_SECONDS) - 1)]
        self._failures += 1
        self._retry_at = now + timedelta(seconds=delay)

    def _reap(self):
        if self._process is None:
            return
        self._process.join(timeout=0.5)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=self._shutdown_timeout)
        if self._process.is_alive():
            self._process.kill()
            self._process.join()
        self._pipe.close()
        self._process.close()
        self._process = self._pipe = None

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._shutdown_timeout + 2)
        with self._lock:
            if self._pipe is not None:
                with contextlib.suppress(EOFError, OSError):
                    self._pipe.send({"command": "STOP"})
                self._process.join(timeout=self._shutdown_timeout)
                self._reap()
