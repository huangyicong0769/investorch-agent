"""Explicit native/provider identity intersection, independent of cache freshness."""

from .errors import MarketDataError
from .symbols import to_xt_history_symbol


class HistoryCapabilities:
    def __init__(self, connection):
        self._connection = connection
        self._decisions = {}

    def inspect(self, instrument):
        key = (instrument.order_book_id, instrument.type)
        if key not in self._decisions:
            self._decisions[key] = self._inspect(instrument)
        return dict(self._decisions[key])

    def require_supported(self, instrument):
        decision = self.inspect(instrument)
        if not decision["supported"]:
            raise MarketDataError("FRESH_HISTORY_UNSUPPORTED", f"{instrument.order_book_id}: {decision['reason']}")
        return decision["provider_symbol"]

    def _inspect(self, instrument):
        try:
            symbol = to_xt_history_symbol(instrument)
        except MarketDataError as exc:
            if exc.code != "UNSUPPORTED_INSTRUMENT":
                raise
            return {"supported": False, "provider_symbol": None, "reason": "CANONICAL_MAPPING_UNAVAILABLE"}
        try:
            api = self._connection.connected_api()
            types = api.get_instrument_type(symbol)
            detail = api.get_instrument_detail(symbol)
            self._connection.check_health()
        except Exception as exc:
            raise MarketDataError("FRESH_HISTORY_NOT_READY", str(exc), transient=True) from exc
        if not isinstance(types, dict) or any(
            not isinstance(key, str) or type(value) is not bool for key, value in types.items()
        ):
            raise MarketDataError("FRESH_HISTORY_INVALID", "Malformed provider instrument-type metadata.")
        # Pinned xtdata returns None when the canonical identity lookup has no
        # entry; get_instrument_type may still classify that identifier as index.
        if detail is None:
            return {"supported": False, "provider_symbol": symbol, "reason": "PROVIDER_INSTRUMENT_UNKNOWN"}
        if not isinstance(detail, dict) or not all(
            isinstance(detail.get(field), str) and detail[field] for field in ("ExchangeID", "InstrumentID")
        ):
            raise MarketDataError("FRESH_HISTORY_INVALID", "Malformed provider instrument identity.")
        if types.get("stock") is True and types.get("index") is True:
            raise MarketDataError("FRESH_HISTORY_INVALID", "Ambiguous provider instrument type.")
        expected = "stock" if instrument.type == "CS" else "index"
        if types.get(expected) is not True:
            return {"supported": False, "provider_symbol": symbol, "reason": "PROVIDER_TYPE_MISMATCH"}
        if detail["ExchangeID"] != symbol[-2:] or detail["InstrumentID"] != symbol[:6]:
            return {"supported": False, "provider_symbol": symbol, "reason": "PROVIDER_IDENTITY_MISMATCH"}
        return {"supported": True, "provider_symbol": symbol, "reason": None}
