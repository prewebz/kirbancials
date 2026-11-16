"""Balance sheet totals, asset cards, and grouped tables.

Assets come from FINANCE_ASSETS_CSV. Cash, other loans, and credit cards
come from the balance file. Mortgages come from the loan schedule's current
remaining balance. Trailing-12-month income and loan payments reuse the
income and schedule helpers. A missing asset file still produces cash and
liabilities.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import pandas as pd
import streamlit as st

from kirbancials.activity import (
    account_balance_kind,
    latest_account_balances,
    load_balances,
    resolve_balances,
)
from kirbancials.assets import NO_ASSETS_NOTE, AssetLoad, AssetRecord, resolve_assets
from kirbancials.data import FinanceDataError
from kirbancials.formatting import format_money, format_signed_money
from kirbancials.income import load_income, received_for_source, resolve_income
from kirbancials.periods import Period, resolve_period
from kirbancials.schedules import (
    loan_snapshots,
    loan_sources,
    remaining_balance,
    resolve_loan_schedule,
    rows_for_loan,
    scheduled_balance_total,
    scheduled_payments,
)
from kirbancials.sources import (
    expense_source_for_account,
    income_source_by_id,
    loan_id_from_schedule_source,
)

CASH_GROUP = "Cash"
MORTGAGE_GROUP = "Mortgages / property loans"
OTHER_LOAN_GROUP = "Other loans"
CREDIT_GROUP = "Credit cards"
TOTAL_GROUP = "Total"

VALUATION_NOTE = (
    "Market values are entered manually from appraisals; update when a new valuation is available."
)
CASH_NOTE = "No checking or savings accounts are labeled in the balance file."
NA = "n/a"


@dataclass(frozen=True)
class AssetPosition:
    """One asset card. None means an input is missing and the page shows n/a."""

    asset_id: str
    name: str
    category: str
    acquired: date | None
    notes: str
    market_value: float | None
    valuation_date: date | None
    valuation_source: str
    cost_basis: float | None
    gain_loss: float | None
    loan_linked: bool
    loan_found: bool
    loan_name: str
    loan_balance: float | None
    equity: float | None
    ltv: float | None
    income_source_id: str
    income_name: str
    t12_income: float | None
    yield_on_value: float | None
    t12_loan_payments: float | None
    cash_on_equity: float | None


@dataclass(frozen=True)
class SheetRow:
    group: str
    item: str
    amount: float | None
    role: str


@dataclass(frozen=True)
class BalanceSheet:
    as_of: date
    assets_configured: bool
    total_assets: float
    total_liabilities: float
    net_worth: float
    positions: tuple[AssetPosition, ...]
    asset_rows: tuple[SheetRow, ...]
    liability_rows: tuple[SheetRow, ...]


def show_money(value: float | None) -> str:
    """Money for the balance sheet. A missing input is the text n/a."""
    if value is None:
        return NA
    return format_money(value)


def show_percent(value: float | None) -> str:
    """A ratio as a percent. A missing input is the text n/a."""
    if value is None:
        return NA
    return f"{value * 100:.2f}%"


def build_balance_sheet(
    assets: Sequence[AssetRecord],
    balances: pd.DataFrame | None,
    schedule: pd.DataFrame | None,
    deposits: pd.DataFrame | None,
    today: date,
    *,
    assets_configured: bool,
    income_configured: bool,
) -> BalanceSheet:
    """Assemble totals and both tables for `today`.

    A balance row whose account is a scheduled loan's Loan ID, or that
    loan's `Lender - Loan Name`, is left out. The schedule's remaining
    balance is the liability.
    """
    period = resolve_period("last-12-months", today)
    identities = _scheduled_identity_keys(schedule)
    cash, other_loans, cards = _split_balances(balances, identities)
    positions = tuple(
        _position(asset, schedule, deposits, today, period, income_configured) for asset in assets
    )
    cash_total = _sum_amounts(cash)
    market_total = _sum_known(position.market_value for position in positions)
    mortgage_total = scheduled_balance_total(loan_snapshots(schedule, today))
    total_assets = cash_total + market_total
    total_liabilities = mortgage_total + _sum_amounts(other_loans) + _sum_amounts(cards)
    asset_rows = _asset_rows(cash, positions, total_assets)
    liability_rows = _liability_rows(schedule, today, other_loans, cards, total_liabilities)
    return BalanceSheet(
        as_of=today,
        assets_configured=assets_configured,
        total_assets=total_assets,
        total_liabilities=total_liabilities,
        net_worth=total_assets - total_liabilities,
        positions=positions,
        asset_rows=tuple(asset_rows),
        liability_rows=tuple(liability_rows),
    )


def render_balance_sheet() -> None:
    """Draw the balance sheet. Login has already run in the entrypoint."""
    today = date.today()
    asset_load = resolve_assets()
    balances, balance_error, balance_sample = _load_balance_rows()
    deposits, income_error, income_configured = _load_deposits()
    schedule, schedule_warning = resolve_loan_schedule()
    sheet = build_balance_sheet(
        asset_load.assets,
        balances,
        schedule,
        deposits,
        today,
        assets_configured=asset_load.configured,
        income_configured=income_configured,
    )
    _render(sheet, asset_load, balance_error, balance_sample, income_error, schedule_warning, balances)


def _render(
    sheet: BalanceSheet,
    asset_load: AssetLoad,
    balance_error: str | None,
    balance_sample: bool,
    income_error: str | None,
    schedule_warning: str | None,
    balances: pd.DataFrame,
) -> None:
    st.caption(f"As of {sheet.as_of.isoformat()}")
    if asset_load.warning:
        st.warning(asset_load.warning)
    if balance_sample:
        st.warning("Showing bundled sample balances. These are fake round numbers, not your accounts.")
    if balance_error:
        st.error(balance_error)
    if income_error:
        st.error(income_error)
    if schedule_warning:
        st.warning(schedule_warning)

    columns = st.columns(3)
    columns[0].metric("Total assets", show_money(sheet.total_assets), border=True)
    columns[1].metric("Total liabilities", show_money(sheet.total_liabilities), border=True)
    columns[2].metric("Net worth", format_signed_money(sheet.net_worth), border=True)
    if not sheet.assets_configured:
        st.caption(NO_ASSETS_NOTE)

    st.subheader("Real Estate / investments")
    st.caption(
        "Equity is the market value minus the linked loan. "
        "LTV is the loan divided by the market value. "
        "Yield is trailing-12-month income divided by the market value. "
        "Cash on equity is that income, minus trailing-12-month loan payments, divided by equity."
    )
    if sheet.positions:
        for position in sheet.positions:
            _render_position(position)
    elif sheet.assets_configured and asset_load.warning is None:
        st.caption("No assets listed.")

    st.subheader("Assets")
    _show_sheet(sheet.asset_rows)
    if balances.empty:
        st.caption("No balance data yet.")
    elif _group_items(sheet.asset_rows, CASH_GROUP) == ():
        st.caption(CASH_NOTE)

    st.subheader("Liabilities")
    _show_sheet(sheet.liability_rows, amount_label="Balance")
    st.caption(VALUATION_NOTE)


def _render_position(position: AssetPosition) -> None:
    with st.container(border=True):
        st.markdown(f"**{position.name}**")
        st.caption(position.category)
        details = []
        if position.acquired is not None:
            details.append(f"Acquired {position.acquired.isoformat()}")
        if position.notes:
            details.append(position.notes)
        if details:
            st.caption(" · ".join(details))
        valued = position.valuation_date.isoformat() if position.valuation_date is not None else NA
        source = position.valuation_source or NA
        first = st.columns(3)
        first[0].metric(
            "Market value",
            show_money(position.market_value),
            delta=f"{valued} · {source}",
            delta_color="off",
            delta_arrow="off",
        )
        first[1].metric("Cost basis", show_money(position.cost_basis))
        first[2].metric("Gain / loss", show_money(position.gain_loss))
        second = st.columns(3)
        loan_delta = {}
        if position.loan_name:
            loan_delta = {"delta": position.loan_name, "delta_color": "off", "delta_arrow": "off"}
        second[0].metric("Linked loan", show_money(position.loan_balance), **loan_delta)
        second[1].metric("Equity", show_money(position.equity))
        second[2].metric("LTV", show_percent(position.ltv))
        if position.loan_linked and not position.loan_found:
            st.caption("That linked loan is not in the loan schedule.")
        if position.income_source_id:
            st.caption(position.income_name or position.income_source_id)
            third = st.columns(3)
            third[0].metric("Trailing 12-month income", show_money(position.t12_income))
            third[1].metric("Yield on value", show_percent(position.yield_on_value))
            third[2].metric("Cash on equity", show_percent(position.cash_on_equity))


def _show_sheet(rows: Sequence[SheetRow], amount_label: str = "Amount") -> None:
    frame = pd.DataFrame(
        {
            "Category": [row.group for row in rows],
            "Item": [row.item for row in rows],
            amount_label: [show_money(row.amount) for row in rows],
        }
    )
    st.dataframe(frame, width="stretch", hide_index=True)


def _load_balance_rows() -> tuple[pd.DataFrame, str | None, bool]:
    try:
        loaded = load_balances(resolve_balances())
    except FinanceDataError as exc:
        return pd.DataFrame(), str(exc), False
    return loaded.rows, None, loaded.is_sample


def _load_deposits() -> tuple[pd.DataFrame, str | None, bool]:
    origin = resolve_income()
    if origin is None:
        return load_income(None), None, False
    try:
        return load_income(origin), None, True
    except FinanceDataError as exc:
        return load_income(None), str(exc), False


def _position(
    asset: AssetRecord,
    schedule: pd.DataFrame | None,
    deposits: pd.DataFrame | None,
    today: date,
    period: Period,
    income_configured: bool,
) -> AssetPosition:
    loan_linked = asset.linked_loan_id != ""
    loan_name = ""
    loan_balance: float | None = None
    loan_found = False
    t12_payments: float | None
    if not loan_linked:
        t12_payments = 0.0
    else:
        rows = rows_for_loan(schedule, asset.linked_loan_id)
        loan_name = _loan_display_name(schedule, asset.linked_loan_id)
        if rows.empty:
            t12_payments = None
        else:
            loan_balance = remaining_balance(rows, today)
            loan_found = loan_balance is not None
            t12_payments = scheduled_payments(rows, period)
    if asset.market_value is None:
        equity = None
    elif loan_linked and not loan_found:
        equity = None
    elif loan_linked:
        equity = asset.market_value - float(loan_balance)
    else:
        equity = asset.market_value
    if asset.market_value is None or asset.cost_basis is None:
        gain = None
    else:
        gain = asset.market_value - asset.cost_basis
    income_name = ""
    t12_income: float | None = None
    if asset.income_source_id:
        registered = income_source_by_id(asset.income_source_id)
        income_name = registered.name if registered is not None else asset.income_source_id
        if income_configured:
            t12_income = received_for_source(
                deposits if deposits is not None else pd.DataFrame(),
                asset.income_source_id,
                period,
            )
    return AssetPosition(
        asset_id=asset.asset_id,
        name=asset.name,
        category=asset.category,
        acquired=asset.acquired,
        notes=asset.notes,
        market_value=asset.market_value,
        valuation_date=asset.valuation_date,
        valuation_source=asset.valuation_source,
        cost_basis=asset.cost_basis,
        gain_loss=gain,
        loan_linked=loan_linked,
        loan_found=loan_found,
        loan_name=loan_name,
        loan_balance=loan_balance,
        equity=equity,
        ltv=_ratio(loan_balance, asset.market_value),
        income_source_id=asset.income_source_id,
        income_name=income_name,
        t12_income=t12_income,
        yield_on_value=_ratio(t12_income, asset.market_value),
        t12_loan_payments=t12_payments,
        cash_on_equity=_ratio(
            None if t12_income is None or t12_payments is None else t12_income - t12_payments,
            equity,
        ),
    )


def _loan_display_name(schedule: pd.DataFrame | None, loan_id: str) -> str:
    for source, _rows in loan_sources(schedule):
        if loan_id_from_schedule_source(source.id) == loan_id:
            return source.name
    return ""


def _scheduled_identity_keys(schedule: pd.DataFrame | None) -> set[str]:
    """Account labels that repeat a scheduled loan and must not be counted twice."""
    keys: set[str] = set()
    for source, _rows in loan_sources(schedule):
        loan_id = loan_id_from_schedule_source(source.id) or ""
        for label in (loan_id, source.name):
            key = label.strip().casefold()
            if key:
                keys.add(key)
    return keys


def _split_balances(
    balances: pd.DataFrame | None,
    scheduled_keys: set[str],
) -> tuple[list[SheetRow], list[SheetRow], list[SheetRow]]:
    cash: list[SheetRow] = []
    loans: list[SheetRow] = []
    cards: list[SheetRow] = []
    for item in latest_account_balances(balances):
        if item.account.casefold() in scheduled_keys:
            continue
        kind = account_balance_kind(item.account, item.account_type)
        if kind == "cash":
            cash.append(SheetRow(CASH_GROUP, item.account, item.amount, "item"))
        elif kind == "loan":
            loans.append(SheetRow(OTHER_LOAN_GROUP, _account_label(item.account), item.amount, "item"))
        else:
            cards.append(SheetRow(CREDIT_GROUP, item.account, item.amount, "item"))
    cash.sort(key=lambda row: row.item.casefold())
    loans.sort(key=lambda row: row.item.casefold())
    cards.sort(key=lambda row: row.item.casefold())
    return cash, loans, cards


def _account_label(account: str) -> str:
    source = expense_source_for_account(account)
    if source is not None:
        return source.name
    return account


def _asset_rows(
    cash: Sequence[SheetRow],
    positions: Sequence[AssetPosition],
    total_assets: float,
) -> list[SheetRow]:
    """Cash accounts and each asset, once, then Total assets.

    Category subtotals are left off. A category usually has one asset, so a
    subtotal would repeat that amount.
    """
    rows = list(cash)
    grouped: dict[str, list[SheetRow]] = {}
    order: list[str] = []
    for position in positions:
        if position.category not in grouped:
            grouped[position.category] = []
            order.append(position.category)
        grouped[position.category].append(
            SheetRow(position.category, position.name, position.market_value, "item")
        )
    for category in order:
        rows.extend(grouped[category])
    rows.append(SheetRow(TOTAL_GROUP, "Total assets", total_assets, "total"))
    return rows


def _liability_rows(
    schedule: pd.DataFrame | None,
    today: date,
    other_loans: Sequence[SheetRow],
    cards: Sequence[SheetRow],
    total_liabilities: float,
) -> list[SheetRow]:
    mortgages = [
        SheetRow(MORTGAGE_GROUP, item.name, item.balance, "item")
        for item in loan_snapshots(schedule, today)
    ]
    rows: list[SheetRow] = []
    rows.extend(_with_subtotal(MORTGAGE_GROUP, mortgages))
    rows.extend(_with_subtotal(OTHER_LOAN_GROUP, list(other_loans)))
    rows.extend(_with_subtotal(CREDIT_GROUP, list(cards)))
    rows.append(SheetRow(TOTAL_GROUP, "Total liabilities", total_liabilities, "total"))
    return rows


def _with_subtotal(group: str, items: list[SheetRow]) -> list[SheetRow]:
    if not items:
        subtotal: float | None = 0.0
    else:
        known = [item.amount for item in items if item.amount is not None]
        subtotal = float(sum(known)) if known else None
    return [*items, SheetRow(group, "Subtotal", subtotal, "subtotal")]


def _sum_amounts(rows: Sequence[SheetRow]) -> float:
    return float(sum(row.amount for row in rows if row.amount is not None))


def _sum_known(values: Sequence[float | None]) -> float:
    return float(sum(value for value in values if value is not None))


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _group_items(rows: Sequence[SheetRow], group: str) -> tuple[SheetRow, ...]:
    return tuple(row for row in rows if row.group == group and row.role == "item")
