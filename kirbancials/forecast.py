"""Month-end forecast of total expenses for the profit and loss header.

Card charges are split into one-time, recurring, and everyday spend. Large
one-time charges and recurring bills are counted once. Only everyday card
spend is paced, leaning on recent history early in the month and on this
month as the month goes on.

Interest, scheduled loans, and statement-only cards are not paced. They are
included at the full calendar month's amount from the same expense lines as
Total expenses. The result is never below month-to-date total expenses.
"""

from __future__ import annotations

import calendar
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from kirbancials.activity import (
    TXN_ACCOUNT,
    TXN_AMOUNT,
    TXN_CATEGORY,
    TXN_DATE,
    TXN_DESCRIPTION,
    TXN_ID,
    TXN_MERCHANT,
    TXN_PENDING,
)
from kirbancials.discretionary import (
    CATEGORY_UP_RATIO,
    discretionary_line_amount,
    discretionary_previous_month,
    is_discretionary_transaction,
    is_financing_expense,
)
from kirbancials.periods import Period
from kirbancials.spend import expense_lines

# A charge at least this large is one-time even when the card has no history.
ONE_TIME_MIN_AMOUNT = 1000.0
# Or at least this many times the card's median daily spend over the trailing window.
ONE_TIME_MEDIAN_MULTIPLE = 3.0
# Recurring bills match amount and day-of-month within these tolerances.
RECURRING_AMOUNT_TOLERANCE = 0.15
RECURRING_DAY_TOLERANCE = 5
# A bill must hit this many of the prior calendar months before it is expected.
RECURRING_HISTORY_MONTHS = 3
RECURRING_MIN_MONTHS = 2
# Trailing everyday pace uses the long end of the 60–90 day range.
TRAILING_PACE_DAYS = 90
# A pending row and a posted row this close, for the same card and merchant
# and amount, are one charge. The posted row is kept.
PENDING_MATCH_DAYS = 4


@dataclass(frozen=True)
class FlaggedCharge:
    """A charge the forecast refused to spread across the rest of the month."""

    when: date
    account: str
    merchant: str
    amount: float
    kind: str


@dataclass(frozen=True)
class ExpenseForecast:
    """Total-expense forecast for one calendar month, through `as_of`.

    `other_expenses` is interest, scheduled loans, and statement cards for
    the full month. `other_card_spend` is card spend already in Total
    expenses that the pace split did not list on its own.
    """

    total: float
    one_time: float
    recurring_posted: float
    bills_still_to_come: float
    everyday_to_date: float
    everyday_pace: float
    days_elapsed: int
    days_remaining: int
    last_month_actual: float | None
    one_time_charges: tuple[FlaggedCharge, ...]
    recurring_charges: tuple[FlaggedCharge, ...]
    expected_bills: tuple[FlaggedCharge, ...]
    other_expenses: float = 0.0
    other_card_spend: float = 0.0
    month_to_date: float = 0.0
    categories: tuple[CategoryTrend, ...] = ()


@dataclass(frozen=True)
class CategoryTrend:
    """Discretionary spend for one category."""

    category: str
    month_to_date: float
    forecast: float
    last_month: float
    trailing_average: float
    increase: float
    trending: bool
    transactions: tuple[FlaggedCharge, ...]


@dataclass(frozen=True)
class _Charge:
    when: date
    account: str
    merchant: str
    amount: float
    pending: bool
    transaction_id: str
    category: str
    description: str


@dataclass(frozen=True)
class _Pattern:
    account: str
    merchant_key: str
    merchant: str
    amount: float
    day: int


def month_end_forecast(
    transactions: pd.DataFrame | None,
    as_of: date,
    monthly: pd.DataFrame | None = None,
    balances: pd.DataFrame | None = None,
    schedule: pd.DataFrame | None = None,
) -> ExpenseForecast:
    """Forecast total expenses through the end of the month that contains `as_of`.

    `as_of` is the last day already counted. Card charges already in Total
    expenses stay in the forecast. A negative everyday pace does not reduce
    them. Interest and statement cards use the full month.
    """
    month_start, through, month_end = _month_bounds(as_of)
    charges = _spend_charges(transactions)
    days_elapsed = (through - month_start).days + 1
    days_in_month = (month_end - month_start).days + 1
    days_remaining = days_in_month - days_elapsed

    history_months = set(_prior_month_keys(month_start, RECURRING_HISTORY_MONTHS))
    patterns = _patterns(charges, history_months)
    medians = _median_daily_spend(charges, month_start)

    one_time: list[_Charge] = []
    recurring: list[_Charge] = []
    everyday: list[_Charge] = []
    for charge in charges:
        if not _in_month_through(charge.when, month_start, through):
            continue
        if _matches_pattern(charge, patterns) is not None:
            recurring.append(charge)
        elif _is_one_time(charge, medians):
            one_time.append(charge)
        else:
            everyday.append(charge)

    expected = _expected_bills(patterns, recurring, through, month_end)
    one_time_total = _sum(one_time)
    recurring_total = _sum(recurring)
    everyday_total = _sum(everyday)
    month_pace = everyday_total / days_elapsed if days_elapsed else 0.0
    trailing_pace = _trailing_everyday_pace(charges, patterns, month_start)
    weight = days_elapsed / days_in_month if days_in_month else 0.0
    blended = (1.0 - weight) * trailing_pace + weight * month_pace
    paced = max(blended, 0.0) * days_remaining
    card_mtd, other_mtd, _card_full, other_full = _expense_split(
        transactions, monthly, balances, schedule, month_start, through, month_end
    )
    classified = one_time_total + recurring_total + everyday_total
    other_card = max(card_mtd - classified, 0.0)
    other_expenses = max(other_full, other_mtd)
    bills = _sum_flagged(expected)
    total = classified + other_card + bills + paced + other_expenses
    month_to_date = card_mtd + other_mtd
    if total < month_to_date:
        other_expenses += month_to_date - total
        total = month_to_date
    categories = _category_trends(
        charges,
        one_time,
        recurring,
        everyday,
        expected,
        patterns,
        medians,
        month_start,
        through,
        days_elapsed,
        days_in_month,
        transactions,
        monthly,
        balances,
        schedule,
    )
    return ExpenseForecast(
        total=total,
        one_time=one_time_total,
        recurring_posted=recurring_total,
        bills_still_to_come=bills,
        everyday_to_date=everyday_total,
        everyday_pace=blended,
        days_elapsed=days_elapsed,
        days_remaining=days_remaining,
        last_month_actual=_last_month_total(
            transactions, monthly, balances, schedule, month_start
        ),
        one_time_charges=tuple(_flag(charge, "one-time") for charge in one_time),
        recurring_charges=tuple(_flag(charge, "recurring") for charge in recurring),
        expected_bills=tuple(expected),
        other_expenses=other_expenses,
        other_card_spend=other_card,
        month_to_date=month_to_date,
        categories=categories,
    )


def _expense_split(
    transactions: pd.DataFrame | None,
    monthly: pd.DataFrame | None,
    balances: pd.DataFrame | None,
    schedule: pd.DataFrame | None,
    month_start: date,
    through: date,
    month_end: date,
) -> tuple[float, float, float, float]:
    """Transaction and other totals for month-to-date and for the full month."""
    card_mtd, other_mtd = _line_totals(transactions, monthly, balances, schedule, month_start, through)
    card_full, other_full = _line_totals(transactions, monthly, balances, schedule, month_start, month_end)
    return card_mtd, other_mtd, card_full, other_full


def _line_totals(
    transactions: pd.DataFrame | None,
    monthly: pd.DataFrame | None,
    balances: pd.DataFrame | None,
    schedule: pd.DataFrame | None,
    start: date,
    end: date,
) -> tuple[float, float]:
    period = Period("forecast", "Forecast", start, end)
    lines = expense_lines(
        transactions if transactions is not None else pd.DataFrame(),
        monthly if monthly is not None else pd.DataFrame(),
        period,
        balances,
        schedule,
    )
    card = 0.0
    other = 0.0
    for line in lines:
        amount = discretionary_line_amount(line, transactions, period)
        if amount is None or is_financing_expense(line):
            continue
        if line.data_type == "transactions":
            card += amount
        else:
            other += amount
    return card, other


def _last_month_total(
    transactions: pd.DataFrame | None,
    monthly: pd.DataFrame | None,
    balances: pd.DataFrame | None,
    schedule: pd.DataFrame | None,
    month_start: date,
) -> float | None:
    """Last month's discretionary total, hidden when that month has none."""
    period = Period("forecast", "Forecast", month_start, month_start)
    return discretionary_previous_month(transactions, monthly, period, balances, schedule)


def _category_trends(
    charges: list[_Charge],
    one_time: list[_Charge],
    recurring: list[_Charge],
    everyday: list[_Charge],
    expected: list[FlaggedCharge],
    patterns: list[_Pattern],
    medians: dict[str, float],
    month_start: date,
    through: date,
    days_elapsed: int,
    days_in_month: int,
    transactions: pd.DataFrame | None,
    monthly: pd.DataFrame | None,
    balances: pd.DataFrame | None,
    schedule: pd.DataFrame | None,
) -> tuple[CategoryTrend, ...]:
    """This month, a paced forecast, last month, and the prior three-month average."""
    one_ids = {id(charge) for charge in one_time}
    recurring_ids = {id(charge) for charge in recurring}
    everyday_ids = {id(charge) for charge in everyday}
    prior_months = _prior_month_keys(month_start, 3)
    prior_keys = set(prior_months)
    last_key = prior_months[0] if prior_months else None
    window_start, window_end = _trailing_window(month_start)
    buckets: dict[str, dict[str, object]] = {}

    def bucket(name: str) -> dict[str, object]:
        label = name.strip() or "Uncategorized"
        found = buckets.get(label)
        if found is None:
            found = {
                "mtd": 0.0,
                "last": 0.0,
                "trail": 0.0,
                "one": 0.0,
                "recurring": 0.0,
                "everyday": 0.0,
                "bills": 0.0,
                "history_everyday": 0.0,
                "transactions": [],
            }
            buckets[label] = found
        return found

    for charge in charges:
        label = charge.category or "Uncategorized"
        key = (charge.when.year, charge.when.month)
        row = bucket(label)
        if _in_month_through(charge.when, month_start, through):
            row["mtd"] = float(row["mtd"]) + charge.amount
            row["transactions"].append(_flag(charge, "spend"))
            if id(charge) in one_ids:
                row["one"] = float(row["one"]) + charge.amount
            elif id(charge) in recurring_ids:
                row["recurring"] = float(row["recurring"]) + charge.amount
            elif id(charge) in everyday_ids:
                row["everyday"] = float(row["everyday"]) + charge.amount
        if last_key is not None and key == last_key:
            row["last"] = float(row["last"]) + charge.amount
        if key in prior_keys:
            row["trail"] = float(row["trail"]) + charge.amount
        if window_start <= charge.when <= window_end and not _is_one_time(charge, medians):
            if _matches_pattern(charge, patterns) is None:
                row["history_everyday"] = float(row["history_everyday"]) + charge.amount

    for bill in expected:
        label = _category_for_bill(bill, charges, month_start)
        row = bucket(label)
        row["bills"] = float(row["bills"]) + bill.amount

    _add_statement_categories(
        buckets, bucket, transactions, monthly, balances, schedule, month_start, through, prior_months
    )

    trends: list[CategoryTrend] = []
    for label, row in buckets.items():
        mtd = float(row["mtd"])
        everyday = float(row["everyday"])
        month_pace = everyday / days_elapsed if days_elapsed else 0.0
        trail_pace = float(row["history_everyday"]) / TRAILING_PACE_DAYS
        weight = days_elapsed / days_in_month if days_in_month else 0.0
        blended = (1.0 - weight) * trail_pace + weight * month_pace
        projected = (
            float(row["one"])
            + float(row["recurring"])
            + float(row["bills"])
            + everyday
            + max(blended, 0.0) * (days_in_month - days_elapsed)
        )
        if projected < mtd:
            projected = mtd
        average = float(row["trail"]) / 3.0
        increase = projected - average
        trending = projected >= average * CATEGORY_UP_RATIO and (projected > 0 or average > 0)
        if mtd == 0 and projected == 0 and float(row["last"]) == 0 and average == 0:
            continue
        transactions_shown = tuple(
            sorted(row["transactions"], key=lambda item: (item.when, item.merchant), reverse=True)
        )
        trends.append(
            CategoryTrend(
                category=label,
                month_to_date=mtd,
                forecast=projected,
                last_month=float(row["last"]),
                trailing_average=average,
                increase=increase,
                trending=trending,
                transactions=transactions_shown,
            )
        )
    trends.sort(key=lambda item: (-item.increase, item.category.casefold()))
    return tuple(trends)


def _category_for_bill(bill: FlaggedCharge, charges: list[_Charge], month_start: date) -> str:
    latest: _Charge | None = None
    for charge in charges:
        if charge.when >= month_start or charge.amount <= 0:
            continue
        if charge.account != bill.account:
            continue
        if _merchant_key(charge.merchant) != _merchant_key(bill.merchant):
            continue
        if latest is None or charge.when > latest.when:
            latest = charge
    if latest is None or not latest.category:
        return "Uncategorized"
    return latest.category


def _add_statement_categories(
    buckets: dict[str, dict[str, object]],
    bucket,
    transactions: pd.DataFrame | None,
    monthly: pd.DataFrame | None,
    balances: pd.DataFrame | None,
    schedule: pd.DataFrame | None,
    month_start: date,
    through: date,
    prior_months: list[tuple[int, int]],
) -> None:
    """Statement cards have no category column. Each card is its own row."""
    periods = [(month_start, through, "mtd")]
    for year, month in prior_months:
        start = date(year, month, 1)
        last = calendar.monthrange(year, month)[1]
        kind = "last" if (year, month) == prior_months[0] else "older"
        periods.append((start, date(year, month, last), kind))
    full_last = calendar.monthrange(month_start.year, month_start.month)[1]
    periods.append((month_start, date(month_start.year, month_start.month, full_last), "full"))
    for start, end, kind in periods:
        period = Period("forecast", "Forecast", start, end)
        lines = expense_lines(
            transactions if transactions is not None else pd.DataFrame(),
            monthly if monthly is not None else pd.DataFrame(),
            period,
            balances,
            schedule,
        )
        for line in lines:
            if line.data_type != "statements" or is_financing_expense(line):
                continue
            row = bucket(line.name)
            if kind == "mtd":
                row["mtd"] = float(row["mtd"]) + line.total
            elif kind == "full":
                row["one"] = float(row["one"]) + line.total
            elif kind == "last":
                row["last"] = float(row["last"]) + line.total
                row["trail"] = float(row["trail"]) + line.total
            else:
                row["trail"] = float(row["trail"]) + line.total


def _month_bounds(as_of: date) -> tuple[date, date, date]:
    start = as_of.replace(day=1)
    last = calendar.monthrange(as_of.year, as_of.month)[1]
    end = date(as_of.year, as_of.month, last)
    through = min(as_of, end)
    return start, through, end


def _prior_month_keys(month_start: date, count: int) -> list[tuple[int, int]]:
    keys: list[tuple[int, int]] = []
    cursor = month_start
    for _ in range(count):
        cursor = (cursor.replace(day=1) - timedelta(days=1)).replace(day=1)
        keys.append((cursor.year, cursor.month))
    return keys


def _spend_charges(transactions: pd.DataFrame | None) -> list[_Charge]:
    if transactions is None or transactions.empty or TXN_DATE not in transactions.columns:
        return []
    if TXN_AMOUNT not in transactions.columns:
        return []
    charges: list[_Charge] = []
    for record in transactions.to_dict("records"):
        when = _as_date(record.get(TXN_DATE))
        amount = _as_float(record.get(TXN_AMOUNT))
        if when is None or amount is None:
            continue
        account = _text(record.get(TXN_ACCOUNT))
        merchant = _text(record.get(TXN_MERCHANT))
        category = _text(record.get(TXN_CATEGORY))
        description = _text(record.get(TXN_DESCRIPTION))
        if not is_discretionary_transaction(amount, category, merchant, description, account):
            continue
        pending = bool(record.get(TXN_PENDING))
        charges.append(
            _Charge(
                when=when,
                account=account,
                merchant=merchant,
                amount=amount,
                pending=pending,
                transaction_id=_text(record.get(TXN_ID)),
                category=category,
                description=description,
            )
        )
    return _drop_pending_copies(charges)


def _drop_pending_copies(charges: list[_Charge]) -> list[_Charge]:
    """Posted wins over pending when they share an id, or the same card, merchant, and amount."""
    by_id: dict[str, _Charge] = {}
    anonymous: list[_Charge] = []
    for charge in charges:
        if not charge.transaction_id:
            anonymous.append(charge)
            continue
        current = by_id.get(charge.transaction_id)
        if current is None or (current.pending and not charge.pending):
            by_id[charge.transaction_id] = charge
    rows = list(by_id.values()) + anonymous
    posted = [charge for charge in rows if not charge.pending]
    kept = list(posted)
    for pending in (charge for charge in rows if charge.pending):
        if any(_same_posting(pending, posted_charge) for posted_charge in posted):
            continue
        kept.append(pending)
    return kept


def _same_posting(pending: _Charge, posted: _Charge) -> bool:
    if pending.account != posted.account:
        return False
    if _merchant_key(pending.merchant) != _merchant_key(posted.merchant):
        return False
    if abs(pending.amount - posted.amount) > 0.009:
        return False
    return abs((pending.when - posted.when).days) <= PENDING_MATCH_DAYS


def _patterns(charges: list[_Charge], months: set[tuple[int, int]]) -> list[_Pattern]:
    grouped: dict[tuple[str, str], list[_Charge]] = {}
    for charge in charges:
        key = (charge.when.year, charge.when.month)
        if key not in months or charge.amount <= 0:
            continue
        grouped.setdefault((charge.account, _merchant_key(charge.merchant)), []).append(charge)
    found: list[_Pattern] = []
    for (account, merchant_key), group in grouped.items():
        found.extend(_patterns_for_merchant(account, merchant_key, group))
    return found


def _patterns_for_merchant(account: str, merchant_key: str, group: list[_Charge]) -> list[_Pattern]:
    unused = list(group)
    patterns: list[_Pattern] = []
    while unused:
        best: tuple[tuple[int, int], list[_Charge]] | None = None
        for seed in unused:
            members = [
                charge
                for charge in unused
                if _amount_close(charge.amount, seed.amount) and _day_close(charge.when.day, seed.when.day)
            ]
            picked = _one_per_month(members, seed.when.day, seed.amount)
            distinct = {(charge.when.year, charge.when.month) for charge in picked}
            if len(distinct) < RECURRING_MIN_MONTHS:
                continue
            rank = (len(distinct), len(picked))
            if best is None or rank > best[0]:
                best = (rank, picked)
        if best is None:
            break
        picked = best[1]
        amounts = [charge.amount for charge in picked]
        days = [charge.when.day for charge in picked]
        label = next((charge.merchant for charge in picked if charge.merchant), merchant_key)
        patterns.append(
            _Pattern(
                account=account,
                merchant_key=merchant_key,
                merchant=label or merchant_key or "Recurring charge",
                amount=float(statistics.median(amounts)),
                day=int(round(statistics.median(days))),
            )
        )
        chosen = {id(charge) for charge in picked}
        unused = [charge for charge in unused if id(charge) not in chosen]
    return patterns


def _one_per_month(members: list[_Charge], day: int, amount: float) -> list[_Charge]:
    by_month: dict[tuple[int, int], _Charge] = {}
    for charge in members:
        key = (charge.when.year, charge.when.month)
        current = by_month.get(key)
        if current is None or _closer(charge, current, day, amount):
            by_month[key] = charge
    return list(by_month.values())


def _closer(candidate: _Charge, current: _Charge, day: int, amount: float) -> bool:
    candidate_rank = (abs(candidate.when.day - day), abs(candidate.amount - amount))
    current_rank = (abs(current.when.day - day), abs(current.amount - amount))
    return candidate_rank < current_rank


def _matches_pattern(charge: _Charge, patterns: list[_Pattern]) -> _Pattern | None:
    if charge.amount <= 0:
        return None
    for pattern in patterns:
        if pattern.account != charge.account:
            continue
        if pattern.merchant_key != _merchant_key(charge.merchant):
            continue
        if _amount_close(charge.amount, pattern.amount) and _day_close(charge.when.day, pattern.day):
            return pattern
    return None


def _expected_bills(
    patterns: list[_Pattern],
    posted: list[_Charge],
    through: date,
    month_end: date,
) -> list[FlaggedCharge]:
    if through >= month_end:
        return []
    expected: list[FlaggedCharge] = []
    for pattern in patterns:
        if any(_matches_pattern(charge, [pattern]) is not None for charge in posted):
            continue
        if through.day > pattern.day + RECURRING_DAY_TOLERANCE:
            continue
        year, month = through.year, through.month
        last = calendar.monthrange(year, month)[1]
        when = date(year, month, min(pattern.day, last))
        expected.append(
            FlaggedCharge(
                when=when,
                account=pattern.account,
                merchant=pattern.merchant,
                amount=pattern.amount,
                kind="expected",
            )
        )
    return expected


def _is_one_time(charge: _Charge, medians: dict[str, float]) -> bool:
    if charge.amount <= 0:
        return False
    if charge.amount >= ONE_TIME_MIN_AMOUNT:
        return True
    median = medians.get(charge.account)
    if median is None:
        return False
    return charge.amount >= ONE_TIME_MEDIAN_MULTIPLE * median


def _median_daily_spend(charges: list[_Charge], month_start: date) -> dict[str, float]:
    """Median positive day per card, over the trailing window before this month.

    Charges at or above the dollar threshold are left out so one large bill
    does not set the bar for every later charge.
    """
    start, end = _trailing_window(month_start)
    daily: dict[tuple[str, date], float] = {}
    for charge in charges:
        if charge.when < start or charge.when > end:
            continue
        if charge.amount >= ONE_TIME_MIN_AMOUNT:
            continue
        key = (charge.account, charge.when)
        daily[key] = daily.get(key, 0.0) + charge.amount
    by_account: dict[str, list[float]] = {}
    for (account, _day), total in daily.items():
        if total > 0:
            by_account.setdefault(account, []).append(total)
    return {
        account: float(statistics.median(totals))
        for account, totals in by_account.items()
        if totals
    }


def _trailing_everyday_pace(
    charges: list[_Charge],
    patterns: list[_Pattern],
    month_start: date,
) -> float:
    start, end = _trailing_window(month_start)
    medians = _median_daily_spend(charges, month_start)
    total = 0.0
    for charge in charges:
        if charge.when < start or charge.when > end:
            continue
        if _is_one_time(charge, medians):
            continue
        if _matches_pattern(charge, patterns) is not None:
            continue
        total += charge.amount
    return total / TRAILING_PACE_DAYS


def _trailing_window(month_start: date) -> tuple[date, date]:
    end = month_start - timedelta(days=1)
    start = end - timedelta(days=TRAILING_PACE_DAYS - 1)
    return start, end


def _in_month_through(when: date, month_start: date, through: date) -> bool:
    return month_start <= when <= through


def _amount_close(amount: float, baseline: float) -> bool:
    if baseline <= 0 or amount <= 0:
        return False
    return abs(amount - baseline) / baseline <= RECURRING_AMOUNT_TOLERANCE


def _day_close(day: int, target: int) -> bool:
    return abs(day - target) <= RECURRING_DAY_TOLERANCE


def _flag(charge: _Charge, kind: str) -> FlaggedCharge:
    return FlaggedCharge(charge.when, charge.account, charge.merchant or charge.account, charge.amount, kind)


def _sum(charges: list[_Charge]) -> float:
    return float(sum(charge.amount for charge in charges))


def _sum_flagged(charges: list[FlaggedCharge]) -> float:
    return float(sum(charge.amount for charge in charges))


def _merchant_key(merchant: str) -> str:
    return " ".join(merchant.casefold().split())


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    return str(value).strip()


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_date(value: object) -> date | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, date) and not isinstance(value, pd.Timestamp):
        # datetime is a date subclass. pd.Timestamp is handled below.
        if type(value) is date:
            return value
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()
