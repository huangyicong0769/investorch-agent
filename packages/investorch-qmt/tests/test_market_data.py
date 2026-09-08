import pytest

from investorch_qmt.market_data.errors import MarketDataError
from investorch_qmt.market_data.symbols import from_xt_symbol, to_xt_symbol


def test_supported_stock_symbols_round_trip_and_other_assets_fail_closed():
    assert to_xt_symbol("600519.XSHG") == "600519.SH"
    assert from_xt_symbol("000001.SZ") == "000001.XSHE"
    for symbol in ["510300.XSHG", "000001.XSHG", "00700.XHKG", "830001.XBEI", "IF2609", "600519.SH"]:
        with pytest.raises(MarketDataError, match="UNSUPPORTED_INSTRUMENT"):
            to_xt_symbol(symbol)


def test_current_daily_data_requires_exact_date_and_finite_unfilled_fields():
    from datetime import date

    import pandas as pd

    from investorch_qmt.market_data.xtdata_adapter import XtDataAdapter

    class Api:
        def get_market_data(self, **kwargs):
            assert kwargs["fill_data"] is False
            assert kwargs["dividend_type"] == "none"
            assert kwargs["period"] == "1d"
            return {
                key: pd.DataFrame([[value]], index=["600519.SH"], columns=["20260908"]) for key, value in row.items()
            }

    row = dict(
        time=1788796800000, open=100, high=105, low=99, close=104, volume=123, amount=12345, preClose=98, suspendFlag=0
    )
    adapter = XtDataAdapter(Api())
    bar = adapter.daily_bar("600519.XSHG", date(2026, 9, 8))
    assert bar["datetime"] == 20260908000000
    assert bar["total_turnover"] == 12345
    assert bar["volume"] == 12300
    assert bar["suspended"] is False
    with pytest.raises(MarketDataError, match="MARKET_DATA_INCOMPLETE"):
        adapter.daily_bar("600519.XSHG", date(2026, 9, 9))
    row["close"] = float("nan")
    with pytest.raises(MarketDataError, match="MARKET_DATA_INCOMPLETE"):
        adapter.daily_bar("600519.XSHG", date(2026, 9, 8))


def test_quote_subscription_preserves_holdings_and_replaces_before_unsubscribe():
    from investorch_qmt.market_data.subscription import SubscriptionManager
    from investorch_qmt.market_data.xtdata_adapter import XtDataAdapter

    class Api:
        def __init__(self):
            self.events = []

        def subscribe_whole_quote(self, symbols, callback):
            self.events.append(("subscribe", symbols))
            callback({"600519.SH": {"time": 1788849000000, "lastPrice": 123.5}})
            return len(self.events)

        def unsubscribe_quote(self, seq):
            self.events.append(("unsubscribe", seq))

        def get_full_tick(self, symbols):
            return {}

        def get_instrument_detail(self, symbol):
            return {"UpStopPrice": 135, "DownStopPrice": 110}

    api = Api()
    adapter = XtDataAdapter(api)
    manager = SubscriptionManager(adapter, {"600519.XSHG"})
    manager.update({"000001.XSHE"})
    manager.update(set())
    assert api.events == [("subscribe", ["000001.SZ", "600519.SH"]), ("subscribe", ["600519.SH"]), ("unsubscribe", 1)]
    assert adapter.last_price("600519.XSHG") == 123.5
    assert adapter.limits("600519.XSHG") == (135, 110)
    with pytest.raises(MarketDataError, match="MARKET_PRICE_UNAVAILABLE"):
        adapter.last_price("000001.XSHE")
    manager.close()
    assert api.events[-1] == ("unsubscribe", 2)


def test_liveness_detects_disconnect_without_waiting_for_quote_silence():
    from threading import Event

    from investorch_qmt.market_data.xtdata_adapter import XtDataAdapter

    disconnected = Event()
    finished = Event()

    class Api:
        def run(self):
            disconnected.wait(2)
            finished.set()
            raise OSError("lost connection")

    adapter = XtDataAdapter(Api())
    adapter.start_liveness()
    adapter.check_health()
    disconnected.set()
    assert finished.wait(2)
    assert adapter.failed.wait(2)
    with pytest.raises(MarketDataError, match="MARKET_DATA_DISCONNECTED"):
        adapter.check_health()


def test_trading_periods_include_opening_auction_and_closing_auction():
    from datetime import time

    from investorch_qmt.market_data.xtdata_adapter import XtDataAdapter

    class Api:
        def get_trading_period(self, symbol):
            return {
                "tradings": [
                    {"status": 2, "time": [0, [91500, 0], [92500, 0]]},
                    {"status": 3, "time": [0, [93000, 0], [113000, 0]]},
                    {"status": 3, "time": [0, [130000, 0], [145700, -1]]},
                    {"status": 8, "time": [0, [145700, 0], [150000, 0]]},
                ]
            }

    periods = XtDataAdapter(Api()).trading_periods("600519.XSHG")
    assert periods[0] == (time(9, 15), time(9, 25))
    assert periods[-1] == (time(14, 57), time(15))
