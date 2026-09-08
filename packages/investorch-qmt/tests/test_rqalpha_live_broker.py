from rqalpha.const import ORDER_STATUS, POSITION_EFFECT, SIDE
from rqalpha.core.events import EVENT, Event
from rqalpha.model.order import MarketOrder, Order
from rqalpha.portfolio import Portfolio
from test_rqalpha_live_bootstrap import native_env  # noqa: F401

from investorch_qmt.rqalpha_live.broker import TradingUnavailableBroker


def test_native_rejection_releases_frozen_cash_without_trade_or_position_change(native_env):  # noqa: F811
    env = native_env
    env.set_portfolio(Portfolio(env.config.base.accounts, env.config.base.init_positions, 0, env))
    env.event_bus.publish_event(Event(EVENT.POST_SYSTEM_INIT))
    broker = TradingUnavailableBroker(env)
    before = env.portfolio.cash
    positions = [(p.order_book_id, p.quantity) for p in env.portfolio.get_positions()]
    trades = []
    lifecycle = []
    env.event_bus.add_listener(EVENT.TRADE, lambda event: trades.append(event.trade))
    env.event_bus.add_listener(EVENT.ORDER_PENDING_NEW, lambda event: lifecycle.append(event.event_type))
    env.event_bus.add_listener(EVENT.ORDER_UNSOLICITED_UPDATE, lambda event: lifecycle.append(event.event_type))
    order = Order.__from_create__("600519.XSHG", 100, SIDE.BUY, MarketOrder(), POSITION_EFFECT.OPEN)
    order.set_frozen_price(20)
    broker.submit_order(order)
    assert order.status is ORDER_STATUS.REJECTED
    assert order.message == "TRADING_BACKEND_NOT_READY"
    assert lifecycle == [EVENT.ORDER_PENDING_NEW, EVENT.ORDER_UNSOLICITED_UPDATE]
    assert env.portfolio.cash == before
    assert [(p.order_book_id, p.quantity) for p in env.portfolio.get_positions()] == positions
    assert trades == []
    assert broker.get_open_orders() == []
