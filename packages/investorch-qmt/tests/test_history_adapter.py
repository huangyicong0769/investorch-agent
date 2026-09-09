from datetime import date

import numpy as np
import pandas as pd
import pytest
from rqalpha.model.instrument import Instrument

from investorch_qmt.market_data.errors import MarketDataError
from investorch_qmt.market_data.history_adapter import XtHistoryAdapter


def instrument(code="600000.XSHG", kind="CS", listed="2000-01-01", delisted="0000-00-00"):
    return Instrument(
        dict(
            order_book_id=code,
            symbol=code,
            type=kind,
            listed_date=listed,
            de_listed_date=delisted,
            exchange=code.split(".")[-1],
            round_lot=100,
        )
    )


def raw(day, *, volume=100, suspended=False):
    return dict(
        time=int(pd.Timestamp(day, tz="Asia/Shanghai").timestamp() * 1000),
        open=10.0,
        high=11.0,
        low=9.0,
        close=10.5,
        volume=volume,
        amount=0.0 if suspended else 100000.0,
        preClose=10.0,
        suspendFlag=1 if suspended else 0,
    )


class API:
    def __init__(self, rows, symbol="600000.SH"):
        self.rows = rows
        self.symbol = symbol

    def get_local_data(self, **kwargs):
        assert kwargs["period"] == "1d"
        assert kwargs["fill_data"] is False
        assert kwargs["dividend_type"] == "none"
        assert kwargs["stock_list"] == [self.symbol]
        return {self.symbol: pd.DataFrame(self.rows)}


@pytest.mark.parametrize("code,kind,xt", [("600000.XSHG", "CS", "600000.SH"), ("000001.XSHG", "INDX", "000001.SH")])
def test_completed_cache_maps_stock_and_index_daily_values_without_download(code, kind, xt):
    adapter = XtHistoryAdapter(API([raw("2026-09-07"), raw("2026-09-08")], xt))
    bars = adapter.read_daily_history(
        instrument(code, kind), date(2026, 9, 7), date(2026, 9, 8), [date(2026, 9, 7), date(2026, 9, 8)]
    )
    np.testing.assert_array_equal(bars["datetime"], [20260907000000, 20260908000000])
    np.testing.assert_array_equal(bars["volume"], [10000, 10000])
    np.testing.assert_array_equal(bars["total_turnover"], [100000, 100000])
    np.testing.assert_array_equal(bars["prev_close"], [10, 10])
    assert not bars["suspended"].any()


@pytest.mark.parametrize("mode", ["missing", "nonfinite", "conflicting"])
def test_cache_missing_and_malformed_are_distinct_failures(mode):
    rows = [raw("2026-09-07"), raw("2026-09-08")]
    if mode == "missing":
        rows.pop()
    elif mode == "nonfinite":
        rows[1]["close"] = float("nan")
    else:
        rows.append({**rows[1], "close": 10.6})
    adapter = XtHistoryAdapter(API(rows))
    with pytest.raises(MarketDataError) as error:
        adapter.read_daily_history(
            instrument(), date(2026, 9, 7), date(2026, 9, 8), [date(2026, 9, 7), date(2026, 9, 8)]
        )
    assert error.value.code == ("FRESH_HISTORY_INCOMPLETE" if mode == "missing" else "FRESH_HISTORY_INVALID")


def test_cache_read_preserves_valid_suspension_and_filters_lifecycle_and_extra_dates():
    rows = [raw("2026-09-08", volume=0, suspended=True), raw("2026-09-09")]
    rows.append(rows[0].copy())
    adapter = XtHistoryAdapter(API(rows))
    bars = adapter.read_daily_history(
        instrument(listed="2026-09-08"),
        date(2026, 9, 7),
        date(2026, 9, 8),
        [date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9)],
    )
    assert list(bars["datetime"]) == [20260908000000]
    assert list(bars["suspended"]) == [True]
    assert list(bars["volume"]) == [0]
    assert list(bars["close"]) == [10.5]
    empty = adapter.read_daily_history(
        instrument(delisted="2026-09-07"), date(2026, 9, 7), date(2026, 9, 8), [date(2026, 9, 7), date(2026, 9, 8)]
    )
    assert len(empty) == 0


def test_history_api_failure_is_not_ready_and_never_downloads_or_falls_back():
    class Unavailable:
        def get_local_data(self, **kwargs):
            raise ConnectionError("provider cache unavailable")

    with pytest.raises(MarketDataError) as error:
        XtHistoryAdapter(Unavailable()).read_daily_history(
            instrument(), date(2026, 9, 8), date(2026, 9, 8), [date(2026, 9, 8)]
        )
    assert error.value.code == "FRESH_HISTORY_NOT_READY"
    assert error.value.transient


@pytest.mark.parametrize("code,kind", [("510300.XSHG", "ETF"), ("830001.XBEI", "CS"), ("00700.XHKG", "CS")])
def test_history_rejects_unsupported_metadata(code, kind):
    with pytest.raises(MarketDataError, match="UNSUPPORTED_INSTRUMENT"):
        XtHistoryAdapter(API([])).read_daily_history(
            instrument(code, kind), date(2026, 9, 8), date(2026, 9, 8), [date(2026, 9, 8)]
        )
