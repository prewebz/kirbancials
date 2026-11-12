"""Daily transaction view."""

import streamlit as st

from kirbancials.dashboard import render_daily_page

st.title("Daily spending")
render_daily_page()
