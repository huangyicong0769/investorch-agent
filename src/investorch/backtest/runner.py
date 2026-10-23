import math
import re
from contextlib import nullcontext
from datetime import date
from pathlib import Path
from threading import Lock

from rqalpha import run_file
from rqalpha.utils.exception import CustomException, is_user_exc

from investorch.config import AppConfig
from investorch.config_schema import BacktestRuntimeConfig

from .bundle import validate_rqalpha_bundle
from .errors import BacktestDataError, BacktestStrategyError
from .execution_log import NativeExecutionLog
from .parameters import resolve_backtest_parameters

_COVERED_DATASETS = ("daily_bars", "adj_factors")
# RQAlpha owns process-global Environment and function caches. run_file includes
# config loading, analyser teardown/result construction, and final cleanup.
_RQALPHA_LOCK = Lock()


def _strategy_failure(error: CustomException, config: AppConfig) -> BacktestStrategyError | None:
    cause = error.error.exc_val
    if not is_user_exc(cause):
        return None
    root = config.workspace_dir.expanduser().resolve()
    location = None
    # Read only filenames and line numbers, never RQAlpha's stack representation:
    # its repr includes source code and every local variable in strategy frames.
    for filename, lineno, *_ in error.error.stacks:
        try:
            relative = Path(filename).resolve().relative_to(root)
        except ValueError:
            continue
        location = f"{relative.as_posix()}:{lineno}"
    if location is None:
        return None
    message = error.error.msg or ""
    for secret in sorted(config.secret_values(), key=len, reverse=True):
        if secret:
            message = message.replace(secret, "<redacted>")
    message = re.sub(r"([\"'])(?:[A-Za-z]:[\\/]|/|\\\\|~/).*?\1", "<path>", message)
    message = re.sub(r"(?:[A-Za-z]:[\\/]|(?<!\w)/|\\\\|~/)[^\"'<>|,;)}\]\r\n]+", "<path>", message)
    message = "".join(char for char in message if char in "\n\t" or ord(char) >= 32)
    message = " ".join(message.split())[:1000]
    return BacktestStrategyError(f"Strategy error at {location}: {type(cause).__name__}: {message}")


def _validate_input(strategy_file: Path, start_date: date, end_date: date, initial_cash: float) -> Path:
    strategy_file = strategy_file.expanduser().resolve()
    if not strategy_file.is_file():
        raise FileNotFoundError(f"strategy file not found: {strategy_file}")
    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")
    if (
        isinstance(initial_cash, bool)
        or not isinstance(initial_cash, (int, float))
        or not math.isfinite(initial_cash)
        or initial_cash <= 0
    ):
        raise ValueError("initial_cash must be greater than zero")
    return strategy_file


def _validate_cnequity_data(config_path: Path, start_date: date, end_date: date) -> None:
    from cnequity.config import load_config as load_cnequity_config
    from cnequity.query import list_datasets

    if not config_path.is_file():
        raise FileNotFoundError(f"CNEquity config not found: {config_path}")
    config = load_cnequity_config(config_path)
    datasets = {row["dataset"]: row for row in list_datasets(config=config).iter_rows(named=True)}
    missing = sorted(set(_COVERED_DATASETS) - datasets.keys())
    if missing:
        raise BacktestDataError(f"CNEquity datasets are unavailable: {', '.join(missing)}")
    for dataset in _COVERED_DATASETS:
        coverage_start = datasets[dataset]["coverage_start"]
        coverage_end = datasets[dataset]["coverage_end"]
        if coverage_start is None or coverage_end is None:
            raise BacktestDataError(f"CNEquity {dataset} has no data coverage")
        if coverage_start > start_date:
            raise BacktestDataError(
                f"CNEquity {dataset} coverage starts at {coverage_start}, but backtest requests {start_date}"
            )
        if coverage_end < end_date:
            raise BacktestDataError(
                f"CNEquity {dataset} coverage ends at {coverage_end}, but backtest requests {end_date}"
            )


def _build_rqalpha_config(
    runtime: BacktestRuntimeConfig,
    cnequity_config_path: Path,
    rqalpha_bundle_path: Path,
    start_date: date,
    end_date: date,
    initial_cash: float,
    benchmark: str | None,
    use_cnequity: bool,
) -> dict:
    return {
        "extra": {"log_file": None},
        "base": {
            "data_bundle_path": str(rqalpha_bundle_path),
            "start_date": start_date,
            "end_date": end_date,
            "run_type": "b",
            "frequency": "1d",
            "accounts": {"stock": initial_cash},
            "capital_gain_tax_rate": runtime.capital_gain_tax_rate,
            "persist": False,
            "auto_update_bundle": False,
        },
        "mod": {
            "investorch_cnequity": {
                "enabled": use_cnequity,
                "lib": "investorch.backtest.rqalpha_mod",
                "cnequity_config_path": str(cnequity_config_path),
            },
            "sys_accounts": {
                "enabled": True,
                "stock_t1": runtime.stock_t1,
                "dividend_reinvestment": runtime.dividend_reinvestment,
                "dividend_tax_enabled": runtime.dividend_tax_enabled,
            },
            "sys_analyser": {
                "enabled": True,
                "benchmark": benchmark,
                "record": True,
                "plot": False,
            },
            "sys_progress": {"enabled": False},
            "sys_risk": {"enabled": True, "validate_price": runtime.validate_price},
            "sys_simulation": {
                "enabled": True,
                "matching_type": runtime.matching_type,
                "price_limit": runtime.price_limit,
                "volume_limit": runtime.volume_limit,
                "inactive_limit": runtime.inactive_limit,
                "slippage_model": runtime.slippage_model,
                "slippage": runtime.slippage,
            },
            "sys_transaction_cost": {
                "enabled": True,
                "stock_min_commission": runtime.stock_min_commission,
                "stock_commission_multiplier": runtime.stock_commission_multiplier,
                "tax_multiplier": runtime.tax_multiplier,
                "pit_tax": runtime.pit_tax,
            },
        },
    }


def run_backtest(
    config: AppConfig,
    strategy_file: Path,
    start_date: date,
    end_date: date,
    initial_cash: float,
    benchmark: str | None,
    runtime: BacktestRuntimeConfig | None = None,
    *,
    execution_log: NativeExecutionLog | None = None,
) -> dict:
    config = config.snapshot()
    if runtime is None:
        runtime = resolve_backtest_parameters(config)
    strategy_file = _validate_input(strategy_file, start_date, end_date, initial_cash)
    use_cnequity = config["backtest.use_cnequity"]
    cnequity_config_path = config.cnequity_config_path
    rqalpha_bundle_path = config.rqalpha_bundle_dir
    validate_rqalpha_bundle(rqalpha_bundle_path, start_date, end_date)
    if use_cnequity:
        _validate_cnequity_data(cnequity_config_path, start_date, end_date)
    with _RQALPHA_LOCK, execution_log.capture() if execution_log is not None else nullcontext():
        try:
            return run_file(
                str(strategy_file),
                config=_build_rqalpha_config(
                    runtime,
                    cnequity_config_path,
                    rqalpha_bundle_path,
                    start_date,
                    end_date,
                    initial_cash,
                    benchmark,
                    use_cnequity,
                ),
            )
        except CustomException as error:
            # RQAlpha wraps data-source failures raised during strategy execution.
            if isinstance(error.error.exc_val, BacktestDataError):
                raise error.error.exc_val from error
            strategy_failure = _strategy_failure(error, config)
            if strategy_failure is not None:
                raise strategy_failure from error
            raise
