"""Small control and lifecycle messages; market data never crosses this channel."""

import threading
from datetime import datetime
from zoneinfo import ZoneInfo


class WorkerControl:
    def __init__(self):
        self.stopped = threading.Event()
        self._lock = threading.Lock()
        self._enabled = False
        self._reason = "CONTROL_NOT_CONFIRMED"
        self._pauses = []

    def gate(self):
        with self._lock:
            return self._enabled, self._reason

    def pauses(self):
        with self._lock:
            return tuple(self._pauses)

    def receive(self, pipe):
        try:
            while not self.stopped.is_set():
                message = pipe.recv()
                if message["command"] == "STOP":
                    self.stopped.set()
                elif message["command"] == "SET_GATE":
                    with self._lock:
                        enabled = message["enabled"] is True
                        now = datetime.now(ZoneInfo("Asia/Shanghai"))
                        if not enabled and (not self._pauses or self._pauses[-1][1] is not None):
                            self._pauses.append((now, None))
                        elif enabled and self._pauses and self._pauses[-1][1] is None:
                            self._pauses[-1] = (self._pauses[-1][0], now)
                        self._enabled = enabled
                        self._reason = message.get("reason")
                else:
                    self.stopped.set()
        except (EOFError, OSError, KeyError, TypeError):
            self.stopped.set()
