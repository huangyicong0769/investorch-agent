from datetime import date
from time import monotonic

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


class CapabilityAPI:
    def get_instrument_type(self, code):
        return {"index" if code in {"000001.SH", "399001.SZ"} else "stock": True}

    def get_instrument_detail(self, code):
        return {"ExchangeID": code[-2:], "InstrumentID": code[:6]}


class API(CapabilityAPI):
    def __init__(self, rows, symbol="600000.SH"):
        self.rows = rows
        self.symbol = symbol

    def get_local_data(self, **kwargs):
        assert kwargs["period"] == "1d"
        assert kwargs["fill_data"] is False
        assert kwargs["dividend_type"] == "none"
        return {symbol: pd.DataFrame(self.rows if symbol == self.symbol else []) for symbol in kwargs["stock_list"]}

    def get_market_data(self, **kwargs):
        return {}


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
    with pytest.raises(MarketDataError, match="FRESH_HISTORY_UNSUPPORTED"):
        XtHistoryAdapter(API([])).read_daily_history(
            instrument(code, kind), date(2026, 9, 8), date(2026, 9, 8), [date(2026, 9, 8)]
        )


def test_maintenance_download_batches_instruments_and_reports_truthful_progress():
    observed = []

    class Downloader(CapabilityAPI):
        def download_history_data2(self, stocks, **kwargs):
            assert kwargs == dict(period="1d", start_time="20260907", end_time="20260908", incrementally=False)
            observed.extend(stocks)
            return {}

    symbols = [instrument(f"{600000 + n:06}.XSHG") for n in range(201)]
    progress = []
    XtHistoryAdapter(Downloader()).download_daily_history(
        symbols, date(2026, 9, 7), date(2026, 9, 8), progress=lambda finished, total: progress.append((finished, total))
    )
    assert observed == [f"{600000 + n:06}.SH" for n in range(201)]
    assert progress[-1] == (201, 201)
    assert all(0 <= finished <= total == 201 for finished, total in progress)
    assert any(0 < finished < 201 for finished, _ in progress)


def test_download_failure_never_reports_completion():
    class Downloader(CapabilityAPI):
        def download_history_data2(self, stocks, **kwargs):
            raise ConnectionError("download interrupted")

    progress = []
    with pytest.raises(MarketDataError, match="HISTORY_SYNC_FAILED"):
        XtHistoryAdapter(Downloader()).download_daily_history(
            [instrument()], date(2026, 9, 7), date(2026, 9, 8), progress=lambda *value: progress.append(value)
        )
    assert (1, 1) not in progress


def test_maintenance_completes_multiple_batches_when_provider_needs_time_between_downloads():
    available_at = 0.0
    cached = set()

    class Downloader(CapabilityAPI):
        def download_history_data2(self, stocks, **kwargs):
            nonlocal available_at
            if monotonic() < available_at:
                raise ConnectionError("Previous native download is still settling")
            cached.update(stocks)
            available_at = monotonic() + 0.05
            return {}

    symbols = [instrument(f"{600000 + n:06}.XSHG") for n in range(601)]
    progress = []
    XtHistoryAdapter(Downloader()).download_daily_history(
        symbols, date(2026, 9, 7), date(2026, 9, 8), progress=lambda *value: progress.append(value)
    )
    assert cached == {f"{600000 + n:06}.SH" for n in range(601)}
    assert progress[-1] == (601, 601)


def test_maintenance_progress_interruption_stops_before_downloading_remaining_instruments():
    cached = set()

    class Downloader(CapabilityAPI):
        def download_history_data2(self, stocks, **kwargs):
            cached.update(stocks)
            return {}

    def stop(finished, total):
        if finished < total:
            raise InterruptedError("History maintenance stopped")

    symbols = [instrument(f"{600000 + n:06}.XSHG") for n in range(601)]
    with pytest.raises(InterruptedError, match="History maintenance stopped"):
        XtHistoryAdapter(Downloader()).download_daily_history(
            symbols, date(2026, 9, 7), date(2026, 9, 8), progress=stop
        )
    assert cached
    assert cached < {f"{600000 + n:06}.SH" for n in range(601)}


def test_lazy_factors_map_real_sdk_event_multiplier_schema_and_allow_empty():
    class Factors(CapabilityAPI):
        empty = False

        def get_divid_factors(self, symbol, *, start_time, end_time):
            assert (symbol, start_time, end_time) == ("600000.SH", "20260701", "20260731")
            if self.empty:
                return pd.DataFrame()
            return pd.DataFrame(
                [
                    dict(
                        time=int(pd.Timestamp("2026-07-16", tz=SHANGHAI).timestamp() * 1000),
                        interest=0.42,
                        stockBonus=0.0,
                        stockGift=0.0,
                        allotNum=0.0,
                        allotPrice=0.0,
                        gugai=0.0,
                        dr=1.047244,
                    )
                ],
                index=["20260716"],
            )

    from investorch_qmt.market_data.daily import SHANGHAI

    api = Factors()
    adapter = XtHistoryAdapter(api)
    assert adapter.get_dividend_factors(instrument(), date(2026, 7, 1), date(2026, 7, 31)) == (
        (date(2026, 7, 16), 1.047244),
    )
    api.empty = True
    assert adapter.get_dividend_factors(instrument(), date(2026, 7, 1), date(2026, 7, 31)) == ()


@pytest.mark.parametrize(
    "changes",
    [
        {"dr": 0},
        {"dr": float("nan")},
        {"interest": -1},
        {"allotNum": 0.1},
        {"allotPrice": 5},
        {"gugai": 0.5},
        {"time": float("inf")},
    ],
)
def test_invalid_or_unsupported_factor_payload_fails_closed(changes):
    row = dict(
        time=int(pd.Timestamp("2026-07-16", tz="Asia/Shanghai").timestamp() * 1000),
        interest=0.42,
        stockBonus=0,
        stockGift=0,
        allotNum=0,
        allotPrice=0,
        gugai=0,
        dr=1.047244,
    )
    row.update(changes)

    class Factors(CapabilityAPI):
        def get_divid_factors(self, *_args, **_kwargs):
            return pd.DataFrame([row], index=["20260716"])

    with pytest.raises(MarketDataError, match="FRESH_FACTOR_INVALID"):
        XtHistoryAdapter(Factors()).get_dividend_factors(instrument(), date(2026, 7, 1), date(2026, 7, 31))


def test_factor_request_failure_is_not_successful_empty_history():
    class Factors(CapabilityAPI):
        def get_divid_factors(self, *_args, **_kwargs):
            raise ConnectionError("factor query disconnected")

    with pytest.raises(MarketDataError, match="FRESH_FACTOR_NOT_READY"):
        XtHistoryAdapter(Factors()).get_dividend_factors(instrument(), date(2026, 7, 1), date(2026, 7, 31))


def test_supported_identity_is_not_removed_when_completed_cache_is_missing():
    api = API([])
    adapter = XtHistoryAdapter(api)
    assert adapter.history_capability(instrument())["supported"] is True
    with pytest.raises(MarketDataError, match="FRESH_HISTORY_INCOMPLETE"):
        adapter.read_daily_history(instrument(), date(2026, 9, 8), date(2026, 9, 8), [date(2026, 9, 8)])
    assert adapter.require_supported(instrument()) == "600000.SH"


class SuspensionAPI(CapabilityAPI):
    def __init__(self, rows, filled, *, axis=None):
        self.rows = rows
        self.filled = filled
        self.axis = axis if axis is not None else [raw("2026-09-07"), raw("2026-09-08"), raw("2026-09-09")]

    def get_local_data(self, *, stock_list, start_time, end_time, count=-1, **kwargs):
        source = self.axis if stock_list[0] in {"000001.SH", "399001.SZ"} else self.rows
        selected = [
            row
            for row in source
            if (
                not start_time
                or pd.Timestamp(row["time"], unit="ms", tz="UTC").tz_convert("Asia/Shanghai").strftime("%Y%m%d")
                >= start_time
            )
            and pd.Timestamp(row["time"], unit="ms", tz="UTC").tz_convert("Asia/Shanghai").strftime("%Y%m%d")
            <= end_time
        ]
        return {stock_list[0]: pd.DataFrame(selected[-count:] if count > 0 else selected)}

    def get_market_data(self, *, stock_list, **kwargs):
        return {
            field: pd.DataFrame(
                {
                    pd.Timestamp(row["time"], unit="ms", tz="UTC").tz_convert("Asia/Shanghai").strftime("%Y%m%d"): {
                        stock_list[0]: row[field]
                    }
                    for row in self.filled
                }
            )
            for field in raw("2026-09-07")
        }


def filled_suspension(day, price=10.5):
    return {**raw(day, volume=0, suspended=True), **dict.fromkeys(("open", "high", "low", "close", "preClose"), price)}


def test_history_accepts_provider_confirmed_internal_suspension_and_preserves_raw_trades():
    before, after = raw("2026-09-07"), raw("2026-09-09")
    api = SuspensionAPI([before, after], [before, filled_suspension("2026-09-08"), after])
    bars = XtHistoryAdapter(api).read_daily_history(
        instrument(), date(2026, 9, 7), date(2026, 9, 9), [date(2026, 9, day) for day in (7, 8, 9)]
    )
    assert list(bars["datetime"]) == [20260907000000, 20260908000000, 20260909000000]
    assert list(bars["suspended"]) == [False, True, False]
    assert list(bars["close"]) == [10.5, 10.5, 10.5]
    assert list(bars["volume"]) == [10000.0, 0.0, 10000.0]
    assert list(bars["total_turnover"]) == [100000.0, 0.0, 100000.0]


def test_conflicting_filled_trade_cannot_validate_a_missing_suspension():
    before = raw("2026-09-07")
    api = SuspensionAPI([before], [{**before, "close": 10.6}, filled_suspension("2026-09-08")])
    with pytest.raises(MarketDataError, match="FRESH_HISTORY_INCOMPLETE"):
        XtHistoryAdapter(api).read_daily_history(
            instrument(), date(2026, 9, 7), date(2026, 9, 8), [date(2026, 9, day) for day in (7, 8)]
        )
