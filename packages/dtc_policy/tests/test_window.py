from datetime import UTC, date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from dtc_policy.window import days_since_delivery

LA = ZoneInfo("America/Los_Angeles")


def la(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=LA)


@pytest.mark.parametrize(
    ("delivered_at", "now", "expected"),
    [
        pytest.param(la(2026, 9, 1, 8), la(2026, 9, 1, 20), 0, id="same-day"),
        pytest.param(la(2026, 9, 1, 23, 59), la(2026, 9, 2, 0, 1), 1, id="just-past-midnight"),
        pytest.param(la(2026, 9, 1, 8), la(2026, 9, 30, 8), 29, id="case1-29-days"),
        pytest.param(la(2026, 9, 1, 8), la(2026, 10, 1, 8), 30, id="case2-30-days"),
        # Spring forward 2026-03-08: elapsed time is 29d 23h15m, calendar days are 30.
        pytest.param(la(2026, 2, 10, 0, 30), la(2026, 3, 12, 0, 45), 30, id="case4-dst-start"),
        # Fall back 2026-11-01: elapsed time is 29d 1h45m, calendar days are 30.
        pytest.param(la(2026, 10, 15, 23, 30), la(2026, 11, 14, 0, 15), 30, id="case4-dst-end"),
    ],
)
def test_calendar_days_in_store_tz(delivered_at: datetime, now: datetime, expected: int) -> None:
    assert days_since_delivery(now, delivered_at, LA) == expected


def test_case3_late_evening_delivery_counted_in_store_tz() -> None:
    # 23:30 LA on Sep 1 is already Sep 2 in UTC; both inputs arrive as UTC (as from Postgres).
    delivered_at = la(2026, 9, 1, 23, 30).astimezone(UTC)
    now = la(2026, 9, 30, 10).astimezone(UTC)
    assert delivered_at.date() == date(2026, 9, 2)

    assert days_since_delivery(now, delivered_at, LA) == 29


@pytest.mark.parametrize(
    "input_tz", [UTC, LA, ZoneInfo("Asia/Ho_Chi_Minh"), timezone(timedelta(hours=-11))]
)
def test_result_independent_of_input_tz(input_tz: timezone | ZoneInfo) -> None:
    delivered_at = la(2026, 9, 1, 23, 30).astimezone(input_tz)
    now = la(2026, 9, 30, 10).astimezone(input_tz)
    assert days_since_delivery(now, delivered_at, LA) == 29


def test_delivered_slightly_after_now_across_midnight_is_zero() -> None:
    now = la(2026, 9, 1, 23, 58)
    delivered_at = now + timedelta(minutes=3)
    assert days_since_delivery(now, delivered_at, LA) == 0


@given(
    day=st.dates(min_value=date(2020, 1, 1), max_value=date(2030, 12, 31)),
    t_delivered=st.times(),
    t_now=st.times(),
    k=st.integers(min_value=0, max_value=400),
)
def test_property_counts_store_calendar_days(
    day: date, t_delivered: time, t_now: time, k: int
) -> None:
    # Nonexistent/ambiguous local times around DST resolve to an instant on the same local date,
    # so the expected calendar-day difference stays exactly k.
    delivered_at = datetime.combine(day, t_delivered, tzinfo=LA).astimezone(UTC)
    now = datetime.combine(day + timedelta(days=k), t_now, tzinfo=LA).astimezone(UTC)
    assert days_since_delivery(now, delivered_at, LA) == k
