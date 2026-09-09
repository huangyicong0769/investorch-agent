"""Dedicated maintenance process; bars and factors never travel over IPC."""

from investorch_qmt.market_data.errors import MarketDataError

from .model import latest_completed


def _check_stop(stopped):
    if stopped.is_set():
        raise InterruptedError("History maintenance stopped.")


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
    covered = max(native, spec.fresh_through or native)
    dates = [day for day in calendar if covered < day <= target]
    if dates:
        if not instruments:
            raise MarketDataError("HISTORY_SYNC_FAILED", "Native historical universe is empty.")
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
