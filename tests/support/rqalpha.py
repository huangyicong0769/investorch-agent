"""Small native RQAlpha bundle for exercising the real engine without network data."""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import h5py
import numpy as np


def write_native_bundle(path: Path) -> None:
    path.mkdir(parents=True)
    days = np.array([20240503, 20240506, 20240507, 20240508, 20240509, 20240510, 20240513])
    np.save(path / "trading_dates.npy", days)
    instrument = {
        "order_book_id": "000001.XSHE",
        "symbol": "Fixture stock",
        "type": "CS",
        "exchange": "XSHE",
        "board_type": "MainBoard",
        "round_lot": 100,
        "listed_date": "1991-04-03",
        "de_listed_date": "2999-12-31",
        "market_tplus": 1,
    }
    (path / "instruments.pk").write_bytes(pickle.dumps([instrument]))
    (path / "future_info.json").write_text(
        json.dumps([{"underlying_symbol": "IF", "commission_type": "by_money", "margin_rate": 0.1}])
    )
    (path / "share_transformation.json").write_text("{}")
    bar_dtype = [("datetime", "i8")] + [
        (name, "f8") for name in ("open", "close", "high", "low", "volume", "total_turnover", "limit_up", "limit_down")
    ]
    bars = np.array(
        [(day * 1000000, 10, 10, 10, 10, 1000000, 10000000, 11, 9) for day in days],
        dtype=bar_dtype,
    )
    with h5py.File(path / "stocks.h5", "w") as store:
        store.create_dataset("000001.XSHE", data=bars)
    with h5py.File(path / "indexes.h5", "w") as store:
        store.create_dataset("000001.XSHG", data=bars)
    for name in ("funds", "futures", "dividends", "split_factor", "ex_cum_factor", "suspended_days", "st_stock_days"):
        with h5py.File(path / f"{name}.h5", "w"):
            pass
    with h5py.File(path / "yield_curve.h5", "w") as store:
        store.create_dataset("data", data=np.array([(day, 0.02) for day in days], dtype=[("date", "i8"), ("0S", "f8")]))
