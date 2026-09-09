import shutil
from datetime import date, datetime
from types import SimpleNamespace

import h5py
import numpy as np
import pandas as pd
import pytest
from rqalpha.const import DEFAULT_ACCOUNT_TYPE
from rqalpha.data.base_data_source import BaseDataSource
from rqalpha.data.base_data_source.storages import DayBarStore, SecuritiesDayBarStore
from rqalpha.environment import Environment
from rqalpha.main import cleanup_resources
from test_rqalpha_live_events import SH, Clock
from test_rqalpha_live_runtime import bundle as bundle

from investorch_qmt.market_data.history_adapter import XtHistoryAdapter
from investorch_qmt.rqalpha_live.fresh_data_source import FreshDailyDataSource

DAYS = [date(2026, 9, day) for day in [1, 2, 3, 4, 7, 8, 9]]


@pytest.fixture
def history_bundle(bundle, tmp_path):
    np.save(bundle / "trading_dates.npy", np.array([int(d.strftime("%Y%m%d")) for d in DAYS]))
    rows = np.array(
        [
            (
                int(d.strftime("%Y%m%d")) * 1000000,
                20 + n,
                21 + n,
                22 + n,
                19 + n,
                10000 + n * 100,
                200000 + n * 100,
                30,
                10,
            )
            for n, d in enumerate(DAYS)
        ],
        dtype=SecuritiesDayBarStore.DEFAULT_DTYPE,
    )
    for filename, symbol in [("stocks", "600519.XSHG"), ("indexes", "000001.XSHG")]:
        with h5py.File(bundle / f"{filename}.h5", "w") as h:
            h.create_dataset(symbol, data=rows)
    full_path = tmp_path / "full_bundle"
    shutil.copytree(bundle, full_path)
    for filename, symbol in [("stocks", "600519.XSHG"), ("indexes", "000001.XSHG")]:
        with h5py.File(bundle / f"{filename}.h5", "w") as h:
            h.create_dataset(symbol, data=rows[:4])
    base = SimpleNamespace(data_bundle_path=str(bundle), accounts={DEFAULT_ACCOUNT_TYPE.STOCK: 1}, start_date=DAYS[-1])
    env = Environment(SimpleNamespace(base=base), False)
    native = BaseDataSource(base)
    env.set_data_source(native)
    oracle = BaseDataSource(SimpleNamespace(data_bundle_path=str(full_path)))
    yield native, oracle, rows, full_path
    cleanup_resources(env)


class CacheAPI:
    def __init__(self, rows):
        self.rows = rows

    def get_local_data(self, **kwargs):
        output = []
        for row in self.rows:
            day = datetime.strptime(str(int(row["datetime"]))[:8], "%Y%m%d").date()
            output.append(
                dict(
                    time=int(pd.Timestamp(day, tz=SH).timestamp() * 1000),
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    volume=row["volume"] / 100,
                    amount=row["total_turnover"],
                    preClose=row["close"] - 1,
                    suspendFlag=1 if row["volume"] == 0 else 0,
                )
            )
        return {symbol: pd.DataFrame(output) for symbol in kwargs["stock_list"]}


def test_history_merges_native_prefix_with_complete_cache_tail_at_frozen_global_cutoff(history_bundle):
    native, oracle, rows, _ = history_bundle
    stock = next(native.get_instruments(["600519.XSHG"]))
    clock = Clock(datetime(2026, 9, 9, 9, tzinfo=SH))
    fresh = FreshDailyDataSource(
        native, XtHistoryAdapter(CacheAPI(rows)), fresh_through=lambda: date(2026, 9, 8), clock=clock
    )
    result = fresh.history_bars(stock, 5, "1d", None, datetime(2026, 9, 8), adjust_type="none")
    expected = oracle.history_bars(
        stock, 5, "1d", list(DayBarStore.DEFAULT_DTYPE.names), datetime(2026, 9, 8), adjust_type="none"
    )
    np.testing.assert_array_equal(result, expected)
    assert fresh.native_cutoff == date(2026, 9, 4)
    assert fresh.available_data_range("1d") == (date(2026, 9, 1), date(2026, 9, 8))


@pytest.mark.parametrize("symbol", ["600519.XSHG", "000001.XSHG"])
@pytest.mark.parametrize("fields", [None, "close", ["datetime", "volume", "close"]])
@pytest.mark.parametrize("count", [1, 3, 5, 20])
def test_raw_history_field_projection_and_counts_match_native(history_bundle, symbol, fields, count):
    native, oracle, rows, _ = history_bundle
    instrument = next(native.get_instruments([symbol]))
    fresh = FreshDailyDataSource(
        native,
        XtHistoryAdapter(CacheAPI(rows[::-1])),
        fresh_through=lambda: DAYS[-2],
        clock=Clock(datetime(2026, 9, 9, 14, tzinfo=SH)),
    )
    actual = fresh.history_bars(instrument, count, "1d", fields, datetime(2026, 9, 9), adjust_type="none")
    expected = oracle.history_bars(
        instrument,
        count,
        "1d",
        list(DayBarStore.DEFAULT_DTYPE.names) if fields is None else fields,
        datetime(2026, 9, 8),
        adjust_type="none",
    )
    np.testing.assert_array_equal(actual, expected)


def test_watermark_update_is_visible_but_does_not_change_native_cutoff(history_bundle):
    native, _, rows, _ = history_bundle
    through = [None]
    fresh = FreshDailyDataSource(
        native,
        XtHistoryAdapter(CacheAPI(rows)),
        fresh_through=lambda: through[0],
        clock=Clock(datetime(2026, 9, 9, 14, tzinfo=SH)),
    )
    assert fresh.available_data_range("1d")[1] == DAYS[3]
    through[0] = DAYS[-1]
    assert fresh.available_data_range("1d")[1] == DAYS[-2]
    assert fresh.native_cutoff == DAYS[3]


def test_incomplete_tail_or_unvalidated_watermark_never_falls_back(history_bundle):
    from investorch_qmt.market_data.errors import MarketDataError

    native, _, rows, _ = history_bundle
    stock = next(native.get_instruments(["600519.XSHG"]))
    for cached, through, code in [
        (rows[:4], DAYS[-2], "FRESH_HISTORY_INCOMPLETE"),
        (rows, DAYS[3], "FRESH_HISTORY_NOT_READY"),
    ]:
        fresh = FreshDailyDataSource(
            native,
            XtHistoryAdapter(CacheAPI(cached)),
            fresh_through=lambda through=through: through,
            clock=Clock(datetime(2026, 9, 9, 14, tzinfo=SH)),
        )
        with pytest.raises(MarketDataError) as error:
            fresh.history_bars(stock, 1, "1d", "close", datetime(2026, 9, 8), adjust_type="none")
        assert error.value.code == code


def test_get_bar_uses_exact_completed_day_and_rejects_forming_day(history_bundle):
    from investorch_qmt.market_data.errors import MarketDataError

    native, _, rows, _ = history_bundle
    stock = next(native.get_instruments(["600519.XSHG"]))
    fresh = FreshDailyDataSource(
        native,
        XtHistoryAdapter(CacheAPI(rows)),
        fresh_through=lambda: DAYS[-1],
        clock=Clock(datetime(2026, 9, 9, 14, tzinfo=SH)),
    )
    for day in [4, 8]:
        actual = fresh.get_bar(stock, datetime(2026, 9, day), "1d")
        assert actual["datetime"] == 20260900000000 + day * 1000000
    with pytest.raises(MarketDataError, match="incomplete trading day"):
        fresh.get_bar(stock, datetime(2026, 9, 9), "1d")
    with pytest.raises(MarketDataError, match="not provided"):
        fresh.history_bars(stock, 1, "1d", "limit_up", datetime(2026, 9, 8), adjust_type="none")


def test_weekend_query_uses_last_trading_session_coverage(history_bundle):
    native, _, rows, _ = history_bundle
    stock = next(native.get_instruments(["600519.XSHG"]))
    fresh = FreshDailyDataSource(
        native,
        XtHistoryAdapter(CacheAPI(rows)),
        fresh_through=lambda: DAYS[3],
        clock=Clock(datetime(2026, 9, 9, 14, tzinfo=SH)),
    )
    actual = fresh.history_bars(stock, 1, "1d", "datetime", datetime(2026, 9, 6), adjust_type="none")
    assert actual.tolist() == [20260904000000]


@pytest.mark.parametrize("skip_suspended", [True, False])
def test_suspension_filter_matches_native_for_fresh_zero_volume(history_bundle, skip_suspended):
    native, _, rows, full_path = history_bundle
    rows = rows.copy()
    rows[4]["volume"] = 0
    rows[4]["total_turnover"] = 0
    rows[4]["open"] = rows[4]["close"] = rows[4]["high"] = rows[4]["low"] = rows[3]["close"]
    with h5py.File(full_path / "stocks.h5", "w") as h:
        h.create_dataset("600519.XSHG", data=rows)
    oracle = BaseDataSource(SimpleNamespace(data_bundle_path=str(full_path)))
    stock = next(native.get_instruments(["600519.XSHG"]))
    fresh = FreshDailyDataSource(
        native,
        XtHistoryAdapter(CacheAPI(rows)),
        fresh_through=lambda: DAYS[-2],
        clock=Clock(datetime(2026, 9, 9, 14, tzinfo=SH)),
    )
    actual = fresh.history_bars(
        stock, 4, "1d", "datetime", datetime(2026, 9, 8), skip_suspended=skip_suspended, adjust_type="none"
    )
    expected = oracle.history_bars(
        stock, 4, "1d", "datetime", datetime(2026, 9, 8), skip_suspended=skip_suspended, adjust_type="none"
    )
    np.testing.assert_array_equal(actual, expected)


def test_real_strategy_reads_fresh_stock_and_index_before_trading(history_bundle, tmp_path):
    import json

    from test_rqalpha_live_events import Control
    from test_rqalpha_live_runtime import Market, artifacts_for

    from investorch_qmt.rqalpha_live.runtime import run_live

    _, _, rows, _ = history_bundle
    output = tmp_path / "fresh-strategy.json"
    clock = Clock(datetime(2026, 9, 9, 9, 29, tzinfo=SH))

    class FreshControl(Control):
        def history_through(self):
            return DAYS[-2]

    control = FreshControl()
    clock.on_wait = lambda _: control.stopped.set() if output.exists() else None
    source = f"""
import json
def before_trading(context):
    stock = history_bars('600519.XSHG', 5, '1d', ['datetime', 'close'], adjust_type='none')
    index = history_bars('000001.XSHG', 5, '1d', ['datetime', 'close'], adjust_type='none')
    with open({str(output)!r}, 'w') as file:
        json.dump(dict(stock=stock.tolist(), index=index.tolist()), file)
"""
    run_live(
        artifacts_for(source, tmp_path),
        control,
        lambda *_args, **_kwargs: None,
        market=Market(clock),
        history=XtHistoryAdapter(CacheAPI(rows)),
        clock=clock,
    )
    observed = json.loads(output.read_text())
    expected = [[int(row["datetime"]), float(row["close"])] for row in rows[1:6]]
    assert observed == {"stock": expected, "index": expected}
