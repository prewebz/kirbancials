"""Reload dashboard data without ending the login session."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

PHOENIX = ZoneInfo("America/Phoenix")
REFRESHED_AT_KEY = "data_refreshed_at"


def phoenix_now() -> datetime:
    return datetime.now(PHOENIX)


def format_last_refreshed(stamp: datetime) -> str:
    """Format a timestamp in America/Phoenix, which stays on MST all year."""
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=PHOENIX)
    else:
        stamp = stamp.astimezone(PHOENIX)
    return stamp.strftime("%Y-%m-%d %I:%M:%S %p %Z")


def refreshed_caption() -> str:
    stamp = st.session_state.get(REFRESHED_AT_KEY)
    if not isinstance(stamp, datetime):
        stamp = phoenix_now()
        st.session_state[REFRESHED_AT_KEY] = stamp
    return f"Last refreshed: {format_last_refreshed(stamp)}"


def clear_data_caches() -> None:
    """Drop cached data and resources so the next run reads env vars and files."""
    st.cache_data.clear()
    st.cache_resource.clear()


def refresh_data() -> None:
    """Clear caches, stamp the refresh, and rerun. Login session state stays."""
    clear_data_caches()
    st.session_state[REFRESHED_AT_KEY] = phoenix_now()
    st.rerun()
