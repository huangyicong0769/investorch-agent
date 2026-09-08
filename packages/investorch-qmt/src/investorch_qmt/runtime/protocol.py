"""Small control and lifecycle messages; market data never crosses this channel."""

import threading


class WorkerControl:
    def __init__(self):
        self.stopped = threading.Event()
        self._lock = threading.Lock()
        self._enabled = False
        self._reason = "CONTROL_NOT_CONFIRMED"

    def gate(self):
        with self._lock:
            return self._enabled, self._reason

    def receive(self, pipe):
        try:
            while not self.stopped.is_set():
                message = pipe.recv()
                if message["command"] == "STOP":
                    self.stopped.set()
                elif message["command"] == "SET_GATE":
                    with self._lock:
                        self._enabled = message["enabled"] is True
                        self._reason = message.get("reason")
                else:
                    self.stopped.set()
        except (EOFError, OSError, KeyError, TypeError):
            self.stopped.set()
