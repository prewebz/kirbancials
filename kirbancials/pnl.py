"""Profit and loss page and the per-source detail page."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from kirbancials.activity import load_balances, load_transactions, resolve_balances, resolve_transactions
from kirbancials.alerts import render_alert_strip
from kirbancials.data import FinanceDataError, load_dataset, resolve_origin
from kirbancials.discretionary import (
    FINANCING_NOTE,
    discretionary_history_frame,
    discretionary_line_amount,
    discretionary_previous_month,
)
from kirbancials.forecast import ExpenseForecast, month_end_forecast
from kirbancials.formatting import format_money, format_signed_money, show_money_table
from kirbancials.income import (
    IncomeLine,
    deposits_for_source,
    income_line_for_id,
    income_lines,
    income_table,
    load_income,
    net_excluding_lbsf,
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
    recent_month_periods,
)
from kirbancials.periods import PERIOD_KEYS, PERIOD_LABELS, Period, format_period, period_choice, resolve_period
from kirbancials.schedules import (
    format_rate,
    loan_snapshots,
    resolve_loan_schedule,
    rows_for_loan,
    schedule_label,
    schedule_payment_table,
    snapshot_line,
)
from kirbancials.sources import expense_source_by_id, income_source_by_id, loan_id_from_schedule_source
from kirbancials.spend import ExpenseLine, caveats, expense_lines, line_for_source_id, rows_for_line, total_income

DETAIL_PAGE = "views/source_detail.py"
HOME_PAGE = "views/profit_and_loss.py"


def render_profit_and_loss() -> None:
    # Profit & Loss is the overview. Urgent alerts sit above the period figures.
    render_alert_strip()
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
    schedule, schedule_warning = resolve_loan_schedule()
    if schedule_warning:
        st.warning(schedule_warning)
    received = income_lines(deposits, period)
    income = total_income(deposits, period)
    lines = expense_lines(transactions, monthly, period, balances, schedule)
    shown = _discretionary_rows(lines, transactions, period)
    expenses = sum(amount for _line, amount in shown)
    net = net_profit(income, expenses)
    # Same income lines and expenses as Net. Only LBSF income is left out.
    net_ex_lbsf = net_excluding_lbsf(income, expenses, received)
    available_from = expense_data_start(transactions)
    prior_expenses = discretionary_previous_month(transactions, monthly, period, balances, schedule)
    forecast = month_end_forecast(transactions, period.end or today, monthly, balances, schedule)

    _render_summary(
        income,
        expenses,
        net,
        period,
        available_from,
        prior_expenses,
        forecast,
        net_ex_lbsf,
    )
    _render_snapshot(balances, deposits, today, schedule)
    _render_trend(transactions, monthly, deposits, balances, today, available_from, schedule)

    st.divider()
    st.subheader("Income")
    if deposits.empty:
        st.caption("No income data yet")
    _income_rows(received, period, income)

    st.subheader("Expenses")
    st.caption(FINANCING_NOTE)
    _expense_rows(shown, period)
    _category_trends(forecast)
    total = st.columns([3, 2, 2])
    total[0].markdown("**Total expenses**")
    total[1].markdown(f"**{format_money(expenses)}**")
    total[2].write("")
    for note in caveats([line for line, _amount in shown]):
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

    today = date.today()
    period = _period_selector(today)
    deposits, income_error = _load_income()
    if income_error:
        st.error(income_error)
    schedule, schedule_warning = resolve_loan_schedule()
    if schedule_warning:
        st.warning(schedule_warning)
    if _is_income_source(source_id, deposits):
        _render_income_detail(source_id, deposits, period)
        return

    line = line_for_source_id(source_id, transactions, monthly, period, balances, schedule)
    if line is None:
        st.write("That expense source was not found.")
        _back_link(period)
        return

    st.subheader(line.name)
    _back_link(period)
    if line.data_type == "loan":
        _render_loan_detail(line, transactions, period)
        return
    if line.data_type == "schedule":
        _render_schedule_detail(line, schedule, period, today)
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
    prior_expenses: float | None = None,
    forecast: ExpenseForecast | None = None,
    net_ex_lbsf: float | None = None,
) -> None:
    columns = st.columns(4)
    columns[0].metric("Total income", format_money(income), border=True)
    expense_metric: dict[str, object] = {"border": True}
    if prior_expenses is not None:
        expense_metric["delta"] = f"Last month: {format_money(prior_expenses)}"
        expense_metric["delta_color"] = "gray"
        expense_metric["delta_arrow"] = "off"
    columns[1].metric("Total expenses", format_money(expenses), **expense_metric)
    # No LBSF lines means the secondary figure is the same Net.
    excluded = net if net_ex_lbsf is None else net_ex_lbsf
    columns[2].metric(
        "Net",
        format_signed_money(net),
        delta=_net_excl_lbsf_delta(excluded),
        delta_color="gray",
        delta_arrow="off",
        border=True,
    )
    _forecast_metric(columns[3], forecast)
    note = expense_coverage_note(period.start, available_from)
    if note:
        st.caption(note)
    if forecast is not None:
        pieces = [
            f"One-time so far {format_money(forecast.one_time)}",
            f"Bills still to come {format_money(forecast.bills_still_to_come)}",
            f"Everyday pace {format_money(max(forecast.everyday_pace, 0.0))}/day",
            f"Statement cards {format_money(forecast.other_expenses)}",
        ]
        if forecast.other_card_spend:
            pieces.append(f"Other card charges {format_money(forecast.other_card_spend)}")
        st.caption(" · ".join(pieces))
        st.caption(
            "Forecast of discretionary spending. Card charges are paced. "
            "Statement cards count for the full month."
        )
        st.caption(FINANCING_NOTE)
        _forecast_expander(forecast)


def _net_excl_lbsf_delta(amount: float) -> str:
    """Secondary line under Net. Same gray treatment as the other summary deltas."""
    return f"Net excl. LBSF: {format_money(amount)}"


def _forecast_metric(column, forecast: ExpenseForecast | None) -> None:
    """Month-end total-expense forecast. Last month's actual total sits under it in gray."""
    if forecast is None:
        column.metric("Month-end forecast", format_money(None), border=True)
        return
    metric: dict[str, object] = {"border": True}
    if forecast.last_month_actual is not None:
        metric["delta"] = f"Last month: {format_money(forecast.last_month_actual)}"
        metric["delta_color"] = "gray"
        metric["delta_arrow"] = "off"
    column.metric("Month-end forecast", format_money(forecast.total), **metric)


def _forecast_expander(forecast: ExpenseForecast) -> None:
    with st.expander("Charges flagged this month"):
        st.caption(
            "One-time charges and recurring bills are counted once. "
            "They are not spread over the rest of the month. "
            "Statement cards are included in the forecast total, not in this list."
        )
        _flag_block("One-time", forecast.one_time_charges)
        _flag_block("Recurring", forecast.recurring_charges)
        _flag_block("Bills still to come", forecast.expected_bills)


def _flag_block(title: str, charges: tuple) -> None:
    st.markdown(f"**{title}**")
    if not charges:
        st.caption("None")
        return
    for charge in charges:
        st.write(f"{charge.when.isoformat()} · {charge.merchant} · {format_money(charge.amount)}")


def _render_snapshot(
    balances: pd.DataFrame,
    deposits: pd.DataFrame,
    today: date,
    schedule: pd.DataFrame,
) -> None:
    left, right = st.columns(2)
    with left.container(border=True):
        st.markdown("**Debt snapshot**")
        cards, interest = st.columns(2)
        cards.metric("Credit card balances", format_money(credit_card_balance_total(balances)))
        snapshots = loan_snapshots(schedule, today)
        help_text = "Estimated from the current balance and APR, for this month through today."
        if snapshots:
            help_text = (
                "Estimated interest from the current balance and APR, "
                "plus scheduled interest for payment dates in this month."
            )
        interest.metric(
            "Est. loan interest this month",
            format_money(estimated_loan_interest_this_month(balances, today, schedule)),
            help=help_text,
        )
        for item in snapshots:
            st.write(snapshot_line(item))
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
    schedule: pd.DataFrame | None = None,
) -> None:
    st.subheader("Income and expenses")
    st.caption("Last 6 months of discretionary spending. The current month runs through today. Net is the line.")
    months = recent_month_periods(today)
    note = expense_coverage_note(months[0].start if months else None, available_from)
    if note:
        st.caption(note)
    frame = discretionary_history_frame(transactions, monthly, deposits, today)
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


def _ordered_by_amount(lines: Sequence[IncomeLine] | Sequence[ExpenseLine]) -> list:
    """Highest dollar amount first. Equal amounts keep their current order.

    Expense totals use the charge convention, where a larger number is a
    larger expense. The same descending order therefore lists the largest
    expense first. Totals and subtotals are not part of these line lists.
    """
    return sorted(lines, key=lambda line: line.total, reverse=True)


def _income_rows(lines: list[IncomeLine], period: Period, income: float) -> None:
    header = st.columns([3, 2])
    header[0].markdown("**Income description**")
    header[1].markdown("**Total received**")
    for line in _ordered_by_amount(lines):
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
    if loan_id_from_schedule_source(source_id) is not None:
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


def _discretionary_rows(
    lines: list[ExpenseLine],
    transactions: pd.DataFrame,
    period: Period,
) -> list[tuple[ExpenseLine, float]]:
    rows: list[tuple[ExpenseLine, float]] = []
    for line in lines:
        amount = discretionary_line_amount(line, transactions, period)
        if amount is None:
            continue
        rows.append((line, amount))
    return rows


def _category_trends(forecast: ExpenseForecast | None) -> None:
    st.markdown("**Categories**")
    st.caption(
        "This month, the forecast, last month, and the prior 3-month average. "
        "Up means the forecast is at least 20% above that average."
    )
    if forecast is None or not forecast.categories:
        st.caption("No discretionary spending in this month.")
        return
    for trend in forecast.categories:
        marker = " · Up" if trend.trending else ""
        label = (
            f"{trend.category}{marker}"
            f" · {format_money(trend.month_to_date)} now"
            f" · {format_money(trend.forecast)} forecast"
        )
        with st.expander(label):
            st.caption(
                f"Last month {format_money(trend.last_month)}"
                f" · 3-month average {format_money(trend.trailing_average)}"
            )
            if not trend.transactions:
                st.caption("No card transactions in this category this month.")
                continue
            for charge in trend.transactions:
                st.write(
                    f"{charge.when.isoformat()} · {charge.merchant} · {format_money(charge.amount)}"
                )


def _expense_rows(rows: list[tuple[ExpenseLine, float]], period: Period) -> None:
    if not rows:
        return
    header = st.columns([3, 2, 2])
    header[0].markdown("**Expense description**")
    header[1].markdown("**Total spend**")
    header[2].markdown("**Data through**")
    ordered = sorted(rows, key=lambda item: item[1], reverse=True)
    for line, amount in ordered:
        row = st.columns([3, 2, 2])
        label = loan_label_for(line)
        row[0].page_link(
            DETAIL_PAGE,
            label=label,
            query_params={"source": line.source_id, "period": period.key},
        )
        row[1].write(format_money(amount))
        row[2].write(line.data_through.isoformat() if line.data_through else "—")


def loan_label_for(line: ExpenseLine) -> str:
    if line.data_type == "schedule":
        return schedule_label(line.name, line.apr)
    if line.data_type == "loan" and line.apr is not None:
        return f"{line.name} ({line.apr:.2f}% APR)"
    return line.name


def _render_schedule_detail(
    line: ExpenseLine,
    schedule: pd.DataFrame,
    period: Period,
    today: date,
) -> None:
    loan_id = loan_id_from_schedule_source(line.source_id) or ""
    rows = rows_for_loan(schedule, loan_id)
    snapshots = [item for item in loan_snapshots(schedule, today) if item.source_id == line.source_id]
    if line.apr is not None:
        st.write(f"Rate {format_rate(line.apr)}%")
    current = snapshots[0] if snapshots else None
    if current is not None and current.collateral:
        st.caption(f"Collateral: {current.collateral}")
    if current is None:
        st.write(f"Remaining balance {format_money(None)}")
    else:
        st.write(f"Remaining balance {format_money(current.balance)}")
        if current.next_date is None or current.next_amount is None:
            st.write("No further scheduled payments.")
        else:
            st.write(
                f"Next payment {current.next_date.isoformat()} for {format_money(current.next_amount)}."
            )
    st.caption("Scheduled interest. Principal is not an expense.")
    st.write(f"Scheduled interest this period {format_money(line.total)}")
    payments = schedule_payment_table(rows, period)
    st.subheader("Schedule")
    if payments.empty:
        st.info("No scheduled payments in this period.")
        return
    show_money_table(payments, ["Payment", "Interest", "Principal", "Remaining Balance"])


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
            "Date": pd.to_datetime(frame["Date"], errors="coerce"),
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
        display["Statement Closing Date"] = pd.to_datetime(
            display["Statement Closing Date"], errors="coerce"
        )
    return display
