"""Verify the installed Windows native SDK without connecting to a terminal."""

from importlib import import_module
from importlib.metadata import version


def main() -> None:
    assert version("xtquant") == "250807.1.2"
    from xtquant import xtdata

    required = (
        "connect",
        "disconnect",
        "run",
        "subscribe_whole_quote",
        "subscribe_quote",
        "unsubscribe_quote",
        "get_market_data",
        "get_full_tick",
        "get_instrument_detail",
        "get_instrument_type",
        "get_trading_period",
        "get_local_data",
        "download_history_data",
        "download_history_data2",
        "get_divid_factors",
    )
    for name in required:
        assert callable(getattr(xtdata, name, None)), name
    import_module("investorch_qmt.runtime.worker")
    import_module("investorch_qmt.market_data.xtdata_adapter")
    import_module("investorch_qmt.market_data.history_adapter")
    import_module("investorch_qmt.history.worker")
    print("xtquant 250807.1.2: native imports and required API symbols passed; no connection attempted")


if __name__ == "__main__":
    main()
