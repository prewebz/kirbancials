"""Manually entered assets for the balance sheet.

FINANCE_ASSETS_CSV is plain CSV text. One row is one asset. Market values
are typed in from appraisals. If the variable is missing or blank, there
are no assets. A file that cannot be read returns a warning and no rows.
The warning does not include any cell from the file.
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

ASSETS_ENV = "FINANCE_ASSETS_CSV"

ASSET_COLUMNS = [
    "Asset ID",
    "Asset Name",
    "Category",
    "Acquired Date",
    "Cost Basis",
    "Market Value",
    "Valuation Date",
    "Valuation Source",
    "Linked Loan ID",
    "Income Source ID",
    "Notes",
]

ASSET_HEADER = ",".join(ASSET_COLUMNS)

NO_ASSETS_NOTE = "No assets are configured."

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DECIMAL = re.compile(r"-?\d+(?:\.\d+)?")

_MISSING_COLUMN = "The assets CSV is missing a required column."
_UNREADABLE = "The assets CSV has a row that could not be read."
_UNPARSED = "The assets CSV could not be parsed."
_OMITTED = "The assets CSV could not be read. Assets are left out of this view."


@dataclass(frozen=True)
class AssetRecord:
    asset_id: str
    name: str
    category: str
    acquired: date | None
    cost_basis: float | None
    market_value: float | None
    valuation_date: date | None
    valuation_source: str
    linked_loan_id: str
    income_source_id: str
    notes: str


@dataclass(frozen=True)
class AssetLoad:
    """Parsed assets, a safe warning, and whether the env var was set."""

    assets: tuple[AssetRecord, ...]
    warning: str | None
    configured: bool


def resolve_assets(environ: Mapping[str, str] | None = None) -> AssetLoad:
    """Return assets from FINANCE_ASSETS_CSV.

    A missing or blank variable is an empty load with no warning. A CSV that
    cannot be used is an empty load with a warning. Either way the caller
    still renders cash and liabilities.
    """
    env = os.environ if environ is None else environ
    raw = env.get(ASSETS_ENV)
    if raw is None or str(raw).lstrip("\ufeff").strip() == "":
        return AssetLoad((), None, False)
    try:
        return AssetLoad(tuple(parse_assets(str(raw))), None, True)
    except FinanceDataError as exc:
        return AssetLoad((), str(exc), True)
    except Exception:
        return AssetLoad((), _OMITTED, True)


def parse_assets(text: str) -> list[AssetRecord]:
    """Parse asset CSV text. Raises FinanceDataError when the file is unusable."""
    try:
        frame = pd.read_csv(io.StringIO(text.lstrip("\ufeff")), dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError as exc:
        raise FinanceDataError(_UNPARSED) from exc
    except pd.errors.ParserError as exc:
        raise FinanceDataError(_UNPARSED) from exc
    frame.columns = [str(column).strip() for column in frame.columns]
    missing = [column for column in ASSET_COLUMNS if column not in frame.columns]
    if missing:
        raise FinanceDataError(_MISSING_COLUMN)
    chosen = frame.loc[:, ASSET_COLUMNS]
    records: list[AssetRecord] = []
    seen: set[str] = set()
    for raw in chosen.to_dict("records"):
        if _blank_row(raw):
            continue
        parsed = _parse_record(raw)
        if parsed is None or parsed.asset_id in seen:
            raise FinanceDataError(_UNREADABLE)
        seen.add(parsed.asset_id)
        records.append(parsed)
    return records


def _blank_row(raw: Mapping[str, object]) -> bool:
    return all(str(raw.get(column, "")).strip() == "" for column in ASSET_COLUMNS)


def _parse_record(raw: Mapping[str, object]) -> AssetRecord | None:
    asset_id = _required_text(raw.get("Asset ID"))
    name = _required_text(raw.get("Asset Name"))
    category = _required_text(raw.get("Category"))
    if asset_id is None or name is None or category is None:
        return None
    acquired, acquired_ok = _optional_date(raw.get("Acquired Date"))
    cost, cost_ok = _optional_decimal(raw.get("Cost Basis"))
    market, market_ok = _optional_decimal(raw.get("Market Value"))
    valued, valued_ok = _optional_date(raw.get("Valuation Date"))
    if not (acquired_ok and cost_ok and market_ok and valued_ok):
        return None
    return AssetRecord(
        asset_id=asset_id,
        name=name,
        category=category,
        acquired=acquired,
        cost_basis=cost,
        market_value=market,
        valuation_date=valued,
        valuation_source=_optional_text(raw.get("Valuation Source")),
        linked_loan_id=_optional_text(raw.get("Linked Loan ID")),
        income_source_id=_optional_text(raw.get("Income Source ID")),
        notes=_optional_text(raw.get("Notes")),
    )


def _required_text(value: object) -> str | None:
    text = _optional_text(value)
    return text or None


def _optional_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _optional_date(value: object) -> tuple[date | None, bool]:
    text = _optional_text(value)
    if text == "":
        return None, True
    if _DATE.fullmatch(text) is None:
        return None, False
    try:
        return datetime.strptime(text, "%Y-%m-%d").date(), True
    except ValueError:
        return None, False


def _optional_decimal(value: object) -> tuple[float | None, bool]:
    text = _optional_text(value)
    if text == "":
        return None, True
    if _DECIMAL.fullmatch(text) is None:
        return None, False
    number = float(text)
    if not math.isfinite(number):
        return None, False
    return number, True
