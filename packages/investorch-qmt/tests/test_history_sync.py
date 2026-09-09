from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from test_rqalpha_live_runtime import bundle  # noqa: F401

from investorch_qmt.history.model import latest_completed

SHANGHAI = ZoneInfo("Asia/Shanghai")
CALENDAR = (date(2026, 9, 4), date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9))


class SupportedCache:
    def history_capability(self, instrument):
        return {"supported": True, "provider_symbol": None, "reason": None}


@pytest.mark.parametrize(
    "now,expected",
    [
        (datetime(2026, 9, 8, 16, 29, tzinfo=SHANGHAI), date(2026, 9, 7)),
        (datetime(2026, 9, 8, 16, 30, tzinfo=SHANGHAI), date(2026, 9, 8)),
        (datetime(2026, 9, 6, 17, tzinfo=SHANGHAI), date(2026, 9, 4)),
    ],
)
def test_completed_target_respects_calendar_and_after_close_boundary(now, expected):
    assert latest_completed(CALENDAR, now) == expected


def test_restart_validates_cache_and_downloads_only_first_missing_range():
    import threading
    from types import SimpleNamespace

    from investorch_qmt.history.model import SyncSpec
    from investorch_qmt.history.worker import synchronize
    from investorch_qmt.market_data.errors import MarketDataError

    instrument = SimpleNamespace(order_book_id="600000.XSHG")

    class Cache(SupportedCache):
        def __init__(self):
            self.through = date(2026, 9, 7)
            self.downloads = []

        def read_daily_history(self, instrument, start, end, calendar):
            if end > self.through:
                raise MarketDataError("FRESH_HISTORY_INCOMPLETE")
            return []

        def download_daily_history(self, instruments, start, end, progress=None):
            self.downloads.append((start, end))
            self.through = end
            progress(1, 1)

    cache, events = Cache(), []
    synchronize(
        SyncSpec(datetime(2026, 9, 8, 17, tzinfo=SHANGHAI)),
        cache,
        (date(2026, 9, 4), CALENDAR, [instrument]),
        events.append,
        threading.Event(),
    )
    assert cache.downloads == [(date(2026, 9, 8), date(2026, 9, 8))]
    assert events[-1] == {"phase": "SUCCEEDED", "target_through": "2026-09-08"}
    assert all(event["phase"] != "SUCCEEDED" for event in events[:-1])
    cache.downloads.clear()
    synchronize(
        SyncSpec(datetime(2026, 9, 8, 17, tzinfo=SHANGHAI)),
        cache,
        (date(2026, 9, 4), CALENDAR, [instrument]),
        events.append,
        threading.Event(),
    )
    assert cache.downloads == []


def test_download_success_without_cache_validation_cannot_publish_watermark():
    import threading

    from investorch_qmt.history.model import SyncSpec
    from investorch_qmt.history.worker import synchronize
    from investorch_qmt.market_data.errors import MarketDataError

    class BrokenCache(SupportedCache):
        def read_daily_history(self, *args):
            raise MarketDataError("FRESH_HISTORY_INCOMPLETE")

        def download_daily_history(self, *args, progress=None):
            progress(1, 1)

    events = []
    with pytest.raises(MarketDataError, match="FRESH_HISTORY_INCOMPLETE"):
        synchronize(
            SyncSpec(datetime(2026, 9, 8, 17, tzinfo=SHANGHAI)),
            BrokenCache(),
            (date(2026, 9, 4), CALENDAR, [object()]),
            events.append,
            threading.Event(),
        )
    assert any(event["phase"] == "PROGRESS" for event in events)
    assert all(event["phase"] != "SUCCEEDED" for event in events)


class FakePipe:
    def __init__(self):
        self.messages = []
        self.sent = []
        self.closed = False

    def poll(self, timeout=0):
        return bool(self.messages)

    def recv(self):
        return self.messages.pop(0)

    def send(self, message):
        self.sent.append(message)

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self):
        self.alive = True
        self.closed = False
        self.terminated = False

    def start(self):
        pass

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        pass

    def terminate(self):
        self.terminated = True
        self.alive = False

    def kill(self):
        self.alive = False

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self):
        self.processes = []
        self.parents = []

    def Pipe(self, duplex):
        pipe = FakePipe()
        self.parents.append(pipe)
        return pipe, FakePipe()

    def Process(self, *, target, args, name):
        process = FakeProcess()
        self.processes.append(process)
        return process


def reference_event():
    return {
        "phase": "REFERENCE",
        "native_through": "2026-09-04",
        "target_through": "2026-09-07",
        "calendar": [day.isoformat() for day in CALENDAR],
    }


def test_manager_startup_schedule_single_child_and_readonly_snapshot():
    from investorch_qmt.history.sync import HistorySyncManager

    clock = [datetime(2026, 9, 8, 8, tzinfo=SHANGHAI)]
    context = FakeContext()
    manager = HistorySyncManager(clock=lambda: clock[0], context=context)
    try:
        manager.start(background=False)
        assert len(context.processes) == 1
        assert manager.snapshot()["status"] == "SYNCING"
        manager.poll()
        assert len(context.processes) == 1
        context.parents[-1].messages.extend([reference_event(), {"phase": "SUCCEEDED", "target_through": "2026-09-07"}])
        context.processes[-1].alive = False
        manager.poll()
        assert manager.snapshot() == {
            "status": "READY",
            "provider": "xtdata",
            "native_through": "2026-09-04",
            "fresh_through": "2026-09-07",
            "target_through": "2026-09-07",
        }
        clock[0] = datetime(2026, 9, 8, 16, 30, tzinfo=SHANGHAI)
        assert manager.snapshot()["status"] == "READY"  # Read-only, does not schedule work.
        manager.poll()
        manager.poll()
        assert len(context.processes) == 2
        assert manager.snapshot()["target_through"] == "2026-09-08"
    finally:
        manager.close()
    assert all(process.closed for process in context.processes)


def test_manager_preserves_validated_coverage_on_failure_then_retries():
    from investorch_qmt.history.sync import HistorySyncManager

    clock = [datetime(2026, 9, 8, 8, tzinfo=SHANGHAI)]
    context = FakeContext()
    manager = HistorySyncManager(clock=lambda: clock[0], context=context)
    manager.start(background=False)
    context.parents[-1].messages.extend([reference_event(), {"phase": "SUCCEEDED", "target_through": "2026-09-07"}])
    context.processes[-1].alive = False
    manager.poll()
    clock[0] = datetime(2026, 9, 8, 16, 30, tzinfo=SHANGHAI)
    manager.poll()
    context.parents[-1].messages.append({"phase": "FAILED", "reason": "FRESH_HISTORY_INCOMPLETE", "retryable": True})
    manager.poll()
    assert manager.snapshot()["status"] == "NOT_READY"
    assert manager.snapshot()["fresh_through"] == "2026-09-07"
    clock[0] = datetime(2026, 9, 8, 16, 30, 59, tzinfo=SHANGHAI)
    manager.poll()
    assert len(context.processes) == 2
    clock[0] = datetime(2026, 9, 8, 16, 31, tzinfo=SHANGHAI)
    manager.poll()
    assert len(context.processes) == 3
    context.parents[-1].messages.extend(
        [reference_event() | {"target_through": "2026-09-08"}, {"phase": "SUCCEEDED", "target_through": "2026-09-08"}]
    )
    context.processes[-1].alive = False
    manager.poll()
    assert manager.snapshot()["status"] == "READY"
    assert manager.snapshot()["fresh_through"] == "2026-09-08"
    manager.close()


def test_sync_crossing_schedule_finishes_original_target_before_next_child():
    from investorch_qmt.history.sync import HistorySyncManager

    clock = [datetime(2026, 9, 8, 16, 29, tzinfo=SHANGHAI)]
    context = FakeContext()
    manager = HistorySyncManager(clock=lambda: clock[0], context=context)
    manager.start(background=False)
    context.parents[-1].messages.append(reference_event())
    manager.poll()
    clock[0] = datetime(2026, 9, 8, 16, 30, tzinfo=SHANGHAI)
    manager.poll()
    assert len(context.processes) == 1
    context.parents[-1].messages.append({"phase": "SUCCEEDED", "target_through": "2026-09-07"})
    context.processes[-1].alive = False
    manager.poll()
    assert manager.snapshot()["fresh_through"] == "2026-09-07"
    assert manager.snapshot()["status"] == "SYNCING"
    assert len(context.processes) == 2
    manager.close()


@pytest.mark.parametrize("outcome", ["crash", "bad_progress", "unvalidated_success"])
def test_child_failures_remain_not_ready_and_reap_process(outcome):
    from investorch_qmt.history.sync import HistorySyncManager

    context = FakeContext()
    manager = HistorySyncManager(clock=lambda: datetime(2026, 9, 8, 8, tzinfo=SHANGHAI), context=context)
    manager.start(background=False)
    if outcome == "crash":
        context.processes[-1].alive = False
    elif outcome == "bad_progress":
        context.parents[-1].messages.append({"phase": "PROGRESS", "finished": 3, "total": 2})
    else:
        context.parents[-1].messages.append({"phase": "SUCCEEDED", "target_through": "2026-09-07"})
    manager.poll()
    assert manager.snapshot()["status"] == "NOT_READY"
    assert manager.snapshot()["fresh_through"] is None
    assert context.processes[-1].closed
    manager.close()


def test_shutdown_stops_and_reaps_blocking_child_without_restart():
    from investorch_qmt.history.sync import HistorySyncManager

    context = FakeContext()
    manager = HistorySyncManager(context=context)
    manager.start(background=False)
    manager.close()
    manager.close()
    manager.start(background=False)
    manager.poll()
    assert context.parents[0].sent == [{"command": "STOP"}]
    assert context.processes[0].terminated
    assert len(context.processes) == 1


def test_maintenance_disconnects_before_success_and_reports_close_failure():
    import threading

    from investorch_qmt.history.model import SyncSpec
    from investorch_qmt.history.worker import worker_main

    class Pipe:
        def __init__(self):
            self.closed = threading.Event()
            self.events = []

        def recv(self):
            self.closed.wait(2)
            raise EOFError

        def send(self, value):
            self.events.append(value)

        def close(self):
            self.closed.set()

    class Adapter:
        def connect(self):
            pass

        def close(self):
            raise OSError("disconnect failed")

    pipe = Pipe()
    worker_main(
        SyncSpec(datetime(2026, 9, 8, 8, tzinfo=SHANGHAI)),
        pipe,
        reference_loader=lambda _: (date(2026, 9, 7), CALENDAR, []),
        adapter_factory=Adapter,
    )
    assert pipe.events[-1]["phase"] == "FAILED"
    assert all(event["phase"] != "SUCCEEDED" for event in pipe.events)


def test_process_creation_failure_is_retryable_maintenance_state():
    from investorch_qmt.history.sync import HistorySyncManager

    class BrokenContext(FakeContext):
        def Process(self, **kwargs):
            raise OSError("cannot create child")

    context = BrokenContext()
    manager = HistorySyncManager(context=context)
    manager.start(background=False)
    assert manager.snapshot()["status"] == "NOT_READY"
    assert manager.snapshot()["reason"] == "HISTORY_SYNC_FAILED"
    assert context.parents[0].closed
    manager.close()


def test_native_coverage_does_not_claim_unvalidated_provider_watermark():
    from investorch_qmt.history.sync import HistorySyncManager

    context = FakeContext()
    manager = HistorySyncManager(clock=lambda: datetime(2026, 9, 8, 8, tzinfo=SHANGHAI), context=context)
    manager.start(background=False)
    context.parents[-1].messages.extend(
        [
            reference_event() | {"native_through": "2026-09-07"},
            {"phase": "SUCCEEDED", "target_through": "2026-09-07"},
        ]
    )
    context.processes[-1].alive = False
    manager.poll()
    assert manager.snapshot()["status"] == "READY"
    assert manager.snapshot()["fresh_through"] is None
    manager.close()


def test_native_bootstrap_filters_lifecycle_universe_and_keeps_node_cutoff(bundle):  # noqa: F811
    import pickle

    from investorch_qmt.history.model import SyncSpec
    from investorch_qmt.history.worker import load_native_reference, synchronize

    # Reuse the same on-disk native bundle fixture as real engine acceptance.
    path = bundle
    original = pickle.loads((path / "instruments.pk").read_bytes())
    original.extend(
        [
            original[0] | {"order_book_id": "000001.XSHE", "exchange": "XSHE", "listed_date": "2026-09-08"},
            original[0] | {"order_book_id": "600002.XSHG", "listed_date": "2026-09-09"},
            original[0] | {"order_book_id": "600003.XSHG", "de_listed_date": "2026-09-08"},
            original[0] | {"order_book_id": "510001.XSHG", "type": "ETF"},
        ]
    )
    (path / "instruments.pk").write_bytes(pickle.dumps(original))
    spec = SyncSpec(datetime(2026, 9, 9, 8, tzinfo=SHANGHAI))
    cutoff, calendar, instruments = load_native_reference(spec)
    assert cutoff == date(2026, 9, 7)
    assert calendar == CALENDAR
    assert {instrument.order_book_id for instrument in instruments} == {"600519.XSHG", "000001.XSHG", "000001.XSHE"}
    # Existing workers can retain an older cutoff than a newly loaded native file.
    frozen = SyncSpec(spec.now, native_through=date(2026, 9, 4))
    reference = load_native_reference(frozen)
    assert "600003.XSHG" in {instrument.order_book_id for instrument in reference[2]}
    events = []

    class CompleteCache(SupportedCache):
        def read_daily_history(self, *args):
            return []

    import threading

    synchronize(frozen, CompleteCache(), reference, events.append, threading.Event())
    assert events[0]["native_through"] == "2026-09-04"


def test_sync_reports_explicit_capability_exclusions_and_keeps_supported_missing_data(tmp_path):
    import json
    import threading
    from types import SimpleNamespace

    from investorch_qmt.history.model import SyncSpec
    from investorch_qmt.history.worker import synchronize
    from investorch_qmt.market_data.errors import MarketDataError

    allowed = SimpleNamespace(order_book_id="600000.XSHG", type="CS")
    excluded = SimpleNamespace(order_book_id="H50032.XSHG", type="INDX")
    report_path = tmp_path / "history-exclusions.json"
    events = []

    class Cache(SupportedCache):
        def history_capability(self, instrument):
            return {
                "supported": instrument is allowed,
                "provider_symbol": "600000.SH" if instrument is allowed else None,
                "reason": None if instrument is allowed else "CANONICAL_MAPPING_UNAVAILABLE",
            }

        def read_daily_history(self, instrument, *args):
            assert instrument is allowed
            raise MarketDataError("FRESH_HISTORY_INCOMPLETE")

        def download_daily_history(self, instruments, *args, **kwargs):
            assert instruments == [allowed]

    with pytest.raises(MarketDataError, match="FRESH_HISTORY_INCOMPLETE"):
        synchronize(
            SyncSpec(datetime(2026, 9, 8, 17, tzinfo=SHANGHAI), exclusions_path=str(report_path)),
            Cache(),
            (date(2026, 9, 7), CALENDAR, [allowed, excluded]),
            events.append,
            threading.Event(),
        )
    scope = next(event["scope"] for event in events if event["phase"] == "SCOPE")
    assert scope["candidate_count"] == 2
    assert scope["supported_count"] == 1
    assert scope["excluded_count"] == 1
    assert scope["exclusion_reasons"] == {"CANONICAL_MAPPING_UNAVAILABLE": 1}
    assert json.loads(report_path.read_text())["excluded"] == [
        {
            "order_book_id": "H50032.XSHG",
            "instrument_type": "INDX",
            "provider_symbol": None,
            "reason": "CANONICAL_MAPPING_UNAVAILABLE",
        }
    ]
    assert not any(event["phase"] == "SUCCEEDED" for event in events)


def test_manager_publishes_bounded_scope_and_exclusion_artifact_location(tmp_path):
    from investorch_qmt.history.sync import HistorySyncManager

    context = FakeContext()
    path = tmp_path / "history-exclusions.json"
    manager = HistorySyncManager(
        clock=lambda: datetime(2026, 9, 8, 8, tzinfo=SHANGHAI), context=context, exclusions_path=path
    )
    manager.start(background=False)
    scope = {
        "candidate_count": 2,
        "supported_count": 1,
        "excluded_count": 1,
        "exclusion_reasons": {"CANONICAL_MAPPING_UNAVAILABLE": 1},
        "exclusions_path": str(path),
        "observed_at": "2026-09-08T08:00:00+08:00",
    }
    context.parents[-1].messages.extend(
        [reference_event(), {"phase": "SCOPE", "scope": scope}, {"phase": "SUCCEEDED", "target_through": "2026-09-07"}]
    )
    context.processes[-1].alive = False
    manager.poll()
    assert manager.snapshot()["status"] == "READY"
    assert manager.snapshot()["scope"] == scope
    changed = manager.snapshot()
    changed["scope"]["exclusion_reasons"].clear()
    assert manager.snapshot()["scope"]["excluded_count"] == 1
    assert manager.snapshot()["scope"]["exclusion_reasons"] == {"CANONICAL_MAPPING_UNAVAILABLE": 1}
    manager.close()


def test_new_supported_instrument_revalidates_history_before_previous_global_watermark():
    import threading
    from types import SimpleNamespace

    from investorch_qmt.history.model import SyncSpec
    from investorch_qmt.history.worker import synchronize
    from investorch_qmt.market_data.errors import MarketDataError

    calendar = (date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 7), date(2026, 9, 8))
    instrument = SimpleNamespace(order_book_id="600000.XSHG")

    class Cache(SupportedCache):
        repaired = False

        def read_daily_history(self, instrument, start, end, calendar):
            if start < date(2026, 9, 8) and not self.repaired:
                raise MarketDataError("FRESH_HISTORY_INCOMPLETE")
            return []

        def download_daily_history(self, instruments, start, end, progress=None):
            assert (start, end) == (date(2026, 9, 1), date(2026, 9, 8))
            self.repaired = True

    cache, events = Cache(), []
    synchronize(
        SyncSpec(datetime(2026, 9, 8, 17, tzinfo=SHANGHAI), fresh_through=date(2026, 9, 7)),
        cache,
        (date(2026, 8, 31), calendar, [instrument]),
        events.append,
        threading.Event(),
    )
    assert cache.repaired
    assert events[-1] == {"phase": "SUCCEEDED", "target_through": "2026-09-08"}
