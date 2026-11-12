"""Canonical statement columns and source-kind rules.

Adding another card is a new Account value in the CSV. Adding an expense or
income stream is a Source Kind value (or a new entry in SOURCE_KINDS). The
statement column names stay stable so existing exports keep loading.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_ACCOUNT = "Nordstrom card ending 5488"

ACCOUNT_COL = "Account"
KIND_COL = "Source Kind"

MONTH_PATTERN = r"^\d{4}-\d{2}$"

# Prototype statement amounts. Missing columns are added as empty values so a
# later expense or income file can omit fields it does not have.
NUM_COLS = [
    "Previous Balance",
    "New Charges (Purchases)",
    "Payments",
    "Credits/Returns",
    "Net Spend",
    "Fees",
    "Interest",
    "Statement Balance",
    "Credit Limit",
    "Available Credit",
    "Minimum Payment Due",
]

# Present only when an income source is added. Not required for phase 1.
INCOME_COL = "Income"

DATE_COLS = ["Statement Closing Date", "Payment Due Date"]

TEXT_COLS = ["Year", "Month", *DATE_COLS]


@dataclass(frozen=True)
class SourceKind:
    """How a row participates in KPIs.

    Register a new kind here when a future source needs different totals.
    Unknown kinds still load and show in the table; they stay out of the
    card, spend, and income totals until they are registered.
    """

    id: str
    label: str
    include_in_spend: bool
    include_in_card_balance: bool
    include_in_income: bool


SOURCE_KINDS: dict[str, SourceKind] = {
    "credit_card": SourceKind(
        id="credit_card",
        label="Credit card",
        include_in_spend=True,
        include_in_card_balance=True,
        include_in_income=False,
    ),
    "expense": SourceKind(
        id="expense",
        label="Expense",
        include_in_spend=True,
        include_in_card_balance=False,
        include_in_income=False,
    ),
    "income": SourceKind(
        id="income",
        label="Income",
        include_in_spend=False,
        include_in_card_balance=False,
        include_in_income=True,
    ),
}

# Accepted CSV spellings. Blank becomes the phase-1 default, credit_card,
# so today's statement file works without a Source Kind column.
KIND_ALIASES = {
    "": "credit_card",
    "credit_card": "credit_card",
    "credit card": "credit_card",
    "card": "credit_card",
    "statement": "credit_card",
    "expense": "expense",
    "expenses": "expense",
    "income": "income",
}


def kind_spec(kind: str) -> SourceKind:
    return SOURCE_KINDS.get(
        kind,
        SourceKind(
            id=kind,
            label=kind.replace("_", " ").title() or "Unknown",
            include_in_spend=False,
            include_in_card_balance=False,
            include_in_income=False,
        ),
    )


def canonicalize_kind(value: object) -> str:
    if value is None:
        return "credit_card"
    text = str(value).strip().lower()
    if text in {"<na>", "nan", "none"}:
        return "credit_card"
    return KIND_ALIASES.get(text, text or "credit_card")
