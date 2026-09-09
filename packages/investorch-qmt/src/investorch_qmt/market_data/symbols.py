import re

from .errors import MarketDataError


def to_xt_history_symbol(instrument) -> str:
    """Map native-authorized Shanghai/Shenzhen stock and index metadata."""
    code = instrument.order_book_id
    if instrument.type not in {"CS", "INDX"} or not re.fullmatch(r"\d{6}\.(XSHG|XSHE)", code):
        raise MarketDataError("UNSUPPORTED_INSTRUMENT", code)
    return code[:6] + (".SH" if code.endswith(".XSHG") else ".SZ")


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
