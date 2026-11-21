"""Bills, property taxes, and entity fees.

Each tab is one environment variable of plain CSV text, the same pattern as
FINANCE_ASSETS_CSV:

- FINANCE_BILLS_CSV
- FINANCE_PROP_TAXES_CSV
- FINANCE_ENTITY_FEES_CSV

A missing or blank variable leaves that tab empty. A file that cannot be
read shows a warning and no rows. The warning does not include any cell
from the file. Real spreadsheets stay out of git.

Bills may include an optional Account Match column. When that text matches
a credit-card or loan account in the balance file, Current Balance on the
page is that account's latest balance. Checking and savings accounts are
left alone. A match that is missing keeps the amount typed in the CSV.

Bills may also include an optional Section column. The Bills tab groups
rows into Credit Cards, Lines of Credit, Car Payments, Property Taxes, and
Other. A missing column, a blank cell, or an unrecognized value is inferred
from the description. The column is not shown.
"""

from __future__ import annotations

import csv
import io
import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from kirbancials.activity import (
    account_balance_kind,
    latest_account_balances,
    load_balances,
    resolve_balances,
)
from kirbancials.data import FinanceDataError
from kirbancials.formatting import show_money_table

BILLS_ENV = "FINANCE_BILLS_CSV"
PROP_TAXES_ENV = "FINANCE_PROP_TAXES_CSV"
ENTITY_FEES_ENV = "FINANCE_ENTITY_FEES_CSV"

ACCOUNT_MATCH = "Account Match"
SECTION_COLUMN = "Section"
SECTION_CREDIT = "Credit Cards"
SECTION_LOC = "Lines of Credit"
SECTION_CAR = "Car Payments"
SECTION_TAX = "Property Taxes"
SECTION_OTHER = "Other"
BILL_SECTIONS = (
    SECTION_CREDIT,
    SECTION_LOC,
    SECTION_CAR,
    SECTION_TAX,
    SECTION_OTHER,
)
ALL_TYPES = "All"
NO_TYPE = "(No type)"
TOTAL_LABEL = "Total"
SUBTOTAL_LABEL = "Subtotal"
NONE_YET = "None yet"
DUE_SOON_DAYS = 7
TYPE_KEY = "bills_type_filter"

OVERDUE = "overdue"
DUE_SOON = "due_soon"
OVERDUE_STYLE = "background-color: rgba(198, 40, 40, 0.22)"
DUE_SOON_STYLE = "background-color: rgba(230, 160, 0, 0.32)"
# The only row colors on this page. Property taxes and entity fees are plain.
BILL_LEGEND: tuple[tuple[str, str], ...] = (
    (OVERDUE_STYLE, "Past due, still owed"),
    (DUE_SOON_STYLE, "Due within 7 days"),
)

NO_BILLS = "No bills are configured. Set FINANCE_BILLS_CSV to load this tab."
NO_BILLS_YET = "No bills yet."
NO_BILLS_FOR_TYPE = "No bills for this type."
NO_PROPERTY_TAXES = "No property taxes are configured. Set FINANCE_PROP_TAXES_CSV to load this tab."
NO_PROPERTY_TAXES_YET = "No property taxes yet."
NO_ENTITY_FEES = "No entity fees are configured. Set FINANCE_ENTITY_FEES_CSV to load this tab."
NO_ENTITY_FEES_YET = "No entity fees yet."

FLAG_NOTE = (
    "Red rows are past due and still have an amount due. "
    "Amber rows are due today or within the next 7 days."
)
ACCOUNT_MATCH_NOTE = (
    "Current Balance uses the latest credit-card or loan balance "
    "when Account Match names that account."
)
SAMPLE_BALANCE_NOTE = "Showing bundled sample balances. These are fake round numbers, not your accounts."
EXPORT_NOTE = (
    "Export to Excel writes every row on three sheets, with a totals row, "
    "dates, dollars, and percents. Download CSV uses the rows on this tab. "
    "On Bills, that follows the type filter."
)

MONEY_FORMAT = "$#,##0.00"
PERCENT_FORMAT = "0.00%"
DATE_FORMAT = "YYYY-MM-DD"
SHEET_BILLS = "Bills"
SHEET_TAXES = "Property Taxes"
SHEET_FEES = "Entity Fees"

_DECIMAL = re.compile(r"-?\d+(?:\.\d+)?")
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_US_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_ZERO_TIME = (" 00:00:00", "T00:00:00", "T00:00:00Z")

# Loan, line-of-credit, and property-tax phrases are checked before card
# names, so "BOA Auto Loan" stays a car payment.
_OBLIGATION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bproperty tax(?:es)?\b", re.IGNORECASE), SECTION_TAX),
    (re.compile(r"\bline of credit\b|\bloc\b", re.IGNORECASE), SECTION_LOC),
    (re.compile(r"\b(?:auto|jeep) loans?\b", re.IGNORECASE), SECTION_CAR),
)
_CARD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bamex\b", re.IGNORECASE),
    re.compile(r"\bciti(?:bank)?\b", re.IGNORECASE),
    re.compile(r"\bboa\b", re.IGNORECASE),
    re.compile(r"\bbank of america\b", re.IGNORECASE),
    re.compile(r"\bcapital one\b", re.IGNORECASE),
    re.compile(r"\bnordstrom\b", re.IGNORECASE),
    re.compile(r"\bneiman\b", re.IGNORECASE),
    re.compile(r"\bbonvoy\b", re.IGNORECASE),
)
_SECTION_KEYS = {name.casefold(): name for name in BILL_SECTIONS}

_HEADER_FONT = Font(bold=True)
_TOTAL_FONT = Font(bold=True)
_OVERDUE_FILL = PatternFill("solid", fgColor="F4C7C3")
_DUE_SOON_FILL = PatternFill("solid", fgColor="FCE8B2")
_TOTAL_FILL = PatternFill("solid", fgColor="EEEEEE")


@dataclass(frozen=True)
class _Messages:
    missing: str
    unreadable: str
    unparsed: str
    omitted: str


_BILL_MESSAGES = _Messages(
    missing="The bills CSV is missing a required column.",
    unreadable="The bills CSV has a row that could not be read.",
    unparsed="The bills CSV could not be parsed.",
    omitted="The bills CSV could not be read.",
)
_TAX_MESSAGES = _Messages(
    missing="The property tax CSV is missing a required column.",
    unreadable="The property tax CSV has a row that could not be read.",
    unparsed="The property tax CSV could not be parsed.",
    omitted="The property tax CSV could not be read.",
)
_FEE_MESSAGES = _Messages(
    missing="The entity fee CSV is missing a required column.",
    unreadable="The entity fee CSV has a row that could not be read.",
    unparsed="The entity fee CSV could not be parsed.",
    omitted="The entity fee CSV could not be read.",
)


@dataclass(frozen=True)
class TableLoad:
    """Parsed rows, a safe warning, and whether the env var was set."""

    records: tuple
    warning: str | None
    configured: bool


@dataclass(frozen=True)
class BillRecord:
    description: str
    bill_type: str
    current_balance: float | None
    statement_balance: float | None
    amount_paid: float | None
    amount_still_due: float | None
    interest_rate: float | None
    statement_ending: date | None
    due_date: date | None
    paid_date: date | None
    notes: str
    account_match: str = ""
    section: str = ""
    balance_from_account: bool = False


@dataclass(frozen=True)
class PropertyTaxRecord:
    property_name: str
    total_amount_due: float | None
    first_half: float | None
    amount_paid: float | None
    remaining_due: float | None
    notes: str


@dataclass(frozen=True)
class EntityFeeRecord:
    llc: str
    state: str
    tax_file_number: str
    registered_agent: str
    status: str
    reg_agent_fee: float | None
    reg_agent_due: date | None
    franchise_tax: float | None
    franchise_tax_due: date | None
    paid_from: str
    notes: str


# (column header, kind). Kind is text, money, percent, or date.
BILL_EXPORT: tuple[tuple[str, str], ...] = (
    ("Description", "text"),
    ("Type", "text"),
    ("Current Balance", "money"),
    ("Statement Balance", "money"),
    ("Amount Paid", "money"),
    ("Amount Still Due", "money"),
    ("Interest Rate", "percent"),
    ("Statement Ending Date", "date"),
    ("Due Date", "date"),
    ("Paid Date", "date"),
    ("Notes", "text"),
)
PROP_TAX_EXPORT: tuple[tuple[str, str], ...] = (
    ("Property", "text"),
    ("Total Amount Due", "money"),
    ("1st Half", "money"),
    ("Amount Paid", "money"),
    ("Remaining Due", "money"),
    ("Notes", "text"),
)
ENTITY_FEE_EXPORT: tuple[tuple[str, str], ...] = (
    ("LLC", "text"),
    ("State", "text"),
    ("Tax File Number", "text"),
    ("Registered Agent", "text"),
    ("Status", "text"),
    ("Reg Agent Fee", "money"),
    ("Reg Agent Due Date", "date"),
    ("Franchise Tax", "money"),
    ("Franchise Tax Due Date", "date"),
    ("Paid From", "text"),
    ("Notes", "text"),
)

BILL_COLUMNS = [name for name, _kind in BILL_EXPORT]
PROP_TAX_COLUMNS = [name for name, _kind in PROP_TAX_EXPORT]
ENTITY_FEE_COLUMNS = [name for name, _kind in ENTITY_FEE_EXPORT]
BILL_HEADER = ",".join(BILL_COLUMNS)
PROP_TAX_HEADER = ",".join(PROP_TAX_COLUMNS)
ENTITY_FEE_HEADER = ",".join(ENTITY_FEE_COLUMNS)
BILL_MONEY = [name for name, kind in BILL_EXPORT if kind == "money"]
PROP_TAX_MONEY = [name for name, kind in PROP_TAX_EXPORT if kind == "money"]
ENTITY_FEE_MONEY = [name for name, kind in ENTITY_FEE_EXPORT if kind == "money"]
# These values stay readable after the column header scrolls away.
BILL_EMPHASIS = ("Amount Still Due", "Due Date")


def resolve_bills(environ: Mapping[str, str] | None = None) -> TableLoad:
    """Return bills from FINANCE_BILLS_CSV. Blank means an empty tab."""
    return _resolve(BILLS_ENV, _parse_bills, _BILL_MESSAGES, environ)


def resolve_property_taxes(environ: Mapping[str, str] | None = None) -> TableLoad:
    """Return property taxes from FINANCE_PROP_TAXES_CSV."""
    return _resolve(PROP_TAXES_ENV, _parse_property_taxes, _TAX_MESSAGES, environ)


def resolve_entity_fees(environ: Mapping[str, str] | None = None) -> TableLoad:
    """Return entity fees from FINANCE_ENTITY_FEES_CSV."""
    return _resolve(ENTITY_FEES_ENV, _parse_entity_fees, _FEE_MESSAGES, environ)


def apply_account_balances(
    bills: Sequence[BillRecord],
    balances: pd.DataFrame | None,
) -> tuple[BillRecord, ...]:
    """Replace Current Balance when Account Match names a card or a loan.

    The match ignores capitals and surrounding spaces. Cash accounts are
    not used. A name that is not in the balance file keeps the CSV amount.
    """
    found: dict[str, float] = {}
    for item in latest_account_balances(balances):
        kind = account_balance_kind(item.account, item.account_type)
        if kind not in {"credit", "loan"}:
            continue
        key = item.account.casefold()
        if key not in found:
            found[key] = item.amount
    updated: list[BillRecord] = []
    for bill in bills:
        key = bill.account_match.strip().casefold()
        if key and key in found:
            updated.append(replace(bill, current_balance=found[key], balance_from_account=True))
        else:
            updated.append(bill)
    return tuple(updated)


def sort_bills(bills: Sequence[BillRecord]) -> tuple[BillRecord, ...]:
    """Due date ascending. A blank due date sorts last, then description."""
    return tuple(
        sorted(
            bills,
            key=lambda bill: (bill.due_date is None, bill.due_date or date.min, bill.description.casefold()),
        )
    )


def canonical_section(value: str) -> str | None:
    """A section name, or None when the text is blank or not one of the five."""
    key = " ".join(value.split()).casefold()
    if not key:
        return None
    return _SECTION_KEYS.get(key)


def infer_section(description: str) -> str:
    """Section from the description when Section is missing or not recognized.

    Property tax, line of credit, and auto or Jeep loan phrases win over a
    card name in the same text. Anything else is Other.
    """
    for pattern, section in _OBLIGATION_PATTERNS:
        if pattern.search(description):
            return section
    for pattern in _CARD_PATTERNS:
        if pattern.search(description):
            return SECTION_CREDIT
    return SECTION_OTHER


def bill_section(bill: BillRecord) -> str:
    """Section column when it names a section. Otherwise infer from Description."""
    named = canonical_section(bill.section)
    if named is not None:
        return named
    return infer_section(bill.description)


def section_groups(bills: Sequence[BillRecord]) -> tuple[tuple[str, tuple[BillRecord, ...]], ...]:
    """The five sections, in display order. Each group is sorted by due date.

    A section with no rows is an empty tuple. The page still shows that heading.
    """
    buckets: dict[str, list[BillRecord]] = {name: [] for name in BILL_SECTIONS}
    for bill in bills:
        buckets[bill_section(bill)].append(bill)
    return tuple((name, sort_bills(rows)) for name, rows in buckets.items())


def type_options(bills: Sequence[BillRecord]) -> list[str]:
    """All, then each non-blank type, then a choice for rows with no type."""
    named = sorted({bill.bill_type for bill in bills if bill.bill_type}, key=str.casefold)
    options = [ALL_TYPES, *named]
    if any(bill.bill_type == "" for bill in bills):
        options.append(NO_TYPE)
    return options


def filter_bills(bills: Sequence[BillRecord], selected: str) -> tuple[BillRecord, ...]:
    """Keep file order. All returns every row."""
    if selected == ALL_TYPES:
        return tuple(bills)
    if selected == NO_TYPE:
        return tuple(bill for bill in bills if bill.bill_type == "")
    return tuple(bill for bill in bills if bill.bill_type == selected)


def bill_flag(bill: BillRecord, today: date) -> str:
    """Past due with an amount still due, or due within the next 7 days.

    Today counts as due soon, not past due. A due date 7 days out counts.
    A blank due date is neither.
    """
    if bill.due_date is None:
        return ""
    still_due = bill.amount_still_due is not None and bill.amount_still_due > 0
    if bill.due_date < today and still_due:
        return OVERDUE
    if today <= bill.due_date <= today + timedelta(days=DUE_SOON_DAYS):
        return DUE_SOON
    return ""


def bill_row_styles(bills: Sequence[BillRecord], today: date) -> list[str]:
    """One CSS style per bill. An empty string leaves the row uncolored."""
    styles: list[str] = []
    for bill in bills:
        flag = bill_flag(bill, today)
        if flag == OVERDUE:
            styles.append(OVERDUE_STYLE)
        elif flag == DUE_SOON:
            styles.append(DUE_SOON_STYLE)
        else:
            styles.append("")
    return styles


def bill_records(bills: Sequence[BillRecord]) -> list[dict[str, object]]:
    """Display columns only. Account Match and Section stay off the table."""
    return [
        {
            "Description": bill.description,
            "Type": bill.bill_type,
            "Current Balance": bill.current_balance,
            "Statement Balance": bill.statement_balance,
            "Amount Paid": bill.amount_paid,
            "Amount Still Due": bill.amount_still_due,
            "Interest Rate": bill.interest_rate,
            "Statement Ending Date": bill.statement_ending,
            "Due Date": bill.due_date,
            "Paid Date": bill.paid_date,
            "Notes": bill.notes,
        }
        for bill in bills
    ]


def property_tax_records(rows: Sequence[PropertyTaxRecord]) -> list[dict[str, object]]:
    return [
        {
            "Property": row.property_name,
            "Total Amount Due": row.total_amount_due,
            "1st Half": row.first_half,
            "Amount Paid": row.amount_paid,
            "Remaining Due": row.remaining_due,
            "Notes": row.notes,
        }
        for row in rows
    ]


def entity_fee_records(rows: Sequence[EntityFeeRecord]) -> list[dict[str, object]]:
    return [
        {
            "LLC": row.llc,
            "State": row.state,
            "Tax File Number": row.tax_file_number,
            "Registered Agent": row.registered_agent,
            "Status": row.status,
            "Reg Agent Fee": row.reg_agent_fee,
            "Reg Agent Due Date": row.reg_agent_due,
            "Franchise Tax": row.franchise_tax,
            "Franchise Tax Due Date": row.franchise_tax_due,
            "Paid From": row.paid_from,
            "Notes": row.notes,
        }
        for row in rows
    ]


def sum_amounts(values: Sequence[object]) -> float:
    """Sum known amounts. A blank cell counts as zero."""
    total = 0.0
    for value in values:
        if value is None:
            continue
        if isinstance(value, float) and math.isnan(value):
            continue
        total += float(value)
    return total


def totals_record(
    records: Sequence[Mapping[str, object]],
    columns: Sequence[tuple[str, str]],
    label_column: str,
    label: str = TOTAL_LABEL,
) -> dict[str, object]:
    """One totals row. Money columns are summed. Other cells stay blank."""
    total: dict[str, object] = {}
    for name, kind in columns:
        if name == label_column:
            total[name] = label
        elif kind == "money":
            total[name] = sum_amounts([record.get(name) for record in records])
        elif kind == "text":
            total[name] = ""
        else:
            total[name] = None
    return total


def display_frame(
    records: Sequence[Mapping[str, object]],
    columns: Sequence[tuple[str, str]],
) -> pd.DataFrame:
    """A frame whose money, percent, and date columns keep real values."""
    names = [name for name, _kind in columns]
    if not records:
        frame = pd.DataFrame(columns=names)
    else:
        frame = pd.DataFrame(list(records))
    for name, kind in columns:
        if name not in frame.columns:
            frame[name] = pd.Series([pd.NA] * len(frame), dtype="object")
        if kind in {"money", "percent"}:
            frame[name] = pd.to_numeric(frame[name], errors="coerce")
        elif kind == "date":
            frame[name] = pd.to_datetime(frame[name], errors="coerce")
        else:
            frame[name] = frame[name].map(_text_cell)
    return frame.loc[:, names]


def table_csv(
    records: Sequence[Mapping[str, object]],
    columns: Sequence[tuple[str, str]],
) -> str:
    """CSV of the rows plus a totals row when there is at least one row.

    Dates are YYYY-MM-DD. Money and interest rates are plain numbers.
    Interest rate stays in percent points, such as 5.90 for 5.90%.
    """
    headers = [name for name, _kind in columns]
    body = list(records)
    if body:
        body.append(totals_record(body, columns, headers[0]))
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(headers)
    for record in body:
        writer.writerow([_csv_cell(record.get(name), kind) for name, kind in columns])
    return buffer.getvalue()


def workbook_bytes(
    bills: Sequence[BillRecord],
    taxes: Sequence[PropertyTaxRecord],
    fees: Sequence[EntityFeeRecord],
    today: date,
) -> bytes:
    """One workbook. Three sheets, totals rows, and Excel number formats.

    Bills are every row, not the type filter. Past-due and due-soon rows
    use the same rules as the page.
    """
    book = Workbook()
    bill_rows = bill_records(bills)
    flags = [bill_flag(bill, today) for bill in bills]
    _write_sheet(book.active, SHEET_BILLS, BILL_EXPORT, bill_rows, flags)
    _write_sheet(book.create_sheet(SHEET_TAXES), SHEET_TAXES, PROP_TAX_EXPORT, property_tax_records(taxes), None)
    _write_sheet(book.create_sheet(SHEET_FEES), SHEET_FEES, ENTITY_FEE_EXPORT, entity_fee_records(fees), None)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def render_bills() -> None:
    """Draw the three tabs. Login has already run in the entrypoint."""
    today = date.today()
    bills_load = resolve_bills()
    tax_load = resolve_property_taxes()
    fee_load = resolve_entity_fees()
    balances, balance_sample = _balance_rows()
    bills = sort_bills(apply_account_balances(bills_load.records, balances))
    taxes = tax_load.records
    fees = fee_load.records

    st.caption(f"As of {today.isoformat()}")
    st.download_button(
        "Export to Excel",
        data=workbook_bytes(bills, taxes, fees, today),
        file_name="kirbancials-bills.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="bills_export_xlsx",
    )
    st.caption(EXPORT_NOTE)

    bills_tab, tax_tab, fee_tab = st.tabs([SHEET_BILLS, SHEET_TAXES, SHEET_FEES])
    with bills_tab:
        _render_bills_tab(bills_load, bills, today, balance_sample)
    with tax_tab:
        _render_table_tab(
            tax_load,
            property_tax_records(taxes),
            PROP_TAX_EXPORT,
            PROP_TAX_MONEY,
            NO_PROPERTY_TAXES,
            NO_PROPERTY_TAXES_YET,
            "property-taxes.csv",
            "property_taxes_csv",
        )
    with fee_tab:
        _render_table_tab(
            fee_load,
            entity_fee_records(fees),
            ENTITY_FEE_EXPORT,
            ENTITY_FEE_MONEY,
            NO_ENTITY_FEES,
            NO_ENTITY_FEES_YET,
            "entity-fees.csv",
            "entity_fees_csv",
        )


def _render_bills_tab(
    load: TableLoad,
    bills: Sequence[BillRecord],
    today: date,
    balance_sample: bool,
) -> None:
    if load.warning:
        st.warning(load.warning)
    if not load.configured:
        st.info(NO_BILLS)
        return
    if not bills:
        if load.warning is None:
            st.info(NO_BILLS_YET)
        _download_csv("bills.csv", table_csv([], BILL_EXPORT), "bills_csv")
        return
    options = type_options(bills)
    if st.session_state.get(TYPE_KEY) not in options:
        st.session_state[TYPE_KEY] = ALL_TYPES
    selected = st.selectbox("Type", options, key=TYPE_KEY)
    shown = filter_bills(bills, selected)
    if balance_sample and any(bill.balance_from_account for bill in shown):
        st.warning(SAMPLE_BALANCE_NOTE)
    records = bill_records(shown)
    if not shown:
        st.info(NO_BILLS_FOR_TYPE)
    else:
        _render_bill_sections(shown, today)
        st.caption(FLAG_NOTE)
        if any(bill.balance_from_account for bill in shown):
            st.caption(ACCOUNT_MATCH_NOTE)
    _download_csv("bills.csv", table_csv(records, BILL_EXPORT), "bills_csv")


def _render_bill_sections(bills: Sequence[BillRecord], today: date) -> None:
    """One compact heading per section, then the grand total."""
    for name, rows in section_groups(bills):
        st.subheader(name)
        if not rows:
            st.caption(NONE_YET)
            continue
        section_records = bill_records(rows)
        show_money_table(
            display_frame(section_records, BILL_EXPORT),
            BILL_MONEY,
            ["Interest Rate"],
            row_styles=bill_row_styles(rows, today),
            bold_columns=BILL_EMPHASIS,
        )
        _show_total(section_records, "Subtotal", SUBTOTAL_LABEL)
    _show_total(bill_records(bills), "Totals", TOTAL_LABEL)


def _show_total(records: Sequence[Mapping[str, object]], caption: str, label: str) -> None:
    st.caption(caption)
    show_money_table(
        display_frame([totals_record(records, BILL_EXPORT, "Description", label)], BILL_EXPORT),
        BILL_MONEY,
        ["Interest Rate"],
        bold_columns=BILL_EMPHASIS,
    )


def _render_table_tab(
    load: TableLoad,
    records: Sequence[Mapping[str, object]],
    columns: Sequence[tuple[str, str]],
    money: Sequence[str],
    empty_message: str,
    none_yet: str,
    file_name: str,
    key: str,
) -> None:
    if load.warning:
        st.warning(load.warning)
    if not load.configured:
        st.info(empty_message)
        return
    if not records:
        if load.warning is None:
            st.info(none_yet)
    else:
        show_money_table(display_frame(records, columns), money)
        st.caption("Totals")
        label = columns[0][0]
        show_money_table(display_frame([totals_record(records, columns, label)], columns), money)
    _download_csv(file_name, table_csv(records, columns), key)


def _download_csv(file_name: str, text: str, key: str) -> None:
    st.download_button(
        "Download CSV",
        data=text,
        file_name=file_name,
        mime="text/csv",
        key=key,
    )


def _balance_rows() -> tuple[pd.DataFrame | None, bool]:
    """Latest balances, or nothing when that file cannot be read.

    A failure here leaves the typed Current Balance in place. The balance
    sheet still reports its own error.
    """
    try:
        loaded = load_balances(resolve_balances())
    except Exception:
        return None, False
    return loaded.rows, loaded.is_sample


def _resolve(env_name: str, parse, messages: _Messages, environ: Mapping[str, str] | None) -> TableLoad:
    env = os.environ if environ is None else environ
    raw = env.get(env_name)
    if raw is None or str(raw).lstrip("\ufeff").strip() == "":
        return TableLoad((), None, False)
    try:
        return TableLoad(tuple(parse(str(raw))), None, True)
    except FinanceDataError as exc:
        return TableLoad((), str(exc), True)
    except Exception:
        return TableLoad((), messages.omitted, True)


def _parse_bills(text: str) -> list[BillRecord]:
    rows = _read_rows(text, BILL_COLUMNS, _BILL_MESSAGES)
    records: list[BillRecord] = []
    for raw in rows:
        if _blank_row(raw, [*BILL_COLUMNS, ACCOUNT_MATCH, SECTION_COLUMN]):
            continue
        parsed = _parse_bill(raw)
        if parsed is None:
            raise FinanceDataError(_BILL_MESSAGES.unreadable)
        records.append(parsed)
    return records


def _parse_property_taxes(text: str) -> list[PropertyTaxRecord]:
    rows = _read_rows(text, PROP_TAX_COLUMNS, _TAX_MESSAGES)
    records: list[PropertyTaxRecord] = []
    for raw in rows:
        if _blank_row(raw, PROP_TAX_COLUMNS):
            continue
        parsed = _parse_property_tax(raw)
        if parsed is None:
            raise FinanceDataError(_TAX_MESSAGES.unreadable)
        records.append(parsed)
    return records


def _parse_entity_fees(text: str) -> list[EntityFeeRecord]:
    rows = _read_rows(text, ENTITY_FEE_COLUMNS, _FEE_MESSAGES)
    records: list[EntityFeeRecord] = []
    for raw in rows:
        if _blank_row(raw, ENTITY_FEE_COLUMNS):
            continue
        parsed = _parse_entity_fee(raw)
        if parsed is None:
            raise FinanceDataError(_FEE_MESSAGES.unreadable)
        records.append(parsed)
    return records


def _parse_bill(raw: Mapping[str, object]) -> BillRecord | None:
    description = _required_text(raw.get("Description"))
    if description is None:
        return None
    current, current_ok = _optional_money(raw.get("Current Balance"))
    statement, statement_ok = _optional_money(raw.get("Statement Balance"))
    paid, paid_ok = _optional_money(raw.get("Amount Paid"))
    still_due, still_ok = _optional_money(raw.get("Amount Still Due"))
    rate, rate_ok = _optional_rate(raw.get("Interest Rate"))
    ending, ending_ok = _optional_date(raw.get("Statement Ending Date"))
    due, due_ok = _optional_date(raw.get("Due Date"))
    paid_on, paid_on_ok = _optional_date(raw.get("Paid Date"))
    if not all((current_ok, statement_ok, paid_ok, still_ok, rate_ok, ending_ok, due_ok, paid_on_ok)):
        return None
    return BillRecord(
        description=description,
        bill_type=_optional_text(raw.get("Type")),
        current_balance=current,
        statement_balance=statement,
        amount_paid=paid,
        amount_still_due=still_due,
        interest_rate=rate,
        statement_ending=ending,
        due_date=due,
        paid_date=paid_on,
        notes=_optional_text(raw.get("Notes")),
        account_match=_optional_text(raw.get(ACCOUNT_MATCH)),
        section=_optional_text(raw.get(SECTION_COLUMN)),
    )


def _parse_property_tax(raw: Mapping[str, object]) -> PropertyTaxRecord | None:
    name = _required_text(raw.get("Property"))
    if name is None:
        return None
    total, total_ok = _optional_money(raw.get("Total Amount Due"))
    first, first_ok = _optional_money(raw.get("1st Half"))
    paid, paid_ok = _optional_money(raw.get("Amount Paid"))
    remaining, remaining_ok = _optional_money(raw.get("Remaining Due"))
    if not all((total_ok, first_ok, paid_ok, remaining_ok)):
        return None
    return PropertyTaxRecord(
        property_name=name,
        total_amount_due=total,
        first_half=first,
        amount_paid=paid,
        remaining_due=remaining,
        notes=_optional_text(raw.get("Notes")),
    )


def _parse_entity_fee(raw: Mapping[str, object]) -> EntityFeeRecord | None:
    llc = _required_text(raw.get("LLC"))
    if llc is None:
        return None
    fee, fee_ok = _optional_money(raw.get("Reg Agent Fee"))
    franchise, franchise_ok = _optional_money(raw.get("Franchise Tax"))
    agent_due, agent_due_ok = _optional_date(raw.get("Reg Agent Due Date"))
    tax_due, tax_due_ok = _optional_date(raw.get("Franchise Tax Due Date"))
    if not all((fee_ok, franchise_ok, agent_due_ok, tax_due_ok)):
        return None
    return EntityFeeRecord(
        llc=llc,
        state=_optional_text(raw.get("State")),
        tax_file_number=_optional_text(raw.get("Tax File Number")),
        registered_agent=_optional_text(raw.get("Registered Agent")),
        status=_optional_text(raw.get("Status")),
        reg_agent_fee=fee,
        reg_agent_due=agent_due,
        franchise_tax=franchise,
        franchise_tax_due=tax_due,
        paid_from=_optional_text(raw.get("Paid From")),
        notes=_optional_text(raw.get("Notes")),
    )


def _read_rows(text: str, columns: Sequence[str], messages: _Messages) -> list[dict[str, object]]:
    try:
        frame = pd.read_csv(io.StringIO(text.lstrip("\ufeff")), dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError as exc:
        raise FinanceDataError(messages.unparsed) from exc
    except pd.errors.ParserError as exc:
        raise FinanceDataError(messages.unparsed) from exc
    frame.columns = [str(column).strip() for column in frame.columns]
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise FinanceDataError(messages.missing)
    return frame.to_dict("records")


def _blank_row(raw: Mapping[str, object], columns: Sequence[str]) -> bool:
    return all(_optional_text(raw.get(column)) == "" for column in columns)


def _required_text(value: object) -> str | None:
    text = _optional_text(value)
    return text or None


def _optional_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        return str(value).strip()
    return str(value).strip()


def _optional_date(value: object) -> tuple[date | None, bool]:
    text = _optional_text(value)
    if text == "":
        return None, True
    for suffix in _ZERO_TIME:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    iso = _ISO_DATE.fullmatch(text)
    if iso:
        return _make_date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    us = _US_DATE.fullmatch(text)
    if us:
        return _make_date(int(us.group(3)), int(us.group(1)), int(us.group(2)))
    return None, False


def _make_date(year: int, month: int, day: int) -> tuple[date | None, bool]:
    try:
        return date(year, month, day), True
    except ValueError:
        return None, False


def _optional_money(value: object) -> tuple[float | None, bool]:
    text = _optional_text(value).replace(",", "").replace("$", "").replace(" ", "")
    if text == "":
        return None, True
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    if _DECIMAL.fullmatch(text) is None:
        return None, False
    number = float(text)
    if negative:
        number = -abs(number)
    if not math.isfinite(number):
        return None, False
    return number, True


def _optional_rate(value: object) -> tuple[float | None, bool]:
    """Percent points. `5.9` and `5.9%` are both 5.9, not a fraction."""
    text = _optional_text(value).replace(" ", "")
    if text.endswith("%"):
        text = text[:-1]
    if text == "":
        return None, True
    if _DECIMAL.fullmatch(text) is None:
        return None, False
    number = float(text)
    if not math.isfinite(number):
        return None, False
    return number, True


def _text_cell(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        return str(value)
    return str(value)


def _csv_cell(value: object, kind: str) -> str:
    if kind == "text":
        return "" if value is None else str(value)
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    if kind == "date":
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return ""
    if kind in {"money", "percent"}:
        return f"{float(value):.2f}"
    return str(value)


def _write_sheet(
    sheet: Worksheet,
    title: str,
    columns: Sequence[tuple[str, str]],
    records: Sequence[Mapping[str, object]],
    flags: Sequence[str] | None,
) -> None:
    sheet.title = title
    header_font = _HEADER_FONT
    for index, (name, _kind) in enumerate(columns, start=1):
        cell = sheet.cell(1, index, name)
        cell.font = header_font
    for row_index, record in enumerate(records, start=2):
        flag = "" if flags is None or row_index - 2 >= len(flags) else flags[row_index - 2]
        fill = _OVERDUE_FILL if flag == OVERDUE else _DUE_SOON_FILL if flag == DUE_SOON else None
        for column_index, (name, kind) in enumerate(columns, start=1):
            cell = sheet.cell(row_index, column_index)
            _assign(cell, record.get(name), kind)
            if fill is not None:
                cell.fill = fill
    if records:
        total = totals_record(records, columns, columns[0][0])
        total_row = len(records) + 2
        for column_index, (name, kind) in enumerate(columns, start=1):
            cell = sheet.cell(total_row, column_index)
            _assign(cell, total.get(name), kind)
            cell.font = _TOTAL_FONT
            cell.fill = _TOTAL_FILL
        last_column = get_column_letter(len(columns))
        sheet.auto_filter.ref = f"A1:{last_column}{len(records) + 1}"
    sheet.freeze_panes = "A2"
    for index, (name, _kind) in enumerate(columns, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = max(len(name) + 2, 14)


def _assign(cell, value: object, kind: str) -> None:
    if kind == "text":
        text = "" if value is None else str(value)
        cell.value = text
        # openpyxl stores a leading "=" as a formula. Notes stay text.
        if cell.data_type == "f":
            cell.value = text
            cell.data_type = "s"
        return
    if value is None or (isinstance(value, float) and math.isnan(value)):
        cell.value = None
        return
    if kind == "money":
        cell.value = float(value)
        cell.number_format = MONEY_FORMAT
        return
    if kind == "percent":
        cell.value = float(value) / 100.0
        cell.number_format = PERCENT_FORMAT
        return
    if kind == "date":
        if isinstance(value, datetime):
            cell.value = value.date()
        elif isinstance(value, date):
            cell.value = value
        else:
            cell.value = None
            return
        cell.number_format = DATE_FORMAT
        return
    cell.value = str(value)
