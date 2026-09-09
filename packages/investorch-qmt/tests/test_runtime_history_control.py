from datetime import date

from investorch_qmt.runtime.protocol import WorkerControl


def test_history_updates_are_independent_of_safety_gate_and_pause_history():
    control = WorkerControl(history_through=date(2026, 9, 7))
    assert control.history_through() == date(2026, 9, 7)
    observed = []

    class Pipe:
        def __init__(self):
            self.messages = iter(
                [
                    {"command": "SET_GATE", "enabled": True},
                    {"command": "SET_HISTORY", "fresh_through": "2026-09-08"},
                    {"command": "SET_HISTORY", "fresh_through": None},
                    {"command": "STOP"},
                ]
            )

        def recv(self):
            observed.append((control.history_through(), control.gate(), control.pauses(), control.stopped.is_set()))
            return next(self.messages)

    control.receive(Pipe())
    assert observed[2] == (date(2026, 9, 8), (True, None), (), False)
    assert observed[3] == (None, (True, None), (), False)
    assert control.stopped.is_set()
