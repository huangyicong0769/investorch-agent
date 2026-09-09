"""The diagnostic report preserves provider matrix fields without proving suspension."""

import importlib.util
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


def test_report_compares_local_and_matrix_fields_and_exposes_invalid_time():
    path = Path(__file__).parents[1] / "scripts" / "probe_suspension_history.py"
    spec = importlib.util.spec_from_file_location("suspension_probe", path)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    days = [
        "2026-08-24",
        "2026-08-25",
        "2026-08-26",
        "2026-08-27",
        "2026-08-28",
        "2026-08-31",
        "2026-09-01",
        "2026-09-02",
    ]
    dates = ["20260831", "20260901", "20260902"]
    times = [
        int(datetime.strptime(day, "%Y%m%d").replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)
        for day in dates
    ]
    records = [
        dict(
            time=stamp,
            open=10.0,
            high=10.0,
            low=10.0,
            close=10.0,
            preClose=9.0,
            volume=0 if index == 1 else 100,
            amount=0 if index == 1 else 1000,
            suspendFlag=1 if index == 1 else 0,
        )
        for index, stamp in enumerate(times)
    ]

    class Provider:
        def get_local_data(self, stock_list, fill_data, **kwargs):
            frame = pd.DataFrame(records, index=dates)
            return {symbol: frame if fill_data else frame.drop("20260901") for symbol in stock_list}

        def get_market_data(self, stock_list, **kwargs):
            frames = {
                field: pd.DataFrame(
                    [[row[field] for row in records]] * len(stock_list), index=stock_list, columns=dates
                )
                for field in probe.FIELDS
            }
            frames["time"]["20260901"] = 0
            return frames

    report = []
    probe.run_probe(
        Provider(),
        [{"symbol": "600929.SH"}],
        days,
        {"600929.SH": {"date": "2026-08-31", "close": 10, "volume": 100}},
        start="2026-09-01",
        end="2026-09-02",
        anchor_days=5,
        emit=report.append,
    )
    summary = next(event for event in report if event["event"] == "phase_summary")
    assert summary["raw_missing"] == ["2026-09-01"]
    assert all(summary["repeat_equal"].values())
    local = summary["candidate_fields"]["local_filled"][0]
    assert local["candidate_field_pattern"] is False
    assert local["close_equals_preClose"] is False
    assert local["preClose_equals_previous_row_close"] is False
    assert local["row"]["preClose"] == 9.0
    matrix = summary["candidate_fields"]["market_filled"][0]
    assert matrix["date"] == "2026-09-01"
    assert matrix["exact_time"] is False
    assert matrix["candidate_field_pattern"] is False
    assert matrix["row"]["time"] == 0
    assert not any(event["event"] == "download_enter" for event in report)
