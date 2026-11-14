"""Profit and loss date ranges.

This month, quarter to date, year to date, and the last 12 months end today.
Last month is the previous calendar month, first day through last day.
Quarter to date starts on the first day of the current calendar quarter.
All time stays resolvable for older links and tests. It is not in the picker.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta


PERIOD_KEYS: tuple[str, ...] = (
    "this-month",
    "last-month",
    "qtd",
    "ytd",
    "last-12-months",
)

PERIOD_LABELS: dict[str, str] = {
    "this-month": "This month",
    "last-month": "Last month",
    "qtd": "Quarter to date",
    "ytd": "Year to date",
    "last-12-months": "Last 12 months",
}


@dataclass(frozen=True)
class Period:
    key: str
    label: str
    start: date | None
    end: date | None

    def contains(self, day: date | None) -> bool:
        if day is None:
            return False
        if self.start is not None and day < self.start:
            return False
        if self.end is not None and day > self.end:
            return False
        return True


def add_months(day: date, months: int) -> date:
    """Move a date by whole months, clamping the day to the target month."""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def quarter_start(day: date) -> date:
    """First day of the calendar quarter that contains `day`."""
    month = ((day.month - 1) // 3) * 3 + 1
    return date(day.year, month, 1)


def resolve_period(key: str, today: date) -> Period:
    if key == "all-time":
        return Period(key, "All time", None, None)
    if key not in PERIOD_LABELS:
        key = "this-month"
    label = PERIOD_LABELS[key]
    if key == "this-month":
        return Period(key, label, today.replace(day=1), today)
    if key == "last-month":
        first_this_month = today.replace(day=1)
        end = first_this_month - timedelta(days=1)
        return Period(key, label, end.replace(day=1), end)
    if key == "qtd":
        return Period(key, label, quarter_start(today), today)
    if key == "ytd":
        return Period(key, label, date(today.year, 1, 1), today)
    start = add_months(today, -12) + timedelta(days=1)
    return Period(key, label, start, today)


def format_period(period: Period) -> str:
    if period.start is None and period.end is None:
        return "All time"
    assert period.start is not None and period.end is not None
    return f"{period.start.isoformat()} – {period.end.isoformat()}"


def period_choice(
    query_value: str | None,
    widget_value: str | None,
    last_seen_query: str | None,
) -> tuple[str, str]:
    """Pick the period key and the query value to remember.

    A changed URL wins over the select box so a link can carry the period.
    Otherwise the select box wins.
    """
    requested = query_value if query_value in PERIOD_LABELS else "this-month"
    if last_seen_query != requested:
        return requested, requested
    if widget_value in PERIOD_LABELS:
        return widget_value, requested
    return requested, requested
