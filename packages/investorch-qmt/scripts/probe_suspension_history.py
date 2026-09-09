"""Observe pinned MiniQMT fill behavior; candidate fields never prove suspension."""

import argparse
import hashlib
import inspect
import json
import math
import time
from datetime import date, datetime
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

FIELDS = ("time", "open", "high", "low", "close", "volume", "amount", "preClose", "suspendFlag")
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _scalar(value):
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _day(value):
    value = _scalar(value)
    if value is None:
        return None
    text = str(value)
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d").date().isoformat()
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, SHANGHAI).date().isoformat()
    return date.fromisoformat(text[:10]).isoformat()


def _read(api, symbols, start, end, *, market=False, fill=False):
    symbols = list(dict.fromkeys(symbols))
    params = dict(
        field_list=list(FIELDS),
        stock_list=symbols,
        period="1d",
        start_time=start.replace("-", ""),
        end_time=end.replace("-", ""),
        count=-1,
        dividend_type="none",
        fill_data=fill,
    )
    payload = (api.get_market_data if market else api.get_local_data)(**params)
    result = {}
    for symbol in symbols:
        rows = []
        if market:
            columns = list(dict.fromkeys(col for frame in payload.values() for col in frame.columns))
            for col in columns:
                values = {
                    field: _scalar(frame.loc[symbol, col])
                    for field, frame in payload.items()
                    if symbol in frame.index and col in frame.columns
                }
                rows.append({"date": _day(col), "time_date": _day(values.get("time")), **values})
        else:
            frame = payload.get(symbol)
            if frame is not None:
                for index, row in frame.iterrows():
                    values = {field: _scalar(value) for field, value in row.items()}
                    rows.append({"date": _day(index), "time_date": _day(values.get("time")), **values})
        result[symbol] = rows
    return result


def _candidates(raw, filled, expected):
    raw_dates = {row["date"] for row in raw}
    results = []
    previous_close = None
    for row in filled:
        day = row["date"]
        if day in expected and day not in raw_dates:
            prices = [row.get(field) for field in ("open", "high", "low", "close")]
            flat = all(isinstance(value, (int, float)) and value > 0 for value in prices) and len(set(prices)) == 1
            results.append(
                {
                    "date": day,
                    "exact_time": row["time_date"] == day,
                    "flat_positive_ohlc": flat,
                    "close_equals_preClose": row.get("close") == row.get("preClose"),
                    "close_equals_previous_row_close": row.get("close") == previous_close,
                    "preClose_equals_previous_row_close": row.get("preClose") == previous_close,
                    "candidate_field_pattern": bool(
                        row["time_date"] == day
                        and row.get("suspendFlag") == 1
                        and row.get("volume") == 0
                        and row.get("amount") == 0
                        and flat
                        and row.get("close") == previous_close
                        and row.get("preClose") == previous_close
                    ),
                    "row": row,
                }
            )
        previous_close = row.get("close")
    return results


def run_probe(api, cases, calendar, anchors, *, start, end, anchor_days=10, ensure_history=False, emit):
    """Emit reviewable observations using explicit provider and native-reference boundaries."""
    expected = [day for day in calendar if start <= day <= end]
    prior = [day for day in calendar if day < start]
    if len(prior) < anchor_days or not expected:
        raise ValueError("Native calendar does not cover the requested anchored window.")
    for case in cases:
        symbol = case["symbol"]
        anchor = anchors[symbol]
        query_start = min(prior[-anchor_days], anchor["date"])
        emit(
            {
                "event": "case_start",
                "symbol": symbol,
                "case": case,
                "query_start": query_start,
                "query_end": end,
                "native_price_anchor": anchor,
                "anchor_limitation": "Native price anchor does not establish later suspension status.",
            }
        )
        observations = {}
        for phase in ("before", "after") if ensure_history else ("before",):
            if phase == "after":
                time.sleep(1.0)
                emit({"event": "download_enter", "symbol": symbol, "start": query_start, "end": end})
                # Pinned single-symbol API delegates to synchronous supply_history_data.
                result = api.download_history_data(
                    symbol,
                    period="1d",
                    start_time=query_start.replace("-", ""),
                    end_time=end.replace("-", ""),
                    incrementally=False,
                )
                emit({"event": "download_return", "symbol": symbol, "result": repr(result)})
            modes = {}
            for name, market, fill in (
                ("local_raw", False, False),
                ("local_filled", False, True),
                ("market_filled", True, True),
            ):
                rows = _read(api, [symbol], query_start, end, market=market, fill=fill)[symbol]
                modes[name] = rows
                emit({"event": "read", "symbol": symbol, "phase": phase, "api": name, "rows": rows})
            pair = "600000.SH" if symbol.endswith(".SH") else "000001.SZ"
            pair_raw = _read(api, [pair], query_start, end)[pair]
            pair_expected = {day for day in calendar if query_start <= day <= end}
            pair_normal = {
                row["date"]
                for row in pair_raw
                if row.get("volume", 0) > 0 and row.get("suspendFlag") == 0 and row["time_date"] == row["date"]
            }
            emit(
                {
                    "event": "paired_anchor_raw",
                    "symbol": symbol,
                    "phase": phase,
                    "anchor_symbol": pair,
                    "complete_normal_axis": pair_expected <= pair_normal,
                    "missing_or_not_normal": sorted(pair_expected - pair_normal),
                    "rows": pair_raw,
                }
            )
            paired = _read(api, [symbol, pair], query_start, end, market=True, fill=True)
            modes["market_paired_filled"] = paired[symbol]
            emit({"event": "paired_read", "symbol": symbol, "phase": phase, "anchor_symbol": pair, "rows": paired})
            repeat = {
                name: _read(api, [symbol], query_start, end, market=market, fill=fill)[symbol]
                for name, market, fill in (
                    ("local_raw", False, False),
                    ("local_filled", False, True),
                    ("market_filled", True, True),
                )
            }
            repeat["market_paired_filled"] = _read(api, [symbol, pair], query_start, end, market=True, fill=True)[
                symbol
            ]
            observations[phase] = modes
            emit(
                {
                    "event": "phase_summary",
                    "symbol": symbol,
                    "phase": phase,
                    "raw_missing": sorted(set(expected) - {row["date"] for row in modes["local_raw"]}),
                    "normal_raw_comparison": {
                        name: [
                            {
                                "date": row["date"],
                                "fields_equal": row
                                == next((filled for filled in rows if filled["date"] == row["date"]), None),
                            }
                            for row in modes["local_raw"]
                            if row.get("volume", 0) > 0
                        ]
                        for name, rows in modes.items()
                        if name != "local_raw"
                    },
                    "repeat_equal": {name: rows == repeat[name] for name, rows in modes.items()},
                    "candidate_fields": {
                        name: _candidates(modes["local_raw"], rows, expected)
                        for name, rows in modes.items()
                        if name != "local_raw"
                    },
                    "unresolved_by_api": {
                        name: sorted(set(expected) - {row["date"] for row in rows}) for name, rows in modes.items()
                    },
                }
            )
        emit(
            {
                "event": "case_complete",
                "symbol": symbol,
                "download_changed_reads": {
                    name: rows != observations["after"][name] for name, rows in observations["before"].items()
                }
                if ensure_history
                else None,
            }
        )


def _native(cases, start):
    from rqalpha.const import DEFAULT_ACCOUNT_TYPE, TRADING_CALENDAR_TYPE
    from rqalpha.data.base_data_source import BaseDataSource
    from rqalpha.environment import Environment
    from rqalpha.main import cleanup_resources

    base = SimpleNamespace(
        data_bundle_path=str(Path.home() / ".rqalpha" / "bundle"),
        accounts={DEFAULT_ACCOUNT_TYPE.STOCK: 1},
        start_date=date.fromisoformat(start),
    )
    env = Environment(SimpleNamespace(base=base), False)
    try:
        native = BaseDataSource(base)
        env.set_data_source(native)
        cutoff = native.available_data_range("1d")[1]
        calendar = [str(day.date()) for day in native.get_trading_calendars()[TRADING_CALENDAR_TYPE.CN_STOCK]]
        anchors = {}
        for case in cases:
            symbol = case["symbol"]
            code = symbol.replace(".SH", ".XSHG").replace(".SZ", ".XSHE")
            instrument = next(native.get_instruments([code]))
            bars = native.history_bars(
                instrument,
                1,
                "1d",
                ["datetime", "close", "volume"],
                datetime.combine(min(cutoff, date.fromisoformat(start)), datetime.min.time()),
                skip_suspended=True,
                include_now=False,
                adjust_type="none",
            )
            if bars is None or not len(bars):
                raise ValueError(f"No native traded price anchor for {symbol}.")
            bar = bars[-1]
            anchors[symbol] = {
                "date": datetime.strptime(str(int(bar["datetime"]))[:8], "%Y%m%d").date().isoformat(),
                "close": float(bar["close"]),
                "volume": float(bar["volume"]),
            }
        return calendar, anchors, str(cutoff)
    finally:
        cleanup_resources(env)


def main():
    """Run explicit bounded symbols; default is read-only and output cannot overwrite evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", action="append", default=[])
    parser.add_argument("--samples", type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--anchor-days", type=int, default=10)
    parser.add_argument("--ensure-history", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 5 <= args.anchor_days <= 60:
        parser.error("--anchor-days must be between 5 and 60 native trading days")
    if not date.fromisoformat(args.start) <= date.fromisoformat(args.end) < datetime.now(SHANGHAI).date():
        parser.error("Use an ordered completed historical range ending before today.")
    cases = json.loads(args.samples.read_text(encoding="utf-8"))["cases"] if args.samples else []
    cases = [{"symbol": symbol, "classification": "control_or_unspecified"} for symbol in args.symbol] + cases
    if not cases or len(cases) > 30 or len({case["symbol"] for case in cases}) != len(cases):
        parser.error("Provide 1 to 30 unique symbols.")
    versions = {name: version(name) for name in ("xtquant", "rqalpha")}
    if versions != {"xtquant": "250807.1.2", "rqalpha": "6.3.0"}:
        raise RuntimeError(f"Pinned versions required: {versions}")
    from xtquant import xtdata

    calendar, anchors, cutoff = _native(cases, args.start)
    with args.output.open("x", encoding="utf-8") as stream:

        def emit(value):
            value = {"observed_at": datetime.now(SHANGHAI).isoformat(), **value}
            stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
            if value["event"] not in ("read", "paired_read"):
                print(json.dumps({key: value[key] for key in ("event", "symbol", "phase") if key in value}), flush=True)

        emit(
            {
                "event": "metadata",
                "versions": versions,
                "native_cutoff": cutoff,
                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "api_source": {
                    name: inspect.getsource(getattr(xtdata, name))
                    for name in ("get_market_data", "get_local_data", "download_history_data", "download_history_data2")
                },
                "negative_control": "Normal rows only; a genuine non-suspension missing row is not established.",
                "semantics": "Candidate field patterns are observations, not confirmed historical suspension.",
            }
        )
        xtdata.enable_hello = False
        try:
            xtdata.connect()
            run_probe(
                xtdata,
                cases,
                calendar,
                anchors,
                start=args.start,
                end=args.end,
                anchor_days=args.anchor_days,
                ensure_history=args.ensure_history,
                emit=emit,
            )
            emit({"event": "complete"})
        finally:
            xtdata.disconnect()


if __name__ == "__main__":
    main()
