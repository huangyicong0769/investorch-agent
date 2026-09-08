"""Live execution prices from the child-local MiniQMT adapter."""

from rqalpha.interface import AbstractPriceBoard


class LivePriceBoard(AbstractPriceBoard):
    def __init__(self, market):
        self.market = market

    def get_last_price(self, order_book_id):
        return self.market.last_price(order_book_id)

    def get_limit_up(self, order_book_id):
        return self.market.limits(order_book_id)[0]

    def get_limit_down(self, order_book_id):
        return self.market.limits(order_book_id)[1]
