"""Kirbancials finance dashboard.

Login is required before any figures are shown. The password is APP_PASSWORD.
Statement data is read from the environment, a Render secret file, a path, or
the bundled fake sample. A sidebar upload overrides that choice for the
session and is not stored.
"""

from __future__ import annotations

import hmac
import os

import altair as alt
import pandas as pd
import streamlit as st

from kirbancials.data import (
    FinanceDataError,
    balance_series,
    charges_and_payments,
    filter_rows,
    load_dataset,
    resolve_origin,
    summarize,
)
from kirbancials.formatting import format_money, format_money_columns
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


def password_matches(provided: str, expected: str) -> bool:
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def require_login() -> None:
    expected = os.environ.get("APP_PASSWORD")
    if not expected:
        st.error(
            "APP_PASSWORD is not set. The dashboard stays locked until a password is configured."
        )
        st.stop()
    if st.session_state.get("authenticated") is True:
        return

    st.header("Log in")
    st.caption("Kirbancials Finance Dashboard")
    with st.form("login"):
        entered = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")
    if submitted:
        if password_matches(entered, expected):
            st.session_state["authenticated"] = True
            st.rerun()
        st.error("Incorrect password.")
    st.stop()


def load_current_dataset():
    uploaded = st.sidebar.file_uploader(
        "CSV upload (this session only)",
        type=["csv"],
        help="Replaces the configured source until you clear it or end the session. Nothing is written to disk.",
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


def render_dashboard() -> None:
    with st.sidebar:
        st.header("Kirbancials")
        if st.button("Log out"):
            st.session_state["authenticated"] = False
            st.rerun()

    data = load_current_dataset()

    with st.sidebar:
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

    st.title("Kirbancials Finance Dashboard")
    st.caption("Credit card statements and other expenses.")
    if data.is_sample:
        st.warning(
            "Showing bundled sample data. These are fake round numbers for local development, not real statements. "
            "Set FINANCE_DATA_CSV, add the Render secret file statement_summary_combined.csv, "
            "or set FINANCE_DATA_PATH to load your own CSV."
        )

    _render_metrics(snapshot)
    _render_charts(filtered.monthly)
    _render_monthly_table(filtered.monthly)
    _render_period_totals(filtered.period_totals)


def _multiselect(label: str, options: list[str], key: str, format_func=None) -> list[str]:
    """Keep a multiselect selection when the available options change.

    A session CSV upload can replace the account list. Streamlit rejects a
    stored selection that is not in the new options, so invalid entries are
    dropped. If that removes every entry, select the new options.
    """
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


def _render_charts(monthly: pd.DataFrame) -> None:
    balance, bars = st.columns(2)
    with balance:
        st.subheader("Statement balance by month")
        series = balance_series(monthly)
        if series.empty:
            st.info("No credit card statement balances in this filter.")
        else:
            encoding = {
                "x": alt.X("Date:T", title="Month", axis=alt.Axis(format="%b %Y")),
                "y": alt.Y("Statement Balance:Q", title="Statement balance"),
                "tooltip": [
                    alt.Tooltip("Month:N", title="Month"),
                    alt.Tooltip(f"{ACCOUNT_COL}:N", title="Account"),
                    alt.Tooltip("Balance label:N", title="Statement balance"),
                ],
            }
            if series[ACCOUNT_COL].nunique() > 1:
                encoding["color"] = alt.Color(f"{ACCOUNT_COL}:N", title="Account")
            chart = alt.Chart(series).mark_line(point=True).encode(**encoding).properties(height=320)
            _show_chart(chart)
    with bars:
        st.subheader("New charges vs payments")
        long = charges_and_payments(monthly)
        if long.empty:
            st.info("No charges or payments in this filter.")
        else:
            chart = (
                alt.Chart(long)
                .mark_bar()
                .encode(
                    x=alt.X("Date:T", title="Month", axis=alt.Axis(format="%b %Y")),
                    y=alt.Y("Amount:Q", title="Amount"),
                    color=alt.Color("Measure:N", title=None),
                    xOffset="Measure:N",
                    tooltip=[
                        alt.Tooltip("Date:T", title="Month", format="%b %Y"),
                        alt.Tooltip("Measure:N", title="Measure"),
                        alt.Tooltip("Amount label:N", title="Amount"),
                    ],
                )
                .properties(height=320)
            )
            _show_chart(chart)


def _show_chart(chart: alt.Chart) -> None:
    st.altair_chart(chart, width="stretch")


def _render_monthly_table(monthly: pd.DataFrame) -> None:
    st.subheader("Monthly detail")
    if monthly.empty:
        st.info("No monthly rows in this filter.")
        return
    table = monthly.sort_values(["Date", ACCOUNT_COL], ascending=[False, True], kind="mergesort")
    st.dataframe(_display_table(table), width="stretch", hide_index=True)


def _render_period_totals(periods: pd.DataFrame) -> None:
    with st.expander("Period totals"):
        st.caption("Rows whose Month is not YYYY-MM, such as quarter, year, year-to-date, and grand totals.")
        if periods.empty:
            st.info("No period totals in this filter.")
            return
        st.dataframe(_display_table(periods), width="stretch", hide_index=True)


def _display_table(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [column for column in DETAIL_COLUMNS if column in frame.columns]
    if INCOME_COL in frame.columns and frame[INCOME_COL].notna().any():
        columns.append(INCOME_COL)
    display = frame.loc[:, columns].copy()
    if KIND_COL in display.columns:
        display[KIND_COL] = display[KIND_COL].map(lambda kind: kind_spec(str(kind)).label)
    money = [column for column in (*NUM_COLS, INCOME_COL) if column in display.columns]
    display = format_money_columns(display, money)
    for column in display.columns:
        if pd.api.types.is_string_dtype(display[column]):
            display[column] = display[column].fillna("").astype(str).replace({"<NA>": ""})
    return display


st.set_page_config(
    page_title="Kirbancials Finance Dashboard",
    page_icon=":material/account_balance_wallet:",
    layout="wide",
)
require_login()
render_dashboard()
