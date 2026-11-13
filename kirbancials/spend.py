"""Expense totals for the profit and loss page.

Transaction spend is charges minus refunds. Card payments are transfers, not
expenses. Statement spend is net spend plus interest and fees for statement
months whose closing date falls in the period.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import pandas as pd

from kirbancials.income import received_for_source, total_received
from kirbancials.loans import current_balance, elapsed_days, estimated_interest
from kirbancials.periods import Period
from kirbancials.sources import (
    EXPENSE_SOURCES,
    ExpenseSource,
    account_from_unmapped_id,
    expense_source_by_id,
    loan_accounts,
    mapped_accounts,
    unmapped_source_id,
)

_CATEGORY_PHRASES = ("loan payment", "transfer in", "transfer out")


def is_card_payment(
    amount: object,
    category: object = "",
    merchant: object = "",
    description: object = "",
) -> bool:
    """Return True only for a credit that pays the card down.

    The amount must be negative in the positive=charge convention. A charge
    stays in spend even when the text contains PYMT or PAYMENT, such as
    "US TREAS TAX PYMT" or "ACME PAYMENT PROCESSING". A credit is a payment
    when its category looks like a loan payment or a transfer, or when its
    merchant or description contains PAYMENT, PYMT, AUTOPAY, or AUTO PAY.
    """
    number = _as_float(amount)
    if not _is_credit(number):
        return False
    category_text = _text(category).lower()
    if any(phrase in category_text for phrase in _CATEGORY_PHRASES):
        return True
    blob = f"{_text(merchant)} {_text(description)}".upper()
    return any(token in blob for token in ("PAYMENT", "PYMT", "AUTOPAY", "AUTO PAY"))


def _is_credit(number: float | None) -> bool:
    """True when the amount reduces the balance (negative). Zero is not a credit."""
    return number is not None and number < 0


@dataclass(frozen=True)
class ExpenseLine:
    source_id: str
    name: str
    data_type: str
    total: float
    data_through: date | None
    pending_charges: int
    interest: float
    fees: float
    latest_closing: date | None
    apr: float | None = None
    balance: float | None = None
    balance_missing: bool = False
    elapsed_days: int | None = None


def expense_lines(
    transactions: pd.DataFrame,
    monthly: pd.DataFrame,
    period: Period,
    balances: pd.DataFrame | None = None,
) -> list[ExpenseLine]:
    """Registry sources first, then unmapped accounts that have rows in the period."""
    lines = [
        line_for_source(source, transactions, monthly, period, balances)
        for source in EXPENSE_SOURCES
    ]
    claimed = mapped_accounts()
    txn_accounts = _accounts(transactions, "Account") - claimed
    statement_accounts = _accounts(monthly, "Account") - claimed - txn_accounts
    for account in sorted(txn_accounts):
        line = _transaction_line(account, account, transactions, period, unmapped_source_id(account))
        if line.data_through is not None or line.total != 0:
            lines.append(line)
    for account in sorted(statement_accounts):
        line = _statement_line(account, account, monthly, period, unmapped_source_id(account))
        if line.data_through is not None or line.total != 0:
            lines.append(line)
    return lines


def line_for_source_id(
    source_id: str,
    transactions: pd.DataFrame,
    monthly: pd.DataFrame,
    period: Period,
    balances: pd.DataFrame | None = None,
) -> ExpenseLine | None:
    source = expense_source_by_id(source_id)
    if source is not None:
        return line_for_source(source, transactions, monthly, period, balances)
    account = account_from_unmapped_id(source_id)
    if account is None or expense_source_for_guard(account):
        return None
    if account in _accounts(transactions, "Account"):
        return _transaction_line(account, account, transactions, period, source_id)
    if account in _accounts(monthly, "Account"):
        return _statement_line(account, account, monthly, period, source_id)
    return None


def expense_source_for_guard(account: str) -> bool:
    return account in mapped_accounts()


def line_for_source(
    source: ExpenseSource,
    transactions: pd.DataFrame,
    monthly: pd.DataFrame,
    period: Period,
    balances: pd.DataFrame | None = None,
) -> ExpenseLine:
    if source.data_type == "loan":
        return _loan_line(source, period, balances)
    if source.data_type == "statements":
        return _statement_line(source.name, source.accounts, monthly, period, source.id)
    return _transaction_line(source.name, source.accounts, transactions, period, source.id)


def rows_for_line(
    line: ExpenseLine,
    transactions: pd.DataFrame,
    monthly: pd.DataFrame,
    period: Period,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (expense rows, payment rows) for a transaction source, or (statement rows, empty)."""
    accounts = _line_accounts(line)
    if line.data_type == "statements":
        rows = _statement_rows(monthly, accounts)
        dates = _statement_dates(rows)
        mask = dates.map(lambda value: period.contains(_as_date(value)))
        return rows.loc[mask].copy(), rows.iloc[0:0]
    if line.data_type == "loan":
        empty = transactions.iloc[0:0]
        return empty, empty
    rows = _drop_loan_accounts(_transaction_rows(transactions, accounts))
    dates = rows["Date"].map(_as_date) if not rows.empty else pd.Series(dtype=object)
    in_period = rows.loc[dates.map(period.contains)] if not rows.empty else rows
    if in_period.empty:
        return in_period, in_period
    payments = in_period.apply(
        lambda row: is_card_payment(row["Amount"], row.get("Category"), row.get("Merchant"), row.get("Description")),
        axis=1,
    )
    return in_period.loc[~payments].copy(), in_period.loc[payments].copy()


def caveats(lines: list[ExpenseLine]) -> list[str]:
    notes: list[str] = []
    for line in lines:
        if line.data_type == "transactions" and line.pending_charges:
            noun = "charge" if line.pending_charges == 1 else "charges"
            notes.append(f"{line.name} includes {line.pending_charges} pending {noun}.")
        if line.data_type == "statements" and line.latest_closing is not None:
            notes.append(
                f"{line.name} from monthly statements, latest statement closed {line.latest_closing.isoformat()}."
            )
        if line.data_type == "loan" and line.balance_missing:
            notes.append(f"{line.name} balance is missing, so interest is $0.")
        elif line.data_type == "loan" and line.elapsed_days is None:
            notes.append(f"{line.name} interest is not estimated because the period has no fixed length.")
    return notes


def total_income(
    deposits: pd.DataFrame | None = None,
    period: Period | None = None,
) -> float:
    """Sum cash income in the period. No deposit table means $0.

    Expense lines never call this. Card and statement spend stay on their own.
    """
    return total_received(deposits, period)


def income_for_source(
    source_id: str,
    deposits: pd.DataFrame | None = None,
    period: Period | None = None,
) -> float:
    """Cash received for one source id in the period. No deposit table means $0."""
    if deposits is None or period is None:
        return 0.0
    return received_for_source(deposits, source_id, period)


def _loan_line(
    source: ExpenseSource,
    period: Period,
    balances: pd.DataFrame | None,
) -> ExpenseLine:
    account = source.accounts[0]
    apr = source.apr if source.apr is not None else 0.0
    balance, as_of = current_balance(balances, account)
    days = elapsed_days(period)
    missing = balance is None
    return ExpenseLine(
        source_id=source.id,
        name=source.name,
        data_type="loan",
        total=estimated_interest(balance, apr, days),
        data_through=as_of,
        pending_charges=0,
        interest=estimated_interest(balance, apr, days),
        fees=0.0,
        latest_closing=None,
        apr=source.apr,
        balance=balance,
        balance_missing=missing,
        elapsed_days=days,
    )


def _transaction_line(
    name: str,
    accounts: str | tuple[str, ...],
    transactions: pd.DataFrame,
    period: Period,
    source_id: str,
) -> ExpenseLine:
    rows = _transaction_rows(transactions, _as_account_tuple(accounts))
    rows = _drop_loan_accounts(rows)
    if rows.empty:
        return _empty_line(source_id, name, "transactions")
    dates = rows["Date"].map(_as_date)
    in_period = rows.loc[dates.map(period.contains)]
    if in_period.empty:
        return _empty_line(source_id, name, "transactions")
    payment_mask = in_period.apply(
        lambda row: is_card_payment(row["Amount"], row.get("Category"), row.get("Merchant"), row.get("Description")),
        axis=1,
    )
    spending = in_period.loc[~payment_mask]
    total = float(spending["Amount"].sum()) if not spending.empty else 0.0
    pending = int(((spending["Amount"] > 0) & spending["Pending"].astype(bool)).sum()) if not spending.empty else 0
    through_dates = [day for day in dates.loc[in_period.index].tolist() if day is not None]
    through = max(through_dates) if through_dates else None
    return ExpenseLine(
        source_id=source_id,
        name=name,
        data_type="transactions",
        total=total,
        data_through=through,
        pending_charges=pending,
        interest=0.0,
        fees=0.0,
        latest_closing=None,
    )


def _statement_line(
    name: str,
    accounts: str | tuple[str, ...],
    monthly: pd.DataFrame,
    period: Period,
    source_id: str,
) -> ExpenseLine:
    rows = _statement_rows(monthly, _as_account_tuple(accounts))
    latest = _latest_closing(rows)
    if rows.empty:
        line = _empty_line(source_id, name, "statements")
        return ExpenseLine(
            source_id=line.source_id,
            name=line.name,
            data_type=line.data_type,
            total=0.0,
            data_through=None,
            pending_charges=0,
            interest=0.0,
            fees=0.0,
            latest_closing=latest,
        )
    dates = _statement_dates(rows)
    mask = dates.map(lambda value: period.contains(_as_date(value)))
    chosen = rows.loc[mask]
    chosen_dates = dates.loc[mask]
    interest = _sum_numbers(chosen, "Interest") if not chosen.empty else 0.0
    fees = _sum_numbers(chosen, "Fees") if not chosen.empty else 0.0
    net = _sum_numbers(chosen, "Net Spend") if not chosen.empty else 0.0
    through_values = [day for day in (_as_date(value) for value in chosen_dates) if day is not None]
    return ExpenseLine(
        source_id=source_id,
        name=name,
        data_type="statements",
        total=net + interest + fees,
        data_through=max(through_values) if through_values else None,
        pending_charges=0,
        interest=interest,
        fees=fees,
        latest_closing=latest,
    )


def _statement_rows(monthly: pd.DataFrame, accounts: tuple[str, ...]) -> pd.DataFrame:
    if monthly.empty or "Account" not in monthly.columns:
        return monthly.iloc[0:0]
    return monthly.loc[monthly["Account"].isin(accounts)].copy()


def _drop_loan_accounts(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty or "Account" not in rows.columns:
        return rows
    return rows.loc[~rows["Account"].isin(loan_accounts())].copy()


def _transaction_rows(transactions: pd.DataFrame, accounts: tuple[str, ...]) -> pd.DataFrame:
    if transactions.empty or "Account" not in transactions.columns:
        return transactions.iloc[0:0]
    return transactions.loc[transactions["Account"].isin(accounts)].copy()


def _statement_dates(rows: pd.DataFrame) -> pd.Series:
    if rows.empty:
        return pd.Series(dtype="object")
    closing = pd.to_datetime(rows["Statement Closing Date"], errors="coerce") if "Statement Closing Date" in rows.columns else pd.Series(pd.NaT, index=rows.index)
    if "Date" in rows.columns:
        fallback = pd.to_datetime(rows["Date"], errors="coerce")
    elif "Month" in rows.columns:
        fallback = pd.to_datetime(rows["Month"].astype(str) + "-01", errors="coerce")
    else:
        fallback = pd.Series(pd.NaT, index=rows.index)
    combined = closing.fillna(fallback)
    return combined


def _latest_closing(rows: pd.DataFrame) -> date | None:
    if rows.empty or "Statement Closing Date" not in rows.columns:
        return None
    parsed = pd.to_datetime(rows["Statement Closing Date"], errors="coerce").dropna()
    if parsed.empty:
        return None
    return _as_date(parsed.max())


def _sum_numbers(frame: pd.DataFrame, column: str) -> float:
    if column not in frame.columns or not frame[column].notna().any():
        return 0.0
    return float(frame[column].fillna(0).sum())


def _accounts(frame: pd.DataFrame, column: str) -> set[str]:
    if frame.empty or column not in frame.columns:
        return set()
    values = frame[column].astype("string").fillna("").str.strip()
    return {value for value in values.tolist() if value and value.lower() not in {"<na>", "nan", "none"}}


def _as_account_tuple(accounts: str | tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(accounts, str):
        return (accounts,)
    return tuple(accounts)


def _empty_line(source_id: str, name: str, data_type: str) -> ExpenseLine:
    return ExpenseLine(
        source_id=source_id,
        name=name,
        data_type=data_type,
        total=0.0,
        data_through=None,
        pending_charges=0,
        interest=0.0,
        fees=0.0,
        latest_closing=None,
    )


def _line_accounts(line: ExpenseLine) -> tuple[str, ...]:
    source = expense_source_by_id(line.source_id)
    if source is not None:
        return source.accounts
    account = account_from_unmapped_id(line.source_id)
    if account is None:
        return (line.name,)
    return (account,)


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _as_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, pd.Timestamp):
        # datetime is a date subclass; Timestamp is handled below
        if type(value) is date:
            return value
    try:
        if pd.isna(value):
            return None
    except TypeError:
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        return ""
    return str(value).strip()
