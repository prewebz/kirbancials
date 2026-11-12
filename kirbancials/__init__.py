"""Finance data layer for the Kirbancials dashboard.

Statement CSVs stay the source of truth. Loaders normalize them into monthly
rows and period totals, tag an account and a source kind, and leave room for
more cards, expense sources, and income without changing the dashboard.
"""

from kirbancials.data import FinanceData, load_dataset, resolve_origin, summarize
from kirbancials.formatting import format_money

__all__ = [
    "FinanceData",
    "format_money",
    "load_dataset",
    "resolve_origin",
    "summarize",
]
