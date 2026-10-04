"""Usage period: the calendar month in UTC."""
import math
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Period:
    start: datetime  # first instant of the month, UTC (inclusive)
    end: datetime    # first instant of the next month, UTC (exclusive)

    def seconds_until_reset(self, now: datetime) -> int:
        return max(1, math.ceil((self.end - now).total_seconds()))


def current_period(now: datetime | None = None) -> Period:
    now = now or datetime.now(timezone.utc)
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    if now.month == 12:
        end = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    return Period(start=start, end=end)


def iso_z(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
