"""Landing page.

The compact title comes from the shared helper, same as every other page.
"""

from kirbancials.page_title import render_page_title
from kirbancials.pnl import render_profit_and_loss

render_page_title("Profit & Loss")
render_profit_and_loss()
