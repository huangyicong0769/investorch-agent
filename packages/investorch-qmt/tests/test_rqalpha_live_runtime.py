import json
import pickle
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import h5py
import numpy as np
import pytest
from rqalpha.data.base_data_source.storages import SecuritiesDayBarStore
from test_rqalpha_live_contracts import snapshot_wire
from test_rqalpha_live_events import SH, Clock, Control

from investorch_qmt.rqalpha_live.runtime import run_live


@pytest.fixture
def bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.chdir(tmp_path)
    path = tmp_path / ".rqalpha" / "bundle"
    path.mkdir(parents=True)
    (path / "future_info.json").write_text(
        json.dumps([{"underlying_symbol": "IF", "commission_type": "by_money", "margin_rate": 0.1}])
    )
    (path / "share_transformation.json").write_text("{}")
    instruments = [
        {
            "order_book_id": code,
            "symbol": code,
            "type": kind,
            "listed_date": "2000-01-01",
            "de_listed_date": "0000-00-00",
            "round_lot": 100,
            "exchange": "XSHG",
            "board_type": "MainBoard",
        }
        for code, kind in [("600519.XSHG", "CS"), ("000001.XSHG", "INDX")]
    ]
    (path / "instruments.pk").write_bytes(pickle.dumps(instruments))
    np.save(path / "trading_dates.npy", np.array([20260904, 20260907, 20260908, 20260909]))
    bars = np.array(
        [(20260904000000, 20, 20, 21, 19, 1000, 20000, 22, 18), (20260907000000, 20, 21, 22, 19, 1000, 21000, 22, 18)],
        dtype=SecuritiesDayBarStore.DEFAULT_DTYPE,
    )
    for name in [
        "stocks",
        "indexes",
        "funds",
        "futures",
        "yield_curve",
        "suspended_days",
        "st_stock_days",
        "dividends",
        "split_factor",
        "ex_cum_factor",
    ]:
        with h5py.File(path / f"{name}.h5", "w") as store:
            if name == "yield_curve":
                store.create_dataset("data", data=np.array([(20260907, 0.02)], dtype=[("date", "i8"), ("1Y", "f8")]))
            if name in {"stocks", "indexes"}:
                store.create_dataset("600519.XSHG" if name == "stocks" else "000001.XSHG", data=bars)
    return path


class Market:
    def __init__(self, clock):
        self.clock = clock
        self.prices = 25
        self.closed = False
        self.subscriptions = []

    def connect(self):
        pass

    def start_liveness(self):
        pass

    def check_health(self):
        pass

    def close(self):
        self.closed = True

    def last_price(self, code):
        return self.prices

    def limits(self, code):
        return 30, 10

    def trading_periods(self, code):
        from datetime import time

        return ((time(9, 30), time(11, 30)), (time(13), time(15)))

    def instrument_detail(self, code):
        return {"UpStopPrice": 30, "DownStopPrice": 10}

    def subscribe(self, symbols):
        self.subscriptions.append(set(symbols))
        return len(self.subscriptions)

    def unsubscribe(self, seq):
        pass

    def daily_bar(self, code, day):
        return dict(
            datetime=int(day.strftime("%Y%m%d")) * 1000000,
            open=22,
            high=30,
            low=20,
            close=self.prices,
            volume=1000,
            total_turnover=25000,
            prev_close=21,
            suspended=False,
        )


@pytest.mark.parametrize("live_price", [25, 29])
def test_real_strategy_freezes_d_minus_one_signal_and_rejects_order_without_trade(bundle, tmp_path, live_price):
    output = tmp_path / "observations.json"
    source = """
import json
from rqalpha.core.events import EVENT
from rqalpha.environment import Environment

def init(context):
    context.events = ['init']
    context.trades = []
    subscribe('600519.XSHG')
    env = Environment.get_instance()
    env.event_bus.add_listener(EVENT.TRADE,lambda event: context.trades.append(event.trade))
    env.event_bus.add_listener(EVENT.POST_SETTLEMENT,lambda event: save(context))

def before_trading(context):
    context.events.append('before')
    bars = history_bars('600519.XSHG',1,'1d',['datetime','close'])
    context.signal = [int(bars[0]['datetime']),float(bars[0]['close'])]

def handle_bar(context,bars):
    context.events.append('bar')
    context.current_price = bars['600519.XSHG'].close
    order = order_shares('600519.XSHG',100)
    context.order_status = order.status.name
    context.order_message = order.message

def after_trading(context):
    context.events.append('after')
    context.open_orders = len(get_open_orders())

def save(context):
    context.events.append('settlement')
    with open(context.investorch_parameters['output'],'w') as f:
        json.dump(dict(events=context.events,signal=context.signal,price=context.current_price,
                       status=context.order_status,message=context.order_message,trades=len(context.trades),
                       cash=context.portfolio.cash,quantity=context.portfolio.positions['600519.XSHG'].quantity,
                       open_orders=context.open_orders),f)
"""
    clock, control = Clock(datetime(2026, 9, 8, 9, 29, tzinfo=SH)), Control()
    market = Market(clock)
    market.prices = live_price

    def stop_after_day(clock):
        if output.exists():
            control.stopped.set()

    clock.on_wait = stop_after_day
    artifacts = SimpleNamespace(
        source=source.encode(),
        bootstrap=snapshot_wire(),
        manifest={"strategy_parameters": {"output": str(output)}},
        deployment_dir=tmp_path,
    )
    statuses = []
    run_live(
        artifacts,
        control,
        lambda phase, **kw: statuses.append(phase),
        market=market,
        clock=clock,
        history=SimpleNamespace(get_dividend_factors=lambda *_: ()),
    )
    observed = json.loads(output.read_text())
    assert observed == dict(
        events=["init", "before", "bar", "after", "settlement"],
        signal=[20260907000000, 21.0],
        price=live_price,
        status="REJECTED",
        message="TRADING_BACKEND_NOT_READY",
        trades=0,
        cash=100000.0,
        quantity=100,
        open_orders=0,
    )
    assert statuses[0:2] == ["READY", "RUNNING"]
    assert market.closed


def artifacts_for(source, path):
    return SimpleNamespace(
        source=source.encode(), bootstrap=snapshot_wire(), manifest={"strategy_parameters": {}}, deployment_dir=path
    )


def test_stale_native_bundle_fails_before_strategy_before_trading(bundle, tmp_path):
    from investorch_qmt.runtime.model import RuntimeFailure

    with h5py.File(bundle / "indexes.h5", "a") as store:
        del store["000001.XSHG"]
        store.create_dataset(
            "000001.XSHG",
            data=np.array(
                [(20260904000000, 20, 20, 21, 19, 1000, 20000, 22, 18)], dtype=SecuritiesDayBarStore.DEFAULT_DTYPE
            ),
        )
    clock, control = Clock(datetime(2026, 9, 8, 9, 29, tzinfo=SH)), Control()
    source = "def before_trading(context): raise AssertionError('must never run')"
    with pytest.raises(RuntimeFailure) as error:
        run_live(
            artifacts_for(source, tmp_path), control, lambda *args, **kwargs: None, market=Market(clock), clock=clock
        )
    assert error.value.code == "FRESH_HISTORY_NOT_READY"


def test_current_snapshot_rejects_in_real_strategy_instead_of_native_fallback(bundle, tmp_path):
    from investorch_qmt.runtime.model import RuntimeFailure

    clock, control = Clock(datetime(2026, 9, 8, 9, 29, tzinfo=SH)), Control()
    source = "def handle_bar(context,bars): current_snapshot('600519.XSHG')"
    with pytest.raises(RuntimeFailure) as error:
        run_live(
            artifacts_for(source, tmp_path), control, lambda *args, **kwargs: None, market=Market(clock), clock=clock
        )
    assert error.value.code == "LIVE_CURRENT_SNAPSHOT_UNSUPPORTED"


def test_live_suspended_bar_uses_current_day_state_in_native_bar_object(bundle, tmp_path):
    output = tmp_path / "suspended.json"
    clock, control = Clock(datetime(2026, 9, 8, 9, 29, tzinfo=SH)), Control()

    class SuspendedMarket(Market):
        def daily_bar(self, code, day):
            bar = super().daily_bar(code, day)
            bar.update(suspended=True, volume=0, total_turnover=0)
            return bar

    clock.on_wait = lambda _: control.stopped.set() if output.exists() else None
    source = f"""
import json
def handle_bar(context,bars):
    bar = bars['600519.XSHG']
    with open({str(output)!r},'w') as f:
        json.dump(dict(suspended=bar.suspended,volume=bar.volume,close=bar.close),f)
"""
    run_live(
        artifacts_for(source, tmp_path),
        control,
        lambda *args, **kwargs: None,
        market=SuspendedMarket(clock),
        clock=clock,
    )
    assert json.loads(output.read_text()) == {"suspended": True, "volume": 0, "close": 25}


def test_non_trading_day_start_waits_after_ready_without_synthetic_callbacks(bundle, tmp_path):
    clock, control = Clock(datetime(2026, 9, 6, 12, tzinfo=SH)), Control()
    statuses = []

    def report(phase, **kwargs):
        statuses.append(phase)
        if phase == "RUNNING":
            control.stopped.set()

    source = "def before_trading(context): raise AssertionError('weekend callback')"
    run_live(artifacts_for(source, tmp_path), control, report, market=Market(clock), clock=clock)
    assert statuses == ["READY", "RUNNING"]


def test_start_waits_for_a_usable_quote_for_nonempty_desired_subscription(bundle, tmp_path):
    from investorch_qmt.market_data.errors import MarketDataError
    from investorch_qmt.runtime.model import RuntimeFailure

    class MissingQuoteMarket(Market):
        def last_price(self, code):
            raise MarketDataError("MARKET_PRICE_UNAVAILABLE")

    clock, control = Clock(datetime(2026, 9, 8, 9, 29, tzinfo=SH)), Control()
    statuses = []
    with pytest.raises(RuntimeFailure) as error:
        run_live(
            artifacts_for("def init(context): pass", tmp_path),
            control,
            lambda phase, **kwargs: statuses.append(phase),
            market=MissingQuoteMarket(clock),
            clock=clock,
        )
    assert error.value.code == "MARKET_PRICE_UNAVAILABLE"
    assert error.value.retryable
    assert "READY" not in statuses
