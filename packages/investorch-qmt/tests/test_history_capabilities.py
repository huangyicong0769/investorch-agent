from types import SimpleNamespace

from investorch_qmt.market_data.history_capabilities import HistoryCapabilities


def instrument(code="600000.XSHG", kind="CS"):
    return SimpleNamespace(order_book_id=code, type=kind)


class Connection:
    def __init__(self, types=None, detail=None):
        self.types = types if types is not None else {"stock": True}
        self.detail = detail if detail is not None else {"ExchangeID": "SH", "InstrumentID": "600000"}
        self.offline = False

    def connected_api(self):
        if self.offline:
            raise ConnectionError("provider metadata unavailable")
        return self

    def check_health(self):
        pass

    def get_instrument_type(self, code):
        return self.types

    def get_instrument_detail(self, code):
        return self.detail


def test_explicit_provider_identity_and_type_are_cached_without_bar_download():
    connection = Connection()
    capabilities = HistoryCapabilities(connection)
    assert capabilities.inspect(instrument()) == {
        "supported": True,
        "provider_symbol": "600000.SH",
        "reason": None,
    }
    connection.offline = True
    assert capabilities.require_supported(instrument()) == "600000.SH"


def test_no_canonical_mapping_is_explicit_exclusion_without_guessing_alias():
    connection = Connection()
    connection.offline = True
    capabilities = HistoryCapabilities(connection)
    assert capabilities.inspect(instrument("H30252.XSHG", "INDX")) == {
        "supported": False,
        "provider_symbol": None,
        "reason": "CANONICAL_MAPPING_UNAVAILABLE",
    }


def test_absent_registry_entry_is_excluded_but_metadata_failures_are_not():
    import pytest

    from investorch_qmt.market_data.errors import MarketDataError

    connection = Connection(types={})
    connection.detail = None
    capabilities = HistoryCapabilities(connection)
    assert capabilities.inspect(instrument())["reason"] == "PROVIDER_INSTRUMENT_UNKNOWN"
    connection.offline = True
    with pytest.raises(MarketDataError, match="FRESH_HISTORY_UNSUPPORTED"):
        capabilities.require_supported(instrument())

    connection = Connection()
    connection.types = {"stock": "true"}
    capabilities = HistoryCapabilities(connection)
    with pytest.raises(MarketDataError, match="FRESH_HISTORY_INVALID"):
        capabilities.inspect(instrument())
    connection.types = {"stock": True}
    assert capabilities.require_supported(instrument()) == "600000.SH"


def test_ambiguous_provider_type_cannot_be_treated_as_supported():
    import pytest

    from investorch_qmt.market_data.errors import MarketDataError

    connection = Connection(types={"stock": True, "index": True})
    with pytest.raises(MarketDataError, match="FRESH_HISTORY_INVALID"):
        HistoryCapabilities(connection).inspect(instrument())


def test_index_type_and_identity_must_both_match_native_metadata():
    connection = Connection(types={"index": True}, detail={"ExchangeID": "SZ", "InstrumentID": "399001"})
    assert HistoryCapabilities(connection).require_supported(instrument("399001.XSHE", "INDX")) == "399001.SZ"
    assert (
        HistoryCapabilities(connection).inspect(instrument("399001.XSHE", "CS"))["reason"] == "PROVIDER_TYPE_MISMATCH"
    )
    assert (
        HistoryCapabilities(connection).inspect(instrument("000001.XSHG", "INDX"))["reason"]
        == "PROVIDER_IDENTITY_MISMATCH"
    )
