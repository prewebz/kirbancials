"""Profit and loss page and the per-source detail page."""

from __future__ import annotations

from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from kirbancials.activity import load_balances, load_transactions, resolve_balances, resolve_transactions
from kirbancials.data import FinanceDataError, load_dataset, resolve_origin
from kirbancials.formatting import format_money, show_money_table
from kirbancials.income import (
    IncomeLine,
    deposits_for_source,
    income_line_for_id,
    income_lines,
    income_table,
    load_income,
    net_profit,
    resolve_income,
)
from kirbancials.loans import estimated_daily_interest, estimated_monthly_interest, loan_payment_table
from kirbancials.overview import (
    credit_card_balance_total,
    estimated_loan_interest_this_month,
    expected_income_next_month,
    expense_coverage_note,
    expense_data_start,
    income_expense_by_month,
    monthly_run_rate,
    recent_month_periods,
)
from kirbancials.periods import PERIOD_KEYS, PERIOD_LABELS, Period, format_period, period_choice, resolve_period
from kirbancials.sources import expense_source_by_id, income_source_by_id
from kirbancials.spend import ExpenseLine, caveats, expense_lines, line_for_source_id, rows_for_line, total_income

DETAIL_PAGE = "views/source_detail.py"
HOME_PAGE = "views/profit_and_loss.py"


def render_profit_and_loss() -> None:
    transactions, monthly, balances, sample, errors = _load()
    for message in errors:
        st.error(message)
    if sample:
        st.warning("Showing bundled sample data. These are fake round numbers, not your accounts.")

    today = date.today()
    period = _period_selector(today)
    deposits, income_error = _load_income()
    if income_error:
        st.error(income_error)
    received = income_lines(deposits, period)
    income = total_income(deposits, period)
    lines = expense_lines(transactions, monthly, period, balances)
    expenses = sum(line.total for line in lines)
    net = net_profit(income, expenses)
    available_from = expense_data_start(transactions)

    _render_summary(income, expenses, net, period, available_from)
    _render_snapshot(balances, deposits, today)
    _render_trend(transactions, monthly, deposits, balances, today, available_from)

    st.divider()
    st.subheader("Income")
    if deposits.empty:
        st.caption("No income data yet")
    _income_rows(received, period, income)

    st.subheader("Expenses")
    card_lines = [line for line in lines if line.data_type != "loan"]
    loan_lines = [line for line in lines if line.data_type == "loan"]
    _expense_rows(card_lines, period)
    if loan_lines:
        st.markdown("**Loan interest**")
        st.caption("Interest estimated from current balance and APR.")
        _expense_rows(loan_lines, period)
    total = st.columns([3, 2, 2])
    total[0].markdown("**Total expenses**")
    total[1].markdown(f"**{format_money(expenses)}**")
    total[2].write("")
    for note in caveats(lines):
        st.caption(note)

    net = net_profit(income, expenses)
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

    transactions, monthly, balances, sample, errors = _load()
    for message in errors:
        st.error(message)
    if sample:
        st.warning("Showing bundled sample data. These are fake round numbers, not your accounts.")

    period = _period_selector()
    deposits, income_error = _load_income()
    if income_error:
        st.error(income_error)
    if _is_income_source(source_id, deposits):
        _render_income_detail(source_id, deposits, period)
        return

    line = line_for_source_id(source_id, transactions, monthly, period, balances)
    if line is None:
        st.write("That expense source was not found.")
        _back_link(period)
        return

    st.subheader(line.name)
    _back_link(period)
    if line.data_type == "loan":
        _render_loan_detail(line, transactions, period)
        return
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
            show_money_table(
                _statement_table(spending),
                ["Net Spend", "Interest", "Fees", "New Charges (Purchases)", "Credits/Returns", "Payments"],
            )
        return

    st.caption("Charges and refunds. Pending charges are included. Card payments are excluded.")
    if spending.empty:
        st.info("No charges or refunds in this period.")
    else:
        show_money_table(_transaction_table(spending), ["Amount"])
    if not payments.empty:
        with st.expander("Card payments (excluded from spend)"):
            show_money_table(_transaction_table(payments), ["Amount"])


def _period_selector(today: date | None = None) -> Period:
    requested, seen = period_choice(
        st.query_params.get("period"),
        st.session_state.get("pnl_period"),
        st.session_state.get("pnl_period_seen"),
    )
    if requested not in PERIOD_KEYS:
        requested = "this-month"
    st.session_state["pnl_period"] = requested
    st.session_state["pnl_period_seen"] = seen if seen in PERIOD_KEYS else requested
    selected = st.selectbox(
        "Period",
        PERIOD_KEYS,
        format_func=lambda key: PERIOD_LABELS[key],
        key="pnl_period",
    )
    if st.query_params.get("period") != selected:
        st.query_params["period"] = selected
        st.session_state["pnl_period_seen"] = selected
    period = resolve_period(selected, today or date.today())
    st.caption(format_period(period))
    return period


def _render_summary(
    income: float,
    expenses: float,
    net: float,
    period: Period,
    available_from: date | None,
) -> None:
    rate = monthly_run_rate(net, period)
    columns = st.columns(4)
    columns[0].metric("Total income", format_money(income), border=True)
    columns[1].metric("Total expenses", format_money(expenses), border=True)
    columns[2].metric("Net", _colored_net(net), border=True)
    columns[3].metric(
        "Monthly run rate",
        format_money(rate.amount),
        delta=rate.label,
        delta_color="off",
        delta_arrow="off",
        border=True,
    )
    note = expense_coverage_note(period.start, available_from)
    if note:
        st.caption(note)


def _render_snapshot(balances: pd.DataFrame, deposits: pd.DataFrame, today: date) -> None:
    left, right = st.columns(2)
    with left.container(border=True):
        st.markdown("**Debt snapshot**")
        cards, interest = st.columns(2)
        cards.metric("Credit card balances", format_money(credit_card_balance_total(balances)))
        interest.metric(
            "Est. loan interest this month",
            format_money(estimated_loan_interest_this_month(balances, today)),
            help="Estimated from the current balance and APR, for this month through today.",
        )
    expected = expected_income_next_month(deposits, today)
    with right.container(border=True):
        st.markdown("**Expected income next month**")
        st.metric("Expected", format_money(expected.total))
        if expected.note:
            st.caption(expected.note)


def _render_trend(
    transactions: pd.DataFrame,
    monthly: pd.DataFrame,
    deposits: pd.DataFrame,
    balances: pd.DataFrame,
    today: date,
    available_from: date | None,
) -> None:
    st.subheader("Income and expenses")
    st.caption("Last 6 months. The current month runs through today. Net is the line.")
    months = recent_month_periods(today)
    note = expense_coverage_note(months[0].start if months else None, available_from)
    if note:
        st.caption(note)
    frame = income_expense_by_month(transactions, monthly, deposits, balances, today)
    chart = _income_expense_chart(frame)
    if chart is None:
        st.info("No income or expenses in the last 6 months.")
        return
    st.altair_chart(chart, use_container_width=True)


def _income_expense_chart(frame: pd.DataFrame) -> alt.Chart | None:
    if frame.empty:
        return None
    order = frame["month"].tolist()
    long = frame.melt(id_vars=["month"], value_vars=["income", "expenses"], var_name="kind", value_name="amount")
    long["kind"] = long["kind"].map({"income": "Income", "expenses": "Expenses"})
    long = long.dropna(subset=["amount"])
    net = frame.loc[:, ["month", "net"]].dropna(subset=["net"])
    layers: list[alt.Chart] = []
    if not long.empty:
        layers.append(
            alt.Chart(long)
            .mark_bar()
            .encode(
                x=alt.X("month:N", sort=order, title=None),
                xOffset="kind:N",
                y=alt.Y("amount:Q", title=None),
                color=alt.Color(
                    "kind:N",
                    title=None,
                    scale=alt.Scale(domain=["Income", "Expenses"], range=["#2e7d32", "#c62828"]),
                ),
                tooltip=[
                    alt.Tooltip("month:N", title="Month"),
                    alt.Tooltip("kind:N", title=""),
                    alt.Tooltip("amount:Q", title="Amount", format="$,.2f"),
                ],
            )
        )
    if not net.empty:
        layers.append(
            alt.Chart(net)
            .mark_line(point=True, color="#1565c0")
            .encode(
                x=alt.X("month:N", sort=order, title=None),
                y=alt.Y("net:Q", title=None),
                tooltip=[
                    alt.Tooltip("month:N", title="Month"),
                    alt.Tooltip("net:Q", title="Net", format="$,.2f"),
                ],
            )
        )
    if not layers:
        return None
    chart = layers[0]
    for layer in layers[1:]:
        chart = chart + layer
    return chart.resolve_scale(y="shared").properties(height=280)


def _colored_net(net: float) -> str:
    rendered = format_money(net)
    if net > 0:
        return f":green[{rendered}]"
    if net < 0:
        return f":red[{rendered}]"
    return rendered


def _income_rows(lines: list[IncomeLine], period: Period, income: float) -> None:
    header = st.columns([3, 2])
    header[0].markdown("**Income description**")
    header[1].markdown("**Total received**")
    for line in lines:
        row = st.columns([3, 2])
        row[0].write(line.label)
        row[1].page_link(
            DETAIL_PAGE,
            label=format_money(line.total),
            query_params={"source": line.source_id, "period": period.key},
        )
    total = st.columns([3, 2])
    total[0].markdown("**Total income**")
    total[1].markdown(f"**{format_money(income)}**")


def _is_income_source(source_id: str, deposits: pd.DataFrame) -> bool:
    """True for a registered income id or a raw id that appears on a deposit.

    A registered expense id stays on the expense detail page.
    """
    if expense_source_by_id(source_id) is not None:
        return False
    if income_source_by_id(source_id) is not None:
        return True
    if deposits.empty or "Source" not in deposits.columns:
        return False
    return bool((deposits["Source"] == source_id).any())


def _render_income_detail(source_id: str, deposits: pd.DataFrame, period: Period) -> None:
    line = income_line_for_id(source_id, deposits, period)
    if line is None:
        st.write("That income source was not found.")
        _back_link(period)
        return
    st.subheader(line.label)
    _back_link(period)
    st.write(f"Total received {format_money(line.total)}")
    rows = deposits_for_source(deposits, source_id, period)
    if rows.empty:
        st.info("No deposits in this period.")
        return
    show_money_table(income_table(rows), ["Amount"])


def _expense_rows(lines: list[ExpenseLine], period: Period) -> None:
    if not lines:
        return
    header = st.columns([3, 2, 2])
    header[0].markdown("**Expense description**")
    header[1].markdown("**Total spend**")
    header[2].markdown("**Data through**")
    for line in lines:
        row = st.columns([3, 2, 2])
        label = loan_label_for(line)
        row[0].page_link(
            DETAIL_PAGE,
            label=label,
            query_params={"source": line.source_id, "period": period.key},
        )
        row[1].write(format_money(line.total))
        row[2].write(line.data_through.isoformat() if line.data_through else "—")


def loan_label_for(line: ExpenseLine) -> str:
    if line.data_type == "loan" and line.apr is not None:
        return f"{line.name} ({line.apr:.2f}% APR)"
    return line.name


def _render_loan_detail(line: ExpenseLine, transactions: pd.DataFrame, period: Period) -> None:
    st.write(f"APR {line.apr:.2f}%" if line.apr is not None else "APR")
    if line.balance_missing or line.balance is None:
        st.caption(f"{line.name} balance is missing, so interest is $0.")
        st.write(f"Current balance {format_money(None)}")
    else:
        st.write(f"Current balance {format_money(line.balance)}")
        st.write(f"Estimated monthly interest {format_money(estimated_monthly_interest(line.balance, line.apr or 0))}")
        st.write(f"Estimated daily interest {format_money(estimated_daily_interest(line.balance, line.apr or 0))}")
    st.caption("Interest estimated from current balance and APR.")
    st.write(f"Estimated interest this period {format_money(line.total)}")
    account = _loan_account(line)
    payments = loan_payment_table(transactions, account, period) if account else pd.DataFrame()
    st.subheader("Payments")
    if payments.empty:
        st.info("No loan payments in this period.")
        return
    show_money_table(payments, ["Amount"])


def _loan_account(line: ExpenseLine) -> str:
    source = expense_source_by_id(line.source_id)
    if source is None or not source.accounts:
        return ""
    return source.accounts[0]


def _back_link(period: Period) -> None:
    st.page_link(HOME_PAGE, label="Back to Profit & Loss", query_params={"period": period.key})


def _load() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, bool, list[str]]:
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
    try:
        balances = load_balances(resolve_balances())
        sample = sample or balances.is_sample
        balance_rows = balances.rows
    except FinanceDataError as exc:
        errors.append(str(exc))
        balance_rows = pd.DataFrame()
    return txn_rows, monthly, balance_rows, sample, errors


def _load_income() -> tuple[pd.DataFrame, str | None]:
    try:
        return load_income(resolve_income()), None
    except FinanceDataError as exc:
        return load_income(None), str(exc)


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
    return display


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
    return display
