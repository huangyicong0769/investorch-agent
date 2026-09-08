"""RQAlpha rejection lifecycle with no trading backend or executions."""

from rqalpha.core.events import EVENT, Event
from rqalpha.interface import AbstractBroker


class TradingUnavailableBroker(AbstractBroker):
    def __init__(self, env):
        self._env = env

    def submit_order(self, order):
        account = self._env.get_account(order.order_book_id)
        # Native Account freezes funds on PENDING_NEW and releases them on UPDATE.
        self._env.event_bus.publish_event(Event(EVENT.ORDER_PENDING_NEW, account=account, order=order))
        order.mark_rejected("TRADING_BACKEND_NOT_READY")
        self._env.event_bus.publish_event(Event(EVENT.ORDER_UNSOLICITED_UPDATE, account=account, order=order))

    def cancel_order(self, order):
        # Every submitted order is already terminal; there is nothing to cancel.
        return None

    def get_open_orders(self, order_book_id=None):
        return []
