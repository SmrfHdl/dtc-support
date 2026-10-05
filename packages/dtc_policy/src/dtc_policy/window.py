"""Return-window day counting: calendar days in the store timezone."""

from datetime import datetime
from zoneinfo import ZoneInfo


def days_since_delivery(now: datetime, delivered_at: datetime, tz: ZoneInfo) -> int:
    """Calendar days between delivery and now, both taken as dates in `tz`.

    Clamped at 0: `delivered_at` may be slightly after `now` (clock skew allowed by
    PolicyInput validation), which can otherwise cross midnight and give -1.
    """
    days = (now.astimezone(tz).date() - delivered_at.astimezone(tz).date()).days
    return max(0, days)
