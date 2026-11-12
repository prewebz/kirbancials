"""Landing page."""

import streamlit as st

from kirbancials.pnl import render_profit_and_loss

st.title("Profit & Loss")
render_profit_and_loss()
