"""Dashboard figures for the profit and loss page.

The period totals still come from income and expense lines. This module adds
the run rate, the debt snapshot, next month's expected income, and the
six-month chart series. It does not change how a deposit or a charge is counted.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from kirbancials.activity import account_balance_kind, latest_account_balances
from kirbancials.income import total_received
from kirbancials.periods import Period, add_months, resolve_period
from kirbancials.sources import INCOME_SOURCES, IncomeSource
from kirbancials.spend import expense_lines

CHART_MONTHS = 6


@dataclass(frozen=True)
class RunRate:
    """Net per month for a period.

    Periods shorter than one calendar month are scaled to 30 days.
    Longer periods are the average net per calendar month, with a partial
    month counted as a fraction of its length.
    """

    amount: float | None
    label: str

    @property
    def scaled_to_30_days(self) -> bool:
        return self.label == "Scaled to 30 days"


@dataclass(frozen=True)
class ExpectedIncome:
    """Cash expected next calendar month. Irregular sources are left out."""

    total: float
    irregular_names: tuple[str, ...]

    @property
    def note(self) -> str:
        if not self.irregular_names:
            return ""
        return "Irregular excluded: " + ", ".join(self.irregular_names)


def months_spanned(start: date, end: date) -> float:
    """Inclusive fraction of calendar months from start through end."""
    if end < start:
        return 0.0
    total = 0.0
    year, month = start.year, start.month
    while True:
        dim = calendar.monthrange(year, month)[1]
        month_start = date(year, month, 1)
        month_end = date(year, month, dim)
        overlap_start = max(start, month_start)
        overlap_end = min(end, month_end)
        if overlap_end >= overlap_start:
            total += ((overlap_end - overlap_start).days + 1) / dim
        if month_end >= end:
            return total
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1


def monthly_run_rate(net: float, period: Period) -> RunRate:
    """Average net per month, or a 30-day scale when the period is shorter."""
    if period.start is None or period.end is None:
        return RunRate(None, "No fixed period")
    if period.end < period.start:
        return RunRate(0.0, "No days in period")
    days = (period.end - period.start).days + 1
    span = months_spanned(period.start, period.end)
    if span < 1:
        return RunRate(net * 30.0 / days, "Scaled to 30 days")
    return RunRate(net / span, "Average net per month")


def credit_card_balance_total(balances: pd.DataFrame | None) -> float:
    """Sum the latest current balance of every credit-card account.

    Registered auto loans are not cards. An Account Type of depository, cash,
    checking, or savings is cash, and a loan type is a loan. A row with no
    type is still a card unless its name is a registered loan.
    """
    total = 0.0
    for item in latest_account_balances(balances):
        if account_balance_kind(item.account, item.account_type) == "credit":
            total += item.amount
    return float(total)


def estimated_loan_interest_this_month(
    balances: pd.DataFrame | None,
    today: date,
    schedule: pd.DataFrame | None = None,
) -> float:
    """Loan interest from the first of this month through today.

    Estimated rows use the current balance and APR. Scheduled rows add the
    Interest of payments dated in this month. Both come from expense_lines,
    which is the same total the profit and loss page uses.
    """
    period = resolve_period("this-month", today)
    lines = expense_lines(pd.DataFrame(), pd.DataFrame(), period, balances, schedule)
    return sum(line.total for line in lines if line.data_type in {"loan", "schedule"})


def expected_income_next_month(
    deposits: pd.DataFrame | None,
    today: date,
    sources: tuple[IncomeSource, ...] | list[IncomeSource] | None = None,
) -> ExpectedIncome:
    """Sum expected cash for the next calendar month.

    Monthly sources contribute their last payment amount. Quarterly sources
    contribute that amount only when the last payment date plus three months
    falls in the next calendar month. Irregular sources are excluded.
    A payment dated after `today` is not a last payment yet.
    """
    chosen = tuple(INCOME_SOURCES if sources is None else sources)
    window = _next_month(today)
    total = 0.0
    irregular: list[str] = []
    for source in chosen:
        kind = source.frequency.strip().lower()
        if kind == "irregular":
            irregular.append(source.name)
            continue
        last = _last_payment(deposits, source.id, today)
        if last is None:
            continue
        last_date, amount = last
        if kind == "monthly":
            total += amount
        elif kind == "quarterly" and _in_month(add_months(last_date, 3), window):
            total += amount
    return ExpectedIncome(total, tuple(irregular))


def expense_data_start(transactions: pd.DataFrame | None) -> date | None:
    """Earliest transaction date. The payload only covers a short window."""
    if transactions is None or transactions.empty or "Date" not in transactions.columns:
        return None
    parsed = pd.to_datetime(transactions["Date"], errors="coerce").dropna()
    if parsed.empty:
        return None
    return parsed.min().date()


def expense_coverage_note(start: date | None, available_from: date | None) -> str | None:
    """Small note when a range starts before the transaction window."""
    if available_from is None:
        return None
    if start is None or start < available_from:
        return f"Expense data available from {available_from.isoformat()}"
    return None


def recent_month_periods(today: date, count: int = CHART_MONTHS) -> list[Period]:
    """The last `count` calendar months, ending today for the current month."""
    first_this_month = date(today.year, today.month, 1)
    periods: list[Period] = []
    for offset in range(count - 1, -1, -1):
        start = add_months(first_this_month, -offset)
        last_day = calendar.monthrange(start.year, start.month)[1]
        end = min(date(start.year, start.month, last_day), today)
        periods.append(
            Period(
                key=start.strftime("%Y-%m"),
                label=start.strftime("%b %Y"),
                start=start,
                end=end,
            )
        )
    return periods


def income_expense_by_month(
    transactions: pd.DataFrame,
    monthly: pd.DataFrame,
    deposits: pd.DataFrame | None,
    balances: pd.DataFrame | None,
    today: date,
    count: int = CHART_MONTHS,
    schedule: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Income, expenses, and net for each of the last `count` months.

    A month that ends before the transaction window, and whose other expense
    rows also total zero, is left blank instead of plotted as zero.
    """
    available = expense_data_start(transactions)
    rows: list[dict[str, object]] = []
    for period in recent_month_periods(today, count):
        income = total_received(deposits, period)
        expenses = sum(
            line.total for line in expense_lines(transactions, monthly, period, balances, schedule)
        )
        end = period.end
        outside = available is not None and end is not None and end < available
        if outside and expenses == 0:
            expense_value: float | None = None
            net_value: float | None = None
        else:
            expense_value = float(expenses)
            net_value = float(income) - float(expenses)
        rows.append(
            {
                "month": period.label,
                "income": float(income),
                "expenses": expense_value,
                "net": net_value,
            }
        )
    return pd.DataFrame(rows)


def _next_month(today: date) -> tuple[date, date]:
    start = add_months(date(today.year, today.month, 1), 1)
    last_day = calendar.monthrange(start.year, start.month)[1]
    return start, date(start.year, start.month, last_day)


def _in_month(day: date, window: tuple[date, date]) -> bool:
    return window[0] <= day <= window[1]


def _last_payment(
    deposits: pd.DataFrame | None,
    source_id: str,
    today: date,
) -> tuple[date, float] | None:
    """Latest payment on or before today. Same-day amounts are summed."""
    if deposits is None or deposits.empty:
        return None
    if "Source" not in deposits.columns or "Date" not in deposits.columns or "Amount" not in deposits.columns:
        return None
    rows = deposits.loc[deposits["Source"] == source_id].copy()
    if rows.empty:
        return None
    rows["_day"] = rows["Date"].map(_as_date)
    rows["_amount"] = pd.to_numeric(rows["Amount"], errors="coerce")
    rows = rows.loc[rows["_day"].map(lambda day: day is not None and day <= today)]
    rows = rows.dropna(subset=["_amount"])
    if rows.empty:
        return None
    latest = max(day for day in rows["_day"] if day is not None)
    amount = float(rows.loc[rows["_day"] == latest, "_amount"].sum())
    return latest, amount


def _as_date(value: object) -> date | None:
    if value is None or isinstance(value, bool):
        return None
    if type(value) is date:
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        if pd.isna(value):
            return None
    except TypeError:
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()
