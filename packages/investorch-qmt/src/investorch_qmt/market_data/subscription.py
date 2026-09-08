from __future__ import annotations

from collections.abc import Iterable

from .xtdata_adapter import XtDataAdapter


class SubscriptionManager:
    def __init__(self, adapter: XtDataAdapter, holdings: Iterable[str]):
        self._adapter = adapter
        self._holdings = frozenset(holdings)
        self._desired: frozenset[str] = frozenset()
        self._sequence: int | None = None

    def update(self, universe: Iterable[str]) -> None:
        desired = frozenset(universe) | self._holdings
        if desired == self._desired:
            return
        sequence = self._adapter.subscribe(desired) if desired else None
        old = self._sequence
        self._sequence, self._desired = sequence, desired
        if old is not None:
            self._adapter.unsubscribe(old)

    def close(self) -> None:
        if self._sequence is not None:
            self._adapter.unsubscribe(self._sequence)
            self._sequence = None
        self._desired = frozenset()
