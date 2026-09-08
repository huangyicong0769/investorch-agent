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
        "unsubscribe_quote",
        "get_market_data",
        "get_full_tick",
        "get_instrument_detail",
        "get_trading_period",
    )
    for name in required:
        assert callable(getattr(xtdata, name, None)), name
    import_module("investorch_qmt.runtime.worker")
    import_module("investorch_qmt.market_data.xtdata_adapter")
    print("xtquant 250807.1.2: native imports and required API symbols passed; no connection attempted")


if __name__ == "__main__":
    main()
