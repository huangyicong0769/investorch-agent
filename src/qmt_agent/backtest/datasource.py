_CN_TO_RQ_EXCHANGE = {"SH": "XSHG", "SZ": "XSHE"}
_RQ_TO_CN_EXCHANGE = {value: key for key, value in _CN_TO_RQ_EXCHANGE.items()}


def _to_rqalpha_order_book_id(symbol: str) -> str:
    code, separator, exchange = symbol.rpartition(".")
    if not separator or not code or exchange not in _CN_TO_RQ_EXCHANGE:
        raise ValueError(f"unsupported CNEquity symbol: {symbol!r}")
    return f"{code}.{_CN_TO_RQ_EXCHANGE[exchange]}"


def _to_cnequity_symbol(order_book_id: str) -> str:
    code, separator, exchange = order_book_id.rpartition(".")
    if not separator or not code or exchange not in _RQ_TO_CN_EXCHANGE:
        raise ValueError(f"unsupported RQAlpha order_book_id: {order_book_id!r}")
    return f"{code}.{_RQ_TO_CN_EXCHANGE[exchange]}"
