from datetime import date

import numpy as np
from cnequity.config import Config
from cnequity.query import list_datasets, load
from rqalpha.const import INSTRUMENT_TYPE, MARKET
from rqalpha.data.base_data_source import BaseDataSource
from rqalpha.data.base_data_source.storage_interface import (
    AbstractDayBarStore,
    AbstractSimpleFactorStore,
)
from rqalpha.data.base_data_source.storages import SecuritiesDayBarStore
from rqalpha.utils.datetime_func import convert_date_to_int

_RQ_TO_CN_EXCHANGE = {"XSHG": "SH", "XSHE": "SZ"}
_DAY_BAR_DTYPE = SecuritiesDayBarStore.DEFAULT_DTYPE
_ADJ_FACTOR_DTYPE = np.dtype(
    [("start_date", np.int64), ("ex_cum_factor", np.float64)]
)


def _to_cnequity_symbol(order_book_id: str) -> str:
    code, separator, exchange = order_book_id.rpartition(".")
    if not separator or not code or exchange not in _RQ_TO_CN_EXCHANGE:
        raise ValueError(f"unsupported RQAlpha order_book_id: {order_book_id!r}")
    return f"{code}.{_RQ_TO_CN_EXCHANGE[exchange]}"


class CNEquityStockDayBarStore(AbstractDayBarStore):
    def __init__(
        self,
        config: Config,
        fallback: AbstractDayBarStore,
        is_suspended,
        end_date: date,
    ):
        self._config = config
        self._fallback = fallback
        self._is_suspended = is_suspended
        self._end_date = end_date
        self._bars: dict[str, np.ndarray] = {}
        datasets = {
            row["dataset"]: row
            for row in list_datasets(config=config).iter_rows(named=True)
        }
        daily_bars = datasets.get("daily_bars")
        if daily_bars is None:
            raise RuntimeError("CNEquity daily_bars is unavailable")
        self._coverage_start = daily_bars["coverage_start"]
        if self._coverage_start is None:
            raise RuntimeError("CNEquity daily_bars has no data coverage")

    def get_bars(self, order_book_id: str) -> np.ndarray:
        if order_book_id.rpartition(".")[2] not in _RQ_TO_CN_EXCHANGE:
            return self._fallback.get_bars(order_book_id)
        symbol = _to_cnequity_symbol(order_book_id)
        bars = self._bars.get(symbol)
        if bars is None:
            bars = self._load_bars(order_book_id, symbol)
            self._bars[symbol] = bars
        return bars

    def get_date_range(self, order_book_id: str) -> tuple[np.int64, np.int64]:
        if order_book_id.rpartition(".")[2] not in _RQ_TO_CN_EXCHANGE:
            return self._fallback.get_date_range(order_book_id)
        bars = self.get_bars(order_book_id)
        return bars["datetime"][0], bars["datetime"][-1]

    def _load_bars(self, order_book_id: str, symbol: str) -> np.ndarray:
        frame = load(
            "daily_bars",
            start=self._coverage_start,
            end=self._end_date,
            symbols=[symbol],
            config=self._config,
        )
        required = {"symbol", "trade_date", "open", "close", "high", "low", "volume", "amount"}
        missing = sorted(required - set(frame.columns))
        if missing:
            raise RuntimeError(
                f"CNEquity daily_bars for {symbol} is missing fields: {', '.join(missing)}"
            )
        if frame.is_empty():
            raise RuntimeError(f"CNEquity daily_bars has no bars for {symbol}")
        if set(frame["symbol"].to_list()) != {symbol}:
            raise RuntimeError(f"CNEquity daily_bars returned unexpected symbols for {symbol}")
        frame = frame.sort("trade_date")
        if frame["trade_date"].n_unique() != frame.height:
            raise RuntimeError(f"CNEquity daily_bars has duplicate dates for {symbol}")

        bars = np.empty(frame.height, dtype=_DAY_BAR_DTYPE)
        bars["datetime"] = np.fromiter(
            (convert_date_to_int(value) for value in frame["trade_date"]),
            dtype=np.int64,
            count=frame.height,
        )
        for source, target in (
            ("open", "open"),
            ("close", "close"),
            ("high", "high"),
            ("low", "low"),
            ("volume", "volume"),
            ("amount", "total_turnover"),
        ):
            try:
                values = frame[source].fill_null(float("nan")).to_numpy().astype(
                    np.float64, copy=False
                )
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"CNEquity daily_bars has invalid {source} values for {symbol}"
                ) from exc
            if source != "amount" and not np.all(np.isfinite(values)):
                raise RuntimeError(
                    f"CNEquity daily_bars has invalid {source} values for {symbol}"
                )
            bars[target] = values

        native = self._fallback.get_bars(order_book_id)
        positions = native["datetime"].searchsorted(bars["datetime"])
        matched = positions < len(native)
        matched[matched] &= native["datetime"][positions[matched]] == bars["datetime"][matched]
        if not np.all(matched):
            missing_date = str(int(bars["datetime"][np.flatnonzero(~matched)[0]]))[:8]
            raise RuntimeError(
                f"RQAlpha bundle has no stock bar for {order_book_id} on {missing_date}"
            )
        native_in_scope = native[
            (native["datetime"] >= convert_date_to_int(self._coverage_start))
            & (native["datetime"] <= convert_date_to_int(self._end_date))
        ]
        suspended = np.asarray(
            self._is_suspended(order_book_id, native_in_scope["datetime"]),
            dtype=np.bool_,
        )
        native_in_scope = native_in_scope[~suspended]
        cn_positions = bars["datetime"].searchsorted(native_in_scope["datetime"])
        cn_matched = cn_positions < len(bars)
        cn_matched[cn_matched] &= (
            bars["datetime"][cn_positions[cn_matched]]
            == native_in_scope["datetime"][cn_matched]
        )
        if not np.all(cn_matched):
            missing_date = str(
                int(native_in_scope["datetime"][np.flatnonzero(~cn_matched)[0]])
            )[:8]
            raise RuntimeError(
                f"CNEquity daily_bars has no normal trading bar for {symbol} on "
                f"{missing_date}"
            )
        bars["limit_up"] = native["limit_up"][positions]
        bars["limit_down"] = native["limit_down"][positions]
        return bars


class CNEquityExFactorStore(AbstractSimpleFactorStore):
    def __init__(
        self,
        config: Config,
        fallback: AbstractSimpleFactorStore,
        end_date: date,
    ):
        self._config = config
        self._fallback = fallback
        self._end_date = end_date
        self._factors: dict[str, np.ndarray] = {}
        datasets = {
            row["dataset"]: row
            for row in list_datasets(config=config).iter_rows(named=True)
        }
        adj_factors = datasets.get("adj_factors")
        if adj_factors is None:
            raise RuntimeError("CNEquity adj_factors is unavailable")
        self._coverage_start = adj_factors["coverage_start"]
        self._coverage_end = adj_factors["coverage_end"]
        if self._coverage_start is None or self._coverage_end is None:
            raise RuntimeError("CNEquity adj_factors has no data coverage")

    def get_factors(self, order_book_id: str) -> np.ndarray | None:
        if order_book_id.rpartition(".")[2] not in _RQ_TO_CN_EXCHANGE:
            return self._fallback.get_factors(order_book_id)
        symbol = _to_cnequity_symbol(order_book_id)
        factors = self._factors.get(symbol)
        if factors is None:
            factors = self._load_factors(symbol)
            self._factors[symbol] = factors
        return factors

    def _load_factors(self, symbol: str) -> np.ndarray:
        frame = load(
            "adj_factors",
            start=self._coverage_start,
            end=min(self._coverage_end, self._end_date),
            symbols=[symbol],
            config=self._config,
        )
        required = {"symbol", "trade_date", "adjust_type", "factor"}
        missing = sorted(required - set(frame.columns))
        if missing:
            raise RuntimeError(
                f"CNEquity adj_factors for {symbol} is missing fields: {', '.join(missing)}"
            )
        if not frame.is_empty() and set(frame["symbol"].to_list()) != {symbol}:
            raise RuntimeError(f"CNEquity adj_factors returned unexpected symbols for {symbol}")
        frame = frame.filter(frame["adjust_type"] == "hfq").sort("trade_date")
        if frame.is_empty():
            raise RuntimeError(f"CNEquity adj_factors has no hfq factors for {symbol}")

        factors = np.empty(frame.height, dtype=_ADJ_FACTOR_DTYPE)
        factors["start_date"] = np.fromiter(
            (convert_date_to_int(value) for value in frame["trade_date"]),
            dtype=np.int64,
            count=frame.height,
        )
        try:
            factors["ex_cum_factor"] = frame["factor"].to_numpy().astype(
                np.float64, copy=False
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"CNEquity adj_factors has invalid hfq factors for {symbol}"
            ) from exc
        if len(np.unique(factors["start_date"])) != len(factors):
            raise RuntimeError(f"CNEquity adj_factors has duplicate dates for {symbol}")
        if not np.all(np.isfinite(factors["ex_cum_factor"])) or np.any(
            factors["ex_cum_factor"] <= 0
        ):
            raise RuntimeError(f"CNEquity adj_factors has invalid hfq factors for {symbol}")
        return factors


class QMTDataSource(BaseDataSource):
    def __init__(self, base_config, cnequity_config: Config, end_date: date):
        super().__init__(base_config)
        native_stock_store = self._day_bar_stores[INSTRUMENT_TYPE.CS, MARKET.CN]
        native_factor_store = self._ex_factor_stores[INSTRUMENT_TYPE.CS, MARKET.CN]
        self.register_day_bar_store(
            INSTRUMENT_TYPE.CS,
            CNEquityStockDayBarStore(
                cnequity_config,
                native_stock_store,
                self.is_suspended,
                end_date,
            ),
        )
        self.register_ex_factor_store(
            INSTRUMENT_TYPE.CS,
            CNEquityExFactorStore(cnequity_config, native_factor_store, end_date),
        )
