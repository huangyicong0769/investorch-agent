from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from rqalpha.const import INSTRUMENT_TYPE, MARKET
from rqalpha.data.base_data_source import BaseDataSource
from rqalpha.utils.datetime_func import convert_int_to_date


def _download_command(bundle_path: Path) -> str:
    return f"rqalpha download-bundle -d {bundle_path.parent}"


def _open_rqalpha_bundle(bundle_path: Path) -> BaseDataSource:
    download_command = _download_command(bundle_path)
    if not bundle_path.is_dir():
        raise FileNotFoundError(
            f"RQAlpha bundle not found at {bundle_path}. Run `{download_command}` first."
        )
    try:
        return BaseDataSource(
            SimpleNamespace(data_bundle_path=str(bundle_path), future_info={})
        )
    except Exception as exc:
        raise RuntimeError(
            f"RQAlpha bundle at {bundle_path} cannot be loaded. "
            f"Run `{download_command}` to replace it."
        ) from exc


def _native_stocks(data_source: BaseDataSource, bundle_path: Path) -> tuple:
    stocks = tuple(
        instrument
        for instrument in data_source.get_instruments(types=[INSTRUMENT_TYPE.CS])
        if instrument.market == MARKET.CN
    )
    if not stocks:
        raise RuntimeError(
            f"RQAlpha bundle has no native stock metadata. "
            f"Run `{_download_command(bundle_path)}` to replace it."
        )
    return stocks


def _bar_range(data_source: BaseDataSource, instrument) -> tuple[date, date]:
    store = data_source._day_bar_stores[instrument.type, instrument.market]
    start, end = store.get_date_range(instrument.order_book_id)
    return convert_int_to_date(start).date(), convert_int_to_date(end).date()


def validate_rqalpha_bundle(
    bundle_path: Path,
    start_date: date,
    end_date: date,
) -> None:
    data_source = _open_rqalpha_bundle(bundle_path)
    stocks = _native_stocks(data_source, bundle_path)
    ranges = tuple(_bar_range(data_source, instrument) for instrument in stocks)
    coverage_start = min(value[0] for value in ranges)
    coverage_end = max(value[1] for value in ranges)
    download_command = _download_command(bundle_path)
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


def _instrument_date(value: datetime) -> str | None:
    if value.year == 2999:
        return None
    return value.date().isoformat()


def inspect_rqalpha_bundle(
    bundle_path: Path,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    data_source = _open_rqalpha_bundle(bundle_path)

    if symbols is None:
        stocks = _native_stocks(data_source, bundle_path)
        ranges = tuple(_bar_range(data_source, instrument) for instrument in stocks)
        return {
            "source": "rqalpha_bundle",
            "available": True,
            "stock_data": {
                "earliest_observed_bar": min(value[0] for value in ranges).isoformat(),
                "latest_observed_bar": max(value[1] for value in ranges).isoformat(),
                "instrument_count": len(stocks),
            },
        }

    instruments = {
        instrument.order_book_id: instrument
        for instrument in data_source.get_instruments(id_or_syms=symbols)
    }
    results = []
    for order_book_id in symbols:
        instrument = instruments.get(order_book_id)
        if instrument is None:
            results.append({"order_book_id": order_book_id, "exists": False})
            continue
        if (instrument.type, instrument.market) in data_source._day_bar_stores:
            bar_start, bar_end = _bar_range(data_source, instrument)
            bar_start_value = bar_start.isoformat()
            bar_end_value = bar_end.isoformat()
        else:
            bar_start_value = None
            bar_end_value = None
        results.append(
            {
                "order_book_id": order_book_id,
                "exists": True,
                "type": instrument.type.value,
                "symbol": instrument.symbol,
                "listed_date": _instrument_date(instrument.listed_date),
                "de_listed_date": _instrument_date(instrument.de_listed_date),
                "bar_start": bar_start_value,
                "bar_end": bar_end_value,
            }
        )
    return {
        "source": "rqalpha_bundle",
        "available": True,
        "symbols": results,
    }
