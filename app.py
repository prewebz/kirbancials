"""Kirbancials entrypoint.

The password gate runs before every page. Profit & Loss is the landing page.
"""

from __future__ import annotations

import streamlit as st

from kirbancials.auth import require_login

st.set_page_config(page_title="Kirbancials", layout="wide")
require_login()

navigation = st.navigation(
    [
        st.Page("views/profit_and_loss.py", title="Profit & Loss", default=True),
        st.Page("views/source_detail.py", title="Source detail", url_path="source"),
        st.Page("views/daily.py", title="Daily spending", url_path="daily"),
        st.Page("views/statements.py", title="Monthly statements", url_path="statements"),
    ],
    position="hidden",
)

with st.sidebar:
    st.page_link("views/profit_and_loss.py", label="Profit & Loss")
    st.page_link("views/daily.py", label="Daily spending")
    st.page_link("views/statements.py", label="Monthly statements")
    if st.button("Log out"):
        st.session_state["authenticated"] = False
        st.rerun()

navigation.run()
