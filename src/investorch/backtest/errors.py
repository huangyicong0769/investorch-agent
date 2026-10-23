"""Expected backtest failures that can be explained to the user."""


class BacktestDataError(RuntimeError):
    """A required local data source is unavailable or fails its contract."""


class BacktestStrategyError(RuntimeError):
    """A user strategy failed with a bounded, source-relative diagnostic."""
