import math
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from cnequity.config import load_config as load_cnequity_config
from cnequity.query import list_datasets
from rqalpha import run_file
from rqalpha.const import INSTRUMENT_TYPE, MARKET
from rqalpha.data.base_data_source import BaseDataSource
from rqalpha.utils.datetime_func import convert_int_to_date

from qmt_agent.config import load_config as load_app_config

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


def _validate_rqalpha_bundle(bundle_path: Path, start_date: date, end_date: date) -> None:
    download_command = f"rqalpha download-bundle -d {bundle_path.parent}"
    if not bundle_path.is_dir():
        raise FileNotFoundError(
            f"RQAlpha bundle not found at {bundle_path}. Run `{download_command}` first."
        )
    try:
        data_source = BaseDataSource(
            SimpleNamespace(data_bundle_path=str(bundle_path), future_info={})
        )
    except Exception as exc:
        raise RuntimeError(
            f"RQAlpha bundle at {bundle_path} cannot be loaded. "
            f"Run `{download_command}` to replace it."
        ) from exc

    native_stocks = tuple(
        instrument
        for instrument in data_source.get_instruments(types=[INSTRUMENT_TYPE.CS])
        if instrument.market == MARKET.CN
    )
    if not native_stocks:
        raise RuntimeError(
            f"RQAlpha bundle has no native stock metadata. Run `{download_command}` to replace it."
        )
    native_stock_store = data_source._day_bar_stores[INSTRUMENT_TYPE.CS, MARKET.CN]
    ranges = tuple(
        native_stock_store.get_date_range(instrument.order_book_id)
        for instrument in native_stocks
    )
    coverage_start_int = min(value[0] for value in ranges)
    coverage_end_int = max(value[1] for value in ranges)
    coverage_start = convert_int_to_date(coverage_start_int).date()
    coverage_end = convert_int_to_date(coverage_end_int).date()
    if coverage_start > start_date:
        raise RuntimeError(
            f"RQAlpha stock bundle coverage starts at {coverage_start}, but backtest requests "
            f"{start_date}. Run `{download_command}` to update the bundle."
        )
    if coverage_end < end_date:
        raise RuntimeError(
            f"RQAlpha stock bundle coverage ends at {coverage_end}, but backtest requests "
            f"{end_date}. Run `{download_command}` to update the bundle."
        )


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
            "capital_gain_tax_rate": 0,
            "persist": False,
            "auto_update_bundle": False,
        },
        "mod": {
            "qmt_cnequity": {
                "enabled": True,
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
                "benchmark": None,
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
    _validate_rqalpha_bundle(rqalpha_bundle_path, start_date, end_date)
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
