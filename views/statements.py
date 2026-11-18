"""Monthly statement view."""

from kirbancials.dashboard import render_statements_page
from kirbancials.page_title import render_page_title

render_page_title("Monthly statements")
render_statements_page()
