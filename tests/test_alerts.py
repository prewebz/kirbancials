"""Finance alerts.

Fixtures are fake titles and round dates. They are not account exports.
"""

from __future__ import annotations

import inspect
import os
import unittest
from datetime import date
from pathlib import Path

from streamlit.testing.v1 import AppTest

from kirbancials.alerts import (
    ALERT_HEADER,
    ALERT_LEGEND,
    ALERTS_ENV,
    DISMISS_NOTE,
    DUE_SOON,
    INFO,
    NO_ALERTS,
    PAST_DUE,
    STRIP_LIMIT,
    WATCH,
    AlertRecord,
    alerts_nav_label,
    dismiss_key,
    grouped_alerts,
    parse_alerts,
    resolve_alerts,
    strip_alerts,
    visible_alerts,
)
from kirbancials.data import FinanceDataError

ROOT = Path(__file__).resolve().parents[1]
HIDDEN = "HIDDEN-ACCOUNT-999"

FAKE = f"""{ALERT_HEADER}
rent,{PAST_DUE},Sample rent,due Nov 2,2026-11-02,open
card,{DUE_SOON},Sample card minimum,due Nov 18,2026-11-18,open
later,{DUE_SOON},Sample insurance,due Nov 20,2026-11-20,OPEN
tax,{PAST_DUE},Sample tax installment,14 days,2026-11-08,open
watch,{WATCH},Sample rate watch,21 days,2026-11-30,open
info,{INFO},Sample sync note,overnight,,open
old,{PAST_DUE},Sample paid already,paid,2026-11-01,dismissed
,,,,,
"""

# Seven urgent rows so the overview strip has to stop at five.
MANY = "\n".join(
    [
        ALERT_HEADER,
        *(
            f"p{index},{PAST_DUE},Sample past {index},due Nov {index},2026-11-0{index},open"
            for index in range(1, 4)
        ),
        *(
            f"d{index},{DUE_SOON},Sample soon {index},{index} days,2026-11-1{index},open"
            for index in range(1, 5)
        ),
        f"w1,{WATCH},Sample watch only,later,2026-12-01,open",
        f"i1,{INFO},Sample info only,note,2026-12-02,dismissed",
    ]
)


def _loaded(text: str = FAKE):
    loaded = resolve_alerts({ALERTS_ENV: "\ufeff" + text})
    assert loaded.warning is None, loaded.warning
    assert loaded.configured
    return loaded.records


class ParseTests(unittest.TestCase):
    def test_header_matches_the_readme_and_open_rows_group(self) -> None:
        readme = (ROOT / "README.md").read_text()
        self.assertEqual(ALERTS_ENV, "FINANCE_ALERTS_CSV")
        self.assertIn("`id,type,title,detail,date,status`", readme)
        records = _loaded()
        self.assertEqual(
            [record.alert_id for record in records],
            ["rent", "card", "later", "tax", "watch", "info", "old"],
        )
        self.assertEqual(records[2].status, "open")
        self.assertIsNone(records[5].day)
        self.assertEqual(records[5].detail, "overnight")
        groups = grouped_alerts(records, ())
        self.assertEqual([kind for kind, _label, _rows in groups], [PAST_DUE, DUE_SOON, WATCH, INFO])
        past, soon, watch, info = groups
        self.assertEqual(past[1], "Past due")
        self.assertEqual([row.alert_id for row in past[2]], ["rent", "tax"])
        self.assertEqual([row.alert_id for row in soon[2]], ["card", "later"])
        self.assertEqual(watch[2][0].alert_id, "watch")
        self.assertEqual(info[2][0].detail, "overnight")
        self.assertNotIn("old", [row.alert_id for _k, _n, rows in groups for row in rows])

    def test_blank_and_missing_env_are_not_configured(self) -> None:
        for text in (None, "", "   ", "\ufeff\n"):
            environ = {} if text is None else {ALERTS_ENV: text}
            loaded = resolve_alerts(environ)
            self.assertFalse(loaded.configured)
            self.assertIsNone(loaded.warning)
            self.assertEqual(loaded.records, ())
        self.assertEqual(resolve_alerts({}).configured, False)

    def test_session_dismiss_and_strip_keep_urgent_rows_only(self) -> None:
        records = _loaded()
        self.assertEqual(
            [row.alert_id for row in visible_alerts(records, ("tax",))],
            ["rent", "card", "later", "watch", "info"],
        )
        shown = strip_alerts(records, ("rent",))
        self.assertEqual([row.alert_id for row in shown], ["tax", "card", "later"])
        self.assertTrue(all(row.kind in {PAST_DUE, DUE_SOON} for row in shown))
        many = _loaded(MANY)
        capped = strip_alerts(many, ())
        self.assertEqual(len(capped), STRIP_LIMIT)
        self.assertEqual([row.kind for row in capped], [PAST_DUE, PAST_DUE, PAST_DUE, DUE_SOON, DUE_SOON])
        self.assertEqual(capped[0].alert_id, "p1")
        self.assertEqual(len(strip_alerts(many, (), limit=-1)), 7)

    def test_bad_rows_warn_without_the_cell(self) -> None:
        cases = [
            ALERT_HEADER.replace("title", "name") + "\nrent,past_due,Sample rent,due,2026-11-01,open\n",
            f"{ALERT_HEADER}\n{HIDDEN},nope,Sample rent,due,2026-11-01,open\n",
            f"{ALERT_HEADER}\nrent,past_due,,due,2026-11-01,open\n",
            f"{ALERT_HEADER}\nrent,past_due,Sample rent,due,2026-13-01,open\n",
            f"{ALERT_HEADER}\nrent,past_due,Sample rent,due,11/02/2026,open\n",
            f"{ALERT_HEADER}\nrent,past_due,Sample rent,due,2026-11-01,closed\n",
            f"{ALERT_HEADER}\nrent,past_due,Sample rent,due,2026-11-01,open\nrent,info,Again,x,2026-11-02,open\n",
            '"unterminated',
        ]
        for text in cases:
            loaded = resolve_alerts({ALERTS_ENV: text})
            self.assertTrue(loaded.configured)
            self.assertEqual(loaded.records, ())
            self.assertIsNotNone(loaded.warning)
            assert loaded.warning is not None
            self.assertNotIn(HIDDEN, loaded.warning)
            self.assertNotIn("Sample rent", loaded.warning)
        with self.assertRaises(FinanceDataError):
            parse_alerts(f"{ALERT_HEADER}\nrent,past_due,Sample rent,due,2026-11-01,closed\n")

    def test_midnight_timestamp_and_nav_badge(self) -> None:
        text = f"{ALERT_HEADER}\nrent,Past_Due,Sample rent,due Nov 2,2026-11-02T00:00:00,Open\n"
        records = _loaded(text)
        self.assertEqual(records[0].day, date(2026, 11, 2))
        self.assertEqual(records[0].kind, PAST_DUE)
        self.assertEqual(alerts_nav_label(0), "Alerts")
        self.assertEqual(alerts_nav_label(3), "Alerts :red-badge[3]")
        self.assertEqual(
            ALERT_LEGEND,
            (
                ("background-color: #c62828", "Past due"),
                ("background-color: #e6a000", "Due within 7 days"),
                ("background-color: #1565c0", "Watch"),
                ("background-color: #9e9e9e", "Info"),
            ),
        )


class RenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)

    def test_page_groups_open_rows_and_dismiss_hides_one(self) -> None:
        app = AppTest.from_string(_page(FAKE), default_timeout=30).run()
        self.assertEqual(list(app.exception), [])
        captions = [node.value for node in app.caption]
        self.assertTrue(any(line.startswith("As of ") for line in captions))
        self.assertIn(DISMISS_NOTE, captions)
        self.assertNotIn(NO_ALERTS, [node.value for node in app.info])
        html = "\n".join(node.value for node in app.html)
        self.assertIn("Past due", html)
        self.assertIn("Due within 7 days", html)
        self.assertIn("Watch", html)
        self.assertIn("Info", html)
        self.assertIn("text-transform: uppercase", html)
        self.assertIn("#c62828", html)
        self.assertIn("#e6a000", html)
        self.assertIn("#1565c0", html)
        self.assertIn("#9e9e9e", html)
        self.assertIn("alert-past_due", html)
        self.assertIn("border-left", html)
        titles = [node.value for node in app.markdown]
        self.assertIn("Sample rent", titles)
        self.assertIn("due Nov 2", titles)
        self.assertIn(":material/error:", titles)
        self.assertNotIn("Sample paid already", titles)
        self.assertEqual(len([button for button in app.button if button.label == "Dismiss"]), 6)
        self.assertNotIn(HIDDEN, html)
        self.assertNotIn(HIDDEN, "\n".join(titles))

        app.button(key=dismiss_key("rent")).click().run()
        self.assertEqual(list(app.exception), [])
        titles = [node.value for node in app.markdown]
        self.assertNotIn("Sample rent", titles)
        self.assertIn("Sample tax installment", titles)
        self.assertEqual(len([button for button in app.button if button.label == "Dismiss"]), 5)
        html = "\n".join(node.value for node in app.html)
        self.assertIn(
            '<span class="alert-group-name">Past due</span><span class="alert-group-count">1</span>',
            html,
        )

        for record in visible_alerts(_loaded(), ("rent",)):
            app.button(key=dismiss_key(record.alert_id)).click().run()
        self.assertEqual(list(app.exception), [])
        self.assertIn(NO_ALERTS, [node.value for node in app.info])
        self.assertNotIn(DISMISS_NOTE, [node.value for node in app.caption])
        self.assertEqual(list(app.button), [])

    def test_missing_env_says_no_alerts(self) -> None:
        script = """
import os
os.environ.pop("FINANCE_ALERTS_CSV", None)
from kirbancials.alerts import render_alerts
render_alerts()
"""
        app = AppTest.from_string(script, default_timeout=30).run()
        self.assertEqual(list(app.exception), [])
        self.assertIn(NO_ALERTS, [node.value for node in app.info])
        self.assertTrue(any(line.startswith("As of ") for line in (node.value for node in app.caption)))
        self.assertEqual(list(app.button), [])

    def test_bad_file_warns_without_the_cell(self) -> None:
        text = f"{ALERT_HEADER}\n{HIDDEN},past_due,Sample rent,due,not-a-date,open\n"
        app = AppTest.from_string(_page(text), default_timeout=30).run()
        self.assertEqual(list(app.exception), [])
        warnings = [node.value for node in app.warning]
        self.assertTrue(warnings)
        self.assertNotIn(HIDDEN, "\n".join(warnings))
        self.assertNotIn("Sample rent", "\n".join(warnings))
        self.assertIn(NO_ALERTS, [node.value for node in app.info])

    def test_strip_is_urgent_only_and_sits_on_the_overview(self) -> None:
        app = AppTest.from_string(_strip(MANY), default_timeout=30).run()
        self.assertEqual(list(app.exception), [])
        titles = [node.value for node in app.markdown]
        self.assertEqual(
            [title for title in titles if title.startswith("Sample")],
            ["Sample past 1", "Sample past 2", "Sample past 3", "Sample soon 1", "Sample soon 2"],
        )
        self.assertNotIn("Sample watch only", titles)
        self.assertNotIn("Sample info only", titles)
        self.assertEqual(list(app.button), [])
        captions = [node.value for node in app.caption]
        self.assertIn("2 more on Alerts", captions)
        html = "\n".join(node.value for node in app.html)
        self.assertIn(">Alerts<", html)

        quiet = AppTest.from_string(
            _strip(f"{ALERT_HEADER}\nw1,{WATCH},Sample watch only,later,2026-12-01,open\n"),
            default_timeout=30,
        ).run()
        self.assertEqual(list(quiet.exception), [])
        self.assertEqual(list(quiet.markdown), [])
        self.assertEqual(list(quiet.html), [])

        from kirbancials.pnl import render_profit_and_loss

        source = inspect.getsource(render_profit_and_loss)
        self.assertLess(source.index("render_alert_strip()"), source.index("transactions, monthly"))

    def test_title_badge_and_legend_share_one_line(self) -> None:
        script = """
from kirbancials.alerts import ALERT_LEGEND
from kirbancials.page_title import render_page_title

render_page_title("Alerts", legend=ALERT_LEGEND, badge=4)
"""
        app = AppTest.from_string(script, default_timeout=30).run()
        self.assertEqual(list(app.exception), [])
        body = app.html[0].value
        self.assertIn('class="page-heading"', body)
        self.assertIn('class="page-title">Alerts', body)
        self.assertIn('class="page-count page-count-open">4</span>', body)
        self.assertIn('class="page-legend"', body)
        self.assertLess(body.index("page-count"), body.index("page-legend"))
        self.assertLess(body.index('class="page-title"'), body.index('class="page-legend"'))
        for label in ("Past due", "Due within 7 days", "Watch", "Info"):
            self.assertIn(label, body)
        self.assertIn("flex-wrap: nowrap", body)
        zero = AppTest.from_string(
            'from kirbancials.page_title import render_page_title\nrender_page_title("Alerts", badge=0)\n',
            default_timeout=30,
        ).run()
        self.assertIn("page-count-zero", zero.html[0].value)
        self.assertNotIn("page-legend", zero.html[0].value)

    def test_alerts_follows_the_overview_and_login_covers_it(self) -> None:
        text = (ROOT / "app.py").read_text()
        pages = [line for line in text.splitlines() if "st.Page(" in line]
        self.assertIn("profit_and_loss.py", pages[0])
        self.assertIn("default=True", pages[0])
        self.assertIn("alerts.py", pages[1])
        self.assertIn('title="Alerts"', pages[1])
        self.assertIn('url_path="alerts"', pages[1])
        self.assertNotIn("default=True", pages[1])
        sidebar = text.split("with st.sidebar:", 1)[1]
        links = [line for line in sidebar.splitlines() if "st.page_link" in line]
        self.assertIn("profit_and_loss.py", links[0])
        self.assertIn("alerts.py", links[1])
        self.assertIn("alerts_nav_label", links[1])
        self.assertLess(text.index("require_login()"), text.index("navigation.run()"))
        view = (ROOT / "views" / "alerts.py").read_text()
        self.assertIn('render_page_title("Alerts", legend=ALERT_LEGEND, badge=open_alert_count())', view)
        self.assertIn("render_alerts", view)
        self.assertNotIn("require_login", view)
        self.assertNotIn("APP_PASSWORD", view)
        self.assertNotIn("<style>", view)
        self.assertNotIn(HIDDEN, view)
        self.assertNotIn(HIDDEN, (ROOT / "kirbancials" / "alerts.py").read_text())


def _page(text: str) -> str:
    return f"""
import os
os.environ[{ALERTS_ENV!r}] = {text!r}
from kirbancials.alerts import render_alerts
render_alerts()
"""


def _strip(text: str) -> str:
    return f"""
import os
os.environ[{ALERTS_ENV!r}] = {text!r}
from kirbancials.alerts import render_alert_strip
render_alert_strip()
"""


class RecordShapeTests(unittest.TestCase):
    def test_grouped_labels_are_the_legend_labels(self) -> None:
        record = AlertRecord("a", PAST_DUE, "Sample", "due Nov 28", date(2026, 11, 28), "open")
        groups = grouped_alerts((record,), ())
        self.assertEqual(groups[0][1], "Past due")
        self.assertEqual(dict(ALERT_LEGEND).get("background-color: #c62828"), "Past due")
