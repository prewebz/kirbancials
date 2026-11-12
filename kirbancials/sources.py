"""Expense and income source registry.

Add a card by appending one ExpenseSource. Accounts listed here are owned by
that source. Any other Account value found in the data still becomes its own
profit-and-loss row.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExpenseSource:
    id: str
    name: str
    data_type: str  # "transactions" or "statements"
    accounts: tuple[str, ...]


@dataclass(frozen=True)
class IncomeSource:
    """Placeholder for a future income feed.

    Register a source here and teach `income_for_source` how to read it.
    The profit and loss page already sums every registered source.
    """

    id: str
    name: str


EXPENSE_SOURCES: tuple[ExpenseSource, ...] = (
    ExpenseSource(
        id="citi",
        name="Citibank - 7690",
        data_type="transactions",
        accounts=("Citi AAdvantage card ending 7690",),
    ),
    ExpenseSource(
        id="citi_platinum",
        name="Citibank Platinum - 3590",
        data_type="transactions",
        accounts=("Citi AAdvantage Platinum card ending 3590",),
    ),
    ExpenseSource(
        id="bofa",
        name="Bank of America - 5225",
        data_type="transactions",
        accounts=("Bank of America card ending 5225",),
    ),
    ExpenseSource(
        id="bofa_pw_corp_2238",
        name="Premium Web Corp - 2238",
        data_type="transactions",
        accounts=("Premium Web Corp card ending 2238",),
    ),
    ExpenseSource(
        id="bofa_pw_8330",
        name="Premium Web - 8330",
        data_type="transactions",
        accounts=("Premium Web card ending 8330",),
    ),
    ExpenseSource(
        id="bofa_vwh_6484",
        name="VWH - 6484",
        data_type="transactions",
        accounts=("VWH card ending 6484",),
    ),
    ExpenseSource(
        id="bofa_seinom_6315",
        name="Seinom LLC WD - 6315",
        data_type="transactions",
        accounts=("Seinom LLC WD card ending 6315",),
    ),
    ExpenseSource(
        id="capital_one",
        name="Capital One - 2391",
        data_type="transactions",
        accounts=("Capital One card ending 2391",),
    ),
    ExpenseSource(
        id="amex_gold",
        name="Amex Business Gold - 2003",
        data_type="transactions",
        accounts=("Amex Business Gold card ending 2003",),
    ),
    ExpenseSource(
        id="morgan_stanley",
        name="Morgan Stanley - 3008",
        data_type="transactions",
        accounts=("Morgan Stanley card ending 3008",),
    ),
    ExpenseSource(
        id="nordstrom",
        name="Nordstrom - 5488",
        data_type="statements",
        accounts=("Nordstrom card ending 5488",),
    ),
)

INCOME_SOURCES: tuple[IncomeSource, ...] = ()

UNMAPPED_PREFIX = "account:"


def expense_source_by_id(source_id: str) -> ExpenseSource | None:
    for source in EXPENSE_SOURCES:
        if source.id == source_id:
            return source
    return None


def expense_source_for_account(account: str) -> ExpenseSource | None:
    for source in EXPENSE_SOURCES:
        if account in source.accounts:
            return source
    return None


def mapped_accounts() -> set[str]:
    names: set[str] = set()
    for source in EXPENSE_SOURCES:
        names.update(source.accounts)
    return names


def unmapped_source_id(account: str) -> str:
    return f"{UNMAPPED_PREFIX}{account}"


def account_from_unmapped_id(source_id: str) -> str | None:
    if not source_id.startswith(UNMAPPED_PREFIX):
        return None
    account = source_id[len(UNMAPPED_PREFIX) :]
    return account or None
