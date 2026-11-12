"""Daily card transactions and current balances.

Transactions are synced by an outside job. Configured sources, in order:

1. FINANCE_TRANSACTIONS_CSV_GZ — base64 of gzip-compressed CSV text
2. FINANCE_TRANSACTIONS_CSV — plain CSV text
3. /etc/secrets/transactions.csv — Render secret file
4. sample_data/transactions.csv — fake sample, flagged for the banner

If none of those exist, the daily tab says there is no transaction data yet.

Balances come from FINANCE_BALANCES_CSV (plain CSV text), then
sample_data/balances.csv. Amounts on transactions use the Plaid sign:
positive is a charge, negative is a payment, refund, or credit.
"""

from __future__ import annotations

import base64
import gzip
import io
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from kirbancials.data import DataOrigin, FinanceDataError
from kirbancials.schema import DEFAULT_ACCOUNT

RENDER_TRANSACTIONS_CSV = Path("/etc/secrets/transactions.csv")
SAMPLE_TRANSACTIONS = (
    Path(__file__).resolve().parent.parent / "sample_data" / "transactions.csv"
)
SAMPLE_BALANCES = Path(__file__).resolve().parent.parent / "sample_data" / "balances.csv"

TXN_ID = "Transaction ID"
TXN_DATE = "Date"
TXN_ACCOUNT = "Account"
TXN_MERCHANT = "Merchant"
TXN_DESCRIPTION = "Description"
TXN_AMOUNT = "Amount"
TXN_PENDING = "Pending"
TXN_CATEGORY = "Category"
TXN_COLUMNS = [
    TXN_ID,
    TXN_DATE,
    TXN_ACCOUNT,
    TXN_MERCHANT,
    TXN_DESCRIPTION,
    TXN_AMOUNT,
    TXN_PENDING,
    TXN_CATEGORY,
]

BAL_ACCOUNT = "Account"
BAL_AS_OF = "As Of"
BAL_CURRENT = "Current Balance"
BAL_AVAILABLE = "Available Credit"
BAL_LIMIT = "Credit Limit"
BAL_COLUMNS = [BAL_ACCOUNT, BAL_AS_OF, BAL_CURRENT, BAL_AVAILABLE, BAL_LIMIT]

ALL_ACCOUNTS = "All accounts"
_TRUE_TEXT = {"1", "true", "t", "yes", "y", "pending"}


@dataclass
class TransactionData:
    rows: pd.DataFrame
    source_label: str
    is_sample: bool


@dataclass
class BalanceData:
    rows: pd.DataFrame
    source_label: str
    is_sample: bool

    @property
    def last_synced(self) -> pd.Timestamp | None:
        if self.rows.empty or BAL_AS_OF not in self.rows.columns:
            return None
        stamps = self.rows[BAL_AS_OF].dropna()
        if stamps.empty:
            return None
        return pd.Timestamp(stamps.max())


def decode_transactions_gz(payload: str) -> str:
    """Decode base64(gzip(csv text)) from FINANCE_TRANSACTIONS_CSV_GZ."""
    compact = "".join(payload.split())
    try:
        compressed = base64.b64decode(compact, validate=False)
        return gzip.decompress(compressed).decode("utf-8-sig")
    except Exception as exc:
        raise FinanceDataError(
            "FINANCE_TRANSACTIONS_CSV_GZ could not be decoded."
        ) from exc


def resolve_transactions(
    environ: Mapping[str, str] | None = None,
    secret_path: Path = RENDER_TRANSACTIONS_CSV,
    sample_file: Path = SAMPLE_TRANSACTIONS,
) -> DataOrigin | None:
    env = os.environ if environ is None else environ

    gzip_payload = env.get("FINANCE_TRANSACTIONS_CSV_GZ")
    if gzip_payload is not None and gzip_payload.strip() != "":
        text = decode_transactions_gz(gzip_payload).lstrip("\ufeff")
        return DataOrigin(
            source=io.StringIO(text),
            label="environment variable FINANCE_TRANSACTIONS_CSV_GZ",
            is_sample=False,
        )

    raw = env.get("FINANCE_TRANSACTIONS_CSV")
    if raw is not None and raw.strip() != "":
        return DataOrigin(
            source=io.StringIO(raw.lstrip("\ufeff")),
            label="environment variable FINANCE_TRANSACTIONS_CSV",
            is_sample=False,
        )

    if secret_path.is_file() and secret_path.stat().st_size > 0:
        return DataOrigin(
            source=secret_path,
            label="Render secret file transactions.csv",
            is_sample=False,
        )

    if sample_file.is_file() and sample_file.stat().st_size > 0:
        return DataOrigin(
            source=sample_file,
            label="bundled sample data",
            is_sample=True,
        )
    return None


def resolve_balances(
    environ: Mapping[str, str] | None = None,
    sample_file: Path = SAMPLE_BALANCES,
) -> DataOrigin | None:
    env = os.environ if environ is None else environ
    raw = env.get("FINANCE_BALANCES_CSV")
    if raw is not None and raw.strip() != "":
        return DataOrigin(
            source=io.StringIO(raw.lstrip("\ufeff")),
            label="environment variable FINANCE_BALANCES_CSV",
            is_sample=False,
        )
    if sample_file.is_file() and sample_file.stat().st_size > 0:
        return DataOrigin(
            source=sample_file,
            label="bundled sample data",
            is_sample=True,
        )
    return None


def load_transactions(origin: DataOrigin | None) -> TransactionData:
    if origin is None:
        return TransactionData(_empty_transactions(), "none", False)
    frame = _read_transactions(origin.source)
    return TransactionData(frame, origin.label, origin.is_sample)


def load_balances(origin: DataOrigin | None) -> BalanceData:
    if origin is None:
        return BalanceData(_empty_balances(), "none", False)
    frame = _read_balances(origin.source)
    return BalanceData(frame, origin.label, origin.is_sample)


def month_to_date_spend(rows: pd.DataFrame, today: date) -> pd.DataFrame:
    """Charges for the current month, per account. Payments and credits are excluded.

    Pending charges are included in the spend total and also reported on their own.
    """
    columns = ["Account", "Month-to-date spend", "Pending charges"]
    month = _in_month(rows, today)
    if month.empty:
        return pd.DataFrame(columns=columns)
    charges = month[month[TXN_AMOUNT] > 0]
    pending = charges.loc[charges[TXN_PENDING]]
    spend = charges.groupby(TXN_ACCOUNT, as_index=False)[TXN_AMOUNT].sum()
    pending_sum = pending.groupby(TXN_ACCOUNT)[TXN_AMOUNT].sum()
    accounts = sorted(month[TXN_ACCOUNT].unique().tolist())
    table = pd.DataFrame({TXN_ACCOUNT: accounts})
    table = table.merge(spend, on=TXN_ACCOUNT, how="left")
    table[TXN_AMOUNT] = table[TXN_AMOUNT].fillna(0.0)
    table["Pending charges"] = table[TXN_ACCOUNT].map(pending_sum).fillna(0.0)
    table = table.rename(columns={TXN_ACCOUNT: "Account", TXN_AMOUNT: "Month-to-date spend"})
    return table.loc[:, columns]


def daily_charges(rows: pd.DataFrame, account: str, today: date) -> pd.DataFrame:
    """One row per day of charges in the current month for the selected account."""
    columns = ["Date", "Total charges"]
    month = _in_month(rows, today)
    if account != ALL_ACCOUNTS:
        month = month[month[TXN_ACCOUNT] == account]
    charges = month[month[TXN_AMOUNT] > 0]
    if charges.empty:
        return pd.DataFrame(columns=columns)
    grouped = charges.groupby(charges[TXN_DATE].dt.date, as_index=False)[TXN_AMOUNT].sum()
    grouped = grouped.rename(columns={TXN_DATE: "Date", TXN_AMOUNT: "Total charges"})
    grouped["Date"] = pd.to_datetime(grouped["Date"])
    return grouped.sort_values("Date").reset_index(drop=True)


def filter_transactions(
    rows: pd.DataFrame,
    account: str,
    start: date | None,
    end: date | None,
    hide_credits: bool,
) -> pd.DataFrame:
    out = rows
    if account != ALL_ACCOUNTS:
        out = out[out[TXN_ACCOUNT] == account]
    if start is not None and end is not None and not out.empty:
        dates = out[TXN_DATE].dt.date
        out = out[(dates >= start) & (dates <= end)]
    if hide_credits and not out.empty:
        out = out[out[TXN_AMOUNT] >= 0]
    if out.empty:
        return out
    return out.sort_values([TXN_DATE, TXN_ACCOUNT, TXN_MERCHANT], ascending=[False, True, True], kind="mergesort")


def format_synced(stamp: pd.Timestamp | None) -> str | None:
    if stamp is None or pd.isna(stamp):
        return None
    value = pd.Timestamp(stamp)
    if value.tzinfo is None:
        value = value.tz_localize("UTC")
    else:
        value = value.tz_convert("UTC")
    return value.strftime("%Y-%m-%d %H:%M UTC")


def _read_transactions(src: object) -> pd.DataFrame:
    frame = _read_csv(src, "transactions")
    missing = [column for column in TXN_COLUMNS if column not in frame.columns]
    if missing:
        raise FinanceDataError("The transactions CSV is missing a required column.")
    out = frame.loc[:, TXN_COLUMNS].copy()
    out[TXN_ID] = out[TXN_ID].astype("string").fillna("").str.strip()
    out[TXN_DATE] = pd.to_datetime(out[TXN_DATE], format="%Y-%m-%d", errors="coerce")
    out[TXN_AMOUNT] = pd.to_numeric(out[TXN_AMOUNT], errors="coerce")
    out[TXN_PENDING] = out[TXN_PENDING].map(_parse_pending)
    for column in (TXN_ACCOUNT, TXN_MERCHANT, TXN_DESCRIPTION, TXN_CATEGORY):
        out[column] = out[column].astype("string").fillna("").str.strip()
    missing_account = out[TXN_ACCOUNT].eq("")
    out.loc[missing_account, TXN_ACCOUNT] = DEFAULT_ACCOUNT
    out = out.dropna(subset=[TXN_DATE, TXN_AMOUNT])
    return _dedupe_transactions(out.reset_index(drop=True))


def _dedupe_transactions(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per Transaction ID. A posted row wins over a pending row with the same ID."""
    if frame.empty:
        return frame
    has_id = frame[TXN_ID].ne("")
    identified = frame.loc[has_id].sort_values(
        [TXN_ID, TXN_PENDING, TXN_DATE],
        ascending=[True, True, False],
        kind="mergesort",
    )
    identified = identified.drop_duplicates(TXN_ID, keep="first")
    anonymous = frame.loc[~has_id]
    combined = pd.concat([identified, anonymous], ignore_index=True)
    return combined.sort_values([TXN_DATE, TXN_ACCOUNT], kind="mergesort").reset_index(drop=True)


def _read_balances(src: object) -> pd.DataFrame:
    frame = _read_csv(src, "balances")
    missing = [column for column in (BAL_ACCOUNT, BAL_AS_OF, BAL_CURRENT) if column not in frame.columns]
    if missing:
        raise FinanceDataError("The balances CSV is missing a required column.")
    for column in (BAL_AVAILABLE, BAL_LIMIT):
        if column not in frame.columns:
            frame[column] = pd.NA
    out = frame.loc[:, BAL_COLUMNS].copy()
    out[BAL_ACCOUNT] = out[BAL_ACCOUNT].astype("string").fillna("").str.strip()
    out.loc[out[BAL_ACCOUNT].eq(""), BAL_ACCOUNT] = DEFAULT_ACCOUNT
    out[BAL_AS_OF] = pd.to_datetime(out[BAL_AS_OF], utc=True, errors="coerce")
    for column in (BAL_CURRENT, BAL_AVAILABLE, BAL_LIMIT):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out = out.sort_values(BAL_AS_OF, ascending=False, na_position="last", kind="mergesort")
    out = out.drop_duplicates(BAL_ACCOUNT, keep="first")
    return out.sort_values(BAL_ACCOUNT, kind="mergesort").reset_index(drop=True)


def _read_csv(src: object, kind: str) -> pd.DataFrame:
    kwargs: dict[str, object] = {"dtype": str}
    if isinstance(src, (str, Path)):
        kwargs["encoding"] = "utf-8-sig"
    try:
        frame = pd.read_csv(src, **kwargs)
    except pd.errors.EmptyDataError as exc:
        raise FinanceDataError(f"The {kind} CSV is empty.") from exc
    except pd.errors.ParserError as exc:
        raise FinanceDataError(f"The {kind} CSV could not be parsed.") from exc
    except FileNotFoundError as exc:
        raise FinanceDataError(f"The {kind} CSV file was not found.") from exc
    except OSError as exc:
        raise FinanceDataError(f"The {kind} CSV could not be read.") from exc
    frame.columns = [str(column).strip() for column in frame.columns]
    return frame


def _parse_pending(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    try:
        if pd.isna(value):
            return False
    except TypeError:
        return False
    return str(value).strip().lower() in _TRUE_TEXT


def _in_month(rows: pd.DataFrame, today: date) -> pd.DataFrame:
    if rows.empty:
        return rows
    dates = rows[TXN_DATE].dt.date
    return rows[(dates.map(lambda item: item.year) == today.year) & (dates.map(lambda item: item.month) == today.month) & (dates <= today)]


def _empty_transactions() -> pd.DataFrame:
    frame = pd.DataFrame(columns=TXN_COLUMNS)
    frame[TXN_DATE] = pd.to_datetime(frame[TXN_DATE])
    return frame


def _empty_balances() -> pd.DataFrame:
    frame = pd.DataFrame(columns=BAL_COLUMNS)
    frame[BAL_AS_OF] = pd.to_datetime(frame[BAL_AS_OF], utc=True)
    return frame
