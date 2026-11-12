"""Profit and loss page and the per-source detail page."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from kirbancials.activity import load_transactions, resolve_transactions
from kirbancials.data import FinanceDataError, load_dataset, resolve_origin
from kirbancials.formatting import format_money, format_money_columns
from kirbancials.periods import PERIOD_KEYS, PERIOD_LABELS, Period, format_period, period_choice, resolve_period
from kirbancials.sources import INCOME_SOURCES
from kirbancials.spend import ExpenseLine, caveats, expense_lines, line_for_source_id, rows_for_line, total_income

DETAIL_PAGE = "views/source_detail.py"
HOME_PAGE = "views/profit_and_loss.py"


def render_profit_and_loss() -> None:
    transactions, monthly, sample, errors = _load()
    for message in errors:
        st.error(message)
    if sample:
        st.warning("Showing bundled sample data. These are fake round numbers, not your accounts.")

    period = _period_selector()
    income = total_income()
    lines = expense_lines(transactions, monthly, period)
    expenses = sum(line.total for line in lines)

    st.subheader("Income")
    if not INCOME_SOURCES:
        st.caption("No income sources connected yet")
    else:
        for source in INCOME_SOURCES:
            st.write(f"{source.name} {format_money(0)}")
    st.write(f"Total income {format_money(income)}")

    st.subheader("Expenses")
    _expense_table(lines, expenses, period)
    for note in caveats(lines):
        st.caption(note)

    net = income - expenses
    st.subheader("Net profit / (loss)")
    rendered = format_money(net)
    if net < 0:
        st.markdown(f":red[{rendered}]")
    else:
        st.write(rendered)


def render_source_detail() -> None:
    source_id = st.query_params.get("source", "")
    if not source_id:
        st.write("Choose an expense from Profit & Loss.")
        _back_link(_period_selector())
        return

    transactions, monthly, sample, errors = _load()
    for message in errors:
        st.error(message)
    if sample:
        st.warning("Showing bundled sample data. These are fake round numbers, not your accounts.")

    period = _period_selector()
    line = line_for_source_id(source_id, transactions, monthly, period)
    if line is None:
        st.write("That expense source was not found.")
        _back_link(period)
        return

    st.subheader(line.name)
    _back_link(period)
    st.write(f"Total spend {format_money(line.total)}")
    if line.data_through is not None:
        st.caption(f"Data through {line.data_through.isoformat()}")

    spending, payments = rows_for_line(line, transactions, monthly, period)
    if line.data_type == "statements":
        st.caption(
            "Includes net spend, interest, and fees. "
            f"Interest {format_money(line.interest)}, fees {format_money(line.fees)}."
        )
        if spending.empty:
            st.info("No statements in this period.")
        else:
            st.dataframe(_statement_table(spending), width="stretch", hide_index=True)
        return

    st.caption("Charges and refunds. Pending charges are included. Card payments are excluded.")
    if spending.empty:
        st.info("No charges or refunds in this period.")
    else:
        st.dataframe(_transaction_table(spending), width="stretch", hide_index=True)
    if not payments.empty:
        with st.expander("Card payments (excluded from spend)"):
            st.dataframe(_transaction_table(payments), width="stretch", hide_index=True)


def _period_selector() -> Period:
    requested, seen = period_choice(
        st.query_params.get("period"),
        st.session_state.get("pnl_period"),
        st.session_state.get("pnl_period_seen"),
    )
    st.session_state["pnl_period"] = requested
    st.session_state["pnl_period_seen"] = seen
    selected = st.selectbox(
        "Period",
        PERIOD_KEYS,
        format_func=lambda key: PERIOD_LABELS[key],
        key="pnl_period",
    )
    if st.query_params.get("period") != selected:
        st.query_params["period"] = selected
        st.session_state["pnl_period_seen"] = selected
    period = resolve_period(selected, date.today())
    st.caption(format_period(period))
    return period


def _expense_table(lines: list[ExpenseLine], expenses: float, period: Period) -> None:
    header = st.columns([3, 2, 2])
    header[0].markdown("**Expense description**")
    header[1].markdown("**Total spend**")
    header[2].markdown("**Data through**")
    for line in lines:
        row = st.columns([3, 2, 2])
        row[0].page_link(
            DETAIL_PAGE,
            label=line.name,
            query_params={"source": line.source_id, "period": period.key},
        )
        row[1].write(format_money(line.total))
        row[2].write(line.data_through.isoformat() if line.data_through else "—")
    total = st.columns([3, 2, 2])
    total[0].markdown("**Total expenses**")
    total[1].markdown(f"**{format_money(expenses)}**")
    total[2].write("")


def _back_link(period: Period) -> None:
    st.page_link(HOME_PAGE, label="Back to Profit & Loss", query_params={"period": period.key})


def _load() -> tuple[pd.DataFrame, pd.DataFrame, bool, list[str]]:
    errors: list[str] = []
    sample = False
    try:
        transactions = load_transactions(resolve_transactions())
        sample = sample or transactions.is_sample
        txn_rows = transactions.rows
    except FinanceDataError as exc:
        errors.append(str(exc))
        txn_rows = pd.DataFrame()
    try:
        origin = resolve_origin()
        statements = load_dataset(origin.source, label=origin.label, is_sample=origin.is_sample)
        sample = sample or statements.is_sample
        monthly = statements.monthly
    except FinanceDataError as exc:
        errors.append(str(exc))
        monthly = pd.DataFrame()
    return txn_rows, monthly, sample, errors


def _transaction_table(frame: pd.DataFrame) -> pd.DataFrame:
    display = pd.DataFrame(
        {
            "Date": pd.to_datetime(frame["Date"]).dt.strftime("%Y-%m-%d"),
            "Merchant": frame["Merchant"],
            "Amount": frame["Amount"],
            "Pending": frame["Pending"].map(lambda pending: "Pending" if bool(pending) else "Posted"),
            "Category": frame["Category"] if "Category" in frame.columns else "",
        }
    )
    return format_money_columns(display, ["Amount"])


def _statement_table(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        column
        for column in (
            "Month",
            "Statement Closing Date",
            "Net Spend",
            "Interest",
            "Fees",
            "New Charges (Purchases)",
            "Credits/Returns",
            "Payments",
        )
        if column in frame.columns
    ]
    display = frame.loc[:, columns].copy()
    if "Statement Closing Date" in display.columns:
        parsed = pd.to_datetime(display["Statement Closing Date"], errors="coerce")
        display["Statement Closing Date"] = (
            parsed.dt.strftime("%Y-%m-%d").fillna("").replace("NaT", "")
        )
    return format_money_columns(display, ["Net Spend", "Interest", "Fees", "New Charges (Purchases)", "Credits/Returns", "Payments"])
