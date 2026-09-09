"""Same-input parity with the pinned native RQAlpha history implementation."""

from datetime import date, datetime

import h5py
import numpy as np
import pandas as pd
import pytest
from rqalpha.data.base_data_source.storages import DayBarStore
from test_fresh_data_source import CacheAPI
from test_fresh_data_source import history_bundle as history_bundle
from test_rqalpha_live_events import SH, Clock
from test_rqalpha_live_runtime import bundle as bundle

from investorch_qmt.market_data.history_adapter import XtHistoryAdapter
from investorch_qmt.rqalpha_live.fresh_data_source import FreshDailyDataSource


class FactorAPI(CacheAPI):
    def __init__(self, rows, events):
        super().__init__(rows)
        self.events = events
        self.factors_available = True

    def get_divid_factors(self, symbol, start_time, end_time):
        if not self.factors_available:
            raise ConnectionError("Corporate-action provider is unavailable.")
        return pd.DataFrame.from_dict(
            {
                day.strftime("%Y%m%d"): dict(
                    time=int(pd.Timestamp(day, tz=SH).timestamp() * 1000),
                    interest=0.0,
                    stockBonus=0.0,
                    stockGift=0.0,
                    allotNum=0.0,
                    allotPrice=0.0,
                    gugai=0.0,
                    dr=multiplier,
                )
                for day, multiplier in self.events
                if start_time <= day.strftime("%Y%m%d") <= end_time
            },
            orient="index",
        )


@pytest.mark.parametrize("adjust_type", ["pre", "post", "none"])
@pytest.mark.parametrize(
    "fields", [None, ["datetime", "close", "volume", "total_turnover"], "close", "volume", "total_turnover"]
)
@pytest.mark.parametrize("end,origin", [(4, 8), (8, 8), (8, 4)])
@pytest.mark.parametrize("skip_suspended", [False, True])
def test_history_matches_full_native_oracle_for_identical_raw_and_factors(
    history_bundle,
    bundle,
    adjust_type,
    fields,
    end,
    origin,
    skip_suspended,
):
    native, oracle, rows, full_path = history_bundle
    rows = rows.copy()
    rows["volume"][[1, 4]] = 0  # Suspensions on both sides of the source boundary.
    rows["total_turnover"][[1, 4]] = 0
    for path, count in [(bundle, 4), (full_path, len(rows))]:
        with h5py.File(path / "stocks.h5", "w") as h:
            h.create_dataset("600519.XSHG", data=rows[:count])
    # Full native store is the oracle; values are the specified inputs, not adapter output.
    factors = np.array(
        [(0, 1.0), (20260902000000, 2.0), (20260907000000, 3.0), (20260908000000, 6.0)],
        dtype=[("start_date", "i8"), ("ex_cum_factor", "f8")],
    )
    for path in [bundle, full_path]:
        with h5py.File(path / "ex_cum_factor.h5", "w") as h:
            h.create_dataset("600519.XSHG", data=factors)
    api = FactorAPI(rows, [(date(2026, 9, 7), 1.5), (date(2026, 9, 8), 2.0)])
    api.factors_available = adjust_type != "none" and fields != "total_turnover"
    fresh = FreshDailyDataSource(
        native,
        XtHistoryAdapter(api),
        fresh_through=lambda: date(2026, 9, 8),
        clock=Clock(datetime(2026, 9, 9, 9, tzinfo=SH)),
    )
    instrument = next(native.get_instruments(["600519.XSHG"]))
    kwargs = dict(skip_suspended=skip_suspended, adjust_type=adjust_type, adjust_orig=datetime(2026, 9, origin))
    actual = fresh.history_bars(instrument, 5, "1d", fields, datetime(2026, 9, end), **kwargs)
    expected = oracle.history_bars(
        instrument,
        5,
        "1d",
        list(DayBarStore.DEFAULT_DTYPE.names) if fields is None else fields,
        datetime(2026, 9, end),
        **kwargs,
    )
    np.testing.assert_array_equal(actual, expected)
