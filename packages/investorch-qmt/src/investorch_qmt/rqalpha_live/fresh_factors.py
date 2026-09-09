"""Worker-local corporate-action factors across a frozen native coverage boundary."""

from datetime import date, datetime, timedelta
from math import isfinite
from numbers import Real

import numpy as np

from investorch_qmt.market_data.errors import MarketDataError
from investorch_qmt.runtime.model import RuntimeFailure

_FACTOR_DTYPE = [("start_date", "i8"), ("ex_cum_factor", "f8")]


def _stamp(day):
    return int(day.strftime("%Y%m%d")) * 1000000


def _positive(value):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real) or not isfinite(value) or value <= 0:
        raise ValueError("Factors must be finite positive numbers.")
    return float(value)


class FreshFactorCache:
    def __init__(self, native, history, cutoff: date):
        self.native = native
        self.history = history
        self.cutoff = cutoff
        self._entries = {}

    def _native_prefix(self, instrument):
        native = self.native.get_ex_cum_factor(instrument)
        prefix = [(0, 1.0)]
        if native is None:
            return prefix
        saw_initial = False
        for row in native:
            raw_stamp = row["start_date"]
            stamp = int(raw_stamp)
            if stamp > _stamp(self.cutoff):
                continue
            if stamp != raw_stamp or stamp < 0:
                raise ValueError("Invalid native factor date.")
            factor = _positive(row["ex_cum_factor"])
            if stamp == 0 and len(prefix) == 1 and factor == 1.0 and not saw_initial:
                saw_initial = True
                continue
            if stamp <= prefix[-1][0]:
                raise ValueError("Native factors must be strictly ordered.")
            datetime.strptime(str(stamp), "%Y%m%d%H%M%S")
            if stamp % 1000000:
                raise ValueError("Native factors require daily timestamps.")
            prefix.append((stamp, factor))
        return prefix

    def get(self, instrument, through: date):
        if instrument.type == "INDX":
            return None
        key = instrument.order_book_id
        if key not in self._entries:
            try:
                prefix = self._native_prefix(instrument)
            except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
                raise RuntimeFailure("FRESH_FACTOR_INVALID", "Invalid native factor prefix.") from exc
            self._entries[key] = (self.cutoff, prefix)
        checked, factors = self._entries[key]
        if through > checked:
            try:
                events = self.history.get_dividend_factors(instrument, checked + timedelta(days=1), through)
            except (RuntimeFailure, MarketDataError):
                raise
            except Exception as exc:
                raise RuntimeFailure("FRESH_FACTOR_NOT_READY", "Fresh factors could not be fetched.") from exc
            updated = list(factors)
            cumulative = updated[-1][1]
            previous = checked
            try:
                for day, multiplier in events:
                    if type(day) is not date or not previous < day <= through:
                        raise ValueError("Fresh factor dates must be ordered within the requested interval.")
                    cumulative = _positive(cumulative * _positive(multiplier))
                    updated.append((_stamp(day), cumulative))
                    previous = day
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                raise RuntimeFailure("FRESH_FACTOR_INVALID", "Invalid fresh factor events.") from exc
            # Commit only a completely validated response, including a successful empty interval.
            self._entries[key] = (through, updated)
            factors = updated
        return np.array([row for row in factors if row[0] <= _stamp(through)], dtype=_FACTOR_DTYPE)
