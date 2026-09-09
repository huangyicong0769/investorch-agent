from datetime import date, datetime
from types import SimpleNamespace

import numpy as np
import pytest
from rqalpha.data.base_data_source.adjust import adjust_bars

from investorch_qmt.rqalpha_live.fresh_factors import FreshFactorCache
from investorch_qmt.runtime.model import RuntimeFailure

DTYPE = [("start_date", "i8"), ("ex_cum_factor", "f8")]
CS = SimpleNamespace(order_book_id="600000.XSHG", type="CS")
CUTOFF = date(2026, 8, 31)


class Native:
    def __init__(self):
        self.factors = np.array([(0, 1), (20260801000000, 2), (20260902000000, 99)], dtype=DTYPE)

    def get_ex_cum_factor(self, instrument):
        return self.factors


class History:
    def __init__(self):
        self.available = True
        self.available_from = date.min
        self.events = ((date(2026, 9, 2), 1.5), (date(2026, 9, 3), 2.0))

    def get_dividend_factors(self, instrument, start, end):
        if not self.available or start < self.available_from:
            raise ConnectionError("Requested factor interval is unavailable.")
        return tuple((day, factor) for day, factor in self.events if start <= day <= end)


def test_composite_adjusts_both_sides_of_cutoff_using_native_cumulative_baseline():
    cache = FreshFactorCache(Native(), History(), CUTOFF)
    factors = cache.get(CS, date(2026, 9, 3))
    np.testing.assert_array_equal(
        factors, np.array([(0, 1), (20260801000000, 2), (20260902000000, 3), (20260903000000, 6)], dtype=DTYPE)
    )
    bars = np.array(
        [(20260831000000, 30.0, 100.0), (20260902000000, 20.0, 200.0), (20260903000000, 10.0, 300.0)],
        dtype=[("datetime", "i8"), ("close", "f8"), ("volume", "f8")],
    )
    pre = adjust_bars(bars, factors, None, "pre", datetime(2026, 9, 3))
    post = adjust_bars(bars, factors, None, "post", datetime(2026, 9, 3))
    np.testing.assert_allclose(pre["close"], [10, 10, 10])
    np.testing.assert_allclose(pre["volume"], [300, 400, 300])
    np.testing.assert_allclose(post["close"], [60, 60, 60])
    np.testing.assert_allclose(post["volume"], [50, 200 / 3, 50])


def test_empty_interval_cached_later_fetch_incremental_and_native_prefix_frozen():
    native, history = Native(), History()
    cache = FreshFactorCache(native, history, CUTOFF)
    first = cache.get(CS, date(2026, 9, 1))
    native.factors["ex_cum_factor"] = 100
    first["ex_cum_factor"] = 200  # Caller mutation must not poison the cache.
    history.available = False
    cached = cache.get(CS, date(2026, 9, 1))
    assert cached["ex_cum_factor"].tolist() == [1, 2]
    history.available = True
    history.available_from = date(2026, 9, 2)
    later = cache.get(CS, date(2026, 9, 3))
    assert later["ex_cum_factor"].tolist() == [1, 2, 3, 6]
    history.available = False
    assert cache.get(CS, date(2026, 9, 2))["ex_cum_factor"].tolist() == [1, 2, 3]


@pytest.mark.parametrize(
    "events",
    [
        ((date(2026, 9, 2), float("nan")),),
        ((date(2026, 9, 2), float("inf")),),
        ((date(2026, 9, 2), 0),),
        ((date(2026, 9, 2), -1),),
        ((date(2026, 9, 2), True),),
        ((date(2026, 9, 3), 2), (date(2026, 9, 2), 2)),
        ((date(2026, 9, 2), 2), (date(2026, 9, 2), 2)),
        ((date(2026, 8, 31), 2),),
        ((date(2026, 9, 4), 2),),
        ((date(2026, 9, 2), 1e308),),
        None,
    ],
)
def test_invalid_response_does_not_advance_checked_interval(events):
    history = History()
    history.get_dividend_factors = lambda *args: events
    cache = FreshFactorCache(Native(), history, CUTOFF)
    with pytest.raises(RuntimeFailure, match="FRESH_FACTOR_INVALID"):
        cache.get(CS, date(2026, 9, 3))
    history.get_dividend_factors = lambda *args: ((date(2026, 9, 2), 1.5),)
    assert cache.get(CS, date(2026, 9, 3))["ex_cum_factor"].tolist() == [1, 2, 3]


def test_connection_failure_can_retry_same_interval():
    history = History()

    def fail(*args):
        raise ConnectionError("offline")

    history.get_dividend_factors = fail
    cache = FreshFactorCache(Native(), history, CUTOFF)
    with pytest.raises(RuntimeFailure, match="FRESH_FACTOR_NOT_READY"):
        cache.get(CS, date(2026, 9, 3))
    history.get_dividend_factors = History().get_dividend_factors
    assert cache.get(CS, date(2026, 9, 3))["ex_cum_factor"].tolist() == [1, 2, 3, 6]


@pytest.mark.parametrize("native_factors", [None, np.array([], dtype=DTYPE)])
def test_missing_native_factors_use_initial_one(native_factors):
    native = Native()
    native.factors = native_factors
    assert FreshFactorCache(native, History(), CUTOFF).get(CS, date(2026, 9, 2))["ex_cum_factor"].tolist() == [1, 1.5]


def test_index_query_succeeds_while_factor_provider_is_unavailable():
    history = History()
    history.available = False
    assert FreshFactorCache(Native(), history, CUTOFF).get(SimpleNamespace(type="INDX"), date(2026, 9, 3)) is None


@pytest.mark.parametrize(
    "rows",
    [
        [(0, 1), (20260801000000, float("nan"))],
        [(0, 1), (20260801000000, 0)],
        [(0, 1), (20260801000000, 2), (20260801000000, 3)],
        [(0, 1), (20260802000000, 2), (20260801000000, 3)],
        [(0, 1), (20260801120000, 2)],
        [(0, 1), (0, 1)],
    ],
)
def test_invalid_native_prefix_fails_closed(rows):
    native = Native()
    native.factors = np.array(rows, dtype=DTYPE)
    with pytest.raises(RuntimeFailure, match="FRESH_FACTOR_INVALID"):
        FreshFactorCache(native, History(), CUTOFF).get(CS, date(2026, 9, 3))


def test_native_only_query_succeeds_offline_and_missing_initial_is_inserted():
    native, history = Native(), History()
    history.available = False
    native.factors = np.array([(20260801000000, 2)], dtype=DTYPE)
    factors = FreshFactorCache(native, history, CUTOFF).get(CS, CUTOFF)
    assert factors.tolist() == [(0, 1), (20260801000000, 2)]


def test_adapter_invalid_payload_retains_explicit_error_taxonomy():
    from investorch_qmt.market_data.errors import MarketDataError

    history = History()

    def invalid(*args):
        raise MarketDataError("FRESH_FACTOR_INVALID", "bad payload")

    history.get_dividend_factors = invalid
    with pytest.raises(MarketDataError, match="FRESH_FACTOR_INVALID"):
        FreshFactorCache(Native(), history, CUTOFF).get(CS, date(2026, 9, 3))
