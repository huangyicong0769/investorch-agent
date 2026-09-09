from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from investorch_qmt.history.model import latest_completed

SHANGHAI = ZoneInfo("Asia/Shanghai")
CALENDAR = (date(2026, 9, 4), date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9))


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

    class Cache:
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

    class BrokenCache:
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
