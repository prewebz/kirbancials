"""Cash income for the profit and loss page.

FINANCE_INCOME_CSV is plain CSV text (not gzipped). Columns are Transaction ID,
Date, Source, Description, and Amount. Amount is dollars received. A deposit
counts on its Date. If the variable is missing or blank, there are no rows and
every total is $0.

Registered sources always appear. A Source id that is not registered still
appears, under that raw id, when the period contains one of its deposits.
This module does not read card transactions or statement files.
"""

from __future__ import annotations

import io
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from kirbancials.data import DataOrigin, FinanceDataError
from kirbancials.periods import Period
from kirbancials.sources import INCOME_SOURCES, income_source_by_id

INCOME_ID = "Transaction ID"
INCOME_DATE = "Date"
INCOME_SOURCE = "Source"
INCOME_DESCRIPTION = "Description"
INCOME_AMOUNT = "Amount"
INCOME_COLUMNS = [
    INCOME_ID,
    INCOME_DATE,
    INCOME_SOURCE,
    INCOME_DESCRIPTION,
    INCOME_AMOUNT,
]


@dataclass(frozen=True)
class IncomeLine:
    source_id: str
    name: str
    frequency: str
    total: float

    @property
    def label(self) -> str:
        """Name with the frequency beside it, in the same shape as a loan APR."""
        if self.frequency:
            return f"{self.name} ({self.frequency})"
        return self.name


def resolve_income(environ: Mapping[str, str] | None = None) -> DataOrigin | None:
    """Return the plain-text income CSV, or None when it is missing or blank."""
    env = os.environ if environ is None else environ
    raw = env.get("FINANCE_INCOME_CSV")
    if raw is None:
        return None
    text = raw.lstrip("\ufeff").strip()
    if text == "":
        return None
    return DataOrigin(
        source=io.StringIO(text),
        label="environment variable FINANCE_INCOME_CSV",
        is_sample=False,
    )


def load_income(origin: DataOrigin | None) -> pd.DataFrame:
    """Parse income deposits. None means an empty table, not an error."""
    if origin is None:
        return _empty_income()
    return _read_income(origin.source)


def income_lines(deposits: pd.DataFrame, period: Period) -> list[IncomeLine]:
    """Registered sources first, then unknown source ids that paid in the period."""
    chosen = rows_in_period(deposits, period)
    totals = _totals_by_source(chosen)
    lines: list[IncomeLine] = []
    seen: set[str] = set()
    for source in INCOME_SOURCES:
        seen.add(source.id)
        lines.append(
            IncomeLine(
                source_id=source.id,
                name=source.name,
                frequency=source.frequency,
                total=float(totals.get(source.id, 0.0)),
            )
        )
    if chosen.empty or INCOME_SOURCE not in chosen.columns:
        return lines
    present = [source for source in chosen[INCOME_SOURCE].tolist() if source not in seen]
    extras = sorted(set(present))
    for source_id in extras:
        lines.append(
            IncomeLine(
                source_id=source_id,
                name=source_id,
                frequency="",
                total=float(totals.get(source_id, 0.0)),
            )
        )
    return lines


def income_line_for_id(
    source_id: str,
    deposits: pd.DataFrame,
    period: Period,
) -> IncomeLine | None:
    """One source for the detail page, including a raw id that has deposits."""
    for line in income_lines(deposits, period):
        if line.source_id == source_id:
            return line
    source = income_source_by_id(source_id)
    if source is not None:
        return IncomeLine(source.id, source.name, source.frequency, 0.0)
    if _source_present(deposits, source_id):
        return IncomeLine(source_id, source_id, "", 0.0)
    return None


def deposits_for_source(
    deposits: pd.DataFrame,
    source_id: str,
    period: Period,
) -> pd.DataFrame:
    """Deposits for one source in the period, newest date first."""
    chosen = rows_in_period(deposits, period)
    if chosen.empty or INCOME_SOURCE not in chosen.columns:
        return chosen.iloc[0:0]
    matched = chosen.loc[chosen[INCOME_SOURCE] == source_id]
    if matched.empty:
        return matched
    return matched.sort_values(
        [INCOME_DATE, INCOME_ID],
        ascending=[False, True],
        kind="mergesort",
    ).reset_index(drop=True)


def income_table(rows: pd.DataFrame) -> pd.DataFrame:
    """Detail columns: date, description, amount. Amount stays numeric."""
    columns = ["Date", "Description", "Amount"]
    if rows.empty:
        empty = pd.DataFrame(columns=columns)
        empty["Amount"] = pd.to_numeric(empty["Amount"])
        return empty
    table = pd.DataFrame(
        {
            "Date": pd.to_datetime(rows[INCOME_DATE]).dt.strftime("%Y-%m-%d"),
            "Description": rows[INCOME_DESCRIPTION].astype("string").fillna(""),
            "Amount": pd.to_numeric(rows[INCOME_AMOUNT]),
        }
    )
    return table.loc[:, columns].reset_index(drop=True)


def total_received(
    deposits: pd.DataFrame | None = None,
    period: Period | None = None,
) -> float:
    """Sum cash received in the period. Missing deposits are $0."""
    if deposits is None or period is None:
        return 0.0
    return sum(line.total for line in income_lines(deposits, period))


def received_for_source(
    deposits: pd.DataFrame,
    source_id: str,
    period: Period,
) -> float:
    rows = deposits_for_source(deposits, source_id, period)
    if rows.empty:
        return 0.0
    return float(rows[INCOME_AMOUNT].sum())


def net_profit(income: float, expenses: float) -> float:
    """Cash income minus expenses for the same period."""
    return income - expenses


def rows_in_period(deposits: pd.DataFrame, period: Period) -> pd.DataFrame:
    if deposits.empty or INCOME_DATE not in deposits.columns:
        return deposits.iloc[0:0]
    mask = deposits[INCOME_DATE].map(_as_date).map(period.contains)
    return deposits.loc[mask].copy()


def _totals_by_source(chosen: pd.DataFrame) -> pd.Series:
    if chosen.empty or INCOME_SOURCE not in chosen.columns or INCOME_AMOUNT not in chosen.columns:
        return pd.Series(dtype=float)
    return chosen.groupby(INCOME_SOURCE, sort=False)[INCOME_AMOUNT].sum()


def _source_present(deposits: pd.DataFrame, source_id: str) -> bool:
    if deposits.empty or INCOME_SOURCE not in deposits.columns or source_id == "":
        return False
    return bool((deposits[INCOME_SOURCE] == source_id).any())


def _read_income(src: object) -> pd.DataFrame:
    frame = _read_csv(src)
    missing = [column for column in INCOME_COLUMNS if column not in frame.columns]
    if missing:
        raise FinanceDataError("The income CSV is missing a required column.")
    out = frame.loc[:, INCOME_COLUMNS].copy()
    out[INCOME_ID] = out[INCOME_ID].astype("string").fillna("").str.strip()
    out[INCOME_DATE] = pd.to_datetime(out[INCOME_DATE], format="%Y-%m-%d", errors="coerce")
    out[INCOME_SOURCE] = out[INCOME_SOURCE].astype("string").fillna("").str.strip()
    out[INCOME_DESCRIPTION] = out[INCOME_DESCRIPTION].astype("string").fillna("").str.strip()
    out[INCOME_AMOUNT] = pd.to_numeric(out[INCOME_AMOUNT], errors="coerce")
    out = out.dropna(subset=[INCOME_DATE, INCOME_AMOUNT])
    out = out.loc[out[INCOME_SOURCE].ne("")]
    return _dedupe_income(out.reset_index(drop=True))


def _dedupe_income(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per Transaction ID. Blank ids are kept, since they cannot collide."""
    if frame.empty:
        return frame
    has_id = frame[INCOME_ID].ne("")
    identified = frame.loc[has_id].drop_duplicates(INCOME_ID, keep="first")
    anonymous = frame.loc[~has_id]
    combined = pd.concat([identified, anonymous], ignore_index=True)
    return combined.sort_values([INCOME_DATE, INCOME_SOURCE], kind="mergesort").reset_index(drop=True)


def _read_csv(src: object) -> pd.DataFrame:
    kwargs: dict[str, object] = {"dtype": str}
    if isinstance(src, (str, os.PathLike)):
        kwargs["encoding"] = "utf-8-sig"
    try:
        frame = pd.read_csv(src, **kwargs)
    except pd.errors.EmptyDataError as exc:
        raise FinanceDataError("The income CSV is empty.") from exc
    except pd.errors.ParserError as exc:
        raise FinanceDataError("The income CSV could not be parsed.") from exc
    except FileNotFoundError as exc:
        raise FinanceDataError("The income CSV file was not found.") from exc
    except OSError as exc:
        raise FinanceDataError("The income CSV could not be read.") from exc
    frame.columns = [str(column).strip() for column in frame.columns]
    return frame


def _empty_income() -> pd.DataFrame:
    frame = pd.DataFrame(columns=INCOME_COLUMNS)
    frame[INCOME_DATE] = pd.to_datetime(frame[INCOME_DATE])
    frame[INCOME_AMOUNT] = pd.to_numeric(frame[INCOME_AMOUNT])
    return frame


def _as_date(value: object) -> date | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()
