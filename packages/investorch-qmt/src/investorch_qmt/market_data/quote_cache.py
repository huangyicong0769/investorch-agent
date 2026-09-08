from __future__ import annotations

import math
from threading import Event, Lock
from typing import Any

from .errors import MarketDataError
from .symbols import from_xt_symbol


class QuoteCache:
    def __init__(self):
        self._lock = Lock()
        self._quotes: dict[str, dict[str, Any]] = {}
        self.ready = Event()

    def update(self, quotes: dict[str, dict[str, Any]]) -> None:
        for symbol, quote in quotes.items():
            try:
                instrument = from_xt_symbol(symbol)
                timestamp = float(quote["time"])
                if not math.isfinite(timestamp) or timestamp <= 0:
                    continue
            except (MarketDataError, KeyError, TypeError, ValueError):
                continue
            with self._lock:
                previous = self._quotes.get(instrument)
                if previous is None or timestamp >= previous["time"]:
                    self._quotes[instrument] = dict(quote)
                    self.ready.set()

    def last_price(self, instrument: str) -> float:
        with self._lock:
            quote = self._quotes.get(instrument, {})
            try:
                price = float(quote["lastPrice"])
            except (KeyError, ValueError, TypeError):
                price = float("nan")
        if not math.isfinite(price) or price <= 0:
            raise MarketDataError("MARKET_PRICE_UNAVAILABLE", instrument)
        return price
