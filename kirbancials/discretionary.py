"""What the profit and loss page treats as discretionary spending.

Debt service stays off this page. Adjust the sets below to put a financing
line back, or to take another one off. Credit-card payments and transfers
are excluded here too.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from kirbancials.activity import (
    TXN_ACCOUNT,
    TXN_AMOUNT,
    TXN_CATEGORY,
    TXN_DATE,
    TXN_DESCRIPTION,
    TXN_MERCHANT,
)
from kirbancials.overview import expense_data_start, recent_month_periods, total_received
from kirbancials.periods import Period, prior_calendar_month
from kirbancials.sources import account_from_unmapped_id, expense_source_by_id, loan_accounts
from kirbancials.spend import ExpenseLine, expense_lines, is_card_payment

# Registered financing sources. Removing an id shows that line on Profit & Loss again.
DISCRETIONARY_EXCLUDED_SOURCE_IDS = frozenset(
    {
        "wf_auto_7635",
        "wf_auto_8492",
        "truist_auto_1001",
        "jeep_0001",
        "ks_statebank_805_loc_1279",
        "morpho_loan",
    }
)
# Every estimated loan and every amortization schedule, including the FFB
# Chipotle loan, which is created from the schedule file rather than the list above.
DISCRETIONARY_EXCLUDED_DATA_TYPES = frozenset({"loan", "schedule"})
# Card payments and transfers. Matched as phrases so the wording can be edited in one place.
DISCRETIONARY_EXCLUDED_CATEGORY_PHRASES = (
    "loan payment",
    "transfer in",
    "transfer out",
)
FINANCING_NOTE = "Excludes loans and LOC interest (see Bills)"
# A category is trending up when its forecast is at least this much above the prior 3-month average.
CATEGORY_UP_RATIO = 1.20


def is_financing_expense(line: ExpenseLine) -> bool:
    """True for a loan, a line of credit, or scheduled loan interest."""
    if line.data_type in DISCRETIONARY_EXCLUDED_DATA_TYPES:
        return True
    return line.source_id in DISCRETIONARY_EXCLUDED_SOURCE_IDS


def is_discretionary_text(category: object, merchant: object = "", description: object = "") -> bool:
    """False when the text is a loan payment or a transfer."""
    blob = " ".join(_text(part) for part in (category, merchant, description)).casefold()
    return not any(phrase in blob for phrase in DISCRETIONARY_EXCLUDED_CATEGORY_PHRASES)


def is_discretionary_transaction(
    amount: object,
    category: object = "",
    merchant: object = "",
    description: object = "",
    account: object = "",
) -> bool:
    """Card spend that belongs on the discretionary tracker."""
    if _text(account) in loan_accounts():
        return False
    if is_card_payment(amount, category, merchant, description):
        return False
    return is_discretionary_text(category, merchant, description)


def discretionary_lines(lines: list[ExpenseLine]) -> list[ExpenseLine]:
    return [line for line in lines if not is_financing_expense(line)]


def line_accounts(line: ExpenseLine) -> tuple[str, ...]:
    source = expense_source_by_id(line.source_id)
    if source is not None and source.accounts:
        return source.accounts
    account = account_from_unmapped_id(line.source_id)
    if account:
        return (account,)
    return (line.name,)


def discretionary_card_total(
    transactions: pd.DataFrame | None,
    accounts: tuple[str, ...],
    period: Period,
) -> float:
    """Discretionary charges and refunds on these cards in the period."""
    if transactions is None or transactions.empty or TXN_DATE not in transactions.columns:
        return 0.0
    chosen = set(accounts)
    total = 0.0
    for record in transactions.to_dict("records"):
        account = _text(record.get(TXN_ACCOUNT))
        if chosen and account not in chosen:
            continue
        when = _as_date(record.get(TXN_DATE))
        if not period.contains(when):
            continue
        amount = _as_float(record.get(TXN_AMOUNT))
        if amount is None:
            continue
        if not is_discretionary_transaction(
            amount,
            record.get(TXN_CATEGORY),
            record.get(TXN_MERCHANT),
            record.get(TXN_DESCRIPTION),
            account,
        ):
            continue
        total += amount
    return total


def discretionary_line_amount(
    line: ExpenseLine,
    transactions: pd.DataFrame | None,
    period: Period,
) -> float | None:
    """Amount to show for one expense row. None hides a financing line."""
    if is_financing_expense(line):
        return None
    if line.data_type == "statements":
        return line.total
    if line.data_type != "transactions":
        return None
    return discretionary_card_total(transactions, line_accounts(line), period)


def discretionary_total(
    transactions: pd.DataFrame | None,
    monthly: pd.DataFrame | None,
    period: Period,
    balances: pd.DataFrame | None = None,
    schedule: pd.DataFrame | None = None,
) -> float:
    """Same expense lines as the page, with financing removed."""
    lines = expense_lines(
        transactions if transactions is not None else pd.DataFrame(),
        monthly if monthly is not None else pd.DataFrame(),
        period,
        balances,
        schedule,
    )
    total = 0.0
    for line in lines:
        amount = discretionary_line_amount(line, transactions, period)
        if amount is None:
            continue
        total += amount
    return total


def discretionary_previous_month(
    transactions: pd.DataFrame | None,
    monthly: pd.DataFrame | None,
    period: Period,
    balances: pd.DataFrame | None = None,
    schedule: pd.DataFrame | None = None,
) -> float | None:
    """Discretionary total for the calendar month before `period`.

    None when that month has no discretionary charges or statement cards.
    """
    prior = prior_calendar_month(period)
    if prior is None:
        return None
    total = discretionary_total(transactions, monthly, prior, balances, schedule)
    if total != 0:
        return total
    if _period_has_discretionary(transactions, monthly, prior, balances, schedule):
        return 0.0
    return None


def _period_has_discretionary(
    transactions: pd.DataFrame | None,
    monthly: pd.DataFrame | None,
    period: Period,
    balances: pd.DataFrame | None,
    schedule: pd.DataFrame | None,
) -> bool:
    if discretionary_card_total(transactions, (), period) != 0:
        return True
    if transactions is not None and not transactions.empty and TXN_DATE in transactions.columns:
        for record in transactions.to_dict("records"):
            when = _as_date(record.get(TXN_DATE))
            if not period.contains(when):
                continue
            amount = _as_float(record.get(TXN_AMOUNT))
            if amount is None:
                continue
            if is_discretionary_transaction(
                amount,
                record.get(TXN_CATEGORY),
                record.get(TXN_MERCHANT),
                record.get(TXN_DESCRIPTION),
                record.get(TXN_ACCOUNT),
            ):
                return True
    lines = expense_lines(
        transactions if transactions is not None else pd.DataFrame(),
        monthly if monthly is not None else pd.DataFrame(),
        period,
        balances,
        schedule,
    )
    return any(line.data_type == "statements" and line.total != 0 and not is_financing_expense(line) for line in lines)


def discretionary_history_frame(
    transactions: pd.DataFrame | None,
    monthly: pd.DataFrame | None,
    deposits: pd.DataFrame | None,
    today: date,
    count: int = 6,
) -> pd.DataFrame:
    """Income and discretionary expenses for the last `count` months."""
    available = expense_data_start(transactions)
    rows: list[dict[str, object]] = []
    for period in recent_month_periods(today, count):
        income = total_received(deposits, period)
        spent = discretionary_total(transactions, monthly, period)
        end = period.end
        outside = available is not None and end is not None and end < available
        if outside and spent == 0:
            expense_value: float | None = None
            net_value: float | None = None
        else:
            expense_value = float(spent)
            net_value = float(income) - float(spent)
        rows.append(
            {
                "month": period.label,
                "income": float(income),
                "expenses": expense_value,
                "net": net_value,
            }
        )
    return pd.DataFrame(rows)


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
    if isinstance(value, date) and type(value) is date:
        return value
    if value is None or isinstance(value, bool):
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()
