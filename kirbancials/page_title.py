"""Compact page title shared by every financial page.

Profit & Loss introduced this heading: a small line just under the toolbar,
instead of Streamlit's large ``st.title``. Every page calls
``render_page_title`` so the size, padding, and section-heading scale stay
in one place. The login screen does not call it.
"""

from __future__ import annotations

import html

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


def render_page_title(title: str) -> None:
    """Render the compact page heading and the styles that keep it consistent."""
    st.html(_MARKUP.replace("__TITLE__", html.escape(title, quote=True)))
