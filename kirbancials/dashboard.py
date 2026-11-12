"""Secondary pages: daily transactions and monthly statements.

The landing page is the profit and loss summary. These views stay available
from the sidebar.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from kirbancials.activity import (
    ALL_ACCOUNTS,
    BAL_ACCOUNT,
    BAL_AS_OF,
    BAL_AVAILABLE,
    BAL_CURRENT,
    BAL_LIMIT,
    TXN_ACCOUNT,
    TXN_AMOUNT,
    TXN_CATEGORY,
    TXN_DATE,
    TXN_MERCHANT,
    TXN_PENDING,
    daily_charges,
    filter_transactions,
    format_synced,
    load_balances,
    load_transactions,
    month_to_date_spend,
    resolve_balances,
    resolve_transactions,
)
from kirbancials.data import (
    FinanceDataError,
    balance_series,
    filter_rows,
    load_dataset,
    resolve_origin,
    summarize,
)
from kirbancials.formatting import format_money, show_money_table
from kirbancials.sources import loan_accounts
from kirbancials.schema import (
    ACCOUNT_COL,
    INCOME_COL,
    KIND_COL,
    NUM_COLS,
    kind_spec,
)

DETAIL_COLUMNS = [
    ACCOUNT_COL,
    KIND_COL,
    "Year",
    "Month",
    "Statement Closing Date",
    "Previous Balance",
    "New Charges (Purchases)",
    "Payments",
    "Credits/Returns",
    "Net Spend",
    "Fees",
    "Interest",
    "Statement Balance",
    "Credit Limit",
    "Available Credit",
    "Minimum Payment Due",
    "Payment Due Date",
]

BALANCE_MONEY = [BAL_CURRENT, BAL_AVAILABLE, BAL_LIMIT]


def load_current_dataset():
    uploaded = st.file_uploader(
        "Statement CSV upload (this session only)",
        type=["csv"],
        help="Replaces the configured statement file until you clear it or end the session. Nothing is written to disk.",
    )
    try:
        if uploaded is not None:
            return load_dataset(
                uploaded,
                label="uploaded CSV (this session only)",
                is_sample=False,
            )
        origin = resolve_origin()
        return load_dataset(origin.source, label=origin.label, is_sample=origin.is_sample)
    except FinanceDataError as exc:
        st.error(str(exc))
        st.stop()
    except Exception:
        st.error("The CSV could not be read.")
        st.stop()


def load_activity():
    txn_error = None
    bal_error = None
    try:
        transactions = load_transactions(resolve_transactions())
    except FinanceDataError as exc:
        transactions = load_transactions(None)
        txn_error = str(exc)
    try:
        balances = load_balances(resolve_balances())
    except FinanceDataError as exc:
        balances = load_balances(None)
        bal_error = str(exc)
    return transactions, balances, txn_error, bal_error


def render_daily_page() -> None:
    transactions, balances, txn_error, bal_error = load_activity()
    _render_daily(transactions, balances, txn_error, bal_error)


def render_statements_page() -> None:
    _render_monthly()


def _render_daily(transactions, balances, txn_error, bal_error) -> None:
    sample_bits = []
    if transactions.is_sample:
        sample_bits.append("transactions")
    if balances.is_sample:
        sample_bits.append("balances")
    if sample_bits:
        st.warning(
            "Showing bundled sample "
            + " and ".join(sample_bits)
            + ". These are fake round numbers, not your cards."
        )

    if bal_error:
        st.error(bal_error)
    synced = format_synced(balances.last_synced)
    if synced:
        st.caption(f"Data last synced: {synced}")

    st.subheader("Balances")
    if balances.rows.empty:
        st.info("No balance data yet.")
    else:
        show_money_table(_balance_table(balances.rows), BALANCE_MONEY)

    if txn_error:
        st.error(txn_error)
    if transactions.rows.empty:
        if not txn_error:
            st.info("No transaction data yet.")
        return

    today = date.today()
    st.subheader("Month-to-date spend")
    st.caption(
        f"{today:%B %Y}. Charges only. Payments and credits are excluded. Pending charges are included."
    )
    spend = month_to_date_spend(transactions.rows, today)
    if spend.empty:
        st.info("No charges this month.")
    else:
        show_money_table(_money_table(spend), ["Month-to-date spend", "Pending charges"])

    accounts = sorted(
        account
        for account in transactions.rows[TXN_ACCOUNT].dropna().astype(str).unique().tolist()
        if account not in loan_accounts()
    )
    if not accounts:
        st.info("No card charges in this filter.")
        return
    selected = _select_value("Account", [ALL_ACCOUNTS, *accounts], key="txn_account", fallback=accounts[0])
    daily = daily_charges(transactions.rows, selected, today)
    st.subheader("Daily spend")
    st.caption("Current month. Charges only.")
    if daily.empty:
        st.info("No charges this month for this account.")
    else:
        show_money_table(_money_table(daily), ["Total charges"])
        st.bar_chart(daily, x="Date", y="Total charges", x_label="Date", y_label="Total charges", sort=False)

    st.subheader("Transactions")
    dates = transactions.rows[TXN_DATE].dt.date
    start_default = min(dates)
    end_default = max(dates)
    picked = st.date_input("Date range", value=(start_default, end_default), key="txn_range")
    hide_credits = st.checkbox("Hide payments/credits", key="hide_credits")
    start, end = _range_bounds(picked)
    visible = filter_transactions(transactions.rows, selected, start, end, hide_credits)
    if visible.empty:
        st.info("No transactions in this filter.")
        return
    show_money_table(_transaction_table(visible), ["Amount"])


def _render_monthly() -> None:
    data = load_current_dataset()
    if data.is_sample:
        st.warning(
            "Showing bundled sample statements. These are fake round numbers, not your statements."
        )
    else:
        st.caption(f"Source: {data.source_label}")

    accounts = data.accounts
    kinds = data.kinds
    selected_accounts = _multiselect("Accounts", accounts, key="accounts")
    year_options = ["All", *sorted(data.years, reverse=True)]
    selected_year = _select_value("Year", year_options, key="year", fallback="All")
    if len(kinds) > 1:
        selected_kinds = _multiselect(
            "Source kinds",
            kinds,
            key="kinds",
            format_func=lambda kind: kind_spec(kind).label,
        )
    else:
        selected_kinds = kinds

    filtered = filter_rows(data, selected_accounts, selected_year, selected_kinds)
    snapshot = summarize(filtered.monthly)
    _render_metrics(snapshot)
    _render_balance_chart(filtered.monthly)
    _render_monthly_table(filtered.monthly)
    _render_period_totals(filtered.period_totals)


def _multiselect(label: str, options: list[str], key: str, format_func=None) -> list[str]:
    valid = set(options)
    if key not in st.session_state:
        st.session_state[key] = list(options)
    else:
        previous = list(st.session_state[key])
        kept = [item for item in previous if item in valid]
        if not kept and kept != previous:
            kept = list(options)
        st.session_state[key] = kept
    kwargs = {"format_func": format_func} if format_func is not None else {}
    return st.multiselect(label, options, key=key, **kwargs)


def _select_value(label: str, options: list[str], key: str, fallback: str) -> str:
    if not options:
        return fallback
    if key not in st.session_state or st.session_state[key] not in options:
        st.session_state[key] = fallback if fallback in options else options[0]
    return st.selectbox(label, options, key=key)


def _render_metrics(snapshot) -> None:
    labels = [
        (
            "Latest statement balance",
            snapshot.latest_statement_balance,
            "Sum of each credit card's most recent statement balance in this filter.",
        ),
        ("Total net spend", snapshot.total_net_spend, None),
        ("Total payments", snapshot.total_payments, None),
        ("Interest + fees", snapshot.interest_and_fees, None),
    ]
    if snapshot.total_income is not None:
        labels.append(("Income", snapshot.total_income, "Income rows are kept out of the spend totals."))
    columns = st.columns(len(labels))
    for column, (label, value, help_text) in zip(columns, labels):
        column.metric(label, format_money(value), help=help_text)


def _render_balance_chart(monthly: pd.DataFrame) -> None:
    st.subheader("Statement balance by month")
    series = balance_series(monthly)
    if series.empty:
        st.info("No credit card statement balances in this filter.")
        return
    chart = series.loc[:, ["Date", ACCOUNT_COL, "Statement Balance"]]
    if chart[ACCOUNT_COL].nunique() > 1:
        st.line_chart(
            chart,
            x="Date",
            y="Statement Balance",
            color=ACCOUNT_COL,
            x_label="Month",
            y_label="Statement balance",
        )
    else:
        st.line_chart(
            chart,
            x="Date",
            y="Statement Balance",
            x_label="Month",
            y_label="Statement balance",
        )


def _render_monthly_table(monthly: pd.DataFrame) -> None:
    st.subheader("Monthly detail")
    if monthly.empty:
        st.info("No monthly rows in this filter.")
        return
    table = monthly.sort_values(["Date", ACCOUNT_COL], ascending=[False, True], kind="mergesort")
    show_money_table(_display_table(table), [*NUM_COLS, INCOME_COL])


def _render_period_totals(periods: pd.DataFrame) -> None:
    with st.expander("Period totals"):
        st.caption("Rows whose Month is not YYYY-MM, such as quarter, year, year-to-date, and grand totals.")
        if periods.empty:
            st.info("No period totals in this filter.")
            return
        show_money_table(_display_table(periods), [*NUM_COLS, INCOME_COL])


def _display_table(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [column for column in DETAIL_COLUMNS if column in frame.columns]
    if INCOME_COL in frame.columns and frame[INCOME_COL].notna().any():
        columns.append(INCOME_COL)
    display = frame.loc[:, columns].copy()
    if KIND_COL in display.columns:
        display[KIND_COL] = display[KIND_COL].map(lambda kind: kind_spec(str(kind)).label)
    return _clean_text(display)


def _balance_table(frame: pd.DataFrame) -> pd.DataFrame:
    display = frame.copy()
    display[BAL_AS_OF] = display[BAL_AS_OF].map(lambda value: format_synced(value) or "")
    display = display.rename(columns={BAL_ACCOUNT: "Account"})
    return _clean_text(display)


def _transaction_table(frame: pd.DataFrame) -> pd.DataFrame:
    display = pd.DataFrame(
        {
            "Date": frame[TXN_DATE].dt.strftime("%Y-%m-%d"),
            "Account": frame[TXN_ACCOUNT],
            "Merchant": frame[TXN_MERCHANT],
            "Amount": frame[TXN_AMOUNT],
            "Pending": frame[TXN_PENDING].map(lambda pending: "Pending" if pending else "Posted"),
            "Category": frame[TXN_CATEGORY],
        }
    )
    return _clean_text(display)


def _money_table(frame: pd.DataFrame) -> pd.DataFrame:
    display = frame.copy()
    if "Date" in display.columns:
        display["Date"] = pd.to_datetime(display["Date"]).dt.strftime("%Y-%m-%d")
    return _clean_text(display)


def _clean_text(frame: pd.DataFrame) -> pd.DataFrame:
    display = frame.copy()
    for column in display.columns:
        if pd.api.types.is_string_dtype(display[column]) or display[column].dtype == object:
            display[column] = display[column].map(_blank_text)
    return display


def _blank_text(value: object) -> object:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    text = str(value)
    if text in {"<NA>", "NaT", "nan", "None"}:
        return ""
    return text


def _range_bounds(picked) -> tuple[date | None, date | None]:
    if isinstance(picked, (tuple, list)) and len(picked) == 2 and all(item is not None for item in picked):
        return picked[0], picked[1]
    return None, None
