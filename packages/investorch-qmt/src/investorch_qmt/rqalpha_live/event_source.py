"""Wall-clock daily lifecycle; missed safety boundaries are never replayed."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from rqalpha.core.events import EVENT, Event
from rqalpha.interface import AbstractEventSource

from investorch_qmt.runtime.model import RuntimeFailure

SHANGHAI = ZoneInfo("Asia/Shanghai")
FINAL_BAR_WAIT_SECONDS = 30


class WallClock:
    def now(self):
        return datetime.now(SHANGHAI)

    def wait(self, seconds, stopped):
        stopped.wait(seconds)


class DailyEventSource(AbstractEventSource):
    def __init__(self, calendar, periods, clock, control, on_status, freshness, prepare_bars, health):
        self.calendar = tuple(calendar)
        self.periods = periods
        self.clock = clock
        self.control = control
        self.on_status = on_status
        self.freshness = freshness
        self.prepare_bars = prepare_bars
        self.health = health
        self._paused = False

    def first_session(self):
        now = self.clock.now().astimezone(SHANGHAI)
        candidates = [day for day in self.calendar if day >= now.date()]
        if not candidates:
            raise RuntimeFailure("DATA_BUNDLE_NOT_READY", "Native trading calendar has no upcoming session.")
        day = candidates[0]
        if day == now.date() and now.time().replace(tzinfo=None) >= min(start for start, _ in self.periods()):
            raise RuntimeFailure("SESSION_ALREADY_STARTED", "A full session boundary is required.", retryable=True)
        return day

    def _wait_until(self, boundary):
        while not self.control.stopped.is_set():
            self.health()
            now = self.clock.now().astimezone(SHANGHAI)
            enabled, reason = self.control.gate()
            for paused_at, resumed_at in getattr(self.control, "pauses", lambda: ())():
                if paused_at <= boundary <= (resumed_at or now):
                    raise RuntimeFailure("MISSED_RUNTIME_EVENT", "Safety gate crossed an unprocessed event boundary.")
            if not enabled:
                if not self._paused:
                    self.on_status("PAUSED", reason=reason)
                    self._paused = True
                if now >= boundary:
                    raise RuntimeFailure("MISSED_RUNTIME_EVENT", "Safety gate crossed an unprocessed event boundary.")
            elif self._paused:
                if now >= boundary:
                    raise RuntimeFailure("MISSED_RUNTIME_EVENT", "Safety gate resumed after an event boundary.")
                self._paused = False
                self.on_status("RUNNING")
            elif now >= boundary:
                return True
            self.clock.wait(max(0.001, min(0.25, (boundary - now).total_seconds())), self.control.stopped)
        return False

    def events(self, start_date, end_date, frequency):
        if frequency != "1d":
            raise RuntimeFailure("UNSUPPORTED_FREQUENCY", "B3 only supports daily strategies.")
        for day in self.calendar:
            if day < start_date:
                continue
            periods = self.periods()
            opening = datetime.combine(day, min(start for start, _ in periods), SHANGHAI)
            execution = datetime.combine(day, time(14, 57), SHANGHAI)
            closing = datetime.combine(day, max(end for _, end in periods), SHANGHAI)
            if not self._wait_until(opening):
                return
            self.freshness(day)
            yield self._event(EVENT.BEFORE_TRADING, datetime.combine(day, time.min, SHANGHAI))
            if not self._wait_until(execution):
                return
            self.prepare_bars(day, False)
            yield self._event(EVENT.BAR, datetime.combine(day, time(15), SHANGHAI))
            if not self._wait_until(closing):
                return
            deadline = closing + timedelta(seconds=FINAL_BAR_WAIT_SECONDS)
            while not self.control.stopped.is_set():
                if not self._wait_until(closing):
                    return
                try:
                    self.prepare_bars(day, True)
                    break
                except Exception as exc:
                    if getattr(exc, "code", None) != "MARKET_DATA_INCOMPLETE":
                        raise
                    if self.clock.now() >= deadline:
                        raise RuntimeFailure(
                            "MARKET_DATA_INCOMPLETE", "Final daily bar was unavailable at deadline."
                        ) from exc
                    self.clock.wait(0.25, self.control.stopped)
            if self.control.stopped.is_set():
                return
            # Native daily AFTER_TRADING logical time is 15:30, even when live arrival is 15:00+.
            logical_close = datetime.combine(day, time(15, 30), SHANGHAI)
            yield self._event(EVENT.AFTER_TRADING, logical_close)
            if not self._wait_until(closing):
                return
            yield self._event(EVENT.SETTLEMENT, logical_close)
        if not self.control.stopped.is_set():
            raise RuntimeFailure("DATA_BUNDLE_NOT_READY", "Native trading calendar exhausted.")

    @staticmethod
    def _event(kind, instant):
        logical = instant.replace(tzinfo=None)
        return Event(kind, calendar_dt=logical, trading_dt=logical)
