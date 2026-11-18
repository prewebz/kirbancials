"""Landing page.

The title stays on this page only. Other pages still use st.title. The style
block is rendered with the page, so it leaves when you open another page.
"""

import streamlit as st

from kirbancials.pnl import render_profit_and_loss

# Sit just under the app toolbar (3.75rem) instead of the default 4.5rem
# block padding, and replace the large h1. The heading itself is about 1rem.
st.html(
    """
<style>
  div[data-testid="stMainBlockContainer"] {
    padding-top: 3.9rem;
  }
  .pnl-title {
    font-size: 1.125rem;
    font-weight: 600;
    line-height: 1.3;
    margin: 0;
    padding: 0;
  }
  div[data-testid="stElementContainer"]:has(.pnl-title) {
    margin-top: 0;
    margin-bottom: 0.25rem;
  }
  @media (max-width: 768px) {
    div[data-testid="stMainBlockContainer"] {
      padding-top: 3.9rem;
    }
    .pnl-title {
      font-size: 1rem;
    }
  }
</style>
<p class="pnl-title">Profit &amp; Loss</p>
"""
)
render_profit_and_loss()
