"""Kirbancials entrypoint.

The password gate runs before every page. Profit & Loss is the landing page.
Balance Sheet is the next sidebar page. Bills follows Balance Sheet.
VWH P&L follows Bills.
"""

from __future__ import annotations

import streamlit as st

from kirbancials.auth import log_out, remember_me_available, render_cookie_sync, require_login
from kirbancials.refresh import refresh_data, refreshed_caption

st.set_page_config(page_title="Kirbancials", layout="wide")
require_login()

navigation = st.navigation(
    [
        st.Page("views/profit_and_loss.py", title="Profit & Loss", default=True),
        st.Page("views/balance_sheet.py", title="Balance Sheet", url_path="balance-sheet"),
        st.Page("views/bills.py", title="Bills", url_path="bills"),
        st.Page("views/vwh_pl.py", title="VWH P&L", url_path="vwh"),
        st.Page("views/source_detail.py", title="Source detail", url_path="source"),
        st.Page("views/daily.py", title="Daily spending", url_path="daily"),
        st.Page("views/statements.py", title="Monthly statements", url_path="statements"),
    ],
    position="hidden",
)

caption_col, refresh_col = st.columns([8, 1])
with caption_col:
    st.caption(refreshed_caption())
with refresh_col:
    if st.button("Refresh", key="refresh_header"):
        refresh_data()

with st.sidebar:
    if st.button("Refresh data", key="refresh_sidebar"):
        refresh_data()
    st.caption(refreshed_caption())
    st.page_link("views/profit_and_loss.py", label="Profit & Loss")
    st.page_link("views/balance_sheet.py", label="Balance Sheet")
    st.page_link("views/bills.py", label="Bills")
    st.page_link("views/vwh_pl.py", label="VWH P&L")
    st.page_link("views/daily.py", label="Daily spending")
    st.page_link("views/statements.py", label="Monthly statements")
    if not remember_me_available():
        st.caption("Remembered login is off until AUTH_COOKIE_SECRET is set.")
    if st.button("Log out", key="log_out"):
        log_out()
    render_cookie_sync()

navigation.run()
