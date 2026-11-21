"""Compact page title shared by every financial page.

Profit & Loss introduced this heading: a small line just under the toolbar,
instead of Streamlit's large ``st.title``. Every page calls
``render_page_title`` so the size, padding, and section-heading scale stay
in one place. The login screen does not call it.

An optional legend is drawn on that same line, immediately to the right of
the title. It does not add a row.
"""

from __future__ import annotations

import html
from collections.abc import Sequence

import streamlit as st

# The main block's default top padding is about 4.5rem. 3.9rem sits just
# under the app toolbar (3.75rem). The title is 1.125rem; section headings
# (st.subheader, an h3 at 1.75rem by default) are pulled down so the page
# title stays the largest heading.
_MARKUP = """
<style>
  div[data-testid="stMainBlockContainer"] {
    padding-top: 3.9rem;
  }
  .page-title {
    font-size: 1.125rem;
    font-weight: 600;
    line-height: 1.3;
    margin: 0;
    padding: 0;
  }
  div[data-testid="stElementContainer"]:has(.page-title) {
    margin-top: 0;
    margin-bottom: 0.25rem;
  }
  div[data-testid="stHeading"] h3 {
    font-size: 1rem;
    font-weight: 600;
    line-height: 1.3;
    padding: 0.5rem 0 0.3rem 0;
  }
  @media (max-width: 768px) {
    div[data-testid="stMainBlockContainer"] {
      padding-top: 3.9rem;
    }
    .page-title {
      font-size: 1rem;
    }
    div[data-testid="stHeading"] h3 {
      font-size: 0.9375rem;
    }
  }
</style>
<p class="page-title">__TITLE__</p>
"""

# Inserted only when a page passes a legend, so other pages keep the markup above.
_LEGEND_CSS = """
  .page-heading {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin: 0;
    padding: 0;
    flex-wrap: nowrap;
  }
  .page-legend {
    display: flex;
    align-items: center;
    gap: 0.7rem;
    margin: 0;
    padding: 0;
    font-size: 0.75rem;
    font-weight: 400;
    line-height: 1;
    white-space: nowrap;
    color: rgba(49, 51, 63, 0.8);
  }
  .page-legend .swatch {
    display: inline-block;
    width: 0.65rem;
    height: 0.65rem;
    margin-right: 0.28rem;
    border-radius: 2px;
    vertical-align: -1px;
    box-shadow: inset 0 0 0 1px rgba(49, 51, 63, 0.2);
  }
  @media (max-width: 768px) {
    .page-heading {
      gap: 0.45rem;
    }
    .page-legend {
      font-size: 0.6875rem;
      gap: 0.45rem;
    }
  }
"""


def render_page_title(title: str, legend: Sequence[tuple[str, str]] | None = None) -> None:
    """Render the compact page heading and the styles that keep it consistent.

    `legend` is (CSS color declaration, label) pairs. They sit on the title
    line, to its right. Omit it and the heading is the title alone.
    """
    escaped = html.escape(title, quote=True)
    if not legend:
        st.html(_MARKUP.replace("__TITLE__", escaped))
        return
    items = "".join(
        '<span class="legend-item">'
        f'<span class="swatch" style="{html.escape(color, quote=True)}"></span>'
        f"{html.escape(label)}</span>"
        for color, label in legend
    )
    heading = (
        '<div class="page-heading">'
        f'<p class="page-title">{escaped}</p>'
        f'<div class="page-legend">{items}</div>'
        "</div>"
    )
    markup = _MARKUP.replace("</style>", _LEGEND_CSS + "</style>", 1)
    st.html(markup.replace('<p class="page-title">__TITLE__</p>', heading))
