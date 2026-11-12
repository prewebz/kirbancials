"""Password gate shared by every page."""

from __future__ import annotations

import hmac
import os

import streamlit as st


def password_matches(provided: str, expected: str) -> bool:
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def require_login() -> None:
    """Stop the script until APP_PASSWORD matches. Fail closed when it is unset."""
    expected = os.environ.get("APP_PASSWORD")
    if not expected:
        st.error(
            "APP_PASSWORD is not set. The dashboard stays locked until a password is configured."
        )
        st.stop()
    if st.session_state.get("authenticated") is True:
        return

    st.header("Log in")
    st.caption("Kirbancials")
    with st.form("login"):
        entered = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")
    if submitted:
        if password_matches(entered, expected):
            st.session_state["authenticated"] = True
            st.rerun()
        st.error("Incorrect password.")
    st.stop()
