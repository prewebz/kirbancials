"""Balance sheet page."""

from kirbancials.balance_sheet import render_balance_sheet
from kirbancials.page_title import render_page_title

render_page_title("Balance Sheet")
render_balance_sheet()
