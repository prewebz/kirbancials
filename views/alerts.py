"""Alerts page.

Login runs in app.py before this page, the same way it covers the other
financial pages.
"""

from kirbancials.alerts import ALERT_LEGEND, open_alert_count, render_alerts
from kirbancials.page_title import render_page_title

render_page_title("Alerts", legend=ALERT_LEGEND, badge=open_alert_count())
render_alerts()
