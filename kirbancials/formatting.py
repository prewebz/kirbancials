"""Money display helpers.

Negatives render as -$3,820.06. Streamlit's default number format puts the
minus sign after the dollar sign, so metrics and tables use this helper.
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
