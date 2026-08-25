import math
import warnings
from datetime import date
from pathlib import Path

from cnequity.config import load_config as load_cnequity_config
from cnequity.query import list_datasets, load
from rqalpha import run_file

from qmt_agent.config import load_config as load_app_config

_COVERED_DATASETS = ("trading_calendar", "daily_bars", "trading_status", "adj_factors")
_PHASE0_WARNING = """RQAlpha Phase 0 integration mode is running with historical
price-limit enforcement disabled.

The result validates DataSource plumbing only.
It is not a production-correct A-share backtest."""


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
    if not config_path.is_file():
        raise FileNotFoundError(f"CNEquity config not found: {config_path}")
    config = load_cnequity_config(config_path)
    datasets = {
        row["dataset"]: row
        for row in list_datasets(config=config).iter_rows(named=True)
    }
    required = {"instruments", *_COVERED_DATASETS}
    missing = sorted(required - datasets.keys())
    if missing:
        raise RuntimeError(f"CNEquity datasets are unavailable: {', '.join(missing)}")
    if load("instruments", config=config).is_empty():
        raise RuntimeError("CNEquity instruments is empty")
    for dataset in _COVERED_DATASETS:
        coverage_start = datasets[dataset]["coverage_start"]
        coverage_end = datasets[dataset]["coverage_end"]
        if coverage_start is None or coverage_end is None:
            raise RuntimeError(f"CNEquity {dataset} has no data coverage")
        if coverage_start > start_date:
            raise RuntimeError(f"CNEquity {dataset} coverage starts at {coverage_start}, but backtest requests {start_date}")
        if coverage_end < end_date:
            raise RuntimeError(f"CNEquity {dataset} coverage ends at {coverage_end}, but backtest requests {end_date}")


def _build_rqalpha_config(
    cnequity_config_path: Path,
    rqalpha_bundle_path: Path,
    start_date: date,
    end_date: date,
    initial_cash: float,
) -> dict:
    return {
        "base": {
            "data_bundle_path": str(rqalpha_bundle_path),
            "start_date": start_date,
            "end_date": end_date,
            "run_type": "b",
            "frequency": "1d",
            "accounts": {"stock": initial_cash},
            "persist": False,
            "auto_update_bundle": False,
        },
        "mod": {
            "qmt_cnequity": {
                "enabled": True,
                "lib": "qmt_agent.backtest.rqalpha_mod",
                "cnequity_config_path": str(cnequity_config_path),
            },
            "sys_accounts": {"enabled": True, "stock_t1": True},
            "sys_analyser": {
                "enabled": True,
                "benchmark": None,
                "record": True,
                "plot": False,
            },
            "sys_progress": {"enabled": False},
            "sys_risk": {"enabled": True, "validate_price": False},
            "sys_simulation": {
                "enabled": True,
                "matching_type": "current_bar",
                "price_limit": False,
                "volume_limit": True,
                "inactive_limit": True,
                "slippage_model": "PriceRatioSlippage",
                "slippage": 0,
            },
            "sys_transaction_cost": {"enabled": True},
        },
    }


def run_backtest(
    strategy_file: Path,
    start_date: date,
    end_date: date,
    initial_cash: float = 1_000_000,
) -> dict:
    strategy_file = _validate_input(strategy_file, start_date, end_date, initial_cash)
    app_config = load_app_config()
    cnequity_config_path = (app_config.root / "configs" / "cnequity.toml").resolve()
    rqalpha_bundle_path = (app_config.root / ".rqalpha" / "bundle").resolve()
    _validate_cnequity_data(cnequity_config_path, start_date, end_date)
    warnings.warn(_PHASE0_WARNING, RuntimeWarning, stacklevel=2)
    return run_file(
        str(strategy_file),
        config=_build_rqalpha_config(
            cnequity_config_path,
            rqalpha_bundle_path,
            start_date,
            end_date,
            initial_cash,
        ),
    )
