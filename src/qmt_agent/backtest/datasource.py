from collections import defaultdict
from collections.abc import Iterable

from cnequity.config import Config
from cnequity.query import load
from rqalpha.const import INSTRUMENT_TYPE
from rqalpha.interface import AbstractDataSource
from rqalpha.model.instrument import Instrument

_CN_TO_RQ_EXCHANGE = {"SH": "XSHG", "SZ": "XSHE"}
_RQ_TO_CN_EXCHANGE = {value: key for key, value in _CN_TO_RQ_EXCHANGE.items()}
_BOARD_TYPE_BY_EXCHANGE_PREFIX = {
    ("SH", "600"): "MainBoard",
    ("SH", "601"): "MainBoard",
    ("SH", "603"): "MainBoard",
    ("SH", "605"): "MainBoard",
    ("SH", "688"): "KSH",
    ("SH", "510"): "ETF",
    ("SH", "511"): "ETF",
    ("SH", "512"): "ETF",
    ("SH", "513"): "ETF",
    ("SH", "515"): "ETF",
    ("SH", "516"): "ETF",
    ("SH", "517"): "ETF",
    ("SH", "518"): "ETF",
    ("SH", "519"): "ETF",
    ("SH", "520"): "ETF",
    ("SH", "526"): "ETF",
    ("SH", "560"): "ETF",
    ("SH", "561"): "ETF",
    ("SH", "562"): "ETF",
    ("SH", "563"): "ETF",
    ("SH", "588"): "ETF",
    ("SH", "589"): "ETF",
    ("SZ", "000"): "MainBoard",
    ("SZ", "001"): "MainBoard",
    ("SZ", "002"): "MainBoard",
    ("SZ", "003"): "MainBoard",
    ("SZ", "300"): "GEM",
    ("SZ", "301"): "GEM",
    ("SZ", "158"): "ETF",
    ("SZ", "159"): "ETF",
    ("SZ", "160"): "ETF",
    ("SZ", "161"): "ETF",
    ("SZ", "162"): "ETF",
    ("SZ", "163"): "ETF",
    ("SZ", "164"): "ETF",
    ("SZ", "165"): "ETF",
    ("SZ", "166"): "ETF",
    ("SZ", "167"): "ETF",
    ("SZ", "168"): "ETF",
    ("SZ", "169"): "ETF",
}


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


def _board_type(symbol: str, exchange: str) -> str:
    prefix = symbol.partition(".")[0][:3]
    try:
        return _BOARD_TYPE_BY_EXCHANGE_PREFIX[exchange, prefix]
    except KeyError as exc:
        raise ValueError(f"unsupported A-share board: {symbol!r}") from exc


class CNEquityDataSource(AbstractDataSource):
    def __init__(self, config: Config):
        rows = load("instruments", config=config)
        rows = rows.filter(
            (rows["asset_type"] == "stock")
            & rows["exchange"].is_in(_CN_TO_RQ_EXCHANGE)
            & rows["list_date"].is_not_null()
        )
        instruments = tuple(
            self._make_instrument(row)
            for row in rows.iter_rows(named=True)
            if (row["exchange"], row["symbol"][:3]) in _BOARD_TYPE_BY_EXCHANGE_PREFIX
        )
        self._instruments = instruments
        self._instruments_by_id = {instrument.order_book_id: instrument for instrument in instruments}
        self._instruments_by_name: dict[str, list[Instrument]] = defaultdict(list)
        for instrument in instruments:
            self._instruments_by_name[instrument.symbol].append(instrument)

    @staticmethod
    def _make_instrument(row: dict) -> Instrument:
        return Instrument(
            {
                "order_book_id": _to_rqalpha_order_book_id(row["symbol"]),
                "symbol": row["name"],
                "type": "CS",
                "exchange": _CN_TO_RQ_EXCHANGE[row["exchange"]],
                "listed_date": row["list_date"].isoformat(),
                "de_listed_date": row["delist_date"].isoformat() if row["delist_date"] else None,
                "round_lot": 100,
                "market_tplus": 1,
                "board_type": _board_type(row["symbol"], row["exchange"]),
            }
        )

    def get_instruments(
        self,
        id_or_syms: Iterable[str] | None = None,
        types: Iterable[INSTRUMENT_TYPE] | None = None,
    ) -> Iterable[Instrument]:
        if id_or_syms is not None:
            result = []
            seen = set()
            for identifier in id_or_syms:
                candidate = self._instruments_by_id.get(identifier)
                candidates = [candidate] if candidate else self._instruments_by_name.get(identifier, [])
                for instrument in candidates:
                    if instrument.order_book_id not in seen:
                        seen.add(instrument.order_book_id)
                        result.append(instrument)
            return result
        if types is not None:
            accepted = set(types)
            return (instrument for instrument in self._instruments if instrument.type in accepted)
        return self._instruments
