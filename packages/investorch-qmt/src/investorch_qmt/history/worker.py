"""Dedicated maintenance process; bars and factors never travel over IPC."""

import json
import os
import tempfile
from collections import Counter
from contextlib import suppress
from pathlib import Path

from investorch_qmt.market_data.errors import MarketDataError

from .model import latest_completed


def _check_stop(stopped):
    if stopped.is_set():
        raise InterruptedError("History maintenance stopped.")


def _supported_instruments(spec, adapter, candidates, target, report, stopped):
    supported, excluded = [], []
    for instrument in candidates:
        _check_stop(stopped)
        decision = adapter.history_capability(instrument)
        if decision["supported"]:
            supported.append(instrument)
        else:
            excluded.append(
                {
                    "order_book_id": instrument.order_book_id,
                    "instrument_type": str(instrument.type),
                    "provider_symbol": decision["provider_symbol"],
                    "reason": decision["reason"],
                }
            )
    scope = {
        "candidate_count": len(candidates),
        "supported_count": len(supported),
        "excluded_count": len(excluded),
        "exclusion_reasons": dict(Counter(item["reason"] for item in excluded)),
        "exclusions_path": spec.exclusions_path,
        "observed_at": spec.now.isoformat(),
    }
    if excluded and spec.exclusions_path is None:
        raise MarketDataError("HISTORY_SYNC_FAILED", "An explicit exclusion report path is required.")
    if spec.exclusions_path is not None:
        path = Path(spec.exclusions_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(
                    {**scope, "target_through": target.isoformat(), "excluded": excluded},
                    stream,
                    ensure_ascii=False,
                    indent=2,
                )
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    report({"phase": "SCOPE", "scope": scope})
    return supported


def synchronize(spec, adapter, reference, report, stopped):
    native, calendar, instruments = reference
    native = spec.native_through or native
    target = latest_completed(calendar, spec.now)
    if target is None:
        raise MarketDataError("HISTORY_SYNC_FAILED", "Native calendar has no completed session.")
    report(
        {
            "phase": "REFERENCE",
            "native_through": native.isoformat(),
            "calendar": [day.isoformat() for day in calendar],
            "target_through": target.isoformat(),
        }
    )
    instruments = _supported_instruments(spec, adapter, instruments, target, report, stopped)
    covered = max(native, spec.fresh_through or native)
    dates = [day for day in calendar if covered < day <= target]
    if dates:
        if not instruments:
            raise MarketDataError("HISTORY_SYNC_FAILED", "Supported historical universe is empty.")
        report({"phase": "VALIDATING"})
        missing = len(dates)
        for instrument in instruments:
            _check_stop(stopped)
            try:
                adapter.read_daily_history(instrument, dates[0], dates[-1], calendar)
            except MarketDataError as exc:
                if exc.code not in {"FRESH_HISTORY_INCOMPLETE", "FRESH_HISTORY_INVALID"}:
                    raise
                # Find the earliest invalid prefix without one provider call per day.
                lower, upper = 0, len(dates) - 1
                while lower < upper:
                    _check_stop(stopped)
                    middle = (lower + upper) // 2
                    try:
                        adapter.read_daily_history(instrument, dates[0], dates[middle], calendar)
                    except MarketDataError as prefix_exc:
                        if prefix_exc.code not in {"FRESH_HISTORY_INCOMPLETE", "FRESH_HISTORY_INVALID"}:
                            raise
                        upper = middle
                    else:
                        lower = middle + 1
                missing = min(missing, lower)
        if missing < len(dates):
            _check_stop(stopped)

            def progress(finished, total):
                _check_stop(stopped)
                report({"phase": "PROGRESS", "finished": finished, "total": total})

            adapter.download_daily_history(instruments, dates[missing], dates[-1], progress=progress)
            report({"phase": "VALIDATING"})
            for instrument in instruments:
                _check_stop(stopped)
                adapter.read_daily_history(instrument, dates[0], dates[-1], calendar)
    _check_stop(stopped)
    report({"phase": "SUCCEEDED", "target_through": target.isoformat()})


def load_native_reference(spec):
    from datetime import timedelta
    from types import SimpleNamespace

    from rqalpha.const import DEFAULT_ACCOUNT_TYPE, INSTRUMENT_TYPE, TRADING_CALENDAR_TYPE
    from rqalpha.data.base_data_source import BaseDataSource
    from rqalpha.environment import Environment
    from rqalpha.main import cleanup_resources

    base = SimpleNamespace(
        start_date=spec.now.date(),
        data_bundle_path=str(Path.home() / ".rqalpha" / "bundle"),
        accounts={DEFAULT_ACCOUNT_TYPE.STOCK: 1},
    )
    env = Environment(SimpleNamespace(base=base), False)
    try:
        native = BaseDataSource(base)
        env.set_data_source(native)
        cutoff = native.available_data_range("1d")[1]
        calendar = tuple(day.date() for day in native.get_trading_calendars()[TRADING_CALENDAR_TYPE.CN_STOCK])
        target = latest_completed(calendar, spec.now)
        lower = (spec.native_through or cutoff) + timedelta(days=1)
        instruments = [
            instrument
            for instrument in native.get_instruments(types=[INSTRUMENT_TYPE.CS, INSTRUMENT_TYPE.INDX])
            if instrument.order_book_id.endswith((".XSHG", ".XSHE"))
            and target is not None
            and instrument.listed_date.date() <= target
            and instrument.de_listed_date.date() > lower
        ]
        return cutoff, calendar, instruments
    finally:
        cleanup_resources(env)


def worker_main(spec, pipe, *, reference_loader=load_native_reference, adapter_factory=None):
    import threading

    stopped = threading.Event()

    def receive():
        try:
            while not stopped.is_set():
                if pipe.recv().get("command") == "STOP":
                    stopped.set()
        except (EOFError, OSError, TypeError, AttributeError):
            stopped.set()

    reader = threading.Thread(target=receive, daemon=True, name="history-control")
    reader.start()
    adapter = None
    try:
        pipe.send({"phase": "STARTING"})
        reference = reference_loader(spec)
        _check_stop(stopped)
        if adapter_factory is None:
            from investorch_qmt.market_data.history_adapter import XtHistoryAdapter

            adapter_factory = XtHistoryAdapter
        adapter = adapter_factory()
        adapter.connect()
        completed = None

        def report(event):
            nonlocal completed
            if event["phase"] == "SUCCEEDED":
                completed = event
            else:
                pipe.send(event)

        synchronize(spec, adapter, reference, report, stopped)
        closing, adapter = adapter, None
        closing.close()
        _check_stop(stopped)
        pipe.send(completed)
    except InterruptedError:
        pass
    except Exception as exc:
        pipe.send(
            {
                "phase": "FAILED",
                "reason": getattr(exc, "code", "HISTORY_SYNC_FAILED"),
                "retryable": True,
            }
        )
    finally:
        stopped.set()
        try:
            if adapter is not None:
                with suppress(Exception):
                    adapter.close()
        finally:
            pipe.close()
