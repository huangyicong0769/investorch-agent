from __future__ import annotations

import math
import re
from collections.abc import Iterable
from contextlib import suppress
from datetime import date, datetime, time
from importlib.metadata import version
from threading import Event, Thread
from typing import Any
from zoneinfo import ZoneInfo

from .errors import MarketDataError
from .quote_cache import QuoteCache
from .symbols import to_xt_symbol

SHANGHAI = ZoneInfo("Asia/Shanghai")
FIELDS = ("time", "open", "high", "low", "close", "volume", "amount", "preClose", "suspendFlag")


class XtDataAdapter:
    def __init__(self, api: Any = None):
        self._api = api
        self.cache = QuoteCache()
        self.failed = Event()
        self._closed = Event()
        self._thread: Thread | None = None
        self._client: Any = None
        self._failure = ""
        self._original_get_client = None
        self._subscriptions: set[int] = set()
        self._subscription_groups: dict[int, tuple[int, ...]] = {}

    def daily_bar(self, order_book_id: str, trading_date: date) -> dict[str, Any]:
        symbol = to_xt_symbol(order_book_id)
        day = trading_date.strftime("%Y%m%d")
        try:
            self.check_health()
            data = self._api.get_market_data(
                field_list=list(FIELDS),
                stock_list=[symbol],
                period="1d",
                start_time=day,
                end_time=day,
                count=-1,
                dividend_type="none",
                fill_data=False,
            )
            row = {}
            for field in FIELDS:
                series = data[field].loc[symbol]
                if not series.index.equals(data["time"].columns):
                    raise ValueError("Daily fields have inconsistent date columns")
                if len(series) != 1:
                    raise ValueError("Expected exactly one current-day row")
                row[field] = float(series.iloc[0])
            if not all(math.isfinite(value) for value in row.values()):
                raise ValueError("Nonfinite daily field")
            if datetime.fromtimestamp(row["time"] / 1000, SHANGHAI).date() != trading_date:
                raise ValueError("Daily row has a different trading date")
            if row["suspendFlag"] not in (-1, 0, 1):
                raise ValueError("Invalid suspension flag")
            suspended = row["suspendFlag"] == 1
            if row["volume"] < 0 or row["amount"] < 0 or row["preClose"] <= 0:
                raise ValueError("Invalid volume/turnover/previous close")
            prices = [row[field] for field in ("open", "high", "low", "close")]
            if suspended:
                if row["volume"] != 0 or row["amount"] != 0 or min(prices) < 0:
                    raise ValueError("Suspended row carries trades or invalid prices")
            elif min(prices) <= 0 or row["high"] < max(prices) or row["low"] > min(prices):
                raise ValueError("Invalid active daily prices")
            return {
                "datetime": int(day) * 1000000,
                **{field: row[field] for field in ("open", "high", "low", "close")},
                # Official native daily example: amount/volume implies 100-share hands.
                # https://dict.thinktrader.net/dictionary/stock.html
                "volume": row["volume"] * 100,
                "total_turnover": row["amount"],
                "prev_close": row["preClose"],
                "suspended": suspended,
            }
        except MarketDataError:
            raise
        except Exception as exc:
            raise MarketDataError("MARKET_DATA_INCOMPLETE", str(exc)) from exc

    def subscribe(self, instruments: Iterable[str]) -> int:
        symbols = sorted(to_xt_symbol(item) for item in instruments)
        self.check_health()
        allocated = []

        def track(sequence):
            if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= 0:
                raise ValueError("Subscription failed")
            allocated.append(sequence)
            self._subscriptions.add(sequence)
            return sequence

        try:
            whole = track(self._api.subscribe_whole_quote(symbols, callback=self.cache.update))
            for symbol in symbols:
                # A whole-quote feed alone does not populate the server's daily cache.
                # One initial row plus the ongoing daily subscription is sufficient;
                # open start/end retain next-day updates, and queries still require exact D.
                track(
                    self._api.subscribe_quote(symbol, period="1d", start_time="", end_time="", count=1, callback=None)
                )
            self.cache.update(self._api.get_full_tick(symbols))
            self._subscription_groups[whole] = tuple(allocated)
            return whole
        except Exception as exc:
            for sequence in allocated:
                with suppress(Exception):
                    self.unsubscribe(sequence)
            if isinstance(exc, MarketDataError):
                raise
            raise MarketDataError("MARKET_DATA_NOT_READY", str(exc), transient=True) from exc

    def unsubscribe(self, sequence: int) -> None:
        self.check_health()
        group = self._subscription_groups.get(sequence, (sequence,))
        for member in group:
            if member in self._subscriptions:
                self._api.unsubscribe_quote(member)
                self._subscriptions.discard(member)
        self._subscription_groups.pop(sequence, None)

    def instrument_detail(self, instrument: str) -> dict:
        self.check_health()
        detail = self._api.get_instrument_detail(to_xt_symbol(instrument))
        if not detail:
            raise MarketDataError("MARKET_DATA_NOT_READY", instrument, transient=True)
        return detail

    def last_price(self, instrument: str) -> float:
        to_xt_symbol(instrument)
        self.check_health()
        return self.cache.last_price(instrument)

    def limits(self, instrument: str) -> tuple[float, float]:
        detail = self.instrument_detail(instrument)
        try:
            up, down = float(detail["UpStopPrice"]), float(detail["DownStopPrice"])
            if not all(math.isfinite(value) and value > 0 for value in (up, down)) or up < down:
                raise ValueError("Invalid price limits")
            return up, down
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketDataError("MARKET_PRICE_UNAVAILABLE", instrument) from exc

    def connect(self) -> None:
        try:
            if self._api is None:
                if version("xtquant") != "250807.1.2":
                    raise ValueError("xtquant must be exactly 250807.1.2")
                from xtquant import xtdata

                self._api = xtdata
            for name in (
                "connect",
                "run",
                "subscribe_whole_quote",
                "subscribe_quote",
                "unsubscribe_quote",
                "get_market_data",
                "get_full_tick",
                "get_instrument_detail",
                "get_trading_period",
                "disconnect",
                "get_client",
            ):
                if not callable(getattr(self._api, name, None)):
                    raise ValueError(f"Missing xtdata API: {name}")
            self._client = self._api.connect()
            if self._client is None or not self._client.is_connected():
                raise ValueError("xtdata connection unavailable")
            # The pinned SDK reconnects in get_client(). A worker must retain the
            # original connection for its entire lifetime, including API-call races.
            self._original_get_client = self._api.get_client
            self._api.get_client = self._connected_client
        except Exception as exc:
            raise MarketDataError("MARKET_DATA_NOT_READY", str(exc), transient=True) from exc

    def _connected_client(self):
        self.check_health()
        return self._client

    def start_liveness(self) -> None:
        if self._thread is not None:
            return

        def run():
            try:
                self._api.run()
                if not self._closed.is_set():
                    self._failure = "xtdata.run returned unexpectedly"
                    self.failed.set()
            except Exception as exc:
                if not self._closed.is_set():
                    self._failure = str(exc)
                    self.failed.set()

        self._thread = Thread(target=run, name="xtdata-liveness", daemon=True)
        self._thread.start()

    def check_health(self) -> None:
        # Keep the original client reference: get_client() would auto-reconnect.
        if self.failed.is_set() or (self._client is not None and not self._client.is_connected()):
            raise MarketDataError("MARKET_DATA_DISCONNECTED", self._failure)
        if self._closed.is_set():
            raise MarketDataError("MARKET_DATA_DISCONNECTED", "Adapter is closed")

    def close(self) -> None:
        if self._closed.is_set():
            return
        for sequence in tuple(self._subscriptions):
            with suppress(Exception):
                self.unsubscribe(sequence)
        self._closed.set()
        try:
            if self._client is not None:
                self._api.disconnect()
        finally:
            if self._thread is not None:
                self._thread.join(timeout=4)
            # Never restore reconnecting access while run() could still enter it.
            if self._original_get_client is not None and (self._thread is None or not self._thread.is_alive()):
                self._api.get_client = self._original_get_client
                self._original_get_client = None

    def trading_periods(self, instrument: str) -> tuple[tuple[time, time], ...]:
        self.check_health()
        try:
            symbol = to_xt_symbol(instrument)
            try:
                metadata = self._api.get_trading_period(symbol)
            except RuntimeError as exc:
                # Broker MiniQMT may lack this newer SDK metadata endpoint (300000).
                # Use the exchanges' stock auction schedule only for that capability error.
                # SSE 2026 trading rules 2.4.2 and SZSE trading-time guidance:
                # https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml
                # https://www.szse.cn/www/investor/knowledge/stock/deal/t20191204_572383.html
                if re.search(r'"ErrorID"\s*:\s*300000\b', str(exc)) and "function not realize" in str(exc):
                    self.check_health()
                    return (
                        (time(9, 15), time(9, 25)),
                        (time(9, 30), time(11, 30)),
                        (time(13), time(14, 57)),
                        (time(14, 57), time(15)),
                    )
                raise
            periods = []
            for period in metadata["tradings"]:
                if period["status"] not in (2, 3, 8):
                    continue
                offset, begin, end = period["time"]
                if offset != 0:
                    raise ValueError("Unexpected stock trading-day offset")

                def parse(value):
                    return time(value // 10000, (value // 100) % 100, value % 100)

                start, stop = parse(begin[0]), parse(end[0])
                if start >= stop:
                    raise ValueError("Invalid trading period")
                periods.append((start, stop))
            if not periods:
                raise ValueError("Missing stock trading periods")
            return tuple(sorted(periods))
        except MarketDataError:
            raise
        except Exception as exc:
            raise MarketDataError("MARKET_DATA_NOT_READY", str(exc), transient=True) from exc
