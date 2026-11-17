"""Password gate shared by every page.

A successful login can set a 30-day cookie. The cookie holds an HMAC-SHA256
token, not the password. The signing key is derived from APP_PASSWORD and
AUTH_COOKIE_SECRET, so changing either one invalidates existing cookies.

The cookie is SameSite=Lax and Secure on HTTPS. It is not HttpOnly: Streamlit
checks the password inside the running session, and a browser only honors
HttpOnly on an HTTP Set-Cookie header. The page script that stores the token
cannot set that flag.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time

import streamlit as st

COOKIE_NAME = "kirbancials_auth"
COOKIE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
CLOCK_SKEW_SECONDS = 300
_TOKEN_RE = re.compile(r"^v1\.\d{1,12}\.[A-Za-z0-9_-]{1,128}\.[0-9a-f]{64}$")
_AUTHENTICATED = "authenticated"
_LOGGED_OUT = "auth_logged_out"
_AUTH_TOKEN = "auth_token"
_COOKIE_OP = "auth_cookie_op"


def password_matches(provided: str, expected: str) -> bool:
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def cookie_secret() -> str:
    return os.environ.get("AUTH_COOKIE_SECRET", "").strip()


def remember_me_available() -> bool:
    return bool(cookie_secret()) and bool(os.environ.get("APP_PASSWORD"))


def derive_key(password: str, secret: str) -> bytes:
    """HMAC-SHA256 key bound to the password and the server secret."""
    return hmac.new(
        secret.encode("utf-8"),
        b"kirbancials-auth-v1\0" + password.encode("utf-8"),
        hashlib.sha256,
    ).digest()


def issue_token(
    password: str,
    secret: str,
    *,
    now: int | None = None,
    nonce: str | None = None,
) -> str | None:
    """Return a signed token, or None when the password or secret is missing."""
    if not password or not secret:
        return None
    issued_at = int(time.time()) if now is None else int(now)
    expires_at = issued_at + COOKIE_MAX_AGE_SECONDS
    token_nonce = nonce if nonce is not None else secrets.token_urlsafe(16)
    payload = f"v1.{expires_at}.{token_nonce}"
    signature = hmac.new(derive_key(password, secret), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    token = f"{payload}.{signature}"
    if not _TOKEN_RE.fullmatch(token):
        return None
    return token


def verify_token(token: str, password: str, secret: str, *, now: int | None = None) -> bool:
    """True when the token signature matches and the expiry is still ahead."""
    if not password or not secret or not isinstance(token, str):
        return False
    if not _TOKEN_RE.fullmatch(token):
        return False
    version, exp_text, nonce, signature = token.split(".")
    if version != "v1":
        return False
    expires_at = int(exp_text)
    current = int(time.time()) if now is None else int(now)
    if expires_at < current:
        return False
    if expires_at > current + COOKIE_MAX_AGE_SECONDS + CLOCK_SKEW_SECONDS:
        return False
    payload = f"v1.{exp_text}.{nonce}"
    expected = hmac.new(derive_key(password, secret), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def claims_from_cookie(
    cookie: str | None,
    password: str,
    secret: str,
    *,
    now: int | None = None,
) -> str | None:
    """Return the cookie value when it should sign the user in."""
    if not cookie or not verify_token(cookie, password, secret, now=now):
        return None
    return cookie


def auth_cookie_script(*, value: str, max_age: int) -> str:
    """Page script that sets or clears the auth cookie. The password is not included."""
    spec = {"name": COOKIE_NAME, "value": value, "maxAge": int(max_age)}
    return f"""<script>
(function () {{
  var spec = {json.dumps(spec)};
  var protocol = window.location.protocol;
  var secure = protocol === "https:" ? "; Secure" : "";
  var extra = spec.maxAge <= 0 ? "; Expires=Thu, 01 Jan 1970 00:00:00 GMT" : "";
  document.cookie = spec.name + "=" + encodeURIComponent(spec.value)
    + "; Max-Age=" + String(spec.maxAge)
    + "; Path=/; SameSite=Lax" + secure + extra;
}})();
</script>
"""


def read_auth_cookie() -> str | None:
    try:
        cookies = st.context.cookies
    except Exception:
        return None
    raw = cookies.get(COOKIE_NAME) if hasattr(cookies, "get") else None
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    return value or None


def render_cookie_sync() -> None:
    """Write or clear the cookie in the browser. No visible content."""
    op = st.session_state.get(_COOKIE_OP)
    if op == "set":
        token = st.session_state.get(_AUTH_TOKEN)
        if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
            return
        html = auth_cookie_script(value=token, max_age=COOKIE_MAX_AGE_SECONDS)
    elif op == "clear":
        html = auth_cookie_script(value="", max_age=0)
    else:
        return
    st.html(html, unsafe_allow_javascript=True, width="content")


def log_out() -> None:
    st.session_state[_AUTHENTICATED] = False
    st.session_state[_LOGGED_OUT] = True
    st.session_state.pop(_AUTH_TOKEN, None)
    st.session_state[_COOKIE_OP] = "clear"
    st.rerun()


def _start_session(expected: str) -> None:
    st.session_state[_AUTHENTICATED] = True
    st.session_state[_LOGGED_OUT] = False
    token = issue_token(expected, cookie_secret())
    if token:
        st.session_state[_AUTH_TOKEN] = token
        st.session_state[_COOKIE_OP] = "set"
    else:
        st.session_state.pop(_AUTH_TOKEN, None)
        st.session_state[_COOKIE_OP] = None


def require_login() -> None:
    """Stop the script until APP_PASSWORD matches. Fail closed when it is unset."""
    expected = os.environ.get("APP_PASSWORD")
    if not expected:
        st.error(
            "APP_PASSWORD is not set. The dashboard stays locked until a password is configured."
        )
        st.stop()

    logged_out = st.session_state.get(_LOGGED_OUT) is True
    if st.session_state.get(_AUTHENTICATED) is True and not logged_out:
        return

    if logged_out:
        st.session_state[_COOKIE_OP] = "clear"
    else:
        cookie = read_auth_cookie()
        accepted = claims_from_cookie(cookie, expected, cookie_secret())
        if accepted:
            st.session_state[_AUTHENTICATED] = True
            st.session_state[_AUTH_TOKEN] = accepted
            st.session_state[_LOGGED_OUT] = False
            st.session_state[_COOKIE_OP] = "set"
            return
        if cookie:
            st.session_state[_COOKIE_OP] = "clear"

    st.header("Log in")
    st.caption("Kirbancials")
    with st.form("login"):
        entered = st.text_input("Password", type="password", key="login_password")
        submitted = st.form_submit_button("Log in")
    if submitted:
        if password_matches(entered, expected):
            _start_session(expected)
            st.rerun()
        st.error("Incorrect password.")
    render_cookie_sync()
    st.stop()
