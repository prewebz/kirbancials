"""Estimated auto-loan interest.

Interest for a period is current balance times APR/100 divided by 365, times
the number of days in that period. Loan payments are not spend.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from kirbancials.periods import Period
from kirbancials.sources import ExpenseSource


def elapsed_days(period: Period) -> int | None:
    """Inclusive day count. Open-ended periods such as all time have no count."""
    if period.start is None or period.end is None:
        return None
    if period.end < period.start:
        return 0
    return (period.end - period.start).days + 1


def estimated_interest(balance: float | None, apr: float, days: int | None) -> float:
    """Return 0 when the balance or the day count is missing."""
    if balance is None or days is None:
        return 0.0
    return balance * (apr / 100.0) / 365.0 * days


def estimated_monthly_interest(balance: float | None, apr: float) -> float | None:
    if balance is None:
        return None
    return balance * (apr / 100.0) / 12.0


def estimated_daily_interest(balance: float | None, apr: float) -> float | None:
    if balance is None:
        return None
    return balance * (apr / 100.0) / 365.0


def loan_label(source: ExpenseSource) -> str:
    if source.apr is None:
        return source.name
    return f"{source.name} ({source.apr:.2f}% APR)"


def current_balance(balances: pd.DataFrame | None, account: str) -> tuple[float | None, date | None]:
    """Match FINANCE_BALANCES_CSV by account string. Returns (balance, as-of date)."""
    if balances is None or balances.empty or "Account" not in balances.columns:
        return None, None
    names = balances["Account"].astype("string").fillna("").str.strip()
    match = balances.loc[names == account].copy()
    if match.empty or "Current Balance" not in match.columns:
        return None, None
    match["_amount"] = pd.to_numeric(match["Current Balance"], errors="coerce")
    match = match.dropna(subset=["_amount"])
    if match.empty:
        return None, None
    if "As Of" in match.columns:
        match["_as_of"] = pd.to_datetime(match["As Of"], errors="coerce")
        match = match.sort_values("_as_of", ascending=False, na_position="last")
    amount = float(match.iloc[0]["_amount"])
    as_of = None
    if "_as_of" in match.columns and pd.notna(match.iloc[0]["_as_of"]):
        as_of = match.iloc[0]["_as_of"].date()
    return amount, as_of


def loan_payment_table(transactions: pd.DataFrame, account: str, period: Period) -> pd.DataFrame:
    """Payments for one loan in the period, newest first. Amount stays numeric."""
    columns = ["Date", "Description", "Amount"]
    if transactions.empty or "Account" not in transactions.columns:
        return pd.DataFrame(columns=columns)
    names = transactions["Account"].astype("string").fillna("").str.strip()
    rows = transactions.loc[names == account].copy()
    if rows.empty:
        return pd.DataFrame(columns=columns)
    dates = pd.to_datetime(rows["Date"], errors="coerce")
    rows = rows.loc[dates.notna()].copy()
    rows["_day"] = dates.loc[rows.index].dt.date
    rows = rows.loc[rows["_day"].map(period.contains)]
    if rows.empty:
        return pd.DataFrame(columns=columns)
    description = _description(rows)
    table = pd.DataFrame(
        {
            "Date": pd.to_datetime(rows["_day"]).dt.strftime("%Y-%m-%d"),
            "Description": description,
            "Amount": pd.to_numeric(rows["Amount"], errors="coerce"),
            "_sort": rows["_day"],
        }
    )
    table = table.sort_values(["_sort", "Description"], ascending=[False, True], kind="mergesort")
    return table.loc[:, columns].reset_index(drop=True)


def _description(rows: pd.DataFrame) -> pd.Series:
    description = (
        rows["Description"].astype("string").fillna("").str.strip()
        if "Description" in rows.columns
        else pd.Series("", index=rows.index)
    )
    merchant = (
        rows["Merchant"].astype("string").fillna("").str.strip()
        if "Merchant" in rows.columns
        else pd.Series("", index=rows.index)
    )
    return description.where(description.ne(""), merchant)
