"""VWH portfolio profit and loss.

FINANCE_VWH_PL_CSV is plain CSV text, not gzipped, same as the income,
asset, and loan-schedule variables. One row is one month, house, account,
and source. If the variable is missing or blank, there are no rows.

Net income is income minus expense. Rows whose Section is "Below the line"
(account BANK-TAX-PEN, Tax Penalties) are not in that net. A missing account
is zero. A house with no income still appears when it has any row in range.
A negative income amount is a refund and reduces income.

Account lines are grouped by account number. The label includes the number,
such as "5000 Property Management Expenses", because different accounts can
share a name.

Trending sits below the portfolio table. Property tax bills come from
FINANCE_VWH_TAX_CSV. The run rate math lives in kirbancials/vwh_trend.py.
"""

from __future__ import annotations

import calendar
import io
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

import altair as alt
import pandas as pd
import streamlit as st

from kirbancials.data import FinanceDataError
from kirbancials.formatting import format_money, format_signed_money, show_money_table

VWH_ENV = "FINANCE_VWH_PL_CSV"
VWH_PAGE = "views/vwh_pl.py"

NO_VWH_DATA = "No VWH P&L data yet"
MISSING_COLUMN = "The VWH P&L CSV is missing a required column."
UNPARSED = "The VWH P&L CSV could not be parsed."
OMITTED = "The VWH P&L CSV could not be read."

SECTION_INCOME = "Income"
SECTION_EXPENSE = "Expense"
SECTION_BELOW = "Below the line"

VIEW_MONTH = "month"
VIEW_YTD = "ytd"
VIEW_T12 = "t12"
VIEW_LABELS = {
    VIEW_MONTH: "This month",
    VIEW_YTD: "Year to date",
    VIEW_T12: "Trailing 12 months",
}

VWH_COLUMNS = [
    "Month",
    "Property ID",
    "Address",
    "Section",
    "Account Number",
    "Account Name",
    "Amount",
    "Source",
]

_SECTIONS = {
    "income": SECTION_INCOME,
    "expense": SECTION_EXPENSE,
    "below the line": SECTION_BELOW,
}

# Known accounts stay in statement order. Anything else follows them.
_ACCOUNT_RANK = {
    "4000": 10,
    "4300": 20,
    "5000": 25,
    "5200": 30,
    "5300": 35,
    "6000": 38,
    "6400": 40,
    "BANK-INS": 50,
    "BANK-TAX": 60,
    "BANK-TAX-PY": 70,
    "BANK-TAX-PEN": 80,
}

_MONTH = re.compile(r"\d{4}-\d{2}")
_DECIMAL = re.compile(r"[+-]?\d+(?:\.\d+)?")
_TEXT_COLUMNS = [column for column in VWH_COLUMNS if column != "Amount"]


@dataclass(frozen=True)
class AccountLine:
    account_number: str
    account_name: str
    source: str
    section: str
    amount: float


@dataclass(frozen=True)
class PropertyStatement:
    property_id: str
    address: str
    income: float
    expense: float
    net: float
    income_lines: tuple[AccountLine, ...]
    expense_lines: tuple[AccountLine, ...]
    memo_lines: tuple[AccountLine, ...]


@dataclass(frozen=True)
class MonthWindow:
    """Months to add up. `months` are the ones that exist in the file."""

    view: str
    selected: str
    start: str
    end: str
    months: tuple[str, ...]


@dataclass(frozen=True)
class PortfolioStatement:
    window: MonthWindow
    income: float
    expense: float
    net: float
    expense_lines: tuple[AccountLine, ...]
    memo_lines: tuple[AccountLine, ...]
    properties: tuple[PropertyStatement, ...]


@dataclass(frozen=True)
class VwhLoad:
    """Parsed rows, a safe warning, and whether the env var was set."""

    rows: pd.DataFrame
    warning: str | None
    configured: bool


def skipped_message(count: int) -> str:
    """Warning for rows that were left out. It does not quote the file."""
    noun = "row" if count == 1 else "rows"
    return f"{count} {noun} in the VWH P&L CSV were skipped."


def empty_rows() -> pd.DataFrame:
    frame = pd.DataFrame({column: pd.Series(dtype="string") for column in _TEXT_COLUMNS})
    frame["Cents"] = pd.Series(dtype="int64")
    return frame


def resolve_vwh(environ: Mapping[str, str] | None = None) -> VwhLoad:
    """Return rows from FINANCE_VWH_PL_CSV.

    A missing or blank variable is an empty load with no warning. A CSV that
    cannot be used is an empty load with a warning. The warning does not
    include any cell from the file.
    """
    env = os.environ if environ is None else environ
    raw = env.get(VWH_ENV)
    if raw is None or str(raw).lstrip("\ufeff").strip() == "":
        return VwhLoad(empty_rows(), None, False)
    try:
        rows, warning = parse_vwh(str(raw))
        return VwhLoad(rows, warning, True)
    except FinanceDataError as exc:
        return VwhLoad(empty_rows(), str(exc), True)
    except Exception:
        return VwhLoad(empty_rows(), OMITTED, True)


def parse_vwh(text: str) -> tuple[pd.DataFrame, str | None]:
    """Parse VWH CSV text. Raises FinanceDataError when the file is unusable."""
    try:
        frame = pd.read_csv(
            io.StringIO(text.lstrip("\ufeff")),
            dtype=str,
            keep_default_na=False,
        )
    except pd.errors.EmptyDataError as exc:
        raise FinanceDataError(UNPARSED) from exc
    except pd.errors.ParserError as exc:
        raise FinanceDataError(UNPARSED) from exc
    frame.columns = [str(column).strip() for column in frame.columns]
    missing = [column for column in VWH_COLUMNS if column not in frame.columns]
    if missing:
        raise FinanceDataError(MISSING_COLUMN)
    records: list[dict[str, object]] = []
    skipped = 0
    for raw in frame.loc[:, VWH_COLUMNS].to_dict("records"):
        if _blank_row(raw):
            continue
        parsed = _parse_row(raw)
        if parsed is None:
            skipped += 1
            continue
        records.append(parsed)
    if not records:
        return empty_rows(), skipped_message(skipped) if skipped else None
    out = pd.DataFrame.from_records(records)
    for column in _TEXT_COLUMNS:
        out[column] = out[column].astype("string")
    out["Cents"] = out["Cents"].astype("int64")
    warning = skipped_message(skipped) if skipped else None
    return out, warning


def available_months(rows: pd.DataFrame) -> list[str]:
    """Months in the file, newest first."""
    if rows.empty or "Month" not in rows.columns:
        return []
    found = {str(month) for month in rows["Month"].tolist() if parse_month(str(month))}
    return sorted(found, reverse=True)


def parse_month(value: str) -> tuple[int, int] | None:
    """Return (year, month) for YYYY-MM, or None when it is not a real month."""
    if _MONTH.fullmatch(value) is None:
        return None
    year = int(value[:4])
    month = int(value[5:])
    if month < 1 or month > 12:
        return None
    return year, month


def format_month(month: str) -> str:
    """October 2026 for 2026-10. Anything else is returned unchanged."""
    parsed = parse_month(month)
    if parsed is None:
        return month
    year, number = parsed
    return f"{calendar.month_name[number]} {year}"


def month_window(available: Sequence[str], selected: str, view: str) -> MonthWindow:
    """Months to total. An unknown view or month falls back to the latest month."""
    months = sorted({month for month in available if parse_month(month)})
    chosen_view = view if view in VIEW_LABELS else VIEW_MONTH
    if not months:
        return MonthWindow(chosen_view, "", "", "", ())
    if selected not in months:
        selected = months[-1]
    year, number = parse_month(selected) or (0, 1)
    if chosen_view == VIEW_YTD:
        start = f"{year:04d}-01"
    elif chosen_view == VIEW_T12:
        start = _add_months(year, number, -11)
    else:
        start = selected
    included = tuple(month for month in months if start <= month <= selected)
    return MonthWindow(chosen_view, selected, start, selected, included)


def describe_window(window: MonthWindow) -> str:
    """The range under the month picker, in plain language."""
    if not window.end:
        return "No rows in this range."
    if window.view == VIEW_MONTH:
        return format_month(window.end)
    if not window.months:
        span = f"{format_month(window.start)} through {format_month(window.end)}"
        return f"{span}. Months with no rows count as zero."
    first = format_month(window.months[0])
    last = format_month(window.months[-1])
    span = first if len(window.months) == 1 else f"{first} through {last}"
    if len(window.months) < _span_count(window.start, window.end):
        return f"{span}. Months with no rows count as zero."
    return span


def prefer_choice(
    query_value: str | None,
    widget_value: str | None,
    last_seen_query: str | None,
    allowed: set[str],
    default: str,
) -> tuple[str, str]:
    """Pick the select-box value and the query value to remember.

    A changed URL wins over the select box, so a house link can carry the
    month. Otherwise the select box wins.
    """
    requested = query_value if query_value in allowed else default
    if last_seen_query != requested:
        return requested, requested
    if widget_value in allowed:
        return widget_value, requested
    return requested, requested


def summarize(rows: pd.DataFrame, window: MonthWindow) -> PortfolioStatement:
    """Portfolio totals and one row per house for the months in `window`."""
    frame = _rows_in_window(rows, window)
    properties, income_cents, expense_cents = _properties(frame)
    expense_lines, _expense_cents = _collapse(frame, SECTION_EXPENSE)
    memo_lines, _memo_cents = _collapse(frame, SECTION_BELOW)
    return PortfolioStatement(
        window=window,
        income=_dollars(income_cents),
        expense=_dollars(expense_cents),
        net=_dollars(income_cents - expense_cents),
        expense_lines=expense_lines,
        memo_lines=memo_lines,
        properties=properties,
    )


def lookup_property(
    statement: PortfolioStatement,
    rows: pd.DataFrame,
    property_id: str,
) -> PropertyStatement | None:
    """One house in the current range.

    A house that exists in the file but has no rows in range is returned
    with zeros. An unknown id returns None.
    """
    for prop in statement.properties:
        if prop.property_id == property_id:
            return prop
    if rows.empty or "Property ID" not in rows.columns:
        return None
    owned = rows.loc[rows["Property ID"] == property_id]
    if owned.empty:
        return None
    return PropertyStatement(
        property_id=property_id,
        address=_address(owned, property_id),
        income=0.0,
        expense=0.0,
        net=0.0,
        income_lines=(),
        expense_lines=(),
        memo_lines=(),
    )


def address_labels(properties: Sequence[PropertyStatement]) -> dict[str, str]:
    """Address shown in the table. A shared address keeps the house id."""
    return _labels_for([(prop.property_id, prop.address) for prop in properties])


def account_label(number: str, name: str) -> str:
    """'5000 Property Management Expenses'. The number tells same names apart."""
    number = number.strip()
    name = name.strip()
    if not number:
        return name or "Account"
    if not name:
        return number
    folded = name.casefold()
    prefix = number.casefold()
    if folded == prefix or folded.startswith(prefix + " "):
        return name
    return f"{number} {name}"


def account_table(lines: Sequence[AccountLine]) -> pd.DataFrame:
    """Account, source, and amount. The account column includes the number."""
    return pd.DataFrame(
        {
            "Account": [account_label(line.account_number, line.account_name) for line in lines],
            "Source": [line.source for line in lines],
            "Amount": [line.amount for line in lines],
        }
    )


def memo_line_text(line: AccountLine) -> str:
    """One below-the-line line, with the account number in the label."""
    source = f" · {line.source}" if line.source else ""
    label = account_label(line.account_number, line.account_name)
    return f"**{label}**{source} · {format_money(line.amount)}"


def memo_lines_to_show(lines: Sequence[AccountLine]) -> tuple[AccountLine, ...]:
    """Below-the-line lines with a non-zero amount."""
    return tuple(line for line in lines if line.amount != 0)


def render_vwh_pl() -> None:
    """Draw the VWH page. Login has already run in the entrypoint."""
    st.caption("Indianapolis single-family rentals managed by Kline Property Management.")
    loaded = resolve_vwh()
    tax = _trend().resolve_vwh_tax()
    if loaded.warning:
        st.warning(loaded.warning)
    if tax.warning:
        st.warning(tax.warning)
    months = available_months(loaded.rows)
    if not months:
        if loaded.warning is None:
            st.info(NO_VWH_DATA)
        return

    month, view = _controls(months)
    window = month_window(months, month, view)
    statement = summarize(loaded.rows, window)
    st.caption(describe_window(window))

    property_id = _query_value("vwh_property")
    if property_id:
        _render_property(statement, loaded.rows, property_id, month, view, tax)
        return
    _render_portfolio(statement, month, view)
    _render_trending(loaded.rows, tax)


def _controls(months: list[str]) -> tuple[str, str]:
    left, right = st.columns(2)
    with left:
        month = _select("Month", months, "vwh_month", months[0], format_month)
    with right:
        view = _select(
            "Show",
            list(VIEW_LABELS),
            "vwh_view",
            VIEW_MONTH,
            lambda key: VIEW_LABELS[key],
        )
    return month, view


def _select(
    label: str,
    options: list[str],
    key: str,
    default: str,
    format_func,
) -> str:
    chosen, seen = prefer_choice(
        _query_value(key),
        st.session_state.get(key) if key in st.session_state else None,
        st.session_state.get(f"{key}_seen"),
        allowed=set(options),
        default=default,
    )
    st.session_state[key] = chosen
    st.session_state[f"{key}_seen"] = seen if seen in set(options) else chosen
    selected = st.selectbox(label, options, format_func=format_func, key=key)
    if _query_value(key) != selected:
        st.query_params[key] = selected
        st.session_state[f"{key}_seen"] = selected
    return selected


def _render_portfolio(statement: PortfolioStatement, month: str, view: str) -> None:
    _render_metrics(statement.income, statement.expense, statement.net)
    st.subheader("Expense by account")
    _render_accounts(statement.expense_lines, "No expenses in this range.")
    _render_memo(statement.memo_lines)

    st.divider()
    st.subheader("Properties")
    st.caption("Choose an address to see that house.")
    if not statement.properties:
        st.caption("No properties in this range.")
        return
    labels = address_labels(statement.properties)
    header = st.columns([3, 2, 2, 2])
    header[0].markdown("**Address**")
    header[1].markdown("**Income**")
    header[2].markdown("**Expense**")
    header[3].markdown("**Net**")
    for prop in statement.properties:
        row = st.columns([3, 2, 2, 2])
        row[0].page_link(
            VWH_PAGE,
            label=labels[prop.property_id],
            query_params=_page_params(month, view, prop.property_id),
        )
        row[1].write(format_money(prop.income))
        row[2].write(format_money(prop.expense))
        row[3].markdown(format_signed_money(prop.net))
    total = st.columns([3, 2, 2, 2])
    total[0].markdown("**Total**")
    total[1].markdown(f"**{format_money(statement.income)}**")
    total[2].markdown(f"**{format_money(statement.expense)}**")
    total[3].markdown(format_signed_money(statement.net))


def _render_property(
    statement: PortfolioStatement,
    rows: pd.DataFrame,
    property_id: str,
    month: str,
    view: str,
    tax,
) -> None:
    _back_link(month, view)
    prop = lookup_property(statement, rows, property_id)
    if prop is None:
        st.info("That house is not in the file.")
        return
    st.subheader(prop.address)
    st.caption(prop.property_id)
    _render_metrics(prop.income, prop.expense, prop.net)
    st.subheader("Income")
    _render_accounts(prop.income_lines, "No income in this range.")
    st.subheader("Expense")
    _render_accounts(prop.expense_lines, "No expenses in this range.")
    _render_memo(prop.memo_lines)
    _render_property_trending(rows, tax, prop.property_id)


def _back_link(month: str, view: str) -> None:
    st.page_link(
        VWH_PAGE,
        label="Back to portfolio",
        query_params=_page_params(month, view),
    )


def _render_metrics(income: float, expense: float, net: float) -> None:
    columns = st.columns(3)
    columns[0].metric("Income", format_money(income), border=True)
    columns[1].metric("Expense", format_money(expense), border=True)
    columns[2].metric("Net income", format_signed_money(net), border=True)


def _render_accounts(lines: Sequence[AccountLine], empty: str) -> None:
    if not lines:
        st.caption(empty)
        return
    show_money_table(account_table(lines), ["Amount"])


def _render_memo(lines: Sequence[AccountLine]) -> None:
    shown = memo_lines_to_show(lines)
    if not shown:
        return
    st.caption("Not included in net income.")
    for line in shown:
        st.write(memo_line_text(line))


def _render_trending(rows: pd.DataFrame, tax) -> None:
    st.divider()
    st.subheader("Trending")
    st.caption("This section uses the latest months in the file.")
    _render_tax_section(tax, None)
    _render_run_rate_section(rows, tax, None)
    _render_trend_chart(rows)


def _render_property_trending(rows: pd.DataFrame, tax, property_id: str) -> None:
    st.divider()
    st.subheader("Trending")
    st.caption("This section uses the latest months in the file.")
    _render_tax_section(tax, property_id)
    _render_run_rate_section(rows, tax, property_id)


def _choose_tax_year(tax) -> str | None:
    if not tax.configured:
        st.info(_trend().NO_TAX_DATA)
        return None
    years = _trend().available_pay_years(tax.rows)
    if not years:
        if tax.warning is None:
            st.caption("No property tax rows yet.")
        return None
    if len(years) > 1:
        year = _select("Pay year", years, "vwh_tax_year", years[0], str)
    else:
        year = years[0]
        st.caption(f"Pay year {year}")
    st.caption("Spring is due May 10 and fall is due November 10. Amounts are the tax only.")
    return year


def _render_tax_section(tax, property_id: str | None) -> None:
    st.markdown("**Property taxes**")
    year = _choose_tax_year(tax)
    if year is None:
        return
    table = _trend().property_tax_table(tax.rows, year)
    if not table.lines:
        st.caption("No houses for this pay year.")
        return
    if property_id is not None and all(line.property_id != property_id for line in table.lines):
        st.caption(f"This house has no property tax row for {year}.")
        return
    show_money_table(_tax_frame(table, property_id), ["Spring", "Fall", "Annual", "vs last year"])
    st.caption(
        "Change is this year minus the prior pay year. "
        "It is blank when that house has no prior-year row."
    )


def _render_run_rate_section(rows: pd.DataFrame, tax, property_id: str | None) -> None:
    st.markdown("**Estimated annual run rate**")
    rate = _trend().annual_run_rate(rows, tax.rows, tax_configured=tax.configured)
    if property_id is None and not rate.lines:
        st.caption("No houses to estimate.")
        return
    if property_id is not None and all(line.property_id != property_id for line in rate.lines):
        st.caption("This house has no run rate in the latest months.")
        return
    show_money_table(
        _run_rate_frame(rate, property_id),
        ["Income", "Operating expense", "Property tax", "Insurance", "Net", "Net per month"],
    )
    st.caption(_trend().run_rate_note(rate.months, rate.tax_configured))


def _render_trend_chart(rows: pd.DataFrame) -> None:
    st.markdown("**Monthly trend**")
    property_id = _chart_property(_trend().trend_properties(rows))
    frame = _trend().monthly_trend(rows, property_id)
    chart = _trend_chart(frame)
    if chart is None:
        st.caption("No months to chart.")
        return
    st.altair_chart(chart, use_container_width=True)
    st.caption("Income, expense, and net by month. Expense matches the rest of this page.")


def _tax_frame(table, property_id: str | None) -> pd.DataFrame:
    lines = table.lines
    if property_id is not None:
        lines = tuple(line for line in lines if line.property_id == property_id)
    labels = _labels_for([(line.property_id, line.address) for line in lines])
    records = [_tax_record(labels[line.property_id], line) for line in lines]
    if property_id is None:
        records.append(
            {
                "Address": "Total",
                "Spring": table.total_spring,
                "Spring status": "",
                "Fall": table.total_fall,
                "Fall status": "",
                "Annual": table.total_annual,
                "vs last year": table.total_change if table.total_change is not None else float("nan"),
                "vs last year %": table.total_change_pct,
            }
        )
    return pd.DataFrame.from_records(records)


def _tax_record(address: str, line) -> dict[str, object]:
    return {
        "Address": address,
        "Spring": line.spring,
        "Spring status": line.spring_status,
        "Fall": line.fall,
        "Fall status": line.fall_status,
        "Annual": line.annual,
        "vs last year": line.change if line.change is not None else float("nan"),
        "vs last year %": line.change_pct,
    }


def _run_rate_frame(rate, property_id: str | None) -> pd.DataFrame:
    lines = rate.lines
    if property_id is not None:
        lines = tuple(line for line in lines if line.property_id == property_id)
    labels = _labels_for([(line.property_id, line.address) for line in lines])
    records = [_rate_record(labels[line.property_id], line) for line in lines]
    if property_id is None:
        records.append(_rate_record("Total", rate.total))
    return pd.DataFrame.from_records(records)


def _rate_record(address: str, line) -> dict[str, object]:
    return {
        "Address": address,
        "Income": line.income,
        "Operating expense": line.operating_expense,
        "Property tax": line.property_tax,
        "Insurance": line.insurance,
        "Net": line.net,
        "Net per month": line.net_per_month,
    }


def _chart_property(props: Sequence[tuple[str, str]]) -> str | None:
    if len(props) < 2:
        return None
    labels = _labels_for(props)
    options = ["", *[property_id for property_id, _address in props]]
    names = {"": "All properties", **labels}
    if st.session_state.get("vwh_trend_property") not in names:
        st.session_state["vwh_trend_property"] = ""
    selected = st.selectbox(
        "Property",
        options,
        format_func=lambda property_id: names[property_id],
        key="vwh_trend_property",
    )
    return selected or None


def _trend_chart(frame: pd.DataFrame) -> alt.Chart | None:
    if frame.empty:
        return None
    order = frame["Label"].tolist()
    long = frame.melt(
        id_vars=["Label"],
        value_vars=["Income", "Expense", "Net"],
        var_name="Series",
        value_name="Amount",
    )
    return (
        alt.Chart(long)
        .mark_line(point=True)
        .encode(
            x=alt.X("Label:N", sort=order, title=None, axis=alt.Axis(labelAngle=-40)),
            y=alt.Y("Amount:Q", title=None, axis=alt.Axis(format="$,.0f")),
            color=alt.Color(
                "Series:N",
                title=None,
                scale=alt.Scale(
                    domain=["Income", "Expense", "Net"],
                    range=["#2e7d32", "#c62828", "#1565c0"],
                ),
            ),
            tooltip=[
                alt.Tooltip("Label:N", title="Month"),
                alt.Tooltip("Series:N", title=""),
                alt.Tooltip("Amount:Q", title="Amount", format="$,.2f"),
            ],
        )
        .properties(height=280)
    )


def _trend():
    from kirbancials import vwh_trend

    return vwh_trend


def _rows_in_window(rows: pd.DataFrame, window: MonthWindow) -> pd.DataFrame:
    if rows.empty or not window.months or "Month" not in rows.columns:
        return empty_rows()
    return rows.loc[rows["Month"].isin(window.months)].copy()


def _properties(frame: pd.DataFrame) -> tuple[tuple[PropertyStatement, ...], int, int]:
    if frame.empty:
        return (), 0, 0
    found: list[PropertyStatement] = []
    income_cents = 0
    expense_cents = 0
    for property_id, group in frame.groupby("Property ID", sort=False):
        house_id = str(property_id)
        income_lines, house_income = _collapse(group, SECTION_INCOME)
        expense_lines, house_expense = _collapse(group, SECTION_EXPENSE)
        memo_lines, _memo = _collapse(group, SECTION_BELOW)
        income_cents += house_income
        expense_cents += house_expense
        found.append(
            PropertyStatement(
                property_id=house_id,
                address=_address(group, house_id),
                income=_dollars(house_income),
                expense=_dollars(house_expense),
                net=_dollars(house_income - house_expense),
                income_lines=income_lines,
                expense_lines=expense_lines,
                memo_lines=memo_lines,
            )
        )
    found.sort(key=lambda prop: (prop.address.casefold(), prop.property_id))
    return tuple(found), income_cents, expense_cents


def _collapse(frame: pd.DataFrame, section: str) -> tuple[tuple[AccountLine, ...], int]:
    if frame.empty or "Section" not in frame.columns:
        return (), 0
    chosen = frame.loc[frame["Section"] == section]
    if chosen.empty:
        return (), 0
    buckets: dict[tuple[str, str, str], dict[str, object]] = {}
    ordered = chosen.sort_values("Month", kind="mergesort")
    for record in ordered.to_dict("records"):
        number = str(record["Account Number"])
        source = str(record["Source"])
        name = str(record["Account Name"])
        # Account number is the identity. 5000 and 6400 share a name.
        key = (number, source, "") if number else ("", source, name)
        cents = int(record["Cents"])
        bucket = buckets.get(key)
        if bucket is None:
            buckets[key] = {
                "account_number": number,
                "account_name": name or number or "Account",
                "source": source,
                "cents": cents,
                "month": str(record["Month"]),
            }
            continue
        bucket["cents"] = int(bucket["cents"]) + cents
        if str(record["Month"]) >= str(bucket["month"]):
            bucket["month"] = str(record["Month"])
            if name:
                bucket["account_name"] = name
    lines: list[AccountLine] = []
    total = 0
    for bucket in buckets.values():
        cents = int(bucket["cents"])
        total += cents
        lines.append(
            AccountLine(
                account_number=str(bucket["account_number"]),
                account_name=str(bucket["account_name"]),
                source=str(bucket["source"]),
                section=section,
                amount=_dollars(cents),
            )
        )
    lines.sort(
        key=lambda line: (
            _ACCOUNT_RANK.get(line.account_number, 500),
            line.account_number,
            line.source.casefold(),
            line.account_name.casefold(),
        )
    )
    return tuple(lines), total


def _address(frame: pd.DataFrame, property_id: str) -> str:
    ordered = frame.sort_values("Month", kind="mergesort")
    for address in reversed(ordered["Address"].tolist()):
        text = str(address).strip()
        if text and text.lower() not in {"<na>", "nan", "none"}:
            return text
    return property_id


def _parse_row(raw: Mapping[str, object]) -> dict[str, object] | None:
    month = _text(raw.get("Month"))
    if parse_month(month) is None:
        return None
    property_id = _text(raw.get("Property ID"))
    if not property_id:
        return None
    section = _SECTIONS.get(" ".join(_text(raw.get("Section")).casefold().split()))
    if section is None:
        return None
    cents = _cents(_text(raw.get("Amount")))
    if cents is None:
        return None
    account_number = _text(raw.get("Account Number"))
    account_name = _text(raw.get("Account Name")) or account_number
    return {
        "Month": month,
        "Property ID": property_id,
        "Address": _text(raw.get("Address")),
        "Section": section,
        "Account Number": account_number,
        "Account Name": account_name,
        "Source": _text(raw.get("Source")),
        "Cents": cents,
    }


def _blank_row(raw: Mapping[str, object]) -> bool:
    return all(_text(raw.get(column)) == "" for column in VWH_COLUMNS)


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _cents(text: str) -> int | None:
    if _DECIMAL.fullmatch(text) is None:
        return None
    amount = Decimal(text) * 100
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _dollars(cents: int) -> float:
    return cents / 100


def _add_months(year: int, month: int, delta: int) -> str:
    index = year * 12 + (month - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _span_count(start: str, end: str) -> int:
    start_parts = parse_month(start)
    end_parts = parse_month(end)
    if start_parts is None or end_parts is None:
        return 0
    start_index = start_parts[0] * 12 + (start_parts[1] - 1)
    end_index = end_parts[0] * 12 + (end_parts[1] - 1)
    return end_index - start_index + 1


def _labels_for(pairs: Sequence[tuple[str, str]]) -> dict[str, str]:
    counts: dict[str, int] = {}
    for _property_id, address in pairs:
        counts[address] = counts.get(address, 0) + 1
    labels: dict[str, str] = {}
    for property_id, address in pairs:
        if counts[address] > 1:
            labels[property_id] = f"{address} ({property_id})"
        else:
            labels[property_id] = address
    return labels


def _page_params(month: str, view: str, property_id: str | None = None) -> dict[str, str]:
    params = {"vwh_month": month, "vwh_view": view}
    tax_year = _query_value("vwh_tax_year")
    if tax_year:
        params["vwh_tax_year"] = tax_year
    if property_id:
        params["vwh_property"] = property_id
    return params


def _query_value(name: str) -> str | None:
    raw = st.query_params.get(name)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None
