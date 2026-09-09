"""Resolve missing CS dates only from explicit, anchored provider suspension rows."""

import math
from datetime import datetime

import pandas as pd

from .daily import FIELDS, SHANGHAI, normalize_daily_row
from .errors import MarketDataError


class HistoricalSuspensionResolver:
    def __init__(self, connection):
        self._connection = connection

    def resolve(self, symbol, missing, rows, calendar):
        anchors = [
            day for day, row in rows.items() if day < min(missing) and not row["suspended"] and row["volume"] > 0
        ]
        if not anchors:
            return {}
        start, end = max(anchors), max(missing)
        axis = {day for day in calendar if start <= day <= end}
        index = "000001.SH" if symbol.endswith(".SH") else "399001.SZ"
        params = dict(
            field_list=list(FIELDS),
            period="1d",
            start_time=start.strftime("%Y%m%d"),
            end_time=end.strftime("%Y%m%d"),
            count=-1,
            dividend_type="none",
        )
        api = self._connection.connected_api()
        try:
            raw_index = api.get_local_data(stock_list=[index], fill_data=False, **params)
            self._connection.check_health()
            matrix = api.get_market_data(stock_list=[symbol, index], fill_data=True, **params)
            self._connection.check_health()
        except Exception as exc:
            raise MarketDataError("FRESH_HISTORY_NOT_READY", str(exc), transient=True) from exc
        try:
            observed = {}
            for record in raw_index[index].to_dict(orient="records"):
                day = datetime.fromtimestamp(float(record["time"]) / 1000, SHANGHAI).date()
                if day not in axis:
                    continue
                bar = normalize_daily_row(record, day)
                if record["suspendFlag"] != 0 or bar["volume"] <= 0 or bar["total_turnover"] <= 0:
                    raise ValueError("Index does not provide a normal trading-date axis")
                if day in observed and observed[day] != bar:
                    raise ValueError("Conflicting index observations")
                observed[day] = bar
            if observed.keys() != axis:
                raise ValueError("Incomplete index trading-date axis")
            columns = matrix["time"].columns
            for field in FIELDS:
                frame = matrix[field]
                if not isinstance(frame, pd.DataFrame) or not frame.columns.equals(columns):
                    raise ValueError("Inconsistent filled-history field axes")
                if not frame.index.is_unique or not frame.columns.is_unique or symbol not in frame.index:
                    raise ValueError("Ambiguous filled-history rows")
            resolved = {}
            previous_close = rows[start]["close"]
            for day in sorted(axis):
                label = day.strftime("%Y%m%d")
                record = {field: matrix[field].loc[symbol, label] for field in FIELDS}
                bar = normalize_daily_row(record, day)
                if day in rows:
                    if bar["suspended"] != rows[day]["suspended"] or any(
                        not _same_number(bar[field], rows[day][field])
                        for field in ("open", "high", "low", "close", "volume", "total_turnover", "prev_close")
                    ):
                        raise ValueError("Filled history contradicts a raw trading observation")
                    previous_close = rows[day]["close"]
                    continue
                prices = [bar[field] for field in ("open", "high", "low", "close", "prev_close")]
                if not bar["suspended"] or any(not _same_price(price, previous_close) for price in prices):
                    raise ValueError("Missing date lacks anchored suspension evidence")
                if day in missing:
                    resolved[day] = bar
                previous_close = bar["close"]
            return resolved
        except (KeyError, TypeError, ValueError, AttributeError, IndexError) as exc:
            raise MarketDataError("FRESH_HISTORY_INCOMPLETE", str(exc)) from exc


def _same_price(left, right):
    # The two pinned API shapes can differ only by binary floating-point rounding.
    return left > 0 and right > 0 and _same_number(left, right)


def _same_number(left, right):
    return abs(left - right) <= 4 * max(math.ulp(left), math.ulp(right))
