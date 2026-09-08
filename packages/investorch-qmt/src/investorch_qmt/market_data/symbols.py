import re

from .errors import MarketDataError


def to_xt_symbol(order_book_id: str) -> str:
    if re.fullmatch(r"(600|601|603|605|688)\d{3}\.XSHG", order_book_id):
        return order_book_id[:6] + ".SH"
    if re.fullmatch(r"(000|001|002|003|300|301)\d{3}\.XSHE", order_book_id):
        return order_book_id[:6] + ".SZ"
    raise MarketDataError("UNSUPPORTED_INSTRUMENT", order_book_id)


def from_xt_symbol(symbol: str) -> str:
    exchange = {"SH": "XSHG", "SZ": "XSHE"}.get(symbol[-2:])
    candidate = symbol[:6] + "." + str(exchange)
    if to_xt_symbol(candidate) != symbol:
        raise MarketDataError("UNSUPPORTED_INSTRUMENT", symbol)
    return candidate
