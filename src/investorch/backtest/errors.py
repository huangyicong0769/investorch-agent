"""Expected backtest data failures that can be explained to the user."""


class BacktestDataError(RuntimeError):
    """A required local data source is unavailable or fails its contract."""
