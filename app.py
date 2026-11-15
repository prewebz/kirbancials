"""Kirbancials entrypoint.

The password gate runs before every page. Profit & Loss is the landing page.
Balance Sheet is the next sidebar page.
"""

from __future__ import annotations

import streamlit as st

from kirbancials.auth import BALANCE_SHEET_UNLOCKED, end_session, lock_balance_sheet, require_login

st.set_page_config(page_title="Kirbancials", layout="wide")
require_login()

navigation = st.navigation(
    [
        st.Page("views/profit_and_loss.py", title="Profit & Loss", default=True),
        st.Page("views/balance_sheet.py", title="Balance Sheet", url_path="balance-sheet"),
        st.Page("views/source_detail.py", title="Source detail", url_path="source"),
        st.Page("views/daily.py", title="Daily spending", url_path="daily"),
        st.Page("views/statements.py", title="Monthly statements", url_path="statements"),
    ],
    position="hidden",
)

with st.sidebar:
    st.page_link("views/profit_and_loss.py", label="Profit & Loss")
    st.page_link("views/balance_sheet.py", label="Balance Sheet")
    st.page_link("views/daily.py", label="Daily spending")
    st.page_link("views/statements.py", label="Monthly statements")
    if st.session_state.get(BALANCE_SHEET_UNLOCKED) is True and st.button(
        "Lock balance sheet",
        key="lock_balance_sheet_sidebar",
    ):
        lock_balance_sheet(st.session_state)
        st.rerun()
    if st.button("Log out"):
        end_session(st.session_state)
        st.rerun()

navigation.run()
