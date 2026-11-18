"""Bills page.

Login runs in app.py before this page, the same way it covers the other
financial pages.
"""

import streamlit as st

from kirbancials.bills import render_bills

st.title("Bills")
render_bills()
