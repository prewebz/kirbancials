"""Password gates.

APP_PASSWORD locks every page. BALANCE_SHEET_PASSWORD locks the Balance
Sheet again after that. Both comparisons use hmac.compare_digest. A missing
password fails closed. The entered password is never stored or shown.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Mapping, MutableMapping

import streamlit as st

AUTHENTICATED = "authenticated"
BALANCE_SHEET_UNLOCKED = "balance_sheet_unlocked"
BALANCE_SHEET_PASSWORD_ENV = "BALANCE_SHEET_PASSWORD"
BALANCE_SHEET_PASSWORD_MISSING = "Balance Sheet password not set yet."
INCORRECT_PASSWORD = "Incorrect password."


def password_matches(provided: str, expected: str) -> bool:
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def balance_sheet_password(environ: Mapping[str, str] | None = None) -> str | None:
    """Return the balance sheet password, or None when it is unset or blank."""
    env = os.environ if environ is None else environ
    raw = env.get(BALANCE_SHEET_PASSWORD_ENV)
    if raw is None or str(raw).strip() == "":
        return None
    return str(raw)


def is_balance_sheet_unlocked(session: Mapping[str, object]) -> bool:
    return session.get(BALANCE_SHEET_UNLOCKED) is True


def balance_sheet_gate(session: MutableMapping[str, object], environ: Mapping[str, str] | None = None) -> str:
    """Return open, locked, or unconfigured.

    An unset password fails closed and clears a stale unlock. This does not
    read any finance file.
    """
    if balance_sheet_password(environ) is None:
        session[BALANCE_SHEET_UNLOCKED] = False
        return "unconfigured"
    if session.get(BALANCE_SHEET_UNLOCKED) is True:
        return "open"
    return "locked"


def attempt_balance_sheet_unlock(
    session: MutableMapping[str, object],
    provided: str,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Unlock on a match. The returned message never includes the password."""
    expected = balance_sheet_password(environ)
    if expected is None:
        session[BALANCE_SHEET_UNLOCKED] = False
        return BALANCE_SHEET_PASSWORD_MISSING
    if password_matches(provided, expected):
        session[BALANCE_SHEET_UNLOCKED] = True
        return None
    session[BALANCE_SHEET_UNLOCKED] = False
    return INCORRECT_PASSWORD


def lock_balance_sheet(session: MutableMapping[str, object]) -> None:
    session[BALANCE_SHEET_UNLOCKED] = False


def end_session(session: MutableMapping[str, object]) -> None:
    """Log out of the app and lock the balance sheet."""
    session[AUTHENTICATED] = False
    session[BALANCE_SHEET_UNLOCKED] = False


def require_login() -> None:
    """Stop the script until APP_PASSWORD matches. Fail closed when it is unset."""
    expected = os.environ.get("APP_PASSWORD")
    if not expected:
        st.error(
            "APP_PASSWORD is not set. The dashboard stays locked until a password is configured."
        )
        st.stop()
    if st.session_state.get(AUTHENTICATED) is True:
        return

    st.header("Log in")
    st.caption("Kirbancials")
    with st.form("login"):
        entered = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")
    if submitted:
        if password_matches(entered, expected):
            st.session_state[AUTHENTICATED] = True
            st.rerun()
        st.error(INCORRECT_PASSWORD)
    st.stop()
