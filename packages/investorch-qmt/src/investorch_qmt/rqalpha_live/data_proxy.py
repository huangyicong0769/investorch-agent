"""Native reference/history with an explicit current execution-day market view."""

from datetime import datetime

from rqalpha.const import INSTRUMENT_TYPE
from rqalpha.data.data_proxy import DataProxy
from rqalpha.model.bar import BarObject

from investorch_qmt.runtime.model import RuntimeFailure


class LiveDataProxy(DataProxy):
    def __init__(self, native, price_board, market):
        super().__init__(native, price_board)
        self.market = market
        self.current_day = None
        self.current_bars = {}
        self.finalized = False

    def validate_instruments(self, symbols):
        for symbol in symbols:
            instrument = self.instrument(symbol)
            if instrument is None or instrument.type != INSTRUMENT_TYPE.CS or not symbol.endswith((".XSHG", ".XSHE")):
                raise RuntimeFailure("UNSUPPORTED_INSTRUMENT", f"Live runtime requires XSHG/XSHE stock: {symbol}.")

    def require_fresh_history(self, day, symbols=()):
        previous = self.get_previous_trading_date(day).date()
        _, end = self.available_data_range("1d")
        if previous >= day or end < previous:
            raise RuntimeFailure("FRESH_HISTORY_NOT_READY", f"Completed daily history must cover {previous}.")
        for symbol in symbols:
            instrument = self.instrument_not_none(symbol)
            if not instrument.listed_date.date() <= previous < instrument.de_listed_date.date():
                continue
            rows = self._data_source.history_bars(
                instrument,
                1,
                "1d",
                "datetime",
                datetime.combine(previous, datetime.min.time()),
                skip_suspended=False,
                include_now=False,
                adjust_type="none",
            )
            if rows is None or len(rows) != 1 or int(rows[0]) // 1000000 != int(previous.strftime("%Y%m%d")):
                raise RuntimeFailure(
                    "FRESH_HISTORY_NOT_READY", f"Completed history for {symbol} must cover {previous}."
                )
        self.current_day = day
        self.current_bars = {}
        self.finalized = False

    def prepare_bars(self, symbols, day, finalized):
        # Commit the entire desired set atomically; partial final rows are never exposed.
        bars = {symbol: self.market.daily_bar(symbol, day) for symbol in symbols}
        self.current_day, self.current_bars, self.finalized = day, bars, finalized

    def get_bar(self, order_book_id, dt, frequency="1d"):
        day = dt.date() if isinstance(dt, datetime) else dt
        if day == self.current_day:
            if frequency != "1d" or order_book_id not in self.current_bars:
                raise RuntimeFailure("MARKET_DATA_INCOMPLETE", f"No current-day daily bar for {order_book_id}.")
            return BarObject(self.instrument_not_none(order_book_id), self.current_bars[order_book_id])
        return super().get_bar(order_book_id, dt, frequency)

    def is_suspended(self, order_book_id, dt, count=1):
        day = str(dt)[:10].replace("-", "")
        if self.current_day and day == self.current_day.strftime("%Y%m%d") and count == 1:
            if order_book_id not in self.current_bars:
                raise RuntimeFailure("MARKET_DATA_INCOMPLETE", "Current-day suspension state is unavailable.")
            return self.current_bars[order_book_id]["suspended"]
        return super().is_suspended(order_book_id, dt, count)

    def current_snapshot(self, order_book_id, frequency, dt):
        raise RuntimeFailure("LIVE_CURRENT_SNAPSHOT_UNSUPPORTED", "Live current_snapshot() is not supported.")
