"""Frozen native history boundary with a validated completed MiniQMT tail."""

from datetime import datetime, timedelta

import numpy as np
from rqalpha.const import TRADING_CALENDAR_TYPE
from rqalpha.data.base_data_source.adjust import FIELDS_REQUIRE_ADJUSTMENT, adjust_bars
from rqalpha.data.base_data_source.storages import DayBarStore

from investorch_qmt.history.model import latest_completed
from investorch_qmt.market_data.errors import MarketDataError

from .event_source import WallClock
from .fresh_factors import FreshFactorCache

COMMON_FIELDS = DayBarStore.DEFAULT_DTYPE.names


def _date(value):
    return value.date() if isinstance(value, datetime) else value


def _require_native_instrument(instrument):
    if instrument.type not in {"CS", "INDX"} or not instrument.order_book_id.endswith((".XSHG", ".XSHE")):
        raise MarketDataError("UNSUPPORTED_INSTRUMENT", instrument.order_book_id)


class FreshDailyDataSource:
    def __init__(self, native, history, *, fresh_through, clock=None):
        self._native = native
        self._history = history
        self._fresh_through = fresh_through
        self._clock = clock or WallClock()
        self._native_start, self._native_cutoff = native.available_data_range("1d")
        self._calendar = tuple(day.date() for day in native.get_trading_calendars()[TRADING_CALENDAR_TYPE.CN_STOCK])
        self._factors = FreshFactorCache(native, history, self._native_cutoff)

    @property
    def native_cutoff(self):
        return self._native_cutoff

    def __getattr__(self, name):
        return getattr(self._native, name)

    def _completed(self):
        completed = latest_completed(self._calendar, self._clock.now())
        if completed is None:
            raise MarketDataError("FRESH_HISTORY_NOT_READY", "Native calendar has no completed session.")
        return completed

    def available_data_range(self, frequency):
        if frequency != "1d":
            return self._native.available_data_range(frequency)
        through = self._fresh_through()
        end = max(self.native_cutoff, min(through, self._completed())) if through else self.native_cutoff
        return self._native_start, end

    def _require_coverage(self, day):
        if day > self.available_data_range("1d")[1]:
            raise MarketDataError("FRESH_HISTORY_NOT_READY", f"Completed history has not been validated through {day}.")

    def get_bar(self, instrument, dt, frequency):
        if frequency != "1d":
            return self._native.get_bar(instrument, dt, frequency)
        _require_native_instrument(instrument)
        day = _date(dt)
        if day > self._completed():
            raise MarketDataError("FRESH_HISTORY_NOT_READY", "An incomplete trading day is not historical data.")
        if day <= self.native_cutoff:
            return self._native.get_bar(instrument, dt, frequency)
        self._history.require_supported(instrument)
        self._require_coverage(day)
        rows = self._history.read_daily_history(instrument, day, day, self._calendar)
        return rows[0] if len(rows) else None

    def history_bars(
        self,
        instrument,
        bar_count,
        frequency,
        fields,
        dt,
        skip_suspended=True,
        include_now=False,
        adjust_type="pre",
        adjust_orig=None,
    ):
        if frequency != "1d":
            raise MarketDataError("UNSUPPORTED_FREQUENCY", "Completed fresh history only supports 1d.")
        _require_native_instrument(instrument)
        if adjust_type not in {"none", "pre", "post"}:
            raise ValueError("Invalid adjustment type.")
        end = min(_date(dt), self._completed())
        end = next((day for day in reversed(self._calendar) if day <= end), end)
        projected = list(COMMON_FIELDS) if fields is None else fields
        requested = [fields] if isinstance(fields, str) else projected
        if any(field not in COMMON_FIELDS for field in requested) and end > self.native_cutoff:
            raise MarketDataError("FRESH_HISTORY_INVALID", "Requested historical fields are not provided by xtdata.")
        if end <= self.native_cutoff:
            # Raw source stays native even when a later adjustment origin needs fresh factors.
            raw = self._native.history_bars(
                instrument,
                bar_count,
                "1d",
                None,
                datetime.combine(end, datetime.min.time()),
                skip_suspended=skip_suspended,
                include_now=include_now,
                adjust_type="none",
            )
        else:
            self._history.require_supported(instrument)
            self._require_coverage(end)
            prefix = self._native.history_bars(
                instrument,
                bar_count,
                "1d",
                list(COMMON_FIELDS),
                datetime.combine(self.native_cutoff, datetime.min.time()),
                skip_suspended=skip_suspended,
                include_now=False,
                adjust_type="none",
            )
            suffix = self._history.read_daily_history(
                instrument,
                self.native_cutoff + timedelta(days=1),
                end,
                self._calendar,
            )
            cutoff = int(self.native_cutoff.strftime("%Y%m%d")) * 1000000
            until = int(end.strftime("%Y%m%d")) * 1000000
            suffix = suffix[(suffix["datetime"] > cutoff) & (suffix["datetime"] <= until)]
            prefix = prefix if prefix is not None else np.empty(0, dtype=DayBarStore.DEFAULT_DTYPE)
            raw = np.concatenate([prefix, suffix[list(COMMON_FIELDS)]])
            if len(raw):
                _, indices = np.unique(raw["datetime"], return_index=True)
                raw = raw[indices]
            if skip_suspended and instrument.type == "CS":
                raw = raw[raw["volume"] > 0]
        if raw is None:
            raw = np.empty(0, dtype=DayBarStore.DEFAULT_DTYPE)
        if (
            adjust_type != "none"
            and instrument.type == "CS"
            and (not isinstance(fields, str) or fields in FIELDS_REQUIRE_ADJUSTMENT)
        ):
            origin = adjust_orig if adjust_orig is not None else datetime.combine(end, datetime.min.time())
            through = max(end, _date(origin)) if adjust_type == "pre" else end
            raw = adjust_bars(raw, self._factors.get(instrument, through), projected, adjust_type, origin)
        if bar_count is not None:
            raw = raw[-bar_count:] if bar_count else raw[:0]
        return raw[projected]
