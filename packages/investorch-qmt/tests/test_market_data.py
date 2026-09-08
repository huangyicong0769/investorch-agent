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


def test_explicit_suspension_is_distinct_from_missing_or_malformed_daily_data():
    from datetime import date

    import pandas as pd

    from investorch_qmt.market_data.xtdata_adapter import XtDataAdapter

    row = dict(time=1788796800000, open=0, high=0, low=0, close=0, volume=0, amount=0, preClose=98, suspendFlag=1)

    class Api:
        def get_market_data(self, **kwargs):
            return {
                key: pd.DataFrame([[value]], index=["600519.SH"], columns=["20260908"]) for key, value in row.items()
            }

    adapter = XtDataAdapter(Api())
    assert adapter.daily_bar("600519.XSHG", date(2026, 9, 8))["suspended"] is True
    row["suspendFlag"] = 0
    with pytest.raises(MarketDataError, match="MARKET_DATA_INCOMPLETE"):
        adapter.daily_bar("600519.XSHG", date(2026, 9, 8))
    row["suspendFlag"] = 1
    row["volume"] = 5
    with pytest.raises(MarketDataError, match="MARKET_DATA_INCOMPLETE"):
        adapter.daily_bar("600519.XSHG", date(2026, 9, 8))
    row.clear()
    with pytest.raises(MarketDataError, match="MARKET_DATA_INCOMPLETE"):
        adapter.daily_bar("600519.XSHG", date(2026, 9, 8))


def test_quote_cache_rejects_invalid_latest_prices_and_ignores_out_of_order_quotes():
    from investorch_qmt.market_data.quote_cache import QuoteCache

    cache = QuoteCache()
    cache.update({"600519.SH": {"time": 2000, "lastPrice": 123}})
    cache.update({"600519.SH": {"time": 1000, "lastPrice": 120}})
    assert cache.last_price("600519.XSHG") == 123
    cache.update({"600519.SH": {"time": 3000, "lastPrice": float("nan")}})
    with pytest.raises(MarketDataError, match="MARKET_PRICE_UNAVAILABLE"):
        cache.last_price("600519.XSHG")


def test_subscription_failure_keeps_old_subscription_and_cleans_new_sequence():
    from investorch_qmt.market_data.subscription import SubscriptionManager
    from investorch_qmt.market_data.xtdata_adapter import XtDataAdapter

    class Api:
        def __init__(self):
            self.active = set()
            self.next = 0

        def subscribe_whole_quote(self, symbols, callback):
            self.next += 1
            self.active.add(self.next)
            return self.next

        def get_full_tick(self, symbols):
            if self.next > 1:
                raise OSError("market not ready")
            return {}

        def unsubscribe_quote(self, seq):
            self.active.remove(seq)

    api = Api()
    manager = SubscriptionManager(XtDataAdapter(api), set())
    manager.update({"600519.XSHG"})
    with pytest.raises(MarketDataError, match="MARKET_DATA_NOT_READY"):
        manager.update({"000001.XSHE"})
    assert api.active == {1}


def test_sdk_disconnect_race_never_implicitly_reconnects_and_binding_is_restored():
    from investorch_qmt.market_data.xtdata_adapter import XtDataAdapter

    class Client:
        connected = True

        def is_connected(self):
            return self.connected

    class Api:
        def __init__(self):
            self.client = Client()
            self.connects = 0

        def connect(self):
            self.connects += 1
            self.client.connected = True
            return self.client

        def get_client(self):
            if not self.client.is_connected():
                self.connect()
            return self.client

        def get_instrument_detail(self, symbol):
            self.client.connected = False  # connection loss after adapter health precheck
            self.get_client()
            return {"UpStopPrice": 110, "DownStopPrice": 90}

        def disconnect(self):
            self.client.connected = False

        def run(self):
            pass

        subscribe_whole_quote = unsubscribe_quote = get_market_data = get_full_tick = get_trading_period = run

    api = Api()
    original = api.get_client
    adapter = XtDataAdapter(api)
    adapter.connect()
    with pytest.raises(MarketDataError, match="MARKET_DATA_DISCONNECTED"):
        adapter.instrument_detail("600519.XSHG")
    assert api.connects == 1
    adapter.close()
    assert api.get_client == original


def test_official_native_daily_example_maps_hands_to_rqalpha_shares():
    from datetime import date

    import pandas as pd

    from investorch_qmt.market_data.xtdata_adapter import XtDataAdapter

    # https://dict.thinktrader.net/dictionary/stock.html, native Python daily example.
    # 691423400 / 68469500 = 10.0983 yuan/share, within that day's 10.08-10.13 range.
    row = dict(
        time=1700755200000,
        open=10.11,
        high=10.13,
        low=10.08,
        close=10.10,
        volume=684695,
        amount=691423400,
        preClose=10.15,
        suspendFlag=0,
    )

    class Api:
        def get_market_data(self, **kwargs):
            return {
                key: pd.DataFrame([[value]], index=["000001.SZ"], columns=["20231124"]) for key, value in row.items()
            }

    bar = XtDataAdapter(Api()).daily_bar("000001.XSHE", date(2023, 11, 24))
    assert bar["volume"] == 68469500
    assert bar["total_turnover"] == 691423400
    assert bar["prev_close"] == 10.15
    assert bar["low"] <= bar["total_turnover"] / bar["volume"] <= bar["high"]


def test_daily_fields_cannot_mix_different_date_columns():
    from datetime import date

    import pandas as pd

    from investorch_qmt.market_data.xtdata_adapter import XtDataAdapter

    class Api:
        def get_market_data(self, **kwargs):
            row = dict(
                time=1788796800000,
                open=100,
                high=105,
                low=99,
                close=104,
                volume=123,
                amount=1234500,
                preClose=98,
                suspendFlag=0,
            )
            return {
                key: pd.DataFrame(
                    [[value]], index=["600519.SH"], columns=["20260907" if key == "close" else "20260908"]
                )
                for key, value in row.items()
            }

    with pytest.raises(MarketDataError, match="MARKET_DATA_INCOMPLETE"):
        XtDataAdapter(Api()).daily_bar("600519.XSHG", date(2026, 9, 8))
