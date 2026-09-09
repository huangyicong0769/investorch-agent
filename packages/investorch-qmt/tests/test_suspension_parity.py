"""Provider-filled suspension rows preserve the native public history behavior."""

from datetime import datetime
from types import SimpleNamespace

import h5py
import numpy as np
import pandas as pd
import pytest
from rqalpha.data.base_data_source import BaseDataSource
from rqalpha.data.base_data_source.storages import DayBarStore
from test_fresh_data_source import DAYS, CacheAPI
from test_fresh_data_source import history_bundle as history_bundle
from test_rqalpha_live_events import SH, Clock
from test_rqalpha_live_runtime import bundle as bundle

from investorch_qmt.market_data.history_adapter import XtHistoryAdapter
from investorch_qmt.rqalpha_live.fresh_data_source import FreshDailyDataSource


class FilledHistoryAPI(CacheAPI):
    def __init__(self, stock_rows, index_rows):
        super().__init__(stock_rows)
        self.index_rows = index_rows

    def get_local_data(self, stock_list, start_time="", end_time="", count=-1, **kwargs):
        result = {}
        for symbol in stock_list:
            source = self.index_rows if symbol == "000001.SH" else self.rows
            frame = CacheAPI(source).get_local_data(stock_list=[symbol])[symbol]
            if symbol != "000001.SH":
                frame = frame[frame["suspendFlag"] != 1]
            days = pd.to_datetime(frame["time"], unit="ms", utc=True).dt.tz_convert(SH).dt.strftime("%Y%m%d")
            frame = frame[(days >= start_time) & (days <= (end_time or "99999999"))]
            if count > 0:
                frame = frame.tail(count)
            result[symbol] = frame
        return result

    def get_market_data(self, field_list, stock_list, start_time="", end_time="", **kwargs):
        by_symbol = {}
        for symbol in stock_list:
            source = self.index_rows if symbol == "000001.SH" else self.rows
            frame = CacheAPI(source).get_local_data(stock_list=[symbol])[symbol]
            frame.loc[frame["suspendFlag"] == 1, "preClose"] = frame.loc[frame["suspendFlag"] == 1, "close"]
            frame.index = pd.to_datetime(frame["time"], unit="ms", utc=True).dt.tz_convert(SH).dt.strftime("%Y%m%d")
            frame = frame.loc[(frame.index >= start_time) & (frame.index <= end_time)]
            by_symbol[symbol] = frame
        return {
            field: pd.DataFrame({symbol: frame[field] for symbol, frame in by_symbol.items()}).T for field in field_list
        }

    def get_divid_factors(self, *args, **kwargs):
        return pd.DataFrame()


@pytest.fixture
def filled_parity(history_bundle):
    _, _, rows, full_path = history_bundle
    suspended = rows.copy()
    suspended[4]["volume"] = suspended[4]["total_turnover"] = 0
    for field in ("open", "high", "low", "close"):
        suspended[4][field] = rows[3]["close"]
    with h5py.File(full_path / "stocks.h5", "w") as store:
        store.create_dataset("600519.XSHG", data=suspended)
    native_path = full_path.parent / ".rqalpha" / "bundle"
    for path in (native_path, full_path):
        with h5py.File(path / "ex_cum_factor.h5", "w") as store:
            store.create_dataset(
                "600519.XSHG",
                data=np.array([(0, 1.0), (20260903000000, 2.0)], dtype=[("start_date", "i8"), ("ex_cum_factor", "f8")]),
            )
    native = BaseDataSource(SimpleNamespace(data_bundle_path=str(native_path)))
    oracle = BaseDataSource(SimpleNamespace(data_bundle_path=str(full_path)))
    stock = next(native.get_instruments(["600519.XSHG"]))
    fresh = FreshDailyDataSource(
        native,
        XtHistoryAdapter(FilledHistoryAPI(suspended, rows)),
        fresh_through=lambda: DAYS[-2],
        clock=Clock(datetime(2026, 9, 9, 14, tzinfo=SH)),
    )
    return fresh, oracle, stock


def test_provider_filled_get_bar_matches_native_suspension_row(filled_parity):
    fresh, native, stock = filled_parity
    day = datetime(2026, 9, 7)
    actual = fresh.get_bar(stock, day, "1d")
    expected = native.get_bar(stock, day, "1d")
    for field in DayBarStore.DEFAULT_DTYPE.names:
        assert actual[field] == expected[field]
    assert actual["suspended"]


@pytest.mark.parametrize("skip_suspended", [True, False])
@pytest.mark.parametrize("adjust_type", ["none", "pre", "post"])
@pytest.mark.parametrize("fields", [None, ["datetime", "close", "volume"], "close", "volume"])
@pytest.mark.parametrize("bar_count", [1, 5])
def test_provider_filled_history_matches_native_selection_and_adjustment(
    filled_parity, skip_suspended, adjust_type, fields, bar_count
):
    fresh, native, stock = filled_parity
    day = datetime(2026, 9, 7)
    actual = fresh.history_bars(
        stock, bar_count, "1d", fields, day, skip_suspended=skip_suspended, adjust_type=adjust_type, adjust_orig=day
    )
    expected = native.history_bars(
        stock,
        bar_count,
        "1d",
        list(DayBarStore.DEFAULT_DTYPE.names) if fields is None else fields,
        day,
        skip_suspended=skip_suspended,
        adjust_type=adjust_type,
        adjust_orig=day,
    )
    np.testing.assert_array_equal(actual, expected)
