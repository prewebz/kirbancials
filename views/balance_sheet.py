"""Balance sheet page."""

import streamlit as st

from kirbancials.balance_sheet import render_balance_sheet

st.title("Balance Sheet")
render_balance_sheet()
