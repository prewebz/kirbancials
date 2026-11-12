"""Load statement CSVs into monthly rows and period totals.

Source order for the configured file (the sidebar upload is applied by the
app and is never written to disk):

1. FINANCE_DATA_CSV — raw CSV text
2. /etc/secrets/statement_summary_combined.csv — Render secret file
3. FINANCE_DATA_PATH — a filesystem path
4. sample_data/example_statements.csv — fake sample, flagged for a banner
"""

from __future__ import annotations

import io
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from kirbancials.formatting import format_money
from kirbancials.schema import (
    ACCOUNT_COL,
    DATE_COLS,
    DEFAULT_ACCOUNT,
    INCOME_COL,
    KIND_COL,
    MONTH_PATTERN,
    NUM_COLS,
    canonicalize_kind,
    kind_spec,
)

RENDER_SECRET_CSV = Path("/etc/secrets/statement_summary_combined.csv")
SAMPLE_CSV = (
    Path(__file__).resolve().parent.parent / "sample_data" / "example_statements.csv"
)

NUMERIC_COLUMNS = [*NUM_COLS, INCOME_COL]


class FinanceDataError(ValueError):
    """The selected CSV could not be normalized."""


@dataclass(frozen=True)
class DataOrigin:
    source: object
    label: str
    is_sample: bool


@dataclass
class FinanceData:
    monthly: pd.DataFrame
    period_totals: pd.DataFrame
    source_label: str
    is_sample: bool

    @property
    def accounts(self) -> list[str]:
        return _unique_strings(self.monthly, self.period_totals, column=ACCOUNT_COL)

    @property
    def years(self) -> list[str]:
        if self.monthly.empty or "Year" not in self.monthly.columns:
            return []
        return sorted(_string_values(self.monthly["Year"]))

    @property
    def kinds(self) -> list[str]:
        return _unique_strings(self.monthly, self.period_totals, column=KIND_COL)


@dataclass(frozen=True)
class Snapshot:
    latest_statement_balance: float
    total_net_spend: float
    total_payments: float
    interest_and_fees: float
    total_income: float | None


def resolve_origin(
    environ: Mapping[str, str] | None = None,
    secret_path: Path = RENDER_SECRET_CSV,
    sample_file: Path = SAMPLE_CSV,
) -> DataOrigin:
    """Pick the first configured source. Does not read the sidebar upload."""
    env = os.environ if environ is None else environ

    raw = env.get("FINANCE_DATA_CSV")
    if raw is not None and raw.strip() != "":
        text = raw.lstrip("\ufeff")
        return DataOrigin(
            source=io.StringIO(text),
            label="environment variable FINANCE_DATA_CSV",
            is_sample=False,
        )

    if secret_path.is_file() and secret_path.stat().st_size > 0:
        return DataOrigin(
            source=secret_path,
            label="Render secret file",
            is_sample=False,
        )

    path = env.get("FINANCE_DATA_PATH")
    if path:
        candidate = Path(path)
        if candidate.is_file():
            return DataOrigin(
                source=candidate,
                label="environment variable FINANCE_DATA_PATH",
                is_sample=False,
            )

    return DataOrigin(
        source=sample_file,
        label="bundled sample data",
        is_sample=True,
    )


def load_dataset(src: object, *, label: str, is_sample: bool) -> FinanceData:
    frame = _read_frame(src)
    monthly, periods = _split_periods(frame)
    return FinanceData(
        monthly=monthly,
        period_totals=periods,
        source_label=label,
        is_sample=is_sample,
    )


def filter_rows(
    data: FinanceData,
    accounts: Sequence[str],
    year: str,
    kinds: Sequence[str],
) -> FinanceData:
    monthly = data.monthly
    periods = data.period_totals
    account_set = set(accounts)
    kind_set = set(kinds)

    if monthly.empty and periods.empty:
        return FinanceData(monthly.copy(), periods.copy(), data.source_label, data.is_sample)

    monthly = monthly[
        monthly[ACCOUNT_COL].isin(account_set) & monthly[KIND_COL].isin(kind_set)
    ]
    periods = periods[
        periods[ACCOUNT_COL].isin(account_set) & periods[KIND_COL].isin(kind_set)
    ]
    if year != "All":
        monthly = monthly[monthly["Year"].astype("string") == year]
        year_text = periods["Year"].astype("string").fillna("")
        month_text = periods["Month"].astype("string").fillna("")
        periods = periods[
            year_text.str.contains(year, regex=False)
            | month_text.str.contains(year, regex=False)
        ]
    return FinanceData(
        monthly=monthly.copy(),
        period_totals=periods.copy(),
        source_label=data.source_label,
        is_sample=data.is_sample,
    )


def summarize(monthly: pd.DataFrame) -> Snapshot:
    spend = _rows_for(monthly, "include_in_spend")
    cards = _rows_for(monthly, "include_in_card_balance")
    income_rows = _rows_for(monthly, "include_in_income")
    return Snapshot(
        latest_statement_balance=_latest_balance(cards),
        total_net_spend=_sum_column(spend, "Net Spend"),
        total_payments=_sum_column(spend, "Payments"),
        interest_and_fees=_interest_and_fees(spend),
        total_income=None if income_rows.empty else _income_total(income_rows),
    )


def balance_series(monthly: pd.DataFrame) -> pd.DataFrame:
    cards = _rows_for(monthly, "include_in_card_balance")
    if cards.empty or "Statement Balance" not in cards.columns:
        return pd.DataFrame(columns=["Date", "Month", ACCOUNT_COL, "Statement Balance", "Balance label"])
    out = cards.dropna(subset=["Statement Balance"]).loc[
        :, ["Date", "Month", ACCOUNT_COL, "Statement Balance"]
    ].copy()
    out["Balance label"] = out["Statement Balance"].map(format_money)
    return out.sort_values(["Date", ACCOUNT_COL])


def charges_and_payments(monthly: pd.DataFrame) -> pd.DataFrame:
    empty = pd.DataFrame(columns=["Date", "Measure", "Amount", "Amount label"])
    spend = _rows_for(monthly, "include_in_spend")
    needed = ["New Charges (Purchases)", "Payments"]
    if spend.empty or any(column not in spend.columns for column in needed):
        return empty
    grouped = spend.groupby("Date", as_index=False)[needed].sum(min_count=1)
    long = grouped.melt("Date", var_name="Measure", value_name="Amount")
    long = long.dropna(subset=["Amount"])
    if long.empty:
        return empty
    long["Amount label"] = long["Amount"].map(format_money)
    return long.sort_values(["Date", "Measure"])


def _read_frame(src: object) -> pd.DataFrame:
    kwargs: dict[str, object] = {"dtype": {"Year": "string", "Month": "string"}}
    if isinstance(src, (str, Path)):
        kwargs["encoding"] = "utf-8-sig"
    try:
        frame = pd.read_csv(src, **kwargs)
    except pd.errors.EmptyDataError as exc:
        raise FinanceDataError("The CSV is empty.") from exc
    except pd.errors.ParserError as exc:
        raise FinanceDataError("The CSV could not be parsed.") from exc
    except FileNotFoundError as exc:
        raise FinanceDataError("The CSV file was not found.") from exc
    except OSError as exc:
        raise FinanceDataError("The CSV could not be read.") from exc

    frame.columns = [str(column).strip() for column in frame.columns]
    if "Month" not in frame.columns:
        raise FinanceDataError("The CSV is missing the Month column.")
    return _normalize_columns(frame)


def _normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["Month"] = out["Month"].astype("string").fillna("").str.strip()
    if "Year" not in out.columns:
        out["Year"] = pd.Series(pd.NA, index=out.index, dtype="string")
    else:
        out["Year"] = out["Year"].astype("string").str.strip()

    if ACCOUNT_COL not in out.columns:
        out[ACCOUNT_COL] = DEFAULT_ACCOUNT
    else:
        accounts = out[ACCOUNT_COL].astype("string").str.strip()
        lowered = accounts.fillna("").str.lower()
        missing = accounts.isna() | accounts.eq("") | lowered.isin(["<na>", "nan", "none"])
        out[ACCOUNT_COL] = accounts.mask(missing, DEFAULT_ACCOUNT)

    if KIND_COL not in out.columns:
        out[KIND_COL] = "credit_card"
    else:
        out[KIND_COL] = out[KIND_COL].map(canonicalize_kind)

    for column in DATE_COLS:
        if column in out.columns:
            out[column] = out[column].astype("string").str.strip()

    for column in NUMERIC_COLUMNS:
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce")
        elif column in NUM_COLS:
            out[column] = pd.Series(pd.NA, index=out.index, dtype="Float64")
    return out


def _split_periods(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    is_month = frame["Month"].str.match(MONTH_PATTERN, na=False)
    monthly = frame.loc[is_month].copy()
    periods = frame.loc[~is_month].copy()
    if not monthly.empty:
        monthly["Date"] = pd.to_datetime(monthly["Month"] + "-01", errors="coerce")
        bad_dates = monthly["Date"].isna()
        if bad_dates.any():
            periods = pd.concat([periods, monthly.loc[bad_dates].drop(columns=["Date"])], ignore_index=True)
            monthly = monthly.loc[~bad_dates].copy()
        monthly["Year"] = monthly["Date"].dt.year.astype("string")
        monthly = monthly.sort_values(["Date", ACCOUNT_COL], kind="mergesort")
    else:
        monthly["Date"] = pd.Series(dtype="datetime64[ns]")
    return monthly.reset_index(drop=True), periods.reset_index(drop=True)


def _rows_for(monthly: pd.DataFrame, flag: str) -> pd.DataFrame:
    if monthly.empty or KIND_COL not in monthly.columns:
        return monthly.iloc[0:0]
    mask = monthly[KIND_COL].map(lambda kind: getattr(kind_spec(str(kind)), flag))
    return monthly.loc[mask]


def _latest_balance(cards: pd.DataFrame) -> float:
    if cards.empty or "Statement Balance" not in cards.columns:
        return float("nan")
    usable = cards.dropna(subset=["Statement Balance", "Date"])
    if usable.empty:
        return float("nan")
    latest_index = usable.groupby(ACCOUNT_COL)["Date"].idxmax()
    return float(usable.loc[latest_index, "Statement Balance"].sum())


def _sum_column(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame.columns or not frame[column].notna().any():
        return float("nan")
    return float(frame[column].sum(min_count=1))


def _interest_and_fees(frame: pd.DataFrame) -> float:
    if frame.empty:
        return float("nan")
    parts = [frame[column] for column in ("Interest", "Fees") if column in frame.columns]
    if not parts or not any(part.notna().any() for part in parts):
        return float("nan")
    return float(sum(part.fillna(0).sum() for part in parts))


def _income_total(frame: pd.DataFrame) -> float:
    if INCOME_COL in frame.columns and frame[INCOME_COL].notna().any():
        return float(frame[INCOME_COL].sum(min_count=1))
    return _sum_column(frame, "Net Spend")


def _string_values(series: pd.Series) -> list[str]:
    text = series.astype("string").fillna("").str.strip()
    return [value for value in text.unique().tolist() if value and value.lower() not in {"<na>", "nan", "none"}]


def _unique_strings(*frames: pd.DataFrame, column: str) -> list[str]:
    values: list[str] = []
    for frame in frames:
        if column in frame.columns:
            values.extend(_string_values(frame[column]))
    return sorted(set(values))
