"""Finance data layer for the Kirbancials dashboard.

Statement CSVs, daily card transactions, and balance snapshots each have their
own loader. New cards are new Account values. Income is a Source Kind on the
statement file. Transaction rows keep the Plaid sign: positive charges,
negative payments and credits.
"""

from kirbancials.activity import load_balances, load_transactions, resolve_balances, resolve_transactions
from kirbancials.data import FinanceData, load_dataset, resolve_origin, summarize
from kirbancials.formatting import format_money

__all__ = [
    "FinanceData",
    "format_money",
    "load_balances",
    "load_dataset",
    "load_transactions",
    "resolve_balances",
    "resolve_origin",
    "resolve_transactions",
    "summarize",
]
