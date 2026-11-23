"""Vehicles page.

Login runs in app.py before this page, the same way it covers the other
financial pages. Rows come from Category Vehicles on FINANCE_ASSETS_CSV.
"""

from kirbancials.page_title import render_page_title
from kirbancials.vehicles import render_vehicles

render_page_title("Vehicles")
render_vehicles()
