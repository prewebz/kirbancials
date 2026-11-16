"""VWH trending: property tax bills and a yearly run rate.

FINANCE_VWH_TAX_CSV is plain CSV text, not gzipped, same as
FINANCE_VWH_PL_CSV. One row is one pay year and one house. Indianapolis
bills property tax in two installments. The amounts are the tax only.

The run rate turns the monthly Kline totals into a year. Income is every
Kline P&L row in the Income section. Operating expense is every Kline P&L
row in the Expense section. Account numbers are not a fixed list: 5000,
5300, 6000, and any later Kline account count when the section matches.
Both are the average of the latest up-to-12 months in the file, times 12.
A month that is in the file with no row for that house counts as zero.

Property tax is that house's annual bill for the latest pay year. When the
house has no bill, it is the sum of BANK-TAX over those same months.
Insurance is BANK-INS in the latest of those months, times 12. BANK-TAX-PY
and BANK-TAX-PEN are left out. A negative Kline income amount reduces income.
"""

from __future__ import annotations

import calendar
import io
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd

from kirbancials.data import FinanceDataError
from kirbancials.formatting import MISSING
from kirbancials.vwh import SECTION_EXPENSE, SECTION_INCOME, available_months, format_month, parse_month

TAX_ENV = "FINANCE_VWH_TAX_CSV"
NO_TAX_DATA = "Property tax data isn't loaded yet."
TAX_MISSING_COLUMN = "The property tax CSV is missing a required column."
TAX_UNPARSED = "The property tax CSV could not be parsed."
TAX_OMITTED = "The property tax CSV could not be read."

TAX_COLUMNS = [
    "Pay Year",
    "Property ID",
    "Address",
    "Parcel",
    "Spring Tax",
    "Fall Tax",
    "Annual Tax",
    "Spring Status",
    "Fall Status",
]

_TAX_TEXT = ["Pay Year", "Property ID", "Address", "Spring Status", "Fall Status"]
_TAX_CENTS = ["Spring Cents", "Fall Cents", "Annual Cents"]
_KLINE = "kline p&l"
_BANK_TAX = "BANK-TAX"
_BANK_INS = "BANK-INS"
_MAX_MONTHS = 12
_YEAR = re.compile(r"\d{4}")
_DECIMAL = re.compile(r"[+-]?\d+(?:\.\d+)?")


@dataclass(frozen=True)
class TaxLoad:
    """Parsed tax rows, a safe warning, and whether the env var was set."""

    rows: pd.DataFrame
    warning: str | None
    configured: bool


@dataclass(frozen=True)
class TaxLine:
    property_id: str
    address: str
    spring: float
    fall: float
    annual: float
    spring_status: str
    fall_status: str
    change: float | None
    change_pct: str


@dataclass(frozen=True)
class TaxTable:
    pay_year: str
    lines: tuple[TaxLine, ...]
    total_spring: float
    total_fall: float
    total_annual: float
    total_change: float | None
    total_change_pct: str


@dataclass(frozen=True)
class RunRateLine:
    property_id: str
    address: str
    income: float
    operating_expense: float
    property_tax: float
    insurance: float
    net: float
    net_per_month: float


@dataclass(frozen=True)
class RunRate:
    months: tuple[str, ...]
    tax_configured: bool
    lines: tuple[RunRateLine, ...]
    total: RunRateLine


def tax_skipped_message(count: int) -> str:
    """Warning for tax rows that were left out. It does not quote the file."""
    noun = "row" if count == 1 else "rows"
    return f"{count} {noun} in the property tax CSV were skipped."


def empty_tax_rows() -> pd.DataFrame:
    frame = pd.DataFrame({column: pd.Series(dtype="string") for column in _TAX_TEXT})
    for column in _TAX_CENTS:
        frame[column] = pd.Series(dtype="int64")
    return frame


def resolve_vwh_tax(environ: Mapping[str, str] | None = None) -> TaxLoad:
    """Return rows from FINANCE_VWH_TAX_CSV.

    A missing or blank variable is an empty load with no warning. A CSV that
    cannot be used is an empty load with a warning. The warning does not
    include any cell from the file.
    """
    env = os.environ if environ is None else environ
    raw = env.get(TAX_ENV)
    if raw is None or str(raw).lstrip("\ufeff").strip() == "":
        return TaxLoad(empty_tax_rows(), None, False)
    try:
        rows, warning = parse_vwh_tax(str(raw))
        return TaxLoad(rows, warning, True)
    except FinanceDataError as exc:
        return TaxLoad(empty_tax_rows(), str(exc), True)
    except Exception:
        return TaxLoad(empty_tax_rows(), TAX_OMITTED, True)


def parse_vwh_tax(text: str) -> tuple[pd.DataFrame, str | None]:
    """Parse property-tax CSV text. Raises FinanceDataError when unusable."""
    try:
        frame = pd.read_csv(
            io.StringIO(text.lstrip("\ufeff")),
            dtype=str,
            keep_default_na=False,
        )
    except pd.errors.EmptyDataError as exc:
        raise FinanceDataError(TAX_UNPARSED) from exc
    except pd.errors.ParserError as exc:
        raise FinanceDataError(TAX_UNPARSED) from exc
    frame.columns = [str(column).strip() for column in frame.columns]
    missing = [column for column in TAX_COLUMNS if column not in frame.columns]
    if missing:
        raise FinanceDataError(TAX_MISSING_COLUMN)
    records: list[dict[str, object]] = []
    skipped = 0
    for raw in frame.loc[:, TAX_COLUMNS].to_dict("records"):
        if all(_text(raw.get(column)) == "" for column in TAX_COLUMNS):
            continue
        parsed = _parse_tax_row(raw)
        if parsed is None:
            skipped += 1
            continue
        records.append(parsed)
    warning = tax_skipped_message(skipped) if skipped else None
    if not records:
        return empty_tax_rows(), warning
    out = pd.DataFrame.from_records(records)
    for column in _TAX_TEXT:
        out[column] = out[column].astype("string")
    for column in _TAX_CENTS:
        out[column] = out[column].astype("int64")
    return out, warning


def available_pay_years(rows: pd.DataFrame) -> list[str]:
    """Pay years in the file, newest first."""
    if rows.empty or "Pay Year" not in rows.columns:
        return []
    found = {str(year) for year in rows["Pay Year"].tolist() if str(year).strip()}
    return sorted(found, reverse=True)


def property_tax_table(rows: pd.DataFrame, pay_year: str) -> TaxTable:
    """One row per house for `pay_year`, plus totals.

    Change is this year's annual tax minus the prior pay year. A house with
    no prior-year row has no change. The total change adds those houses only.
    """
    current = _tax_buckets(rows, pay_year)
    prior = _tax_buckets(rows, _prior_year(pay_year))
    ordered = sorted(
        current.items(),
        key=lambda item: ((item[1]["address"] or item[0]).casefold(), item[0]),
    )
    lines: list[TaxLine] = []
    spring = fall = annual = 0
    change_sum = 0
    prior_sum = 0
    any_prior = False
    for property_id, bucket in ordered:
        spring += int(bucket["spring"])
        fall += int(bucket["fall"])
        annual += int(bucket["annual"])
        previous = prior.get(property_id)
        if previous is None:
            change = None
            change_pct = MISSING
        else:
            any_prior = True
            change_cents = int(bucket["annual"]) - int(previous["annual"])
            change_sum += change_cents
            prior_sum += int(previous["annual"])
            change = _dollars(change_cents)
            change_pct = format_percent_change(change_cents, int(previous["annual"]))
        lines.append(
            TaxLine(
                property_id=property_id,
                address=str(bucket["address"] or property_id),
                spring=_dollars(int(bucket["spring"])),
                fall=_dollars(int(bucket["fall"])),
                annual=_dollars(int(bucket["annual"])),
                spring_status=str(bucket["spring_status"]),
                fall_status=str(bucket["fall_status"]),
                change=change,
                change_pct=change_pct,
            )
        )
    return TaxTable(
        pay_year=pay_year,
        lines=tuple(lines),
        total_spring=_dollars(spring),
        total_fall=_dollars(fall),
        total_annual=_dollars(annual),
        total_change=_dollars(change_sum) if any_prior else None,
        total_change_pct=format_percent_change(change_sum, prior_sum) if any_prior else MISSING,
    )


def format_percent_change(change_cents: int | None, prior_cents: int | None) -> str:
    """'+5.6%', '-5.6%', '0.0%', or an em dash when it cannot be divided."""
    if change_cents is None or prior_cents is None or prior_cents == 0:
        return MISSING
    pct = (Decimal(change_cents) / Decimal(prior_cents) * Decimal(100)).quantize(
        Decimal("0.1"),
        rounding=ROUND_HALF_UP,
    )
    if pct > 0:
        return f"+{pct}%"
    return f"{pct}%"


def basis_months(rows: pd.DataFrame) -> tuple[str, ...]:
    """The latest 12 months present in the file, oldest first.

    Fewer than 12 months returns every month in the file.
    """
    newest_first = available_months(rows)
    return tuple(reversed(newest_first[:_MAX_MONTHS]))


def annual_run_rate(
    pl_rows: pd.DataFrame,
    tax_rows: pd.DataFrame,
    *,
    tax_configured: bool,
) -> RunRate:
    """One yearly run-rate row per house, plus a portfolio total."""
    months = basis_months(pl_rows)
    month_set = set(months)
    latest_month = months[-1] if months else ""
    years = available_pay_years(tax_rows)
    latest_year = years[0] if years else None
    records = _records(pl_rows)
    bills = _tax_buckets(tax_rows, latest_year)
    seen: set[str] = set()
    property_ids: list[str] = []
    for record in records:
        property_id = str(record["Property ID"])
        if property_id not in seen:
            seen.add(property_id)
            property_ids.append(property_id)
    for property_id in bills:
        if property_id not in seen:
            seen.add(property_id)
            property_ids.append(property_id)

    drafted: list[tuple[str, str, str, int, int, int, int, int, int]] = []
    for property_id in property_ids:
        bill = bills.get(property_id)
        address = _pl_address(records, property_id, month_set) or _pl_address(records, property_id, None)
        if not address and bill is not None:
            address = str(bill["address"])
        address = address or property_id
        income = _annualize(_kline_cents(records, property_id, month_set, SECTION_INCOME), len(months))
        operating = _annualize(_kline_cents(records, property_id, month_set, SECTION_EXPENSE), len(months))
        if bill is None:
            tax = _account_cents(records, property_id, month_set, _BANK_TAX, SECTION_EXPENSE)
        else:
            tax = int(bill["annual"])
        if latest_month:
            insurance = _account_cents(records, property_id, {latest_month}, _BANK_INS, SECTION_EXPENSE) * 12
        else:
            insurance = 0
        net = income - operating - tax - insurance
        drafted.append(
            (
                address.casefold(),
                property_id,
                address,
                income,
                operating,
                tax,
                insurance,
                net,
                _per_month(net),
            )
        )
    drafted.sort()
    totals = [0, 0, 0, 0, 0, 0]
    lines: list[RunRateLine] = []
    for _sort, property_id, address, income, operating, tax, insurance, net, per_month in drafted:
        for index, value in enumerate((income, operating, tax, insurance, net, per_month)):
            totals[index] += value
        lines.append(
            RunRateLine(
                property_id=property_id,
                address=address,
                income=_dollars(income),
                operating_expense=_dollars(operating),
                property_tax=_dollars(tax),
                insurance=_dollars(insurance),
                net=_dollars(net),
                net_per_month=_dollars(per_month),
            )
        )
    total = RunRateLine(
        property_id="",
        address="Total",
        income=_dollars(totals[0]),
        operating_expense=_dollars(totals[1]),
        property_tax=_dollars(totals[2]),
        insurance=_dollars(totals[3]),
        net=_dollars(totals[4]),
        net_per_month=_dollars(totals[5]),
    )
    return RunRate(months=months, tax_configured=tax_configured, lines=tuple(lines), total=total)


def run_rate_note(months: Sequence[str], tax_configured: bool) -> str:
    """One plain sentence block under the run-rate table."""
    count = len(months)
    if count == 0:
        return "No months in the file yet."
    noun = "month" if count == 1 else "months"
    if count == 1:
        span = format_month(months[0])
    else:
        span = f"{format_month(months[0])} through {format_month(months[-1])}"
    if tax_configured:
        tax = "Property tax is the latest annual bill, or the tax paid in those months when a house has no bill."
    else:
        tax = "Property tax is the tax paid in those months, because property tax data isn't loaded yet."
    return (
        f"Based on {span} ({count} {noun}). "
        "Income and operating expense are the Kline monthly average times 12, "
        "and a month with no row counts as $0. "
        f"{tax} "
        "Insurance is the latest month's insurance times 12. "
        "Penalties and prior-year tax are left out."
    )


def monthly_trend(rows: pd.DataFrame, property_id: str | None = None) -> pd.DataFrame:
    """Income, expense, and net for every month in the file, oldest first.

    Expense is every Expense-section row, same as the rest of the page.
    A house with no row in a month that the portfolio has is zero that month.
    """
    months = list(reversed(available_months(rows)))
    if not months:
        return pd.DataFrame(columns=["Month", "Label", "Income", "Expense", "Net"])
    records = _records(rows)
    built: list[dict[str, object]] = []
    for month in months:
        income = _section_cents(records, month, SECTION_INCOME, property_id)
        expense = _section_cents(records, month, SECTION_EXPENSE, property_id)
        built.append(
            {
                "Month": month,
                "Label": _short_month(month),
                "Income": _dollars(income),
                "Expense": _dollars(expense),
                "Net": _dollars(income - expense),
            }
        )
    return pd.DataFrame.from_records(built)


def trend_properties(rows: pd.DataFrame) -> tuple[tuple[str, str], ...]:
    """(property id, latest address) for every house in the profit and loss."""
    records = _records(rows)
    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for record in records:
        property_id = str(record["Property ID"])
        if property_id in seen:
            continue
        seen.add(property_id)
        address = _pl_address(records, property_id, None) or property_id
        pairs.append((property_id, address))
    pairs.sort(key=lambda item: (item[1].casefold(), item[0]))
    return tuple(pairs)


def _parse_tax_row(raw: Mapping[str, object]) -> dict[str, object] | None:
    year = _pay_year(_text(raw.get("Pay Year")))
    property_id = _text(raw.get("Property ID"))
    if year is None or not property_id:
        return None
    spring = _cents(_text(raw.get("Spring Tax")))
    fall = _cents(_text(raw.get("Fall Tax")))
    annual = _cents(_text(raw.get("Annual Tax")))
    if spring is None or fall is None or annual is None:
        return None
    return {
        "Pay Year": year,
        "Property ID": property_id,
        "Address": _clean_address(raw.get("Address")),
        "Spring Status": _text(raw.get("Spring Status")),
        "Fall Status": _text(raw.get("Fall Status")),
        "Spring Cents": spring,
        "Fall Cents": fall,
        "Annual Cents": annual,
    }


def _tax_buckets(rows: pd.DataFrame, year: str | None) -> dict[str, dict[str, object]]:
    """One bucket per house for a pay year. Amounts add. Later text wins."""
    if year is None or rows.empty or "Pay Year" not in rows.columns:
        return {}
    grouped: dict[str, dict[str, object]] = {}
    matched = rows.loc[rows["Pay Year"] == year]
    for record in matched.to_dict("records"):
        property_id = str(record["Property ID"])
        bucket = grouped.get(property_id)
        if bucket is None:
            grouped[property_id] = {
                "address": _clean_address(record.get("Address")),
                "spring": int(record["Spring Cents"]),
                "fall": int(record["Fall Cents"]),
                "annual": int(record["Annual Cents"]),
                "spring_status": _text(record.get("Spring Status")),
                "fall_status": _text(record.get("Fall Status")),
            }
            continue
        bucket["spring"] = int(bucket["spring"]) + int(record["Spring Cents"])
        bucket["fall"] = int(bucket["fall"]) + int(record["Fall Cents"])
        bucket["annual"] = int(bucket["annual"]) + int(record["Annual Cents"])
        address = _clean_address(record.get("Address"))
        if address:
            bucket["address"] = address
        spring_status = _text(record.get("Spring Status"))
        fall_status = _text(record.get("Fall Status"))
        if spring_status:
            bucket["spring_status"] = spring_status
        if fall_status:
            bucket["fall_status"] = fall_status
    return grouped


def _records(rows: pd.DataFrame) -> list[dict[str, object]]:
    if rows.empty:
        return []
    return rows.to_dict("records")


def _kline_cents(
    records: Sequence[Mapping[str, object]],
    property_id: str,
    months: set[str],
    section: str,
) -> int:
    """Signed cents for every Kline account in `section` over `months`."""
    total = 0
    for record in records:
        if str(record["Property ID"]) != property_id:
            continue
        if str(record["Month"]) not in months:
            continue
        if str(record["Section"]) != section:
            continue
        if not _is_kline(record.get("Source")):
            continue
        total += int(record["Cents"])
    return total


def _account_cents(
    records: Sequence[Mapping[str, object]],
    property_id: str,
    months: set[str],
    account: str,
    section: str,
) -> int:
    total = 0
    for record in records:
        if str(record["Property ID"]) != property_id:
            continue
        if str(record["Month"]) not in months:
            continue
        if str(record["Section"]) != section:
            continue
        if str(record["Account Number"]) != account:
            continue
        total += int(record["Cents"])
    return total


def _section_cents(
    records: Sequence[Mapping[str, object]],
    month: str,
    section: str,
    property_id: str | None,
) -> int:
    total = 0
    for record in records:
        if str(record["Month"]) != month:
            continue
        if str(record["Section"]) != section:
            continue
        if property_id is not None and str(record["Property ID"]) != property_id:
            continue
        total += int(record["Cents"])
    return total


def _pl_address(
    records: Sequence[Mapping[str, object]],
    property_id: str,
    months: set[str] | None,
) -> str:
    best_month = ""
    best = ""
    for record in records:
        if str(record["Property ID"]) != property_id:
            continue
        month = str(record["Month"])
        if months is not None and month not in months:
            continue
        address = _clean_address(record.get("Address"))
        if address and month >= best_month:
            best_month = month
            best = address
    return best


def _is_kline(source: object) -> bool:
    return " ".join(_text(source).casefold().split()) == _KLINE


def _prior_year(pay_year: str) -> str | None:
    if _YEAR.fullmatch(pay_year) is None:
        return None
    return f"{int(pay_year) - 1:04d}"


def _pay_year(value: str) -> str | None:
    if _YEAR.fullmatch(value) is None:
        return None
    year = int(value)
    if year < 1990 or year > 2100:
        return None
    return f"{year:04d}"


def _annualize(sum_cents: int, month_count: int) -> int:
    if month_count <= 0:
        return 0
    amount = (Decimal(sum_cents) * Decimal(12)) / Decimal(month_count)
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _per_month(annual_cents: int) -> int:
    amount = Decimal(annual_cents) / Decimal(12)
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _dollars(cents: int) -> float:
    return cents / 100


def _short_month(month: str) -> str:
    parsed = parse_month(month)
    if parsed is None:
        return month
    year, number = parsed
    return f"{calendar.month_abbr[number]} {year}"


def _cents(text: str) -> int | None:
    if _DECIMAL.fullmatch(text) is None:
        return None
    amount = Decimal(text) * 100
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _clean_address(value: object) -> str:
    text = _text(value)
    if text.lower() in {"", "<na>", "nan", "none"}:
        return ""
    return text
