"""Completed raw history in MiniQMT's cache; no InvestOrch bar storage."""

import math
from datetime import datetime

import numpy as np
import pandas as pd
from rqalpha.data.base_data_source.storages import DayBarStore

from .daily import FIELDS, SHANGHAI, normalize_daily_row
from .errors import MarketDataError
from .history_capabilities import HistoryCapabilities
from .xtdata_adapter import XtDataAdapter

RAW_DTYPE = np.dtype([*DayBarStore.DEFAULT_DTYPE.descr, ("prev_close", "f8"), ("suspended", "?")])
BATCH_SIZE = 200
FACTOR_FIELDS = ("time", "interest", "stockBonus", "stockGift", "allotNum", "allotPrice", "gugai", "dr")


class XtHistoryAdapter:
    def __init__(self, api=None, *, connection=None):
        self._connection = connection if connection is not None else XtDataAdapter(api)
        self._owns_connection = connection is None
        self._capabilities = HistoryCapabilities(self._connection)

    def connect(self):
        if self._owns_connection:
            self._connection.connect()
        api = self._connection.connected_api()
        for name in (
            "get_local_data",
            "download_history_data2",
            "get_divid_factors",
            "get_instrument_type",
            "get_instrument_detail",
        ):
            if not callable(getattr(api, name, None)):
                raise MarketDataError("FRESH_HISTORY_NOT_READY", f"Missing xtdata API: {name}", transient=True)

    def history_capability(self, instrument):
        return self._capabilities.inspect(instrument)

    def require_supported(self, instrument):
        return self._capabilities.require_supported(instrument)

    def close(self):
        if self._owns_connection:
            self._connection.close()

    def download_daily_history(self, instruments, start, end, progress=None):
        """Populate MiniQMT cache in maintenance batches; callers must still validate."""
        symbols = list(dict.fromkeys(self.require_supported(instrument) for instrument in instruments))
        for offset in range(0, len(symbols), BATCH_SIZE):
            batch = symbols[offset : offset + BATCH_SIZE]
            try:
                api = self._connection.connected_api()
                result = api.download_history_data2(
                    batch,
                    period="1d",
                    start_time=start.strftime("%Y%m%d"),
                    end_time=end.strftime("%Y%m%d"),
                    incrementally=False,
                )
                self._connection.check_health()
                if result is False:
                    raise ValueError("Provider reported download failure")
            except Exception as exc:
                raise MarketDataError("HISTORY_SYNC_FAILED", str(exc), transient=True) from exc
            if progress:
                progress(offset + len(batch), len(symbols))

    def get_dividend_factors(self, instrument, start, end):
        symbol = self.require_supported(instrument)
        if instrument.type == "INDX":
            return ()
        try:
            api = self._connection.connected_api()
            frame = api.get_divid_factors(symbol, start_time=start.strftime("%Y%m%d"), end_time=end.strftime("%Y%m%d"))
            self._connection.check_health()
        except Exception as exc:
            raise MarketDataError("FRESH_FACTOR_NOT_READY", str(exc), transient=True) from exc
        try:
            if not isinstance(frame, pd.DataFrame):
                raise ValueError("Expected an explicit factor DataFrame")
            if frame.empty:
                return ()
            events = {}
            for index, values in frame.iterrows():
                row = {field: float(values[field]) for field in FACTOR_FIELDS}
                if not all(math.isfinite(value) for value in row.values()):
                    raise ValueError("Nonfinite factor payload")
                day = datetime.fromtimestamp(row["time"] / 1000, SHANGHAI).date()
                if str(index) != day.strftime("%Y%m%d"):
                    raise ValueError("Factor date and timestamp disagree")
                if not start <= day <= end:
                    continue
                if row["dr"] <= 0 or min(row[field] for field in ("interest", "stockBonus", "stockGift")) < 0:
                    raise ValueError("Invalid factor event")
                if any(row[field] != 0 for field in ("allotNum", "allotPrice", "gugai")):
                    raise ValueError("Unsupported rights or share-reform factor semantics")
                if day in events and events[day] != row["dr"]:
                    raise ValueError("Conflicting factor events")
                events[day] = row["dr"]
            return tuple(sorted(events.items()))
        except Exception as exc:
            raise MarketDataError("FRESH_FACTOR_INVALID", str(exc)) from exc

    def read_daily_history(self, instrument, start, end, calendar):
        symbol = self.require_supported(instrument)
        expected = {
            day
            for day in calendar
            if start <= day <= end and instrument.listed_date.date() <= day < instrument.de_listed_date.date()
        }
        if not expected:
            return np.empty(0, dtype=RAW_DTYPE)
        try:
            api = self._connection.connected_api()
            result = api.get_local_data(
                field_list=list(FIELDS),
                stock_list=[symbol],
                period="1d",
                start_time=start.strftime("%Y%m%d"),
                end_time=end.strftime("%Y%m%d"),
                count=-1,
                dividend_type="none",
                fill_data=False,
            )
            self._connection.check_health()
        except Exception as exc:
            raise MarketDataError("FRESH_HISTORY_NOT_READY", str(exc), transient=True) from exc
        try:
            frame = result.get(symbol) if isinstance(result, dict) else None
            if frame is None or frame.empty:
                raise MarketDataError(
                    "FRESH_HISTORY_INCOMPLETE", f"Completed history is missing for {instrument.order_book_id}"
                )
            rows = {}
            for record in frame.to_dict(orient="records"):
                day = datetime.fromtimestamp(float(record["time"]) / 1000, SHANGHAI).date()
                if day not in expected:
                    continue
                bar = normalize_daily_row(record, day)
                if day in rows and rows[day] != bar:
                    raise ValueError("Conflicting completed daily rows")
                rows[day] = bar
            if rows.keys() != expected:
                raise MarketDataError(
                    "FRESH_HISTORY_INCOMPLETE", f"Completed history has gaps for {instrument.order_book_id}"
                )
            return np.array(
                [tuple(rows[day][field] for field in RAW_DTYPE.names) for day in sorted(rows)], dtype=RAW_DTYPE
            )
        except MarketDataError:
            raise
        except Exception as exc:
            raise MarketDataError("FRESH_HISTORY_INVALID", str(exc)) from exc
