"""Balance sheet totals. Fixtures are fake round numbers, not account exports."""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from kirbancials.activity import load_balances, resolve_balances
from kirbancials.assets import (
    ASSET_HEADER,
    ASSETS_ENV,
    NO_ASSETS_NOTE,
    REQUIRED_ASSET_COLUMNS,
    resolve_assets,
)
from kirbancials.auth import (
    AUTHENTICATED,
    BALANCE_SHEET_PASSWORD_ENV,
    BALANCE_SHEET_PASSWORD_MISSING,
    BALANCE_SHEET_UNLOCKED,
    INCORRECT_PASSWORD,
    attempt_balance_sheet_unlock,
    balance_sheet_gate,
    end_session,
    is_balance_sheet_unlocked,
    lock_balance_sheet,
)
from kirbancials.balance_sheet import (
    CASH_GROUP,
    CASH_NOTE,
    CREDIT_GROUP,
    MORTGAGE_GROUP,
    OTHER_LOAN_GROUP,
    TOTAL_GROUP,
    VALUE_CAP_RATE,
    VALUATION_NOTE,
    build_balance_sheet,
    format_cap_rate,
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

LEGACY_HEADER = ",".join(REQUIRED_ASSET_COLUMNS)
ASSETS = f"""{LEGACY_HEADER}
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
    value_basis: str = "appraisal",
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
        value_basis=value_basis,
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
            "Valuation Date,Valuation Source,Linked Loan ID,Income Source ID,Notes,"
            "Market Cap Rate,Cap Rate Source,Cap Rate Date,Estimated Value",
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

        asset_subtotals = {
            CASH_GROUP: 40.0,
            "Real Estate": 100.0,
            "Note Receivable": 15.0,
            "Investment": None,
            "Other": 5.0,
        }
        for group, expected in asset_subtotals.items():
            amount = _subtotal(sheet.asset_rows, group)
            if expected is None:
                self.assertIsNone(amount)
            else:
                self.assertAlmostEqual(amount or 0, expected)
        self.assertAlmostEqual(_subtotal(sheet.liability_rows, MORTGAGE_GROUP) or 0, 60)
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
        self.assertAlmostEqual(_subtotal(sheet.asset_rows, CASH_GROUP) or 0, 40)
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
        self.assertAlmostEqual(_subtotal(sheet.liability_rows, MORTGAGE_GROUP) or 0, 60)

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


CAP_ASSETS = f"""{ASSET_HEADER}
shop-1,Sample Shop,Real Estate,2020-01-15,80.00,100.00,2026-06-01,Sample appraisal,loan-1,chipotle_nnn,Fake note,5.25,Sample broker,2026-06-01,130.00
note-1,Sample IOU,Note Receivable,2024-03-01,10.00,15.00,2026-01-01,Sample statement,,,,,,,
"""


class CapRateTests(unittest.TestCase):
    def test_legacy_header_still_parses(self) -> None:
        loaded = resolve_assets({ASSETS_ENV: ASSETS})
        self.assertIsNone(loaded.warning)
        shop = loaded.assets[0]
        self.assertIsNone(shop.cap_rate)
        self.assertEqual(shop.cap_rate_source, "")
        self.assertIsNone(shop.cap_rate_date)
        self.assertIsNone(shop.estimated_value)
        self.assertEqual(LEGACY_HEADER.split(","), REQUIRED_ASSET_COLUMNS)

    def test_optional_columns_parse_and_a_bad_value_hides_the_cell(self) -> None:
        loaded = resolve_assets({ASSETS_ENV: CAP_ASSETS})
        self.assertIsNone(loaded.warning)
        shop = loaded.assets[0]
        self.assertAlmostEqual(shop.cap_rate or 0, 5.25)
        self.assertEqual(shop.cap_rate_source, "Sample broker")
        self.assertEqual(shop.cap_rate_date, date(2026, 6, 1))
        self.assertAlmostEqual(shop.estimated_value or 0, 130)
        self.assertIsNone(loaded.assets[1].estimated_value)
        self.assertEqual(format_cap_rate(shop.cap_rate), "5.25%")
        self.assertEqual(format_cap_rate(None), "n/a")

        secret = "SECRET-CAP-RATE"
        bad = (
            ASSET_HEADER
            + f"\nshop-1,Sample Shop,Real Estate,2020-01-15,80,100,2026-06-01,Appraisal,loan-1,,note,5.25%,{secret},2026-06-01,130\n"
        )
        failed = resolve_assets({ASSETS_ENV: bad})
        self.assertIsNotNone(failed.warning)
        self.assertNotIn(secret, failed.warning or "")
        self.assertEqual(failed.assets, ())

    def test_default_totals_stay_on_the_appraisal(self) -> None:
        sheet = _sheet(CAP_ASSETS)
        shop = _position(sheet, "shop-1")
        note = _position(sheet, "note-1")
        self.assertAlmostEqual(shop.carrying_value or 0, 100)
        self.assertAlmostEqual(shop.estimate_vs_appraisal or 0, 30)
        self.assertAlmostEqual(shop.equity or 0, 40)
        self.assertAlmostEqual(shop.ltv or 0, 0.6)
        self.assertAlmostEqual(shop.gain_loss or 0, 20)
        self.assertAlmostEqual(shop.yield_on_value or 0, 0.3)
        self.assertAlmostEqual(note.carrying_value or 0, 15)
        self.assertIsNone(note.estimate_vs_appraisal)
        # Cash 40 plus appraisals 100 and 15. The 130 estimate is not in the total.
        self.assertAlmostEqual(sheet.total_assets, 155)
        self.assertAlmostEqual(sheet.net_worth, 155 - 312)
        shop_row = next(row for row in sheet.asset_rows if row.item == "Sample Shop" and row.role == "item")
        self.assertAlmostEqual(shop_row.amount or 0, 100)

    def test_cap_rate_basis_switches_totals_equity_and_ltv_where_an_estimate_exists(self) -> None:
        sheet = _sheet(CAP_ASSETS, value_basis=VALUE_CAP_RATE)
        shop = _position(sheet, "shop-1")
        note = _position(sheet, "note-1")
        self.assertAlmostEqual(shop.carrying_value or 0, 130)
        self.assertAlmostEqual(shop.equity or 0, 70)
        self.assertAlmostEqual(shop.ltv or 0, 60 / 130)
        self.assertAlmostEqual(shop.market_value or 0, 100)
        self.assertAlmostEqual(shop.gain_loss or 0, 20)
        self.assertAlmostEqual(shop.yield_on_value or 0, 0.3)
        self.assertAlmostEqual(shop.cash_on_equity or 0, 20 / 70)
        self.assertAlmostEqual(note.carrying_value or 0, 15)
        self.assertAlmostEqual(note.equity or 0, 15)
        self.assertAlmostEqual(sheet.total_assets, 185)
        self.assertAlmostEqual(sheet.total_liabilities, 312)
        self.assertAlmostEqual(sheet.net_worth, 185 - 312)
        shop_row = next(row for row in sheet.asset_rows if row.item == "Sample Shop" and row.role == "item")
        self.assertAlmostEqual(shop_row.amount or 0, 130)


class BalanceSheetLockTests(unittest.TestCase):
    def test_locked_by_default(self) -> None:
        session: dict[str, object] = {}
        self.assertFalse(is_balance_sheet_unlocked(session))
        self.assertEqual(
            balance_sheet_gate(session, {BALANCE_SHEET_PASSWORD_ENV: "owner-secret"}),
            "locked",
        )

    def test_wrong_password_stays_locked_and_hides_the_secret(self) -> None:
        session: dict[str, object] = {}
        secret = "owner-secret"
        wrong = "wrong-secret-value"
        message = attempt_balance_sheet_unlock(
            session,
            wrong,
            {BALANCE_SHEET_PASSWORD_ENV: secret},
        )
        self.assertEqual(message, INCORRECT_PASSWORD)
        self.assertEqual(message, "Incorrect password.")
        self.assertNotIn(wrong, message or "")
        self.assertNotIn(secret, message or "")
        self.assertFalse(is_balance_sheet_unlocked(session))
        self.assertNotIn(secret, session.values())
        self.assertNotIn(wrong, session.values())

    def test_right_password_unlocks_for_the_session(self) -> None:
        session: dict[str, object] = {}
        secret = "owner-secret"
        message = attempt_balance_sheet_unlock(
            session,
            secret,
            {BALANCE_SHEET_PASSWORD_ENV: secret},
        )
        self.assertIsNone(message)
        self.assertTrue(is_balance_sheet_unlocked(session))
        self.assertEqual(
            balance_sheet_gate(session, {BALANCE_SHEET_PASSWORD_ENV: secret}),
            "open",
        )

    def test_unset_password_fails_closed(self) -> None:
        for env in ({}, {BALANCE_SHEET_PASSWORD_ENV: ""}, {BALANCE_SHEET_PASSWORD_ENV: "   \n"}):
            session: dict[str, object] = {BALANCE_SHEET_UNLOCKED: True}
            self.assertEqual(balance_sheet_gate(session, env), "unconfigured")
            self.assertIs(session[BALANCE_SHEET_UNLOCKED], False)
            provided = "anything-entered"
            message = attempt_balance_sheet_unlock(session, provided, env)
            self.assertEqual(message, BALANCE_SHEET_PASSWORD_MISSING)
            self.assertEqual(message, "Balance Sheet password not set yet.")
            self.assertNotIn(provided, message or "")
            self.assertFalse(is_balance_sheet_unlocked(session))

    def test_logout_relocks_and_the_lock_button_clears_only_the_sheet(self) -> None:
        session: dict[str, object] = {AUTHENTICATED: True, BALANCE_SHEET_UNLOCKED: True}
        end_session(session)
        self.assertIs(session[AUTHENTICATED], False)
        self.assertIs(session[BALANCE_SHEET_UNLOCKED], False)

        still_in = {AUTHENTICATED: True, BALANCE_SHEET_UNLOCKED: True}
        lock_balance_sheet(still_in)
        self.assertIs(still_in[AUTHENTICATED], True)
        self.assertIs(still_in[BALANCE_SHEET_UNLOCKED], False)

        app = (ROOT / "app.py").read_text()
        self.assertIn("end_session", app)
        self.assertIn("Lock balance sheet", app)
        page = (ROOT / "kirbancials" / "balance_sheet.py").read_text()
        self.assertIn('type="password"', page)
        self.assertIn("Lock balance sheet", page)
        prompt = page.split("def _render_balance_sheet_prompt", 1)[1].split("def _render_unlocked", 1)[0]
        self.assertNotIn("resolve_assets", prompt)
        self.assertNotIn("resolve_balances", prompt)
        self.assertNotIn("resolve_loan_schedule", prompt)
        unlocked = page.split("def _render_unlocked", 1)[1]
        self.assertLess(unlocked.index("st.radio"), unlocked.index("resolve_assets()"))

    def test_locked_page_does_not_load_or_render_figures(self) -> None:
        import os
        from unittest.mock import patch

        from streamlit.testing.v1 import AppTest

        saved = os.environ.get(BALANCE_SHEET_PASSWORD_ENV)
        os.environ.pop(BALANCE_SHEET_PASSWORD_ENV, None)
        try:
            with (
                patch("kirbancials.balance_sheet.resolve_assets", side_effect=AssertionError("assets loaded")),
                patch("kirbancials.balance_sheet.resolve_balances", side_effect=AssertionError("balances loaded")),
                patch("kirbancials.balance_sheet.resolve_income", side_effect=AssertionError("income loaded")),
                patch("kirbancials.balance_sheet.resolve_loan_schedule", side_effect=AssertionError("schedule loaded")),
            ):
                screen = AppTest.from_file(str(ROOT / "views" / "balance_sheet.py"), default_timeout=30)
                screen.run()
            self.assertEqual(list(screen.exception), [])
            shown = " ".join(
                getattr(item, "value", "")
                for item in (*screen.error, *screen.caption, *screen.markdown, *screen.metric)
            )
            self.assertIn("Balance Sheet password not set yet.", shown)
            self.assertNotIn("Sample Shop", shown)
            self.assertNotIn("$", shown)
        finally:
            if saved is None:
                os.environ.pop(BALANCE_SHEET_PASSWORD_ENV, None)
            else:
                os.environ[BALANCE_SHEET_PASSWORD_ENV] = saved


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
