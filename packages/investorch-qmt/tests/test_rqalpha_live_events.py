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

    def history_through(self):
        return None


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


@pytest.mark.parametrize("slow_work", ["freshness", "current_bar"])
def test_on_time_dispatch_allows_slow_io_without_changing_event_time(slow_work):
    event_source, clock, control, _ = source(datetime(2026, 9, 7, 9, 29, tzinfo=SH))

    def freshness(day):
        if slow_work == "freshness":
            clock.current += timedelta(seconds=5)

    def prepare(day, final):
        if not final and slow_work == "current_bar":
            clock.current += timedelta(seconds=5)

    event_source.freshness = freshness
    event_source.prepare_bars = prepare
    observed = []
    for event in event_source.events(date(2026, 9, 7), None, "1d"):
        observed.append((event.event_type, clock.now().time(), event.trading_dt))
        if event.event_type == EVENT.SETTLEMENT:
            control.stopped.set()
    assert observed == [
        (EVENT.BEFORE_TRADING, time(9, 30, 5 if slow_work == "freshness" else 0), datetime(2026, 9, 7)),
        (EVENT.BAR, time(14, 57, 5 if slow_work == "current_bar" else 0), datetime(2026, 9, 7, 15)),
        (EVENT.AFTER_TRADING, time(15), datetime(2026, 9, 7, 15, 30)),
        (EVENT.SETTLEMENT, time(15), datetime(2026, 9, 7, 15, 30)),
    ]


@pytest.mark.parametrize("work", ["freshness", "current_bar"])
@pytest.mark.parametrize("gate_loss", ["disabled", "resumed"])
def test_gate_loss_during_io_prevents_event_publication(work, gate_loss):
    event_source, clock, control, _ = source(datetime(2026, 9, 7, 9, 29, tzinfo=SH))

    def lose_gate(*args):
        paused_at = clock.now() + timedelta(seconds=1)
        clock.current += timedelta(seconds=5)
        if gate_loss == "disabled":
            control.enabled = False
        else:
            control.pauses = lambda: ((paused_at, clock.now()),)

    if work == "freshness":
        event_source.freshness = lose_gate
    else:
        event_source.prepare_bars = lose_gate
    observed = []
    with pytest.raises(RuntimeFailure) as error:
        for event in event_source.events(date(2026, 9, 7), None, "1d"):
            observed.append(event.event_type)
    assert error.value.code == "MISSED_RUNTIME_EVENT"
    assert observed == ([] if work == "freshness" else [EVENT.BEFORE_TRADING])


@pytest.mark.parametrize("work", ["freshness", "current_bar"])
def test_io_crossing_next_required_boundary_cannot_publish_previous_event(work):
    event_source, clock, _control, _ = source(datetime(2026, 9, 7, 9, 29, tzinfo=SH))

    def cross_boundary(*args):
        clock.current = (
            datetime(2026, 9, 7, 14, 57, tzinfo=SH) if work == "freshness" else datetime(2026, 9, 7, 15, tzinfo=SH)
        )

    if work == "freshness":
        event_source.freshness = cross_boundary
    else:
        event_source.prepare_bars = cross_boundary
    observed = []
    with pytest.raises(RuntimeFailure) as error:
        for event in event_source.events(date(2026, 9, 7), None, "1d"):
            observed.append(event.event_type)
    assert error.value.code == "MISSED_RUNTIME_EVENT"
    assert observed == ([] if work == "freshness" else [EVENT.BEFORE_TRADING])


@pytest.mark.parametrize("boundary", ["opening", "execution"])
def test_pause_across_unexecuted_boundary_fails_without_replay(boundary):
    event_source, _clock, control, seen = source(datetime(2026, 9, 7, 9, 29, 59, tzinfo=SH))
    stream = event_source.events(date(2026, 9, 7), None, "1d")
    if boundary == "execution":
        assert next(stream).event_type == EVENT.BEFORE_TRADING
        _clock.current = datetime(2026, 9, 7, 14, 56, 59, tzinfo=SH)
        seen.clear()
    control.enabled = False
    with pytest.raises(RuntimeFailure) as exc:
        next(stream)
    assert exc.value.code == "MISSED_RUNTIME_EVENT"
    assert not any(item[0] in {"fresh", "bar"} for item in seen)


def test_final_daily_bar_waits_after_close_without_repeating_execution_bar():
    from investorch_qmt.market_data.errors import MarketDataError

    event_source, clock, control, _ = source(datetime(2026, 9, 7, 9, 29, tzinfo=SH))
    queries = []

    def prepare(day, final):
        queries.append((final, clock.now().time()))
        if final and sum(finalized for finalized, _ in queries) == 1:
            raise MarketDataError("MARKET_DATA_INCOMPLETE")

    event_source.prepare_bars = prepare
    observed = []
    for event in event_source.events(date(2026, 9, 7), None, "1d"):
        observed.append(event.event_type)
        if event.event_type == EVENT.SETTLEMENT:
            control.stopped.set()
    assert observed == [EVENT.BEFORE_TRADING, EVENT.BAR, EVENT.AFTER_TRADING, EVENT.SETTLEMENT]
    assert queries == [(False, time(14, 57)), (True, time(15)), (True, time(15, 0, 0, 250000))]


def test_final_bar_deadline_fails_without_after_trading_or_settlement():
    from investorch_qmt.market_data.errors import MarketDataError

    event_source, clock, _control, _ = source(datetime(2026, 9, 7, 9, 29, tzinfo=SH))

    def prepare(day, final):
        if final:
            raise MarketDataError("MARKET_DATA_INCOMPLETE")

    event_source.prepare_bars = prepare
    observed = []
    with pytest.raises(RuntimeFailure) as error:
        for event in event_source.events(date(2026, 9, 7), None, "1d"):
            observed.append(event.event_type)
    assert error.value.code == "MARKET_DATA_INCOMPLETE"
    assert observed == [EVENT.BEFORE_TRADING, EVENT.BAR]
    assert clock.now().time() == time(15, 0, 30)


def test_pause_can_resume_before_pending_boundary():
    event_source, clock, control, seen = source(datetime(2026, 9, 7, 9, 29, 59, tzinfo=SH))
    control.enabled = False
    clock.on_wait = lambda _: setattr(control, "enabled", True)
    event = next(event_source.events(date(2026, 9, 7), None, "1d"))
    assert event.event_type == EVENT.BEFORE_TRADING
    assert seen[:2] == [("PAUSED",), ("RUNNING",)]


@pytest.mark.parametrize("pending", ["opening", "execution"])
def test_quick_pause_resume_spanning_boundary_is_not_lost_between_polls(pending):
    event_source, clock, control, _ = source(datetime(2026, 9, 7, 9, 29, 59, tzinfo=SH))
    stream = event_source.events(date(2026, 9, 7), None, "1d")
    if pending == "execution":
        assert next(stream).event_type == EVENT.BEFORE_TRADING
        clock.current = datetime(2026, 9, 7, 14, 56, 59, tzinfo=SH)
    boundary = clock.now() + timedelta(seconds=1)

    def transition(_):
        clock.current = boundary + timedelta(milliseconds=10)
        control.pauses = lambda: ((boundary - timedelta(milliseconds=10), clock.current),)

    clock.on_wait = transition
    with pytest.raises(RuntimeFailure) as error:
        next(stream)
    assert error.value.code == "MISSED_RUNTIME_EVENT"


@pytest.mark.parametrize("code", ["MARKET_DATA_INCOMPLETE", "MARKET_DATA_DISCONNECTED", "MARKET_DATA_NOT_READY"])
def test_slow_current_bar_failure_preserves_data_error_without_publishing_bar(code):
    from investorch_qmt.market_data.errors import MarketDataError

    event_source, clock, _control, _ = source(datetime(2026, 9, 7, 9, 29, tzinfo=SH))

    def prepare(day, final):
        clock.current += timedelta(seconds=5)
        raise MarketDataError(code)

    event_source.prepare_bars = prepare
    stream = event_source.events(date(2026, 9, 7), None, "1d")
    assert next(stream).event_type == EVENT.BEFORE_TRADING
    with pytest.raises(MarketDataError) as error:
        next(stream)
    assert error.value.code == code


@pytest.mark.parametrize("work", ["freshness", "current_bar"])
def test_stop_during_io_prevents_event_publication(work):
    event_source, clock, control, _ = source(datetime(2026, 9, 7, 9, 29, tzinfo=SH))

    def stop(*args):
        clock.current += timedelta(seconds=5)
        control.stopped.set()

    if work == "freshness":
        event_source.freshness = stop
    else:
        event_source.prepare_bars = stop
    observed = [event.event_type for event in event_source.events(date(2026, 9, 7), None, "1d")]
    assert observed == ([] if work == "freshness" else [EVENT.BEFORE_TRADING])


def test_delayed_callback_cannot_replay_a_missed_execution_minute():
    event_source, clock, _control, _ = source(datetime(2026, 9, 7, 9, 29, tzinfo=SH))
    stream = event_source.events(date(2026, 9, 7), None, "1d")
    assert next(stream).event_type == EVENT.BEFORE_TRADING
    clock.current = datetime(2026, 9, 7, 14, 58, tzinfo=SH)
    with pytest.raises(RuntimeFailure) as error:
        next(stream)
    assert error.value.code == "MISSED_RUNTIME_EVENT"


def test_pause_during_final_query_prevents_after_trading_and_settlement():
    event_source, _clock, control, _ = source(datetime(2026, 9, 7, 9, 29, tzinfo=SH))

    def prepare(day, final):
        if final:
            control.enabled = False

    event_source.prepare_bars = prepare
    observed = []
    with pytest.raises(RuntimeFailure) as error:
        for event in event_source.events(date(2026, 9, 7), None, "1d"):
            observed.append(event.event_type)
    assert error.value.code == "MISSED_RUNTIME_EVENT"
    assert observed == [EVENT.BEFORE_TRADING, EVENT.BAR]


def test_final_query_returning_valid_data_after_deadline_fails_closed():
    event_source, clock, _control, _ = source(datetime(2026, 9, 7, 9, 29, tzinfo=SH))

    def prepare(day, final):
        if final:
            clock.current += timedelta(seconds=31)

    event_source.prepare_bars = prepare
    observed = []
    with pytest.raises(RuntimeFailure) as error:
        for event in event_source.events(date(2026, 9, 7), None, "1d"):
            observed.append(event.event_type)
    assert error.value.code == "MARKET_DATA_INCOMPLETE"
    assert observed == [EVENT.BEFORE_TRADING, EVENT.BAR]
