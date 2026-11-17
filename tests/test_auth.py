"""Remembered login tokens and the refresh control.

Fixtures use fake passwords and secrets. They are not deployment credentials.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest import mock

from streamlit.testing.v1 import AppTest

from kirbancials.auth import (
    COOKIE_MAX_AGE_SECONDS,
    COOKIE_NAME,
    auth_cookie_script,
    claims_from_cookie,
    derive_key,
    issue_token,
    password_matches,
    remember_me_available,
    verify_token,
)
from kirbancials.refresh import format_last_refreshed, refresh_data

PASSWORD = "test-password"
SECRET = "test-cookie-secret"
NOW = 1_700_000_000

HARNESS = """
import streamlit as st

from kirbancials.auth import log_out, render_cookie_sync, require_login
from kirbancials.refresh import refresh_data, refreshed_caption

require_login()
st.write("DASHBOARD")
st.caption(refreshed_caption())
if st.button("Refresh data", key="refresh_sidebar"):
    refresh_data()
if st.button("Log out", key="log_out"):
    log_out()
with st.sidebar:
    render_cookie_sync()
"""


def _token(**kwargs: object) -> str:
    token = issue_token(PASSWORD, SECRET, now=NOW, **kwargs)  # type: ignore[arg-type]
    assert token is not None
    return token


class TokenTests(unittest.TestCase):
    def test_round_trip_hides_the_password_and_secret(self) -> None:
        token = _token()
        self.assertNotIn(PASSWORD, token)
        self.assertNotIn(SECRET, token)
        self.assertTrue(token.startswith("v1."))
        self.assertTrue(verify_token(token, PASSWORD, SECRET, now=NOW))
        self.assertEqual(claims_from_cookie(token, PASSWORD, SECRET, now=NOW), token)
        self.assertNotEqual(_token(), _token())

    def test_expiry_password_and_secret_are_checked(self) -> None:
        token = _token()
        self.assertTrue(verify_token(token, PASSWORD, SECRET, now=NOW + COOKIE_MAX_AGE_SECONDS))
        self.assertFalse(verify_token(token, PASSWORD, SECRET, now=NOW + COOKIE_MAX_AGE_SECONDS + 1))
        self.assertFalse(verify_token(token, "other-password", SECRET, now=NOW))
        self.assertFalse(verify_token(token, PASSWORD, "other-secret", now=NOW))
        self.assertIsNone(claims_from_cookie(token, "other-password", SECRET, now=NOW))
        self.assertIsNone(issue_token("", SECRET))
        self.assertIsNone(issue_token(PASSWORD, ""))
        self.assertFalse(verify_token(token, "", SECRET, now=NOW))
        self.assertFalse(verify_token(token, PASSWORD, "", now=NOW))

    def test_forged_and_tampered_tokens_are_rejected(self) -> None:
        token = _token()
        flipped = token[:-1] + ("a" if token[-1] != "a" else "b")
        self.assertFalse(verify_token(flipped, PASSWORD, SECRET, now=NOW))
        version, exp_text, nonce, signature = token.split(".")
        moved = ".".join([version, str(int(exp_text) + 10), nonce, signature])
        self.assertFalse(verify_token(moved, PASSWORD, SECRET, now=NOW))
        self.assertFalse(verify_token("v1.not-a-token", PASSWORD, SECRET, now=NOW))
        self.assertFalse(verify_token("", PASSWORD, SECRET, now=NOW))
        self.assertIsNone(claims_from_cookie(None, PASSWORD, SECRET, now=NOW))
        self.assertIsNone(claims_from_cookie(flipped, PASSWORD, SECRET, now=NOW))

    def test_changing_the_password_changes_the_key(self) -> None:
        self.assertNotEqual(derive_key(PASSWORD, SECRET), derive_key("other-password", SECRET))
        self.assertNotEqual(derive_key(PASSWORD, SECRET), derive_key(PASSWORD, "other-secret"))

    def test_cookie_script_sets_samesite_and_omits_secrets(self) -> None:
        token = _token()
        html = auth_cookie_script(value=token, max_age=COOKIE_MAX_AGE_SECONDS)
        self.assertIn("SameSite=Lax", html)
        self.assertIn("Path=/", html)
        self.assertIn(f'"maxAge": {COOKIE_MAX_AGE_SECONDS}', html)
        self.assertIn('protocol === "https:"', html)
        self.assertIn("; Secure", html)
        self.assertNotIn("HttpOnly", html)
        self.assertNotIn(PASSWORD, html)
        self.assertNotIn(SECRET, html)
        self.assertIn(COOKIE_NAME, html)
        cleared = auth_cookie_script(value="", max_age=0)
        self.assertIn('"maxAge": 0', cleared)
        self.assertIn("01 Jan 1970", cleared)
        self.assertNotIn(token, cleared)

    def test_password_compare_and_remember_me_flag(self) -> None:
        self.assertTrue(password_matches(PASSWORD, PASSWORD))
        self.assertFalse(password_matches("nope", PASSWORD))
        with mock.patch.dict("os.environ", {"APP_PASSWORD": PASSWORD, "AUTH_COOKIE_SECRET": SECRET}):
            self.assertTrue(remember_me_available())
        with mock.patch.dict("os.environ", {"APP_PASSWORD": PASSWORD, "AUTH_COOKIE_SECRET": "  "}, clear=False):
            self.assertFalse(remember_me_available())
        with mock.patch.dict("os.environ", {"APP_PASSWORD": "", "AUTH_COOKIE_SECRET": SECRET}, clear=False):
            self.assertFalse(remember_me_available())


class RefreshTests(unittest.TestCase):
    def test_caption_uses_phoenix_time(self) -> None:
        stamp = datetime(2026, 7, 4, 19, 30, 5, tzinfo=timezone.utc)
        self.assertEqual(format_last_refreshed(stamp), "2026-07-04 12:30:05 PM MST")

    def test_refresh_clears_caches_and_keeps_login(self) -> None:
        session = {
            "authenticated": True,
            "auth_token": "v1.token",
            "auth_logged_out": False,
        }
        with mock.patch("kirbancials.refresh.st") as fake_st:
            fake_st.session_state = session
            refresh_data()
            fake_st.cache_data.clear.assert_called_once()
            fake_st.cache_resource.clear.assert_called_once()
            fake_st.rerun.assert_called_once()
        self.assertIs(session["authenticated"], True)
        self.assertEqual(session["auth_token"], "v1.token")
        self.assertIs(session["auth_logged_out"], False)
        self.assertIn("data_refreshed_at", session)


class LoginFlowTests(unittest.TestCase):
    def test_login_refresh_and_logout_keep_or_drop_the_session(self) -> None:
        env = {"APP_PASSWORD": PASSWORD, "AUTH_COOKIE_SECRET": SECRET}
        with mock.patch.dict("os.environ", env, clear=False):
            app = AppTest.from_string(HARNESS, default_timeout=30)
            app.run()
            self.assertIn("Log in", [title.value for title in app.header])
            self.assertFalse(any(node.value == "DASHBOARD" for node in app.markdown))

            app.text_input(key="login_password").set_value("wrong-password")
            app.form_submit_button[0].click()
            app.run()
            self.assertTrue(any("Incorrect password." in node.value for node in app.error))
            self.assertIsNot(app.session_state.get("authenticated"), True)

            app.text_input(key="login_password").set_value(PASSWORD)
            app.form_submit_button[0].click()
            app.run()
            app.run()
            self.assertIs(app.session_state["authenticated"], True)
            self.assertTrue(any(node.value == "DASHBOARD" for node in app.markdown))
            self.assertTrue(any(node.value.startswith("Last refreshed:") for node in app.caption))
            token = app.session_state.get("auth_token")
            self.assertIsInstance(token, str)
            assert isinstance(token, str)
            self.assertNotIn(PASSWORD, token)
            self.assertTrue(verify_token(token, PASSWORD, SECRET))

            before = app.session_state["data_refreshed_at"]
            app.button(key="refresh_sidebar").click()
            app.run()
            app.run()
            self.assertIs(app.session_state["authenticated"], True)
            self.assertNotEqual(app.session_state["data_refreshed_at"], before)
            self.assertTrue(any(node.value == "DASHBOARD" for node in app.markdown))

            app.button(key="log_out").click()
            app.run()
            app.run()
            self.assertIs(app.session_state["authenticated"], False)
            self.assertIs(app.session_state["auth_logged_out"], True)
            self.assertNotIn("auth_token", app.session_state)
            self.assertIn("Log in", [title.value for title in app.header])
