"""Vehicles from the assets file.

The Vehicles page lists rows whose Category is exactly ``Vehicles`` in
FINANCE_ASSETS_CSV, the same file the balance sheet loads. The asset name
is ``YYYY Make Model Trim``. Notes may hold an odometer reading, engine
hours, a VIN last 6, and a loan line. A note that does not match those
pieces is shown raw.

A loan line is ``Loan: <Lender> ending <4 digits>``, for example
``Loan: Wells Fargo ending 7635``, ``Loan: Wells Fargo ending 8492``,
``Loan: Truist ending 1001``, or ``Loan: Stellantis ending 0001``. A
vehicle with no loan has no such text. The four digits match one loan
account in the balance file. Lender words break a tie and reject a
contradictory name. A credit card that shares the last four digits is not
used. Equity is the estimated value minus that balance. With no loan line,
equity is the estimated value.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import pandas as pd
import streamlit as st

from kirbancials.activity import (
    AccountBalance,
    account_balance_kind,
    latest_account_balances,
    load_balances,
    resolve_balances,
)
from kirbancials.assets import NO_ASSETS_NOTE, AssetRecord, resolve_assets
from kirbancials.data import FinanceDataError
from kirbancials.formatting import MISSING, format_money, show_money_table

VEHICLES_CATEGORY = "Vehicles"
VEHICLES_PAGE = "views/vehicles.py"
VEHICLES_LINK_CAPTION = "Miles, loans, and estimated value for each vehicle."
NO_VEHICLES = "No vehicles listed."
SAMPLE_BALANCE_NOTE = (
    "Showing bundled sample balances. These are fake round numbers, not your accounts."
)
USAGE_NOTE = (
    "Estimated value is the market value on the asset. "
    "Miles and the loan line are read from notes. "
    "An estimate is marked with ~ or est. "
    "Hours are shown in the Miles column when the note has hours and no miles."
)

_MULTI_WORD_MAKES = (
    "alfa romeo",
    "aston martin",
    "boston whaler",
    "can am",
    "can-am",
    "harley davidson",
    "harley-davidson",
    "land rover",
    "mercedes benz",
    "rolls royce",
    "rolls-royce",
    "sea ray",
)

_YEAR_PREFIX = re.compile(r"^(?P<year>(?:19|20)\d{2})\s+(?P<rest>.+)$")
_LEADING_WORD = r"(?:about|approximately|approx|around|estimate|est)"
_TRAILING_WORD = r"(?:approximately|approx|estimate|est)"
_NUMBER = r"(?P<num>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
_USAGE = re.compile(
    rf"(?P<tilde>~)?\s*"
    rf"(?:(?P<word1>{_LEADING_WORD})\.?\s+)?"
    rf"{_NUMBER}"
    rf"\s*(?:engine\s+)?"
    rf"(?P<unit>miles?|mi|hours?|hrs?)\b"
    rf"(?:\s+(?P<word2>{_TRAILING_WORD})\.?)?"
    rf"(?:\s*\((?P<paren>[^)]*)\))?",
    re.IGNORECASE,
)
_HOURS_FIRST = re.compile(
    rf"(?P<tilde>~)?\s*"
    rf"(?:(?P<word1>{_LEADING_WORD})\.?\s+)?"
    rf"(?:engine\s+)?hours?\s*[:#]?\s*"
    rf"{_NUMBER}"
    rf"(?:\s+(?P<word2>{_TRAILING_WORD})\.?)?",
    re.IGNORECASE,
)
_VIN = re.compile(
    r"\bVIN(?:\s+last\s+6)?\s*[:#-]?\s*(?P<vin>[A-Za-z0-9]{6})\b",
    re.IGNORECASE,
)
# Loan: <Lender> ending <4 digits>. "ending" is the mask keyword, not part of the lender.
_LENDER_WORD = r"(?!(?:ending)\b)[A-Za-z][A-Za-z&.'-]*"
_LOAN = re.compile(
    rf"\bLoan\s*:\s*(?P<lender>{_LENDER_WORD}(?:\s+{_LENDER_WORD})*)"
    rf"\s+ending\s+(?P<mask>\d{{4}})\b",
    re.IGNORECASE,
)
_LOAN_PREFIX = re.compile(r"\bLoan\s*:", re.IGNORECASE)
_MASK = re.compile(r"\bending\s+(\d{4})\b", re.IGNORECASE)
_ESTIMATE_WORD = re.compile(r"\b(?:est(?:imate)?|approx(?:imately)?|around|about)\b", re.IGNORECASE)
_LEFTOVER = re.compile(r"[\s,;|/.\-–—:()·•#]+")
_LENDER_SKIP = frozenset({"loan", "ending", "auto", "the", "and"})


@dataclass(frozen=True)
class VehicleName:
    """Year, make, and model parsed from ``YYYY Make Model Trim``."""

    year: str
    make: str
    model_trim: str
    parsed: bool


@dataclass(frozen=True)
class VehicleNotes:
    """Odometer, VIN, and loan text parsed from one Notes cell."""

    miles: float | None
    miles_tilde: bool
    miles_word: bool
    hours: float | None
    hours_tilde: bool
    hours_word: bool
    vin: str
    loan: str
    loan_mask: str
    loan_unparsed: bool
    raw: str
    show_raw: bool


@dataclass(frozen=True)
class VehicleRow:
    """One vehicles-table row. Missing money stays None so the cell is blank."""

    year: str
    make: str
    model_trim: str
    vin: str
    miles: str
    market_value: float | None
    loan: str
    loan_balance: float | None
    equity: float | None
    valuation_date: date | None
    notes: str
    show_raw: bool


def parse_vehicle_name(name: str) -> VehicleName:
    """Split ``YYYY Make Model Trim``. Anything else keeps the raw name."""
    text = " ".join(name.split())
    if not text:
        return VehicleName(MISSING, MISSING, MISSING, False)
    match = _YEAR_PREFIX.match(text)
    if match is None:
        return VehicleName(MISSING, MISSING, text, False)
    year = match.group("year")
    rest = match.group("rest").strip()
    folded = rest.casefold()
    make = ""
    model = ""
    for phrase in sorted(_MULTI_WORD_MAKES, key=len, reverse=True):
        if folded == phrase or folded.startswith(phrase + " "):
            make = rest[: len(phrase)]
            model = rest[len(phrase) :].strip()
            break
    if not make:
        parts = rest.split(" ", 1)
        make = parts[0]
        model = parts[1].strip() if len(parts) > 1 else ""
    if not make:
        return VehicleName(year, MISSING, rest or MISSING, False)
    return VehicleName(year, make, model or MISSING, True)


def parse_vehicle_notes(notes: str) -> VehicleNotes:
    """Read miles, hours, VIN, and ``Loan: <Lender> ending <4 digits>``.

    A parenthetical such as ``(owner, Nov 22, 2026)`` stays with the mileage.
    A vehicle with no loan has no loan line. Text that is not one of those
    pieces, including a loan line that is not lender-plus-four-digits, sets
    ``show_raw`` so the table can show the original note.
    """
    text = notes.strip()
    empty = VehicleNotes(None, False, False, None, False, False, "", "", "", False, text, False)
    if not text:
        return empty

    spans: list[tuple[int, int]] = []
    miles: tuple[float, bool, bool] | None = None
    hours: tuple[float, bool, bool] | None = None
    mile_count = 0
    hour_count = 0

    for match in _USAGE.finditer(text):
        spans.append(match.span())
        number = _number(match.group("num"))
        tilde, word = _estimate_flags(match)
        if _is_hours(match.group("unit")):
            hour_count += 1
            if hours is None:
                hours = (number, tilde, word)
        else:
            mile_count += 1
            if miles is None:
                miles = (number, tilde, word)

    for match in _HOURS_FIRST.finditer(text):
        if _overlaps(match.span(), spans):
            continue
        spans.append(match.span())
        hour_count += 1
        if hours is None:
            number = _number(match.group("num"))
            tilde, word = _estimate_flags(match)
            hours = (number, tilde, word)

    vins = list(_VIN.finditer(text))
    vin = ""
    if vins:
        vin = vins[0].group("vin").upper()
        for match in vins:
            spans.append(match.span())

    loan = ""
    loan_mask = ""
    loan_matches = list(_LOAN.finditer(text))
    for match in loan_matches:
        spans.append(match.span())
    masks = _MASK.findall(text)
    if len(loan_matches) == 1 and len(masks) == 1:
        lender = " ".join(loan_matches[0].group("lender").split())
        loan_mask = loan_matches[0].group("mask")
        loan = f"{lender} ending {loan_mask}"
    loan_unparsed = bool(_LOAN_PREFIX.search(text)) and not loan

    conflict = mile_count > 1 or hour_count > 1 or len({item.group("vin").upper() for item in vins}) > 1
    conflict = conflict or len(loan_matches) > 1 or loan_unparsed
    leftover = _leftover(text, spans)
    show_raw = conflict or bool(leftover) or not spans
    return VehicleNotes(
        None if miles is None else miles[0],
        False if miles is None else miles[1],
        False if miles is None else miles[2],
        None if hours is None else hours[0],
        False if hours is None else hours[1],
        False if hours is None else hours[2],
        vin,
        loan,
        loan_mask,
        loan_unparsed,
        text,
        show_raw,
    )


def format_usage(notes: VehicleNotes) -> str:
    """Miles for the table. Hours stand in when the note has no miles."""
    if notes.miles is not None:
        return _mark(notes.miles, notes.miles_tilde, notes.miles_word, "")
    if notes.hours is not None:
        return _mark(notes.hours, notes.hours_tilde, notes.hours_word, " hours")
    return MISSING


def loan_balance_for_mask(
    mask: str,
    lender: str,
    balances: pd.DataFrame | None,
) -> float | None:
    """Latest balance for the one loan account whose mask is ``ending ####``.

    Zero matches, or more than one after the lender words are applied, is
    None. The caller leaves Loan Balance and Equity blank in that case.
    """
    if not mask:
        return None
    tokens = _lender_tokens(lender)
    matched: list[AccountBalance] = []
    for item in latest_account_balances(balances):
        if account_balance_kind(item.account, item.account_type) != "loan":
            continue
        if mask not in _MASK.findall(item.account):
            continue
        if not _lender_agrees(item.account, tokens):
            continue
        matched.append(item)
    if len(matched) != 1:
        return None
    return matched[0].amount


def build_vehicle_rows(
    assets: Sequence[AssetRecord],
    balances: pd.DataFrame | None,
) -> tuple[VehicleRow, ...]:
    """Vehicles only, highest estimated value first. Missing values sort last."""
    rows = [
        _vehicle_row(asset, balances)
        for asset in assets
        if asset.category == VEHICLES_CATEGORY
    ]
    rows.sort(
        key=lambda row: (
            row.market_value is None,
            0.0 if row.market_value is None else -float(row.market_value),
            row.make.casefold(),
            row.model_trim.casefold(),
            row.year,
        )
    )
    return tuple(rows)


def vehicle_total(rows: Sequence[VehicleRow]) -> float:
    """Sum of estimated values. A blank market value is left out."""
    return float(sum(row.market_value for row in rows if row.market_value is not None))


def vehicle_frame(rows: Sequence[VehicleRow]) -> pd.DataFrame:
    """Display columns. VIN and Notes appear only when a row has them."""
    include_vin = any(row.vin for row in rows)
    include_notes = any(row.show_raw for row in rows)
    records: list[dict[str, object]] = []
    for row in rows:
        record: dict[str, object] = {
            "Year": row.year,
            "Make": row.make,
            "Model/Trim": row.model_trim,
        }
        if include_vin:
            record["VIN"] = row.vin or MISSING
        record["Miles"] = row.miles
        record["Est. Value"] = _money(row.market_value)
        record["Loan"] = row.loan or MISSING
        record["Loan Balance"] = _money(row.loan_balance)
        record["Equity"] = _money(row.equity)
        record["Valuation Date"] = row.valuation_date
        if include_notes:
            record["Notes"] = row.notes if row.show_raw else MISSING
        records.append(record)
    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        return frame
    frame["Valuation Date"] = pd.to_datetime(frame["Valuation Date"])
    return frame


def render_vehicles() -> None:
    """Draw the vehicles page. Login has already run in the entrypoint."""
    asset_load = resolve_assets()
    balances, balance_error, balance_sample = _load_balances()
    if asset_load.warning:
        st.warning(asset_load.warning)
    if balance_error:
        st.error(balance_error)
    if not asset_load.configured:
        st.caption(NO_ASSETS_NOTE)
        return
    rows = build_vehicle_rows(asset_load.assets, balances)
    if asset_load.warning and not rows:
        return
    st.metric("Total value", format_money(vehicle_total(rows)), border=True)
    if balance_sample and any(row.loan_balance is not None for row in rows):
        st.warning(SAMPLE_BALANCE_NOTE)
    if not rows:
        st.caption(NO_VEHICLES)
        return
    st.caption(USAGE_NOTE)
    show_money_table(vehicle_frame(rows), ["Est. Value", "Loan Balance", "Equity"])


def _vehicle_row(asset: AssetRecord, balances: pd.DataFrame | None) -> VehicleRow:
    name = parse_vehicle_name(asset.name)
    notes = parse_vehicle_notes(asset.notes)
    balance: float | None = None
    if notes.loan_mask:
        balance = loan_balance_for_mask(notes.loan_mask, notes.loan, balances)
    return VehicleRow(
        year=name.year,
        make=name.make,
        model_trim=name.model_trim,
        vin=notes.vin,
        miles=format_usage(notes),
        market_value=asset.market_value,
        loan=notes.loan,
        loan_balance=balance,
        equity=_equity(asset.market_value, balance, bool(notes.loan) or notes.loan_unparsed),
        valuation_date=asset.valuation_date,
        notes=notes.raw,
        show_raw=notes.show_raw,
    )


def _equity(value: float | None, balance: float | None, has_loan: bool) -> float | None:
    if value is None:
        return None
    if not has_loan:
        return value
    if balance is None:
        return None
    return value - balance


def _estimate_flags(match: re.Match[str]) -> tuple[bool, bool]:
    tilde = match.group("tilde") is not None
    word = bool(match.group("word1") or match.group("word2"))
    paren = match.groupdict().get("paren") or ""
    if paren and _ESTIMATE_WORD.search(paren):
        word = True
    return tilde, word


def _is_hours(unit: str) -> bool:
    return unit.casefold().startswith("h")


def _number(text: str) -> float:
    return float(text.replace(",", ""))


def _mark(value: float, tilde: bool, word: bool, suffix: str) -> str:
    body = _format_count(value)
    if tilde and word:
        return f"~{body}{suffix} est."
    if tilde:
        return f"~{body}{suffix}"
    if word:
        return f"{body}{suffix} est."
    return f"{body}{suffix}"


def _format_count(value: float) -> str:
    if math.isfinite(value) and abs(value - round(value)) < 1e-6:
        return f"{int(round(value)):,}"
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return text


def _overlaps(span: tuple[int, int], spans: Sequence[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < other_end and other_start < end for other_start, other_end in spans)


def _leftover(text: str, spans: Sequence[tuple[int, int]]) -> str:
    covered = [False] * len(text)
    for start, end in spans:
        for index in range(max(0, start), min(len(text), end)):
            covered[index] = True
    rest = "".join(char for index, char in enumerate(text) if not covered[index])
    return _LEFTOVER.sub("", rest)


def _lender_tokens(lender: str) -> list[str]:
    tokens: list[str] = []
    for token in re.findall(r"[A-Za-z0-9]+", lender):
        if token.isdigit() or len(token) < 3 or token.casefold() in _LENDER_SKIP:
            continue
        tokens.append(token)
    return tokens


def _lender_agrees(account: str, tokens: Sequence[str]) -> bool:
    if not tokens:
        return True
    folded = account.casefold()
    return all(token.casefold() in folded for token in tokens)


def _money(value: float | None) -> float:
    if value is None:
        return float("nan")
    return float(value)


def _load_balances() -> tuple[pd.DataFrame, str | None, bool]:
    try:
        loaded = load_balances(resolve_balances())
    except FinanceDataError as exc:
        return pd.DataFrame(), str(exc), False
    return loaded.rows, None, loaded.is_sample
