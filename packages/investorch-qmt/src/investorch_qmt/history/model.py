"""Small maintenance messages and authoritative completed-session selection."""

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def latest_completed(calendar, now: datetime) -> date | None:
    now = now.astimezone(SHANGHAI)
    return next(
        (
            day
            for day in reversed(calendar)
            if day < now.date() or (day == now.date() and now.time().replace(tzinfo=None) >= time(16, 30))
        ),
        None,
    )


@dataclass(frozen=True)
class SyncSpec:
    now: datetime
    native_through: date | None = None
    fresh_through: date | None = None
