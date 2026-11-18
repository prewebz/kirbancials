"""Daily transaction view."""

from kirbancials.dashboard import render_daily_page
from kirbancials.page_title import render_page_title

render_page_title("Daily spending")
render_daily_page()
