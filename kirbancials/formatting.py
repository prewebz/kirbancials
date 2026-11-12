"""Money display helpers.

Metrics use format_money so negatives read -$3,820.06. Tables keep the raw
numbers and use Streamlit's "dollar" column format, which is currency display
with the minus sign before the dollar sign, and sorts by numeric value.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import pandas as pd

MISSING = "—"


def format_money(value: object) -> str:
    """Format a scalar amount. Missing values become an em dash."""
    if value is None:
        return MISSING
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "" or stripped == MISSING:
            return MISSING
        value = stripped
    try:
        if pd.isna(value):
            return MISSING
    except (TypeError, ValueError):
        return MISSING
    try:
        number = float(value)
    except (TypeError, ValueError):
        return MISSING
    if math.isnan(number) or math.isinf(number):
        return MISSING
    sign = "-" if number < 0 else ""
    return f"{sign}${abs(number):,.2f}"


def format_money_columns(frame: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    """Return a copy with the given numeric columns replaced by money text."""
    display = frame.copy()
    for column in columns:
        if column in display.columns:
            display[column] = display[column].map(format_money)
    return display


def money_column_config(columns: Sequence[str]) -> dict[str, object]:
    """Number column config so a dataframe sorts by dollar value."""
    import streamlit as st

    return {
        column: st.column_config.NumberColumn(format="dollar")
        for column in columns
    }


def show_money_table(frame: pd.DataFrame, money_columns: Sequence[str]) -> None:
    """Show a table with money columns left numeric and formatted as currency."""
    import streamlit as st

    display = frame.copy()
    present = [column for column in money_columns if column in display.columns]
    for column in present:
        display[column] = pd.to_numeric(display[column], errors="coerce")
    st.dataframe(
        display,
        width="stretch",
        hide_index=True,
        column_config=money_column_config(present),
    )
