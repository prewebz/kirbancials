"""VWH portfolio profit and loss."""

import streamlit as st

from kirbancials.vwh import render_vwh_pl

st.title("VWH P&L")
render_vwh_pl()
