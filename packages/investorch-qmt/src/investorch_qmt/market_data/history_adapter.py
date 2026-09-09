"""Completed raw history in MiniQMT's cache; no InvestOrch bar storage."""

from datetime import datetime

import numpy as np
from rqalpha.data.base_data_source.storages import DayBarStore

from .daily import FIELDS, SHANGHAI, normalize_daily_row
from .errors import MarketDataError
from .symbols import to_xt_history_symbol
from .xtdata_adapter import XtDataAdapter

RAW_DTYPE = np.dtype([*DayBarStore.DEFAULT_DTYPE.descr, ("prev_close", "f8"), ("suspended", "?")])


class XtHistoryAdapter:
    def __init__(self, api=None, *, connection=None):
        self._connection = connection if connection is not None else XtDataAdapter(api)
        self._owns_connection = connection is None

    def connect(self):
        if self._owns_connection:
            self._connection.connect()
        api = self._connection.connected_api()
        for name in ("get_local_data", "download_history_data2", "get_divid_factors"):
            if not callable(getattr(api, name, None)):
                raise MarketDataError("FRESH_HISTORY_NOT_READY", f"Missing xtdata API: {name}", transient=True)

    def close(self):
        if self._owns_connection:
            self._connection.close()

    def read_daily_history(self, instrument, start, end, calendar):
        symbol = to_xt_history_symbol(instrument)
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
