"""Bills page.

Login runs in app.py before this page, the same way it covers the other
financial pages.
"""

from kirbancials.bills import BILL_LEGEND, render_bills
from kirbancials.page_title import render_page_title

render_page_title("Bills", legend=BILL_LEGEND)
render_bills()
