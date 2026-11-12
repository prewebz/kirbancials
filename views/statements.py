"""Monthly statement view."""

import streamlit as st

from kirbancials.dashboard import render_statements_page

st.title("Monthly statements")
render_statements_page()
