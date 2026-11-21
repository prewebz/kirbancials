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


def percent_number(value: object) -> float:
    """Percent points from a number or a label such as '+5.6%'. Missing is NaN."""
    if value is None:
        return float("nan")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return float("nan")
        return number
    text = str(value).strip().replace("%", "").replace("+", "").replace(",", "")
    if text in {"", MISSING, "—"}:
        return float("nan")
    try:
        return float(text)
    except ValueError:
        return float("nan")


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


def format_signed_money(value: float) -> str:
    """Money text. Green when positive, red when negative, plain at zero."""
    rendered = format_money(value)
    if value > 0:
        return f":green[{rendered}]"
    if value < 0:
        return f":red[{rendered}]"
    return rendered


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


def percent_column_config(
    columns: Sequence[str],
    *,
    format: str = "%.2f%%",
) -> dict[str, object]:
    """Number column config so a percent column sorts by its numeric value."""
    import streamlit as st

    return {
        column: st.column_config.NumberColumn(format=format)
        for column in columns
    }


def _datetime_column_config(display: pd.DataFrame) -> dict[str, object]:
    """Date or datetime config for columns that are already real datetimes."""
    import streamlit as st

    config: dict[str, object] = {}
    for column in display.columns:
        series = display[column]
        if not pd.api.types.is_datetime64_any_dtype(series):
            continue
        present = series.dropna()
        has_clock_time = False
        if not present.empty:
            clock = pd.to_datetime(present)
            has_clock_time = bool(
                ((clock.dt.hour != 0) | (clock.dt.minute != 0) | (clock.dt.second != 0)).any()
            )
        if has_clock_time:
            config[column] = st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm")
        else:
            config[column] = st.column_config.DateColumn(format="YYYY-MM-DD")
    return config


_BOLD = "font-weight: 700"


def show_money_table(
    frame: pd.DataFrame,
    money_columns: Sequence[str],
    percent_columns: Sequence[str] = (),
    *,
    percent_format: str = "%.2f%%",
    row_styles: Sequence[str] | None = None,
    bold_columns: Sequence[str] = (),
) -> None:
    """Show a table whose money, percent, and date columns stay sortable values.

    Money and percent columns are coerced to float. Datetime columns stay
    datetimes. Streamlit formats them for display and sorts on the values.
    `row_styles`, when the same length as the frame, is one CSS style per row.
    An empty string leaves that row unstyled. Other callers omit it.
    `bold_columns` draws those values in bold and keeps any row color.
    """
    import streamlit as st

    display = frame.copy()
    money = [column for column in money_columns if column in display.columns]
    percents = [column for column in percent_columns if column in display.columns]
    for column in [*money, *percents]:
        display[column] = pd.to_numeric(display[column], errors="coerce")
    data: object = display
    bold = {column for column in bold_columns if column in display.columns}
    paint_rows = row_styles is not None and len(row_styles) == len(display)
    if paint_rows or bold:
        display = display.reset_index(drop=True)
        styles = list(row_styles) if paint_rows else []

        def paint(row: pd.Series) -> list[str]:
            base = ""
            if paint_rows and int(row.name) < len(styles):
                base = styles[int(row.name)] or ""
            painted: list[str] = []
            for column in row.index:
                parts = [base] if base else []
                if column in bold:
                    parts.append(_BOLD)
                painted.append("; ".join(parts))
            return painted

        data = display.style.apply(paint, axis=1)
    config = money_column_config(money)
    config.update(percent_column_config(percents, format=percent_format))
    config.update(_datetime_column_config(display))
    st.dataframe(
        data,
        width="stretch",
        hide_index=True,
        column_config=config,
    )
