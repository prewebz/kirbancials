"""Balance sheet totals. Fixtures are fake round numbers, not account exports."""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from kirbancials.activity import load_balances, resolve_balances
from kirbancials.assets import ASSET_HEADER, ASSETS_ENV, NO_ASSETS_NOTE, resolve_assets
from kirbancials.balance_sheet import (
    CASH_GROUP,
    CASH_NOTE,
    CREDIT_GROUP,
    MORTGAGE_GROUP,
    OTHER_LOAN_GROUP,
    TOTAL_GROUP,
    VALUATION_NOTE,
    build_balance_sheet,
    show_money,
    show_percent,
)
from kirbancials.formatting import format_signed_money
from kirbancials.income import load_income, received_for_source, resolve_income
from kirbancials.overview import credit_card_balance_total
from kirbancials.periods import resolve_period
from kirbancials.schedules import (
    LOAN_SCHEDULE_ENV,
    SCHEDULE_COLUMNS,
    remaining_balance,
    resolve_loan_schedule,
    rows_for_loan,
    scheduled_payments,
)

TODAY = date(2026, 11, 12)
ROOT = Path(__file__).resolve().parents[1]

ASSETS = f"""{ASSET_HEADER}
shop-1,Sample Shop,Real Estate,2020-01-15,80.00,100.00,2026-06-01,Sample appraisal,loan-1,chipotle_nnn,Fake note
note-1,Sample IOU,Note Receivable,2024-03-01,10.00,15.00,2026-01-01,Sample statement,,,
inv-1,Sample Fund,Investment,2023-05-01,10.00,,,,,,
other-1,Sample Coin,Other,,4.00,5.00,2026-02-01,Sample quote,,,
"""

BALANCES = """Account,As Of,Current Balance,Available Credit,Credit Limit,Account Type
Sample Checking,2026-11-01T00:00:00Z,5000,,,depository
Sample Checking,2026-11-12T00:00:00Z,25,,,depository
Sample Savings,2026-11-12T00:00:00Z,15,,,cash
Citi AAdvantage card ending 7690,2026-11-12T00:00:00Z,40,100,500,credit
Sample card ending 0000,2026-11-12T00:00:00Z,5,,,
Wells Fargo Auto Loan ending 7635,2026-11-12T00:00:00Z,200,,,
Sample Personal Loan,2026-11-12T00:00:00Z,7,,,loan
LOAN-1,2026-11-12T00:00:00Z,999,,,loan
Sample Bank - Sample mortgage,2026-11-12T00:00:00Z,888,,,depository
"""

INCOME = """Transaction ID,Date,Source,Description,Amount
old,2025-11-12,chipotle_nnn,Outside the window,1000.00
rent,2026-06-15,chipotle_nnn,Sample rent,30.00
future,2026-11-13,chipotle_nnn,After today,1000.00
"""

SCHEDULE = f"""{','.join(SCHEDULE_COLUMNS)}
loan-1,Sample Bank,Sample mortgage,Sample Shop,5.0,1,2025-11-12,99.00,1.00,1.00,66.00
loan-1,Sample Bank,Sample mortgage,Sample Shop,5.0,2,2026-06-01,10.00,4.00,6.00,60.00
loan-1,Sample Bank,Sample mortgage,Sample Shop,5.0,3,2026-12-01,10.00,3.00,7.00,53.00
"""

HIDDEN = "HIDDEN-FIGURE-999"


def _balances() -> pd.DataFrame:
    origin = resolve_balances({"FINANCE_BALANCES_CSV": BALANCES})
    assert origin is not None
    loaded = load_balances(origin)
    return loaded.rows


def _schedule() -> pd.DataFrame:
    frame, warning = resolve_loan_schedule({LOAN_SCHEDULE_ENV: SCHEDULE})
    assert warning is None
    return frame


def _deposits() -> pd.DataFrame:
    origin = resolve_income({"FINANCE_INCOME_CSV": INCOME})
    assert origin is not None
    return load_income(origin)


def _assets_text(text: str = ASSETS) -> object:
    return resolve_assets({ASSETS_ENV: text})


def _sheet(
    assets_text: str | None = ASSETS,
    *,
    assets_configured: bool | None = None,
    income_configured: bool = True,
    balances: pd.DataFrame | None = None,
    schedule: pd.DataFrame | None = None,
):
    if assets_text is None:
        loaded = resolve_assets({})
    else:
        loaded = resolve_assets({ASSETS_ENV: assets_text})
    configured = loaded.configured if assets_configured is None else assets_configured
    return build_balance_sheet(
        loaded.assets,
        _balances() if balances is None else balances,
        _schedule() if schedule is None else schedule,
        _deposits(),
        TODAY,
        assets_configured=configured,
        income_configured=income_configured,
    )


def _items(rows, group: str):
    return [row for row in rows if row.group == group and row.role == "item"]


def _subtotal(rows, group: str) -> float | None:
    found = [row.amount for row in rows if row.group == group and row.role == "subtotal"]
    assert len(found) == 1
    return found[0]


class AssetFileTests(unittest.TestCase):
    def test_header_matches_the_env_contract(self) -> None:
        self.assertEqual(ASSETS_ENV, "FINANCE_ASSETS_CSV")
        self.assertEqual(
            ASSET_HEADER,
            "Asset ID,Asset Name,Category,Acquired Date,Cost Basis,Market Value,"
            "Valuation Date,Valuation Source,Linked Loan ID,Income Source ID,Notes",
        )
        self.assertEqual(
            NO_ASSETS_NOTE,
            "No assets are configured.",
        )
        self.assertEqual(
            VALUATION_NOTE,
            "Market values are entered manually from appraisals; "
            "update when a new valuation is available.",
        )
        self.assertEqual(CASH_NOTE, "No checking or savings accounts are labeled in the balance file.")

    def test_plain_decimals_and_dates(self) -> None:
        loaded = resolve_assets({ASSETS_ENV: "\ufeff" + ASSETS})
        self.assertIsNone(loaded.warning)
        self.assertTrue(loaded.configured)
        self.assertEqual([asset.asset_id for asset in loaded.assets], ["shop-1", "note-1", "inv-1", "other-1"])
        shop = loaded.assets[0]
        self.assertEqual(shop.acquired, date(2020, 1, 15))
        self.assertEqual(shop.valuation_date, date(2026, 6, 1))
        self.assertAlmostEqual(shop.cost_basis or 0, 80)
        self.assertAlmostEqual(shop.market_value or 0, 100)
        self.assertEqual(shop.linked_loan_id, "loan-1")
        self.assertEqual(shop.income_source_id, "chipotle_nnn")
        self.assertIsNone(loaded.assets[2].market_value)

    def test_header_only_is_configured_and_empty(self) -> None:
        loaded = resolve_assets({ASSETS_ENV: ASSET_HEADER + "\n"})
        self.assertTrue(loaded.configured)
        self.assertIsNone(loaded.warning)
        self.assertEqual(loaded.assets, ())

    def test_missing_or_blank_env_has_no_warning(self) -> None:
        missing = resolve_assets({})
        blank = resolve_assets({ASSETS_ENV: "  \n"})
        for loaded in (missing, blank):
            self.assertFalse(loaded.configured)
            self.assertIsNone(loaded.warning)
            self.assertEqual(loaded.assets, ())

    def test_malformed_csv_warns_and_hides_cells(self) -> None:
        samples = [
            ASSET_HEADER + f"\nsecret-1,{HIDDEN},Real Estate,not-a-date,10,10,2026-01-01,Appraisal,loan-1,chipotle_nnn,note\n",
            ASSET_HEADER + "\nbad-1,Sample,Other,2020-01-01,\"1,000\",10,2026-01-01,Appraisal,,,\n",
            f"Asset ID,Asset Name\nsecret-1,{HIDDEN}\n",
            '"unterminated',
        ]
        for text in samples:
            loaded = resolve_assets({ASSETS_ENV: text})
            self.assertTrue(loaded.configured, text)
            self.assertIsNotNone(loaded.warning)
            self.assertNotIn(HIDDEN, loaded.warning or "")
            self.assertNotIn("1,000", loaded.warning or "")
            self.assertEqual(loaded.assets, ())


class BalanceSheetTests(unittest.TestCase):
    def test_totals(self) -> None:
        sheet = _sheet()
        # Cash 25 + 15, assets 100 + 15 + 5. The fund has no market value.
        self.assertAlmostEqual(sheet.total_assets, 160)
        # Mortgage 60, other loans 200 + 7, cards 40 + 5.
        self.assertAlmostEqual(sheet.total_liabilities, 312)
        self.assertAlmostEqual(sheet.net_worth, -152)
        self.assertAlmostEqual(sheet.total_assets - sheet.total_liabilities, sheet.net_worth)
        self.assertEqual(sheet.as_of, TODAY)
        self.assertEqual(format_signed_money(sheet.net_worth), ":red[-$152.00]")

        self.assertEqual([row for row in sheet.asset_rows if row.role == "subtotal"], [])
        asset_items = [
            (CASH_GROUP, "Sample Checking", 25.0),
            (CASH_GROUP, "Sample Savings", 15.0),
            ("Real Estate", "Sample Shop", 100.0),
            ("Note Receivable", "Sample IOU", 15.0),
            ("Investment", "Sample Fund", None),
            ("Other", "Sample Coin", 5.0),
        ]
        found_items = [
            (row.group, row.item, row.amount)
            for row in sheet.asset_rows
            if row.role == "item"
        ]
        self.assertEqual([item[:2] for item in found_items], [item[:2] for item in asset_items])
        for found, expected in zip(found_items, asset_items):
            if expected[2] is None:
                self.assertIsNone(found[2])
            else:
                self.assertAlmostEqual(found[2] or 0, expected[2])
        self.assertEqual(len(found_items), len({item for _group, item, _amount in found_items}))
        mortgage_rows = [row for row in sheet.liability_rows if row.group == MORTGAGE_GROUP]
        self.assertEqual(
            [(row.item, row.role) for row in mortgage_rows],
            [("Sample Bank - Sample mortgage", "item")],
        )
        self.assertAlmostEqual(mortgage_rows[0].amount or 0, 60)
        self.assertAlmostEqual(_subtotal(sheet.liability_rows, OTHER_LOAN_GROUP) or 0, 207)
        self.assertAlmostEqual(_subtotal(sheet.liability_rows, CREDIT_GROUP) or 0, 45)
        total_assets = [row for row in sheet.asset_rows if row.role == "total"]
        total_liabilities = [row for row in sheet.liability_rows if row.role == "total"]
        self.assertEqual(total_assets[0].item, "Total assets")
        self.assertEqual(total_liabilities[0].item, "Total liabilities")
        self.assertAlmostEqual(total_assets[0].amount or 0, 160)
        self.assertAlmostEqual(total_liabilities[0].amount or 0, 312)
        self.assertEqual(
            _group_order(sheet.asset_rows),
            [CASH_GROUP, "Real Estate", "Note Receivable", "Investment", "Other", TOTAL_GROUP],
        )
        self.assertEqual(
            _group_order(sheet.liability_rows),
            [MORTGAGE_GROUP, OTHER_LOAN_GROUP, CREDIT_GROUP, TOTAL_GROUP],
        )

    def test_two_assets_in_one_category_are_listed_once_each(self) -> None:
        text = (
            ASSET_HEADER
            + "\nhouse,Sample House,Real Estate,2020-01-01,10,40,2026-01-01,Sample,,,\n"
            + "lot,Sample Lot,Real Estate,2021-01-01,10,20,2026-01-01,Sample,,,\n"
        )
        sheet = _sheet(text, balances=pd.DataFrame(), schedule=_schedule()[:0])
        self.assertEqual(
            [(row.group, row.item, row.role) for row in sheet.asset_rows],
            [
                ("Real Estate", "Sample House", "item"),
                ("Real Estate", "Sample Lot", "item"),
                (TOTAL_GROUP, "Total assets", "total"),
            ],
        )
        self.assertAlmostEqual(sheet.asset_rows[0].amount or 0, 40)
        self.assertAlmostEqual(sheet.asset_rows[1].amount or 0, 20)
        self.assertAlmostEqual(sheet.total_assets, 60)
        self.assertEqual([row for row in sheet.asset_rows if row.role == "subtotal"], [])

    def test_two_mortgages_keep_a_subtotal(self) -> None:
        second = (
            SCHEDULE
            + "loan-2,Sample Credit Union,Sample second mortgage,Sample Lot,4.0,1,2026-06-01,10.00,2.00,8.00,40.00\n"
        )
        schedule, warning = resolve_loan_schedule({LOAN_SCHEDULE_ENV: second})
        self.assertIsNone(warning)
        sheet = _sheet(schedule=schedule)
        mortgage_rows = [row for row in sheet.liability_rows if row.group == MORTGAGE_GROUP]
        self.assertEqual(
            [(row.item, row.role) for row in mortgage_rows],
            [
                ("Sample Bank - Sample mortgage", "item"),
                ("Sample Credit Union - Sample second mortgage", "item"),
                ("Subtotal", "subtotal"),
            ],
        )
        self.assertAlmostEqual(_subtotal(sheet.liability_rows, MORTGAGE_GROUP) or 0, 100)
        self.assertAlmostEqual(sheet.total_assets, 160)
        self.assertAlmostEqual(sheet.total_liabilities, 352)
        self.assertAlmostEqual(sheet.net_worth, 160 - 352)

    def test_equity_ltv_yield_and_cash_on_equity(self) -> None:
        sheet = _sheet()
        shop = _position(sheet, "shop-1")
        note = _position(sheet, "note-1")
        fund = _position(sheet, "inv-1")
        schedule = _schedule()
        loan_rows = rows_for_loan(schedule, "loan-1")
        period = resolve_period("last-12-months", TODAY)

        self.assertAlmostEqual(shop.loan_balance or 0, remaining_balance(loan_rows, TODAY) or 0)
        self.assertAlmostEqual(shop.loan_balance or 0, 60)
        self.assertEqual(shop.loan_name, "Sample Bank - Sample mortgage")
        self.assertAlmostEqual(shop.gain_loss or 0, 20)
        self.assertAlmostEqual(shop.equity or 0, 40)
        self.assertAlmostEqual(shop.ltv or 0, 0.6)
        self.assertAlmostEqual(shop.t12_loan_payments or 0, scheduled_payments(loan_rows, period))
        self.assertAlmostEqual(shop.t12_loan_payments or 0, 10)
        self.assertAlmostEqual(shop.t12_income or 0, received_for_source(_deposits(), "chipotle_nnn", period))
        self.assertAlmostEqual(shop.t12_income or 0, 30)
        self.assertEqual(shop.income_name, "Chipotle NNN")
        self.assertAlmostEqual(shop.yield_on_value or 0, 0.3)
        self.assertAlmostEqual(shop.cash_on_equity or 0, 0.5)
        self.assertEqual(shop.valuation_source, "Sample appraisal")
        self.assertEqual(shop.valuation_date, date(2026, 6, 1))

        self.assertIsNone(note.loan_balance)
        self.assertIsNone(note.ltv)
        self.assertIsNone(note.t12_income)
        self.assertIsNone(note.yield_on_value)
        self.assertIsNone(note.cash_on_equity)
        self.assertAlmostEqual(note.equity or 0, 15)
        self.assertAlmostEqual(note.gain_loss or 0, 5)

        self.assertIsNone(fund.market_value)
        self.assertIsNone(fund.gain_loss)
        self.assertIsNone(fund.equity)
        self.assertIsNone(fund.ltv)
        self.assertEqual(show_money(None), "n/a")
        self.assertEqual(show_percent(None), "n/a")
        self.assertEqual(show_percent(shop.ltv), "60.00%")
        self.assertEqual(show_percent(-0.125), "-12.50%")

    def test_cash_grouping_uses_account_type_and_skips_cards(self) -> None:
        balances = _balances()
        self.assertIn("Account Type", balances.columns)
        self.assertAlmostEqual(credit_card_balance_total(balances), 45)
        sheet = _sheet(balances=balances)
        cash = _items(sheet.asset_rows, CASH_GROUP)
        self.assertEqual([row.item for row in cash], ["Sample Checking", "Sample Savings"])
        self.assertAlmostEqual(cash[0].amount or 0, 25)
        self.assertAlmostEqual(cash[1].amount or 0, 15)
        self.assertEqual([row for row in sheet.asset_rows if row.role == "subtotal"], [])
        self.assertAlmostEqual(sum(row.amount or 0 for row in cash), 40)
        names = {row.item for row in cash}
        self.assertNotIn("Citi AAdvantage card ending 7690", names)
        self.assertNotIn("Sample card ending 0000", names)
        self.assertNotIn("Sample Bank - Sample mortgage", names)

    def test_scheduled_loan_is_not_double_counted(self) -> None:
        sheet = _sheet()
        amounts = [row.amount for row in (*sheet.asset_rows, *sheet.liability_rows)]
        self.assertNotIn(999, amounts)
        self.assertNotIn(999.0, amounts)
        self.assertNotIn(888, amounts)
        self.assertNotIn(888.0, amounts)
        self.assertNotIn(5000, amounts)
        mortgages = _items(sheet.liability_rows, MORTGAGE_GROUP)
        self.assertEqual([row.item for row in mortgages], ["Sample Bank - Sample mortgage"])
        self.assertAlmostEqual(mortgages[0].amount or 0, 60)
        other = _items(sheet.liability_rows, OTHER_LOAN_GROUP)
        self.assertEqual(
            [row.item for row in other],
            ["Sample Personal Loan", "Wells Fargo Auto - 7635"],
        )
        self.assertNotIn("LOAN-1", [row.item for row in other])
        cards = [row.item for row in _items(sheet.liability_rows, CREDIT_GROUP)]
        self.assertEqual(cards, ["Citi AAdvantage card ending 7690", "Sample card ending 0000"])

    def test_missing_env_still_shows_cash_and_liabilities(self) -> None:
        loaded = resolve_assets({})
        sheet = build_balance_sheet(
            loaded.assets,
            _balances(),
            _schedule(),
            _deposits(),
            TODAY,
            assets_configured=loaded.configured,
            income_configured=True,
        )
        self.assertFalse(sheet.assets_configured)
        self.assertIsNone(loaded.warning)
        self.assertEqual(sheet.positions, ())
        self.assertAlmostEqual(sheet.total_assets, 40)
        self.assertAlmostEqual(sheet.total_liabilities, 312)
        self.assertAlmostEqual(sheet.net_worth, -272)
        self.assertEqual(_group_order(sheet.asset_rows), [CASH_GROUP, TOTAL_GROUP])
        self.assertEqual(NO_ASSETS_NOTE, "No assets are configured.")

    def test_malformed_csv_still_shows_cash_and_liabilities(self) -> None:
        text = (
            ASSET_HEADER
            + f"\nsecret-1,{HIDDEN},Real Estate,not-a-date,10,10,2026-01-01,Appraisal,loan-1,,note\n"
        )
        loaded = resolve_assets({ASSETS_ENV: text})
        sheet = build_balance_sheet(
            loaded.assets,
            _balances(),
            _schedule(),
            _deposits(),
            TODAY,
            assets_configured=loaded.configured,
            income_configured=True,
        )
        self.assertIsNotNone(loaded.warning)
        self.assertNotIn(HIDDEN, loaded.warning or "")
        self.assertEqual(sheet.positions, ())
        self.assertAlmostEqual(sheet.total_assets, 40)
        self.assertAlmostEqual(sheet.total_liabilities, 312)
        mortgage_rows = [row for row in sheet.liability_rows if row.group == MORTGAGE_GROUP]
        self.assertEqual(
            [(row.item, row.role) for row in mortgage_rows],
            [("Sample Bank - Sample mortgage", "item")],
        )
        self.assertAlmostEqual(mortgage_rows[0].amount or 0, 60)

    def test_missing_loan_and_missing_income_are_na(self) -> None:
        text = (
            ASSET_HEADER
            + "\nghost-1,Sample Ghost,Other,2020-01-01,1.00,10.00,2026-01-01,Sample,missing-loan,chipotle_nnn,\n"
        )
        missing_loan = _sheet(text)
        ghost = _position(missing_loan, "ghost-1")
        self.assertTrue(ghost.loan_linked)
        self.assertFalse(ghost.loan_found)
        self.assertIsNone(ghost.loan_balance)
        self.assertIsNone(ghost.equity)
        self.assertIsNone(ghost.ltv)
        self.assertIsNone(ghost.cash_on_equity)
        self.assertAlmostEqual(ghost.market_value or 0, 10)
        self.assertAlmostEqual(missing_loan.total_assets, 50)

        no_income = _sheet(income_configured=False)
        shop = _position(no_income, "shop-1")
        self.assertIsNone(shop.t12_income)
        self.assertIsNone(shop.yield_on_value)
        self.assertIsNone(shop.cash_on_equity)
        self.assertAlmostEqual(shop.equity or 0, 40)

    def test_positive_net_worth_is_green(self) -> None:
        balances = pd.DataFrame(
            {
                "Account": ["Sample Checking"],
                "As Of": ["2026-11-12T00:00:00Z"],
                "Current Balance": [80],
                "Account Type": ["checking"],
            }
        )
        text = ASSET_HEADER + "\ncash-asset,Sample Lot,Other,2020-01-01,10,20,2026-01-01,Sample,,,\n"
        loaded = resolve_assets({ASSETS_ENV: text})
        sheet = build_balance_sheet(
            loaded.assets,
            balances,
            _schedule()[:0],
            _deposits(),
            TODAY,
            assets_configured=True,
            income_configured=True,
        )
        self.assertAlmostEqual(sheet.total_assets, 100)
        self.assertAlmostEqual(sheet.total_liabilities, 0)
        self.assertAlmostEqual(sheet.net_worth, 100)
        self.assertEqual(format_signed_money(sheet.net_worth), ":green[$100.00]")
        self.assertEqual(format_signed_money(0), "$0.00")


class NavigationTests(unittest.TestCase):
    def test_balance_sheet_is_the_second_page_and_login_covers_it(self) -> None:
        text = (ROOT / "app.py").read_text()
        pages = [line for line in text.splitlines() if "st.Page(" in line]
        self.assertIn("profit_and_loss.py", pages[0])
        self.assertIn("default=True", pages[0])
        self.assertIn("balance_sheet.py", pages[1])
        self.assertNotIn("default=True", pages[1])
        sidebar = text.split("with st.sidebar:", 1)[1]
        links = [line for line in sidebar.splitlines() if "st.page_link" in line]
        self.assertIn("profit_and_loss.py", links[0])
        self.assertIn("balance_sheet.py", links[1])
        self.assertLess(text.index("require_login()"), text.index("navigation.run()"))
        view = (ROOT / "views" / "balance_sheet.py").read_text()
        self.assertIn("render_balance_sheet", view)
        self.assertNotIn("require_login", view)


def _position(sheet, asset_id: str):
    found = [item for item in sheet.positions if item.asset_id == asset_id]
    assert len(found) == 1
    return found[0]


def _group_order(rows) -> list[str]:
    order: list[str] = []
    for row in rows:
        if row.group not in order:
            order.append(row.group)
    return order
