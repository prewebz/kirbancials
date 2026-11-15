"""Fixed amortization schedules.

FINANCE_LOAN_SCHEDULE_CSV is plain CSV text. One row is one scheduled payment.
Interest whose payment date falls in a period is an expense. Principal is not.
If the variable is missing or blank, there is no schedule and nothing is added.

A row that cannot be read rejects the whole file. The caller shows the warning
and continues with no scheduled loans.
"""

from __future__ import annotations

import io
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from kirbancials.data import FinanceDataError
from kirbancials.formatting import format_money
from kirbancials.periods import Period
from kirbancials.sources import ExpenseSource, scheduled_loan_source

LOAN_SCHEDULE_ENV = "FINANCE_LOAN_SCHEDULE_CSV"

SCHEDULE_COLUMNS = [
    "Loan ID",
    "Lender",
    "Loan Name",
    "Collateral",
    "Rate",
    "Payment Number",
    "Payment Date",
    "Payment",
    "Interest",
    "Principal",
    "Remaining Balance",
]

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

_INTERNAL = [
    "loan_id",
    "lender",
    "loan_name",
    "collateral",
    "rate",
    "payment_number",
    "payment_date",
    "payment",
    "interest",
    "principal",
    "remaining_balance",
]

_UNREADABLE = "The loan schedule CSV has a row that could not be read."
_MISSING_COLUMN = "The loan schedule CSV is missing a required column."
_UNPARSED = "The loan schedule CSV could not be parsed."
_OMITTED = "The loan schedule CSV could not be read. Scheduled loans are left out of this view."


@dataclass(frozen=True)
class ScheduledLoanSnapshot:
    """Remaining balance and the next scheduled payment for one loan."""

    source_id: str
    name: str
    rate: float
    balance: float
    next_date: date | None
    next_amount: float | None
    collateral: str

    @property
    def label(self) -> str:
        return schedule_label(self.name, self.rate)


def format_rate(rate: float) -> str:
    """Annual percent without trailing zeros. 5.9 stays 5.9."""
    rounded = round(float(rate), 4)
    text = f"{rounded:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def schedule_label(name: str, rate: float | None) -> str:
    """Expense-row label, for example `Sample Bank - Sample note (5.9% sched.)`."""
    if rate is None:
        return name
    return f"{name} ({format_rate(rate)}% sched.)"


def resolve_loan_schedule(
    environ: Mapping[str, str] | None = None,
) -> tuple[pd.DataFrame, str | None]:
    """Return (rows, warning).

    A missing or blank variable is (empty, None), the same as no loans.
    A CSV that cannot be used is (empty, warning). The warning is safe to show;
    it does not include any cell from the file.
    """
    env = os.environ if environ is None else environ
    raw = env.get(LOAN_SCHEDULE_ENV)
    if raw is None or str(raw).lstrip("\ufeff").strip() == "":
        return empty_schedule(), None
    try:
        return parse_loan_schedule(str(raw)), None
    except FinanceDataError as exc:
        return empty_schedule(), str(exc)
    except Exception:
        return empty_schedule(), _OMITTED


def parse_loan_schedule(text: str) -> pd.DataFrame:
    """Parse schedule CSV text. Raises FinanceDataError when the file is unusable."""
    try:
        frame = pd.read_csv(io.StringIO(text.lstrip("\ufeff")), dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError as exc:
        raise FinanceDataError(_UNPARSED) from exc
    except pd.errors.ParserError as exc:
        raise FinanceDataError(_UNPARSED) from exc
    frame.columns = [str(column).strip() for column in frame.columns]
    missing = [column for column in SCHEDULE_COLUMNS if column not in frame.columns]
    if missing:
        raise FinanceDataError(_MISSING_COLUMN)
    chosen = frame.loc[:, SCHEDULE_COLUMNS]
    records: list[dict[str, object]] = []
    for raw in chosen.to_dict("records"):
        if _blank_row(raw):
            continue
        parsed = _parse_record(raw)
        if parsed is None:
            raise FinanceDataError(_UNREADABLE)
        records.append(parsed)
    if not records:
        return empty_schedule()
    out = pd.DataFrame.from_records(records, columns=_INTERNAL)
    out["payment_date"] = pd.to_datetime(out["payment_date"])
    return out


def empty_schedule() -> pd.DataFrame:
    frame = pd.DataFrame(columns=_INTERNAL)
    frame["payment_date"] = pd.to_datetime(frame["payment_date"])
    for column in ("rate", "payment_number", "payment", "interest", "principal", "remaining_balance"):
        frame[column] = pd.to_numeric(frame[column])
    return frame


def loan_sources(frame: pd.DataFrame | None) -> list[tuple[ExpenseSource, pd.DataFrame]]:
    """One ExpenseSource per Loan ID, in the order each id first appears."""
    if frame is None or frame.empty or "loan_id" not in frame.columns:
        return []
    grouped: list[tuple[ExpenseSource, pd.DataFrame]] = []
    for loan_id, rows in frame.groupby("loan_id", sort=False):
        first = rows.iloc[0]
        source = scheduled_loan_source(
            str(loan_id),
            str(first["lender"]),
            str(first["loan_name"]),
            float(first["rate"]),
        )
        grouped.append((source, rows.reset_index(drop=True)))
    return grouped


def rows_for_loan(frame: pd.DataFrame | None, loan_id: str) -> pd.DataFrame:
    if frame is None or frame.empty or "loan_id" not in frame.columns or loan_id == "":
        return empty_schedule()
    return frame.loc[frame["loan_id"] == loan_id].copy()


def scheduled_interest(rows: pd.DataFrame, period: Period) -> float:
    """Sum Interest for payment dates in the period. Principal is not included."""
    chosen = _in_period(rows, period)
    if chosen.empty:
        return 0.0
    return float(chosen["interest"].sum())


def latest_payment_date(rows: pd.DataFrame, period: Period) -> date | None:
    chosen = _in_period(rows, period)
    if chosen.empty:
        return None
    days = [day for day in chosen["payment_date"].map(_as_date).tolist() if day is not None]
    return max(days) if days else None


def remaining_balance(rows: pd.DataFrame | None, today: date) -> float | None:
    """Balance still owed on `today`.

    Use the Remaining Balance of the latest payment dated on or before today.
    When every payment is still in the future, use the first row's balance
    before that payment: its Remaining Balance plus its Principal.
    """
    if rows is None or rows.empty:
        return None
    ordered = _ordered(rows)
    days = ordered["payment_date"].map(_as_date)
    eligible = ordered.loc[days.map(lambda day: day is not None and day <= today)]
    if eligible.empty:
        first = ordered.iloc[0]
        return float(first["remaining_balance"]) + float(first["principal"])
    return float(eligible.iloc[-1]["remaining_balance"])


def next_payment(rows: pd.DataFrame | None, today: date) -> tuple[date, float] | None:
    """Earliest payment strictly after today. Payment number 0 counts as a row."""
    if rows is None or rows.empty:
        return None
    ordered = _ordered(rows)
    days = ordered["payment_date"].map(_as_date)
    upcoming = ordered.loc[days.map(lambda day: day is not None and day > today)]
    if upcoming.empty:
        return None
    first = upcoming.iloc[0]
    day = _as_date(first["payment_date"])
    if day is None:
        return None
    return day, float(first["payment"])


def loan_snapshots(frame: pd.DataFrame | None, today: date) -> list[ScheduledLoanSnapshot]:
    snapshots: list[ScheduledLoanSnapshot] = []
    for source, rows in loan_sources(frame):
        nxt = next_payment(rows, today)
        balance = remaining_balance(rows, today)
        collateral = str(rows.iloc[0]["collateral"]).strip() if not rows.empty else ""
        snapshots.append(
            ScheduledLoanSnapshot(
                source_id=source.id,
                name=source.name,
                rate=float(source.apr) if source.apr is not None else 0.0,
                balance=0.0 if balance is None else balance,
                next_date=None if nxt is None else nxt[0],
                next_amount=None if nxt is None else nxt[1],
                collateral=collateral,
            )
        )
    return snapshots


def scheduled_balance_total(snapshots: list[ScheduledLoanSnapshot]) -> float:
    return float(sum(item.balance for item in snapshots))


def snapshot_line(item: ScheduledLoanSnapshot) -> str:
    """One debt-card line: label, remaining balance, and the next payment."""
    balance = format_money(item.balance)
    if item.next_date is None or item.next_amount is None:
        return f"{item.label}: {balance} remaining. No further scheduled payments."
    return (
        f"{item.label}: {balance} remaining. "
        f"Next payment {item.next_date.isoformat()} for {format_money(item.next_amount)}."
    )


def schedule_payment_table(rows: pd.DataFrame, period: Period) -> pd.DataFrame:
    """Detail rows in schedule order: date, payment, interest, principal, balance."""
    columns = ["Date", "Payment", "Interest", "Principal", "Remaining Balance"]
    chosen = _ordered(_in_period(rows, period))
    if chosen.empty:
        empty = pd.DataFrame(columns=columns)
        for column in columns[1:]:
            empty[column] = pd.to_numeric(empty[column])
        return empty
    table = pd.DataFrame(
        {
            "Date": chosen["payment_date"].map(
                lambda value: _as_date(value).isoformat() if _as_date(value) else ""
            ),
            "Payment": pd.to_numeric(chosen["payment"]),
            "Interest": pd.to_numeric(chosen["interest"]),
            "Principal": pd.to_numeric(chosen["principal"]),
            "Remaining Balance": pd.to_numeric(chosen["remaining_balance"]),
        }
    )
    return table.loc[:, columns].reset_index(drop=True)


def _ordered(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return rows
    return rows.sort_values(
        ["payment_date", "payment_number"],
        ascending=[True, True],
        kind="mergesort",
    )


def _in_period(rows: pd.DataFrame | None, period: Period) -> pd.DataFrame:
    if rows is None or rows.empty or "payment_date" not in rows.columns:
        return empty_schedule()
    days = rows["payment_date"].map(_as_date)
    return rows.loc[days.map(period.contains)].copy()


def _blank_row(raw: Mapping[str, object]) -> bool:
    return all(str(raw.get(column, "")).strip() == "" for column in SCHEDULE_COLUMNS)


def _parse_record(raw: Mapping[str, object]) -> dict[str, object] | None:
    loan_id = str(raw.get("Loan ID", "")).strip()
    lender = str(raw.get("Lender", "")).strip()
    loan_name = str(raw.get("Loan Name", "")).strip()
    collateral = str(raw.get("Collateral", "")).strip()
    if loan_id == "" or lender == "" or loan_name == "":
        return None
    rate = _parse_float(raw.get("Rate"))
    payment_number = _parse_int(raw.get("Payment Number"))
    payment_date = _parse_date(raw.get("Payment Date"))
    payment = _parse_float(raw.get("Payment"))
    interest = _parse_float(raw.get("Interest"))
    principal = _parse_float(raw.get("Principal"))
    remaining = _parse_float(raw.get("Remaining Balance"))
    if None in (rate, payment_number, payment_date, payment, interest, principal, remaining):
        return None
    assert rate is not None and payment_number is not None and payment_date is not None
    assert payment is not None and interest is not None and principal is not None and remaining is not None
    return {
        "loan_id": loan_id,
        "lender": lender,
        "loan_name": loan_name,
        "collateral": collateral,
        "rate": rate,
        "payment_number": payment_number,
        "payment_date": payment_date,
        "payment": payment,
        "interest": interest,
        "principal": principal,
        "remaining_balance": remaining,
    }


def _parse_date(value: object) -> date | None:
    text = str(value).strip() if value is not None else ""
    if _DATE.fullmatch(text) is None:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_int(value: object) -> int | None:
    number = _parse_float(value)
    if number is None or not float(number).is_integer():
        return None
    return int(number)


def _parse_float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return number


def _as_date(value: object) -> date | None:
    if value is None:
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
