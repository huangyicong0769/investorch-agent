from datetime import date, datetime, time, timedelta
from threading import Event as StopEvent
from zoneinfo import ZoneInfo

import pytest
from rqalpha.core.events import EVENT

from investorch_qmt.rqalpha_live.event_source import DailyEventSource
from investorch_qmt.runtime.model import RuntimeFailure

SH = ZoneInfo("Asia/Shanghai")


class Clock:
    def __init__(self, now):
        self.current = now
        self.on_wait = None

    def now(self):
        return self.current

    def wait(self, seconds, stopped):
        self.current += timedelta(seconds=seconds)
        if self.on_wait:
            self.on_wait(self)


class Control:
    def __init__(self):
        self.stopped = StopEvent()
        self.enabled = True

    def gate(self):
        return self.enabled, None if self.enabled else "lease lost"


def source(now):
    clock, control = Clock(now), Control()
    seen = []
    source = DailyEventSource(
        [date(2026, 9, 7), date(2026, 9, 8)],
        lambda: ((time(9, 30), time(11, 30)), (time(13), time(15))),
        clock,
        control,
        lambda *args, **kwargs: seen.append(args),
        lambda day: seen.append(("fresh", day)),
        lambda day, final: seen.append(("bar", day, final)),
        lambda: None,
    )
    return source, clock, control, seen


def test_daily_events_execute_at_1457_before_finalization_and_settlement():
    event_source, clock, control, seen = source(datetime(2026, 9, 7, 9, 29, tzinfo=SH))
    assert event_source.first_session() == date(2026, 9, 7)
    stream = event_source.events(date(2026, 9, 7), None, "1d")
    observed = []
    for event in stream:
        observed.append((event.event_type, clock.now().time()))
        if event.event_type is EVENT.SETTLEMENT:
            control.stopped.set()
    assert observed == [
        (EVENT.BEFORE_TRADING, time(9, 30)),
        (EVENT.BAR, time(14, 57)),
        (EVENT.AFTER_TRADING, time(15)),
        (EVENT.SETTLEMENT, time(15)),
    ]
    assert seen == [("fresh", date(2026, 9, 7)), ("bar", date(2026, 9, 7), False), ("bar", date(2026, 9, 7), True)]


def test_mid_session_start_is_retryable():
    event_source, *_ = source(datetime(2026, 9, 7, 9, 30, tzinfo=SH))
    with pytest.raises(RuntimeFailure) as exc:
        event_source.first_session()
    assert exc.value.code == "SESSION_ALREADY_STARTED"
    assert exc.value.retryable


def test_pause_across_unexecuted_boundary_fails_without_replay():
    event_source, clock, control, seen = source(datetime(2026, 9, 7, 9, 29, 59, tzinfo=SH))
    control.enabled = False
    with pytest.raises(RuntimeFailure) as exc:
        next(event_source.events(date(2026, 9, 7), None, "1d"))
    assert exc.value.code == "MISSED_RUNTIME_EVENT"
    assert not any(item[0] == "fresh" for item in seen)
