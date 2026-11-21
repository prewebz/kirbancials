"""Finance alerts loaded from FINANCE_ALERTS_CSV.

The value is plain CSV text, the same pattern as FINANCE_BILLS_CSV. A missing
or blank variable leaves the page empty. A file that cannot be read shows a
warning and no rows. The warning does not include any cell from the file.
Real alerts stay out of git.

Dismiss on the page hides that alert for the Streamlit session only. The
daily sync is what marks an alert dismissed in the CSV for good.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import pandas as pd
import streamlit as st

from kirbancials.data import FinanceDataError

ALERTS_ENV = "FINANCE_ALERTS_CSV"
DISMISSED_KEY = "dismissed_alert_ids"
STRIP_LIMIT = 5

PAST_DUE = "past_due"
DUE_SOON = "due_soon"
WATCH = "watch"
INFO = "info"
OPEN = "open"
DISMISSED = "dismissed"

ALERT_COLUMNS = ("id", "type", "title", "detail", "date", "status")
ALERT_HEADER = ",".join(ALERT_COLUMNS)

NO_ALERTS = "No alerts"
DISMISS_NOTE = (
    "Dismiss hides an alert for this session. Permanent dismissal happens in the daily sync."
)

# (CSS color declaration for the title-line swatch, label). Same line as the title.
ALERT_LEGEND: tuple[tuple[str, str], ...] = (
    ("background-color: #c62828", "Past due"),
    ("background-color: #e6a000", "Due within 7 days"),
    ("background-color: #1565c0", "Watch"),
    ("background-color: #9e9e9e", "Info"),
)

_TYPE_LABELS: tuple[tuple[str, str], ...] = (
    (PAST_DUE, "Past due"),
    (DUE_SOON, "Due within 7 days"),
    (WATCH, "Watch"),
    (INFO, "Info"),
)
_TYPES = {kind for kind, _label in _TYPE_LABELS}
_URGENT = (PAST_DUE, DUE_SOON)
_STATUSES = {OPEN, DISMISSED}
_ICONS = {
    PAST_DUE: ":material/error:",
    DUE_SOON: ":material/schedule:",
    WATCH: ":material/visibility:",
    INFO: ":material/info:",
}

_MISSING_COLUMN = "The alerts CSV is missing a required column."
_UNREADABLE = "The alerts CSV has a row that could not be read."
_UNPARSED = "The alerts CSV could not be parsed."
_OMITTED = "The alerts CSV could not be read."

_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_ZERO_TIME = (" 00:00:00", "T00:00:00", "T00:00:00Z")
_MARKDOWN = ("\\", "$", "*", "_", "`", "[", "]")

# The marker sits in the row so the shell can be tinted. A style-only block
# is sent to Streamlit's event container and does not add a row.
_STYLES = """
<style>
  .alert-group {
    font-size: 0.7rem;
    font-weight: 650;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: rgba(49, 51, 63, 0.62);
    margin: 0.8rem 0 0.15rem 0;
    padding: 0;
  }
  .alert-group-count {
    margin-left: 0.4rem;
    letter-spacing: 0;
  }
  [data-testid="stElementContainer"]:has(.alert-marker) {
    display: none !important;
    height: 0 !important;
    min-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
  }
  [data-testid="stVerticalBlock"]:has(.alert-marker):not(:has([data-testid="stVerticalBlock"] .alert-marker)) {
    border-left-width: 4px;
    border-left-style: solid;
    border-radius: 6px;
    padding: 0.05rem 0.4rem 0.05rem 0.7rem;
    margin: 0.22rem 0;
  }
  [data-testid="stVerticalBlock"]:has(.alert-past_due):not(:has([data-testid="stVerticalBlock"] .alert-past_due)) {
    border-left-color: #c62828;
    background: rgba(198, 40, 40, 0.12);
  }
  [data-testid="stVerticalBlock"]:has(.alert-due_soon):not(:has([data-testid="stVerticalBlock"] .alert-due_soon)) {
    border-left-color: #e6a000;
    background: rgba(230, 160, 0, 0.18);
  }
  [data-testid="stVerticalBlock"]:has(.alert-watch):not(:has([data-testid="stVerticalBlock"] .alert-watch)) {
    border-left-color: #1565c0;
    background: rgba(21, 101, 192, 0.1);
  }
  [data-testid="stVerticalBlock"]:has(.alert-info):not(:has([data-testid="stVerticalBlock"] .alert-info)) {
    border-left-color: #9e9e9e;
    background: rgba(120, 120, 120, 0.12);
  }
  [data-testid="stVerticalBlock"]:has(.alert-marker):not(:has([data-testid="stVerticalBlock"] .alert-marker)) [data-testid="stMarkdown"] p {
    margin: 0;
  }
  [data-testid="stVerticalBlock"]:has(.alert-marker):not(:has([data-testid="stVerticalBlock"] .alert-marker)) [data-testid^="stBaseButton"] {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    padding: 0 0.1rem !important;
    min-height: 0 !important;
    color: rgba(49, 51, 63, 0.62) !important;
    font-size: 0.75rem !important;
    font-weight: 500 !important;
    text-decoration: underline;
  }
</style>
"""


@dataclass(frozen=True)
class AlertRecord:
    alert_id: str
    kind: str
    title: str
    detail: str
    day: date | None
    status: str


@dataclass(frozen=True)
class AlertLoad:
    """Parsed alerts, a safe warning, and whether the env var was set."""

    records: tuple[AlertRecord, ...]
    warning: str | None
    configured: bool


def resolve_alerts(environ: Mapping[str, str] | None = None) -> AlertLoad:
    """Return alerts from FINANCE_ALERTS_CSV. Blank means nothing is configured."""
    env = os.environ if environ is None else environ
    raw = env.get(ALERTS_ENV)
    if raw is None or str(raw).lstrip("\ufeff").strip() == "":
        return AlertLoad((), None, False)
    try:
        return AlertLoad(tuple(parse_alerts(str(raw))), None, True)
    except FinanceDataError as exc:
        return AlertLoad((), str(exc), True)
    except Exception:
        return AlertLoad((), _OMITTED, True)


def parse_alerts(text: str) -> list[AlertRecord]:
    """Parse alert CSV text. Raises FinanceDataError when the file is unusable."""
    try:
        frame = pd.read_csv(io.StringIO(text.lstrip("\ufeff")), dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError as exc:
        raise FinanceDataError(_UNPARSED) from exc
    except pd.errors.ParserError as exc:
        raise FinanceDataError(_UNPARSED) from exc
    frame.columns = [str(column).strip() for column in frame.columns]
    missing = [column for column in ALERT_COLUMNS if column not in frame.columns]
    if missing:
        raise FinanceDataError(_MISSING_COLUMN)
    records: list[AlertRecord] = []
    seen: set[str] = set()
    for raw in frame.to_dict("records"):
        if _blank_row(raw):
            continue
        parsed = _parse_record(raw)
        if parsed is None or parsed.alert_id in seen:
            raise FinanceDataError(_UNREADABLE)
        seen.add(parsed.alert_id)
        records.append(parsed)
    return records


def visible_alerts(
    records: Sequence[AlertRecord],
    dismissed: Sequence[str],
) -> tuple[AlertRecord, ...]:
    """Open alerts that have not been dismissed in this session. File order."""
    hidden = set(dismissed)
    return tuple(
        record
        for record in records
        if record.status == OPEN and record.alert_id not in hidden
    )


def grouped_alerts(
    records: Sequence[AlertRecord],
    dismissed: Sequence[str],
) -> tuple[tuple[str, str, tuple[AlertRecord, ...]], ...]:
    """Open alerts in Past due, Due within 7 days, Watch, Info order.

    Each group is sorted by date, then title. Empty groups are left out.
    """
    chosen = visible_alerts(records, dismissed)
    buckets: dict[str, list[AlertRecord]] = {kind: [] for kind, _label in _TYPE_LABELS}
    for record in chosen:
        buckets[record.kind].append(record)
    groups: list[tuple[str, str, tuple[AlertRecord, ...]]] = []
    for kind, label in _TYPE_LABELS:
        rows = tuple(sorted(buckets[kind], key=_sort_key))
        if rows:
            groups.append((kind, label, rows))
    return tuple(groups)


def strip_alerts(
    records: Sequence[AlertRecord],
    dismissed: Sequence[str],
    limit: int = STRIP_LIMIT,
) -> tuple[AlertRecord, ...]:
    """Open past-due and due-soon alerts, past due first, capped at `limit`."""
    hidden = set(dismissed)
    picked: list[AlertRecord] = []
    for kind in _URGENT:
        rows = [
            record
            for record in records
            if record.kind == kind and record.status == OPEN and record.alert_id not in hidden
        ]
        picked.extend(sorted(rows, key=_sort_key))
    if limit < 0:
        return tuple(picked)
    return tuple(picked[:limit])


def alerts_nav_label(count: int) -> str:
    """Sidebar label. The red count badge appears when an alert is open."""
    if count > 0:
        return f"Alerts :red-badge[{count}]"
    return "Alerts"


def open_alert_count() -> int:
    """Open alerts still visible in this session."""
    loaded = resolve_alerts()
    return len(visible_alerts(loaded.records, session_dismissed_ids()))


def session_dismissed_ids() -> frozenset[str]:
    """Alert ids hidden by Dismiss until this session ends."""
    raw = st.session_state.get(DISMISSED_KEY, ())
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(str(item) for item in raw)


def dismiss_alert(alert_id: str) -> None:
    """Hide one alert for the rest of this session."""
    current = set(session_dismissed_ids())
    current.add(alert_id)
    st.session_state[DISMISSED_KEY] = tuple(sorted(current))


def dismiss_key(alert_id: str) -> str:
    """Stable widget key. The id itself can contain characters keys dislike."""
    digest = hashlib.sha256(alert_id.encode("utf-8")).hexdigest()[:12]
    return f"dismiss_alert_{digest}"


def render_alerts() -> None:
    """Draw the alerts page. Login has already run in the entrypoint."""
    today = date.today()
    loaded = resolve_alerts()
    st.caption(f"As of {today.isoformat()}")
    if loaded.warning:
        st.warning(loaded.warning)
    groups = grouped_alerts(loaded.records, session_dismissed_ids())
    if not groups:
        st.info(NO_ALERTS)
        return
    st.caption(DISMISS_NOTE)
    _styles()
    for kind, label, rows in groups:
        count = len(rows)
        st.html(
            '<p class="alert-group">'
            f'<span class="alert-group-name">{_escape(label)}</span>'
            f'<span class="alert-group-count">{count}</span>'
            "</p>"
        )
        for record in rows:
            _render_row(record, dismiss=True)


def render_alert_strip() -> None:
    """Compact past-due and due-soon rows. Nothing when there are none."""
    loaded = resolve_alerts()
    if loaded.warning or not loaded.configured:
        return
    dismissed = session_dismissed_ids()
    urgent = strip_alerts(loaded.records, dismissed, limit=-1)
    rows = urgent[:STRIP_LIMIT]
    if not rows:
        return
    _styles()
    st.html('<p class="alert-group">Alerts</p>')
    for record in rows:
        _render_row(record, dismiss=False)
    extra = len(urgent) - len(rows)
    if extra:
        st.caption(f"{extra} more on Alerts")


def _render_row(record: AlertRecord, *, dismiss: bool) -> None:
    with st.container(gap="xxsmall"):
        st.html(f'<span class="alert-marker alert-{record.kind}"></span>')
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            st.markdown(_ICONS[record.kind], width="content")
            st.markdown(_plain(record.title), width="stretch")
            if record.detail:
                st.markdown(_plain(record.detail), text_alignment="right", width="content")
            if dismiss:
                st.button(
                    "Dismiss",
                    key=dismiss_key(record.alert_id),
                    type="tertiary",
                    on_click=dismiss_alert,
                    args=(record.alert_id,),
                )


def _styles() -> None:
    st.html(_STYLES)


def _sort_key(record: AlertRecord) -> tuple[bool, date, str, str]:
    return (record.day is None, record.day or date.min, record.title.casefold(), record.alert_id)


def _parse_record(raw: Mapping[str, object]) -> AlertRecord | None:
    alert_id = _required_text(raw.get("id"))
    title = _required_text(raw.get("title"))
    kind = _token(raw.get("type"))
    status = _token(raw.get("status"))
    day, day_ok = _optional_date(raw.get("date"))
    if alert_id is None or title is None or not day_ok:
        return None
    if kind not in _TYPES or status not in _STATUSES:
        return None
    return AlertRecord(
        alert_id=alert_id,
        kind=kind,
        title=title,
        detail=_optional_text(raw.get("detail")),
        day=day,
        status=status,
    )


def _blank_row(raw: Mapping[str, object]) -> bool:
    return all(_optional_text(raw.get(column)) == "" for column in ALERT_COLUMNS)


def _token(value: object) -> str:
    return _optional_text(value).casefold()


def _required_text(value: object) -> str | None:
    text = _optional_text(value)
    return text or None


def _optional_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        return str(value).strip()
    return str(value).strip()


def _optional_date(value: object) -> tuple[date | None, bool]:
    text = _optional_text(value)
    if text == "":
        return None, True
    for suffix in _ZERO_TIME:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    match = _ISO_DATE.fullmatch(text)
    if match is None:
        return None, False
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))), True
    except ValueError:
        return None, False


def _plain(text: str) -> str:
    """Show the cell as typed. Markdown and math markers stay literal."""
    escaped = text
    for char in _MARKDOWN:
        escaped = escaped.replace(char, "\\" + char)
    return escaped


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
