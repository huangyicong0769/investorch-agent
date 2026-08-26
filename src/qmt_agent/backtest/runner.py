import math
from datetime import date
from pathlib import Path

from rqalpha import run_file

from qmt_agent.config import AppConfig

from .bundle import validate_rqalpha_bundle

_COVERED_DATASETS = ("daily_bars", "adj_factors")


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
    datasets = {
        row["dataset"]: row
        for row in list_datasets(config=config).iter_rows(named=True)
    }
    missing = sorted(set(_COVERED_DATASETS) - datasets.keys())
    if missing:
        raise RuntimeError(f"CNEquity datasets are unavailable: {', '.join(missing)}")
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
    benchmark: str | None,
    use_cnequity: bool,
) -> dict:
    return {
        "base": {
            "data_bundle_path": str(rqalpha_bundle_path),
            "start_date": start_date,
            "end_date": end_date,
            "run_type": "b",
            "frequency": "1d",
            "accounts": {"stock": initial_cash},
            "capital_gain_tax_rate": 0,
            "persist": False,
            "auto_update_bundle": False,
        },
        "mod": {
            "qmt_cnequity": {
                "enabled": use_cnequity,
                "lib": "qmt_agent.backtest.rqalpha_mod",
                "cnequity_config_path": str(cnequity_config_path),
            },
            "sys_accounts": {
                "enabled": True,
                "stock_t1": True,
                "dividend_reinvestment": False,
                "dividend_tax_enabled": True,
            },
            "sys_analyser": {
                "enabled": True,
                "benchmark": benchmark,
                "record": True,
                "plot": False,
            },
            "sys_progress": {"enabled": False},
            "sys_risk": {"enabled": True, "validate_price": True},
            "sys_simulation": {
                "enabled": True,
                "matching_type": "current_bar",
                "price_limit": True,
                "volume_limit": True,
                "inactive_limit": True,
                "slippage_model": "PriceRatioSlippage",
                "slippage": 0,
            },
            "sys_transaction_cost": {
                "enabled": True,
                "stock_min_commission": 5,
                "stock_commission_multiplier": 1,
                "tax_multiplier": 1,
                "pit_tax": True,
            },
        },
    }


def run_backtest(
    config: AppConfig,
    strategy_file: Path,
    start_date: date,
    end_date: date,
    initial_cash: float = 1_000_000,
    benchmark: str | None = None,
) -> dict:
    strategy_file = _validate_input(strategy_file, start_date, end_date, initial_cash)
    use_cnequity = config["backtest.use_cnequity"]
    cnequity_config_path = (config.root / "configs" / "cnequity.toml").resolve()
    rqalpha_bundle_path = (config.root / ".rqalpha" / "bundle").resolve()
    validate_rqalpha_bundle(rqalpha_bundle_path, start_date, end_date)
    if use_cnequity:
        _validate_cnequity_data(cnequity_config_path, start_date, end_date)
    return run_file(
        str(strategy_file),
        config=_build_rqalpha_config(
            cnequity_config_path,
            rqalpha_bundle_path,
            start_date,
            end_date,
            initial_cash,
            benchmark,
            use_cnequity,
        ),
    )
