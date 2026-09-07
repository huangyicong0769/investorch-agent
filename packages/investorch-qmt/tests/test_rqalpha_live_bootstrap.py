from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from rqalpha.const import POSITION_DIRECTION
from rqalpha.core.events import EVENT, Event
from rqalpha.environment import Environment
from rqalpha.mod import ModHandler
from rqalpha.model.instrument import Instrument
from rqalpha.portfolio import Portfolio
from rqalpha.utils.config import parse_config
from test_rqalpha_live_contracts import snapshot_wire

from investorch_qmt.rqalpha_live.bootstrap import build_live_config
from investorch_qmt.rqalpha_live.state import PortfolioSync


class DataProxy:
    """Only market metadata needed by the real native cold-start constructor."""

    def instrument(self, order_book_id):
        return Instrument({"order_book_id": order_book_id, "type": "CS"})

    instrument_not_none = instrument

    def get_instrument_history(self, order_book_id, dt):
        return [self.instrument(order_book_id)]

    def get_previous_trading_date(self, dt):
        return dt - timedelta(days=1)

    def get_last_price(self, order_book_id):
        return 20.0

    def get_bar(self, order_book_id, dt):
        return SimpleNamespace(close=20.0)


@pytest.fixture
def native_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    previous = Environment._env
    config = build_live_config(snapshot_wire(), {"lookback": 5, "nested": {"levels": [1, 2]}})
    config["mod"]["sys_analyser"] = {"enabled": False}
    config["base"].update({"start_date": "2026-09-07", "end_date": "2026-09-07"})
    config = parse_config(config, source_code="def init(context): pass")
    env = Environment(config, False)
    handler = ModHandler()
    handler.set_env(env)
    handler.start_up()
    env.set_data_proxy(DataProxy())
    env.trading_dt = datetime(2026, 9, 7)
    yield env
    Environment._env = previous


def test_mod_registers_cash_and_holdings_for_native_portfolio(native_env):
    env = native_env
    # The same constructor and POST_SYSTEM_INIT ordering as rqalpha.main.run.
    env.set_portfolio(Portfolio(env.config.base.accounts, env.config.base.init_positions, 0, env))
    env.event_bus.publish_event(Event(EVENT.POST_SYSTEM_INIT))
    assert env.portfolio.cash == 100000.0
    position = env.portfolio.get_position("600519.XSHG", POSITION_DIRECTION.LONG)
    assert position.quantity == 100
    assert position.avg_price == 20.0  # Native previous close, never historical Core cost.
    assert env.mod_dict["investorch_live"].state.portfolio_sync is PortfolioSync.SYNCED
    assert not env.mod_dict["investorch_live"].state.can_submit_new_order
    checkpoint = env.portfolio.get_state()
    env.portfolio.set_state(checkpoint)
    assert env.portfolio.cash == 100000.0
    assert env.portfolio.get_position("600519.XSHG", POSITION_DIRECTION.LONG).quantity == 100


def test_strategy_receives_nested_parameter_dictionary(native_env):
    from rqalpha.core.strategy import Strategy
    from rqalpha.core.strategy_context import StrategyContext

    env = native_env
    context = StrategyContext()
    for name, value in env.config.extra.context_vars.items():
        setattr(context, name, value)
    received = []

    def init(context):
        received.append(context.investorch_parameters["nested"]["levels"][1])
        assert not hasattr(context, "lookback")

    Strategy(env.event_bus, {"init": init}, context).init()
    assert received == [2]


def test_parameter_input_is_detached_and_empty_default_is_visible():
    import json

    params = {"nested": {"values": [1, {"enabled": True}]}}
    config = build_live_config(snapshot_wire(), params)
    params["nested"]["values"].clear()
    assert json.loads(config["extra"]["context_vars"]) == {
        "investorch_parameters": {"nested": {"values": [1, {"enabled": True}]}},
    }
    assert json.loads(build_live_config(snapshot_wire())["extra"]["context_vars"]) == {"investorch_parameters": {}}


@pytest.mark.parametrize("params", [{1: "value"}, {"x": (1, 2)}, {"x": float("nan")}, [], {"x": object()}])
def test_rejects_non_json_parameter_objects(params):
    with pytest.raises(ValueError, match="JSON"):
        build_live_config(snapshot_wire(), params)


@pytest.mark.parametrize("field,value", [("base_currency", "USD"), ("quantity", "1.5"), ("market", "XNYS")])
def test_native_bootstrap_fails_instead_of_dropping_unsupported_assets(field, value):
    wire = snapshot_wire()
    target = wire if field == "base_currency" else wire["positions"][0]
    target[field] = value
    with pytest.raises(ValueError):
        build_live_config(wire)


def test_mod_fails_closed_when_native_portfolio_does_not_match(native_env):
    env = native_env
    env.set_portfolio(Portfolio({"STOCK": 100}, [], 0, env))
    with pytest.raises(ValueError, match="does not match"):
        env.event_bus.publish_event(Event(EVENT.POST_SYSTEM_INIT))
    assert not env.mod_dict["investorch_live"].state.can_submit_new_order
    assert env.mod_dict["investorch_live"].state.portfolio_sync is PortfolioSync.DESYNCED
