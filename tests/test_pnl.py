"""Payment detection, period boundaries, and sample profit-and-loss totals."""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

from kirbancials.periods import add_months, format_period, period_choice, resolve_period
from kirbancials.sources import EXPENSE_SOURCES, expense_source_by_id, expense_source_for_account
from kirbancials.spend import caveats, expense_lines, is_card_payment, total_income

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 12)


class PaymentTests(unittest.TestCase):
    def test_payment_phrases_are_excluded_from_spend(self) -> None:
        self.assertTrue(is_card_payment(-200, "Payment", "PAYMENT THANK YOU", "thank you"))
        self.assertTrue(is_card_payment(-50, "Transfer", "ONLINE PAYMENT", ""))
        self.assertTrue(is_card_payment(-40, "", "AUTOPAY", "citi autopay"))
        self.assertTrue(is_card_payment(-40, "", "AUTO PAY", ""))
        self.assertTrue(is_card_payment(-30, "Loan Payments", "Sample Bank", "loan"))
        self.assertTrue(is_card_payment(-10, "Transfer Out", "Sample Transfer", ""))
        self.assertTrue(is_card_payment(-10, "Transfer In", "Inbound", ""))
        self.assertTrue(is_card_payment(-80, "Payment", "CAPITAL ONE MOBILE PYMT", ""))
        self.assertTrue(is_card_payment(-80, "", "PAYMENT - THANK YOU", ""))
        self.assertTrue(is_card_payment(-80, "", "ONLINE PAYMENT - THANK YOU", ""))
        self.assertTrue(is_card_payment(-80, "", "AUTOPAY PAYMENT", "amex"))

    def test_charges_and_refunds_are_not_payments(self) -> None:
        self.assertFalse(is_card_payment(100, "Groceries", "Sample Grocery", "groceries"))
        self.assertFalse(is_card_payment(15, "Shopping", "Sample Bookstore", "pending book"))
        self.assertFalse(is_card_payment(-15, "Shopping", "Sample Return", "refund"))
        self.assertFalse(is_card_payment(0, "Payment", "PAYMENT THANK YOU", ""))
        self.assertFalse(is_card_payment(None, "Loan Payments", "PAYMENT", "PAYMENT"))

    def test_charges_that_mention_payment_are_still_spend(self) -> None:
        self.assertFalse(is_card_payment(500, "Taxes", "US TREAS TAX PYMT", "federal tax"))
        self.assertFalse(is_card_payment(20, "Services", "ACME PAYMENT PROCESSING", "processor fee"))
        transactions = pd.DataFrame(
            [
                {
                    "Account": "Citi AAdvantage card ending 7690",
                    "Date": date(2026, 11, 2),
                    "Amount": 500,
                    "Pending": False,
                    "Category": "Taxes",
                    "Merchant": "US TREAS TAX PYMT",
                    "Description": "federal tax",
                },
                {
                    "Account": "Citi AAdvantage card ending 7690",
                    "Date": date(2026, 11, 3),
                    "Amount": 20,
                    "Pending": False,
                    "Category": "Services",
                    "Merchant": "ACME PAYMENT PROCESSING",
                    "Description": "processor fee",
                },
                {
                    "Account": "Citi AAdvantage card ending 7690",
                    "Date": date(2026, 11, 4),
                    "Amount": -80,
                    "Pending": False,
                    "Category": "Payment",
                    "Merchant": "CAPITAL ONE MOBILE PYMT",
                    "Description": "card payment",
                },
            ]
        )
        lines = expense_lines(transactions, pd.DataFrame(), resolve_period("this-month", TODAY))
        citi = next(line for line in lines if line.source_id == "citi")
        self.assertEqual(citi.total, 520)

    def test_amount_columns_stay_numeric_for_sorting(self) -> None:
        from kirbancials.formatting import money_column_config
        from kirbancials.pnl import _statement_table, _transaction_table

        transactions = _transaction_table(
            pd.DataFrame(
                {
                    "Date": pd.to_datetime(["2026-11-01", "2026-11-02", "2026-11-03"]),
                    "Merchant": ["A", "B", "US TREAS TAX PYMT"],
                    "Amount": [2.0, 100.0, 500.0],
                    "Pending": [False, False, False],
                    "Category": ["Food", "Shopping", "Taxes"],
                }
            )
        )
        self.assertTrue(pd.api.types.is_numeric_dtype(transactions["Amount"]))
        self.assertEqual(transactions["Amount"].tolist(), [2.0, 100.0, 500.0])
        statements = _statement_table(
            pd.DataFrame(
                {
                    "Month": ["2026-01", "2026-02"],
                    "Net Spend": [20.0, 3.0],
                    "Interest": [0.0, 1.5],
                    "Fees": [0.0, 0.0],
                }
            )
        )
        self.assertTrue(pd.api.types.is_numeric_dtype(statements["Net Spend"]))
        config = money_column_config(["Amount", "Net Spend"])
        self.assertEqual(config["Amount"]["type_config"]["type"], "number")
        self.assertEqual(config["Amount"]["type_config"]["format"], "dollar")
        self.assertEqual(config["Net Spend"]["type_config"]["format"], "dollar")


class PeriodTests(unittest.TestCase):
    def test_this_month_stops_at_today_and_starts_on_the_first(self) -> None:
        period = resolve_period("this-month", TODAY)
        self.assertEqual(period.start, date(2026, 11, 1))
        self.assertEqual(period.end, TODAY)
        self.assertTrue(period.contains(date(2026, 11, 1)))
        self.assertTrue(period.contains(TODAY))
        self.assertFalse(period.contains(date(2026, 10, 31)))
        self.assertFalse(period.contains(date(2026, 11, 13)))

    def test_last_month_uses_calendar_boundaries(self) -> None:
        november = resolve_period("last-month", TODAY)
        self.assertEqual((november.start, november.end), (date(2026, 10, 1), date(2026, 10, 31)))
        self.assertTrue(november.contains(date(2026, 10, 31)))
        self.assertFalse(november.contains(date(2026, 11, 1)))

        march = resolve_period("last-month", date(2026, 3, 15))
        self.assertEqual((march.start, march.end), (date(2026, 2, 1), date(2026, 2, 28)))
        leap = resolve_period("last-month", date(2024, 3, 15))
        self.assertEqual((leap.start, leap.end), (date(2024, 2, 1), date(2024, 2, 29)))
        january = resolve_period("last-month", date(2026, 1, 1))
        self.assertEqual((january.start, january.end), (date(2025, 12, 1), date(2025, 12, 31)))

    def test_year_to_date_and_trailing_twelve_months(self) -> None:
        ytd = resolve_period("ytd", TODAY)
        self.assertEqual(ytd.start, date(2026, 1, 1))
        self.assertEqual(ytd.end, TODAY)
        self.assertFalse(ytd.contains(date(2025, 12, 31)))

        trailing = resolve_period("last-12-months", TODAY)
        self.assertEqual(trailing.start, date(2025, 11, 13))
        self.assertEqual(trailing.end, TODAY)
        self.assertFalse(trailing.contains(date(2025, 11, 12)))
        self.assertTrue(trailing.contains(date(2025, 11, 13)))

        clamped = add_months(date(2026, 3, 31), -1)
        self.assertEqual(clamped, date(2026, 2, 28))
        self.assertEqual(format_period(resolve_period("all-time", TODAY)), "All time")

    def test_url_period_wins_when_the_link_changes(self) -> None:
        selected, seen = period_choice("ytd", "this-month", "this-month")
        self.assertEqual((selected, seen), ("ytd", "ytd"))
        selected, seen = period_choice("this-month", "last-month", "this-month")
        self.assertEqual(selected, "last-month")


class BofaSourceTests(unittest.TestCase):
    def test_registry_order_and_account_match(self) -> None:
        self.assertEqual(
            [source.id for source in EXPENSE_SOURCES],
            [
                "citi",
                "citi_platinum",
                "bofa",
                "bofa_pw_corp_2238",
                "bofa_pw_8330",
                "bofa_vwh_6484",
                "bofa_seinom_6315",
                "capital_one",
                "amex_gold",
                "morgan_stanley",
                "amex_bonvoy_1004",
                "amex_bonvoy_biz_2009",
                "nordstrom",
                "wf_auto_7635",
                "wf_auto_8492",
                "truist_auto_1001",
                "jeep_0001",
                "ks_statebank_805_loc_1279",
                "morpho_loan",
            ],
        )
        expected_accounts = {
            "citi_platinum": "Citi AAdvantage Platinum card ending 3590",
            "capital_one": "Capital One card ending 2391",
            "amex_gold": "Amex Business Gold card ending 2003",
            "morgan_stanley": "Morgan Stanley card ending 3008",
            "amex_bonvoy_1004": "Marriott Bonvoy Amex card ending 1004",
            "amex_bonvoy_biz_2009": "Bonvoy Business Amex card ending 2009",
            "bofa_pw_corp_2238": "Premium Web Corp card ending 2238",
            "bofa_pw_8330": "Premium Web card ending 8330",
            "bofa_vwh_6484": "VWH card ending 6484",
            "bofa_seinom_6315": "Seinom LLC WD card ending 6315",
        }
        for source_id, account in expected_accounts.items():
            registered = expense_source_by_id(source_id)
            self.assertIsNotNone(registered)
            assert registered is not None
            self.assertEqual(registered.data_type, "transactions")
            self.assertEqual(registered.accounts, (account,))
            self.assertIs(expense_source_for_account(account), registered)
        source = expense_source_by_id("bofa")
        self.assertIsNotNone(source)
        assert source is not None
        self.assertEqual(source.name, "Bank of America - 5225")
        self.assertEqual(source.data_type, "transactions")
        self.assertEqual(source.accounts, ("Bank of America card ending 5225",))
        self.assertIs(expense_source_for_account("Bank of America card ending 5225"), source)
        self.assertIsNone(expense_source_for_account("Bank of America"))
        self.assertIsNone(expense_source_for_account("5225"))

    def test_bofa_spend_uses_the_transaction_rules(self) -> None:
        transactions = pd.DataFrame(
            [
                {
                    "Account": "Bank of America card ending 5225",
                    "Date": date(2026, 11, 2),
                    "Amount": 100,
                    "Pending": False,
                    "Category": "Shopping",
                    "Merchant": "Sample Store",
                    "Description": "goods",
                },
                {
                    "Account": "Bank of America card ending 5225",
                    "Date": date(2026, 11, 3),
                    "Amount": 20,
                    "Pending": True,
                    "Category": "Shopping",
                    "Merchant": "Sample Pending",
                    "Description": "hold",
                },
                {
                    "Account": "Bank of America card ending 5225",
                    "Date": date(2026, 11, 4),
                    "Amount": -10,
                    "Pending": False,
                    "Category": "Shopping",
                    "Merchant": "Sample Return",
                    "Description": "refund",
                },
                {
                    "Account": "Bank of America card ending 5225",
                    "Date": date(2026, 11, 5),
                    "Amount": -50,
                    "Pending": False,
                    "Category": "Payment",
                    "Merchant": "PAYMENT THANK YOU",
                    "Description": "autopay",
                },
            ]
        )
        lines = expense_lines(transactions, pd.DataFrame(), resolve_period("this-month", TODAY))
        ordered = [line.name for line in lines]
        self.assertEqual(
            ordered,
            [
                "Citibank - 7690",
                "Citibank Platinum - 3590",
                "Bank of America - 5225",
                "Premium Web Corp - 2238",
                "Premium Web - 8330",
                "VWH - 6484",
                "Seinom LLC WD - 6315",
                "Capital One - 2391",
                "Amex Business Gold - 2003",
                "Morgan Stanley - 3008",
                "Marriott Bonvoy - 1004",
                "Bonvoy Business - 2009",
                "Nordstrom - 5488",
                "Wells Fargo Auto - 7635",
                "Wells Fargo Auto - 8492",
                "Truist Auto - 1001",
                "Stellantis Jeep - 0001",
                "KS StateBank 805 LOC - 1279 (Prime + 1.5%)",
                "Morpho - Loan (4.5%, BTC-backed)",
            ],
        )
        bofa = next(line for line in lines if line.source_id == "bofa")
        self.assertEqual(bofa.total, 110)
        self.assertEqual(bofa.pending_charges, 1)
        self.assertEqual(bofa.data_through, date(2026, 11, 5))


class SamplePnlTests(unittest.TestCase):
    def test_sample_totals_for_this_month_and_last_month(self) -> None:
        from kirbancials.activity import load_transactions
        from kirbancials.data import DataOrigin, load_dataset

        monthly = load_dataset(
            ROOT / "sample_data" / "example_statements.csv",
            label="sample",
            is_sample=True,
        ).monthly
        tx = load_transactions(
            DataOrigin(ROOT / "sample_data" / "transactions.csv", "sample", True)
        ).rows

        this_month = expense_lines(tx, monthly, resolve_period("this-month", TODAY))
        by_name = {line.name: line for line in this_month}
        self.assertEqual(by_name["Citibank - 7690"].total, 140)
        self.assertEqual(by_name["Citibank - 7690"].pending_charges, 1)
        self.assertEqual(by_name["Citibank - 7690"].data_through, date(2026, 11, 10))
        self.assertEqual(by_name["Nordstrom - 5488"].total, 0)
        self.assertEqual(by_name["Nordstrom - 5488"].latest_closing, date(2026, 1, 20))
        self.assertEqual(by_name["Sample card ending 0000"].total, 40)
        self.assertNotIn("Sample household expenses", by_name)
        self.assertEqual(sum(line.total for line in this_month), 180)
        self.assertEqual(total_income(), 0)
        self.assertIn("Citibank - 7690 includes 1 pending charge.", caveats(this_month))
        self.assertIn(
            "Nordstrom - 5488 from monthly statements, latest statement closed 2026-01-20.",
            caveats(this_month),
        )

        last_month = expense_lines(tx, monthly, resolve_period("last-month", TODAY))
        last_by_name = {line.name: line for line in last_month}
        self.assertEqual(last_by_name["Citibank - 7690"].total, 100)
        self.assertEqual(last_by_name["Citibank - 7690"].data_through, date(2026, 10, 31))
        self.assertNotIn("Sample card ending 0000", last_by_name)

        all_time = expense_lines(tx, monthly, resolve_period("all-time", TODAY))
        all_by_name = {line.name: line for line in all_time}
        self.assertEqual(all_by_name["Nordstrom - 5488"].total, 3100)
        self.assertEqual(all_by_name["Nordstrom - 5488"].interest, 100)
        self.assertEqual(all_by_name["Nordstrom - 5488"].fees, 100)
        self.assertEqual(all_by_name["Sample household expenses"].total, 700)


class LoanInterestTests(unittest.TestCase):
    def test_interest_uses_balance_apr_and_elapsed_days(self) -> None:
        from kirbancials.loans import (
            elapsed_days,
            estimated_daily_interest,
            estimated_interest,
            estimated_monthly_interest,
        )
        from kirbancials.pnl import loan_label_for

        this_month = resolve_period("this-month", TODAY)
        last_month = resolve_period("last-month", TODAY)
        self.assertEqual(elapsed_days(this_month), 12)
        self.assertEqual(elapsed_days(last_month), 31)
        self.assertIsNone(elapsed_days(resolve_period("all-time", TODAY)))
        balance = 10000.0
        apr = 7.49
        self.assertAlmostEqual(estimated_interest(balance, apr, 12), balance * apr / 100 / 365 * 12)
        self.assertAlmostEqual(estimated_interest(balance, apr, 31), balance * apr / 100 / 365 * 31)
        self.assertEqual(estimated_interest(None, apr, 12), 0.0)
        self.assertAlmostEqual(estimated_monthly_interest(balance, apr), balance * apr / 100 / 12)
        self.assertAlmostEqual(estimated_daily_interest(balance, apr), balance * apr / 100 / 365)

        account = "Wells Fargo Auto Loan ending 7635"
        transactions = pd.DataFrame(
            [
                {
                    "Account": "Citi AAdvantage card ending 7690",
                    "Date": pd.Timestamp("2026-11-02"),
                    "Amount": 100,
                    "Pending": False,
                    "Category": "Shopping",
                    "Merchant": "Sample Store",
                    "Description": "goods",
                },
                {
                    "Account": account,
                    "Date": pd.Timestamp("2026-11-03"),
                    "Amount": -400,
                    "Pending": False,
                    "Category": "Loan Payments",
                    "Merchant": "WELLS FARGO",
                    "Description": "AUTO PAY",
                },
                {
                    "Account": account,
                    "Date": pd.Timestamp("2026-11-08"),
                    "Amount": 999,
                    "Pending": False,
                    "Category": "Shopping",
                    "Merchant": "US TREAS TAX PYMT",
                    "Description": "not a card charge",
                },
            ]
        )
        balances = pd.DataFrame(
            {
                "Account": [account],
                "As Of": ["2026-11-12T08:00:00Z"],
                "Current Balance": [balance],
            }
        )
        lines = expense_lines(transactions, pd.DataFrame(), this_month, balances)
        by_id = {line.source_id: line for line in lines}
        self.assertEqual(by_id["citi"].total, 100)
        self.assertAlmostEqual(by_id["wf_auto_7635"].total, estimated_interest(balance, apr, 12))
        self.assertEqual(by_id["wf_auto_7635"].data_through, date(2026, 11, 12))
        self.assertFalse(any(line.source_id.startswith("account:") for line in lines))
        self.assertEqual(loan_label_for(by_id["wf_auto_7635"]), "Wells Fargo Auto - 7635 (7.49% APR)")
        self.assertEqual(
            [line.source_id for line in lines if line.data_type == "loan"],
            [
                "wf_auto_7635",
                "wf_auto_8492",
                "truist_auto_1001",
                "jeep_0001",
                "ks_statebank_805_loc_1279",
                "morpho_loan",
            ],
        )

        missing = expense_lines(transactions, pd.DataFrame(), this_month, pd.DataFrame())
        missing_loan = next(line for line in missing if line.source_id == "wf_auto_7635")
        self.assertEqual(missing_loan.total, 0)
        self.assertTrue(missing_loan.balance_missing)
        self.assertIn("Wells Fargo Auto - 7635 balance is missing, so interest is $0.", caveats(missing))

        from kirbancials.activity import month_to_date_spend
        from kirbancials.loans import loan_payment_table

        spend = month_to_date_spend(transactions, TODAY)
        self.assertNotIn(account, set(spend["Account"]))
        self.assertIn("Citi AAdvantage card ending 7690", set(spend["Account"]))
        payments = loan_payment_table(transactions, account, this_month)
        self.assertTrue(pd.api.types.is_numeric_dtype(payments["Amount"]))
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(payments["Date"]))
        self.assertEqual(
            pd.to_datetime(payments["Date"]).dt.strftime("%Y-%m-%d").tolist(),
            ["2026-11-08", "2026-11-03"],
        )
        self.assertEqual(payments["Amount"].tolist(), [999, -400])

    def test_ks_statebank_loc_interest_uses_the_auto_loan_formula(self) -> None:
        """Fake round balance. The rate is prime plus the margin, not a statement export."""
        from kirbancials.loans import elapsed_days, estimated_interest
        from kirbancials.pnl import loan_label_for

        source = expense_source_by_id("ks_statebank_805_loc_1279")
        self.assertIsNotNone(source)
        assert source is not None
        self.assertEqual(source.name, "KS StateBank 805 LOC - 1279 (Prime + 1.5%)")
        self.assertEqual(source.data_type, "loan")
        self.assertEqual(source.accounts, ("KS StateBank 805 LOC - 1279 (Prime + 1.5%)",))
        self.assertEqual(source.apr, 8.50)

        account = source.accounts[0]
        balance = 1000.0
        this_month = resolve_period("this-month", TODAY)
        days = elapsed_days(this_month)
        balances = pd.DataFrame(
            {
                "Account": [account],
                "As Of": ["2026-11-12T08:00:00Z"],
                "Current Balance": [balance],
            }
        )
        transactions = pd.DataFrame(
            [
                {
                    "Account": account,
                    "Date": pd.Timestamp("2026-11-04"),
                    "Amount": -25,
                    "Pending": False,
                    "Category": "Loan Payments",
                    "Merchant": "SAMPLE BANK",
                    "Description": "LOC PAYMENT",
                }
            ]
        )
        lines = expense_lines(transactions, pd.DataFrame(), this_month, balances)
        line = next(item for item in lines if item.source_id == source.id)
        expected = balance * 8.50 / 100 / 365 * days
        self.assertAlmostEqual(line.total, expected)
        self.assertAlmostEqual(line.interest, expected)
        self.assertAlmostEqual(line.total, estimated_interest(balance, source.apr or 0, days))
        self.assertEqual(
            loan_label_for(line),
            "KS StateBank 805 LOC - 1279 (Prime + 1.5%) (8.50% APR)",
        )

        from kirbancials.activity import month_to_date_spend

        spend = month_to_date_spend(transactions, TODAY)
        self.assertNotIn(account, set(spend["Account"]))

    def test_morpho_loan_interest_uses_the_auto_loan_formula(self) -> None:
        """Fake round balance. Not a wallet export."""
        from kirbancials.loans import elapsed_days, estimated_interest
        from kirbancials.pnl import loan_label_for

        source = expense_source_by_id("morpho_loan")
        self.assertIsNotNone(source)
        assert source is not None
        self.assertEqual(source.name, "Morpho - Loan (4.5%, BTC-backed)")
        self.assertEqual(source.data_type, "loan")
        self.assertEqual(source.accounts, ("Morpho - Loan (4.5%, BTC-backed)",))
        self.assertEqual(source.apr, 4.50)

        account = source.accounts[0]
        balance = 2000.0
        this_month = resolve_period("this-month", TODAY)
        days = elapsed_days(this_month)
        balances = pd.DataFrame(
            {
                "Account": [account],
                "As Of": ["2026-11-12T08:00:00Z"],
                "Current Balance": [balance],
            }
        )
        transactions = pd.DataFrame(
            [
                {
                    "Account": account,
                    "Date": pd.Timestamp("2026-11-04"),
                    "Amount": -40,
                    "Pending": False,
                    "Category": "Loan Payments",
                    "Merchant": "SAMPLE LENDER",
                    "Description": "LOAN PAYMENT",
                }
            ]
        )
        lines = expense_lines(transactions, pd.DataFrame(), this_month, balances)
        line = next(item for item in lines if item.source_id == source.id)
        expected = balance * 4.50 / 100 / 365 * days
        self.assertAlmostEqual(line.total, expected)
        self.assertAlmostEqual(line.interest, expected)
        self.assertAlmostEqual(line.total, estimated_interest(balance, source.apr or 0, days))
        self.assertEqual(loan_label_for(line), "Morpho - Loan (4.5%, BTC-backed) (4.50% APR)")

        from kirbancials.activity import month_to_date_spend

        spend = month_to_date_spend(transactions, TODAY)
        self.assertNotIn(account, set(spend["Account"]))


PAGE_TITLES = {
    "profit_and_loss.py": "Profit & Loss",
    "balance_sheet.py": "Balance Sheet",
    "bills.py": "Bills",
    "vwh_pl.py": "VWH P&L",
    "source_detail.py": "Source detail",
    "daily.py": "Daily spending",
    "statements.py": "Monthly statements",
}


class PageTitleTests(unittest.TestCase):
    def test_every_page_uses_the_shared_compact_title(self) -> None:
        for name, title in PAGE_TITLES.items():
            view = (ROOT / "views" / name).read_text()
            self.assertIn(f'render_page_title("{title}")', view, name)
            self.assertNotIn("st.title(", view, name)
            self.assertNotIn("pnl-title", view, name)
            self.assertNotIn("<style>", view, name)

    def test_compact_title_is_smaller_than_the_old_h1_and_above_sections(self) -> None:
        app = AppTest.from_string(
            """
from kirbancials.page_title import render_page_title
import streamlit as st

render_page_title("Profit & Loss")
st.subheader("Income")
""",
            default_timeout=30,
        ).run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(list(app.title), [])
        self.assertEqual(app.subheader[0].value, "Income")
        body = app.html[0].value
        self.assertIn('class="page-title"', body)
        self.assertIn("Profit &amp; Loss", body)
        self.assertNotIn("Profit & Loss", body.split("<p", 1)[-1])
        self.assertIn("padding-top: 3.9rem", body)
        self.assertIn("font-size: 1.125rem", body)
        self.assertIn("@media (max-width: 768px)", body)
        self.assertIn('div[data-testid="stHeading"] h3', body)
        self.assertIn("font-size: 1rem", body)
        self.assertIn("font-size: 0.9375rem", body)
        title_rule = body.split(".page-title {", 1)[1].split("}", 1)[0]
        section_rule = body.split('div[data-testid="stHeading"] h3 {', 1)[1].split("}", 1)[0]
        self.assertIn("font-size: 1.125rem", title_rule)
        self.assertIn("font-size: 1rem", section_rule)
        self.assertNotIn("1.125rem", section_rule)

    def test_last_refreshed_stays_in_the_sidebar_only(self) -> None:
        text = (ROOT / "app.py").read_text()
        before, sidebar = text.split("with st.sidebar:", 1)
        self.assertNotIn("refreshed_caption()", before)
        self.assertIn("st.caption(refreshed_caption())", sidebar)
        self.assertIn('key="refresh_header"', before)
        self.assertIn('key="refresh_sidebar"', sidebar)


def _expense_line(source_id: str, name: str, total: float, data_type: str = "transactions"):
    from kirbancials.spend import ExpenseLine

    return ExpenseLine(
        source_id=source_id,
        name=name,
        data_type=data_type,
        total=total,
        data_through=None,
        pending_charges=0,
        interest=0.0,
        fees=0.0,
        latest_closing=None,
    )


class PnlLineOrderTests(unittest.TestCase):
    """Fake amounts only. $1,500 must rank above $900 even though the text does not."""

    def test_amount_order_is_numeric_and_ties_keep_their_place(self) -> None:
        from kirbancials.formatting import format_money
        from kirbancials.income import IncomeLine
        from kirbancials.pnl import _ordered_by_amount

        lines = [
            IncomeLine("small", "Sample small", "Monthly", 900),
            IncomeLine("big", "Sample big", "Monthly", 1500),
            IncomeLine("tie_first", "Sample tie first", "Quarterly", 900),
            IncomeLine("mid", "Sample mid", "Irregular", 400),
            IncomeLine("credit", "Sample credit", "", -80),
        ]
        self.assertEqual(
            [line.source_id for line in _ordered_by_amount(lines)],
            ["big", "small", "tie_first", "mid", "credit"],
        )
        as_text = [format_money(line.total) for line in lines if line.total > 0]
        self.assertEqual(sorted(as_text, reverse=True)[0], "$900.00")
        self.assertNotEqual(sorted(as_text, reverse=True)[0], "$1,500.00")

    def test_expenses_use_the_charge_convention(self) -> None:
        from kirbancials.pnl import _ordered_by_amount

        lines = [
            _expense_line("refund", "Sample net credit", -250),
            _expense_line("small", "Sample small card", 25),
            _expense_line("big", "Sample big card", 900),
            _expense_line("same", "Sample same card", 25),
        ]
        self.assertEqual(
            [line.source_id for line in _ordered_by_amount(lines)],
            ["big", "small", "same", "refund"],
        )

    def test_page_lists_each_group_highest_first_and_keeps_totals_last(self) -> None:
        import inspect

        from kirbancials.formatting import format_money
        from kirbancials.pnl import render_profit_and_loss

        # The harness lives next to app.py so the source-detail page link resolves.
        # It is deleted before the test returns. Amounts are fake.
        harness = ROOT / "_pnl_order_harness.py"
        harness.write_text(
            """
import streamlit as st
from datetime import date

from kirbancials.income import IncomeLine
from kirbancials.periods import resolve_period
from kirbancials.pnl import _expense_rows, _income_rows
from kirbancials.spend import ExpenseLine

st.navigation(
    [
        st.Page("views/profit_and_loss.py", title="Profit & Loss", default=True),
        st.Page("views/source_detail.py", title="Source detail", url_path="source"),
    ]
)
period = resolve_period("this-month", date(2026, 11, 12))
_income_rows(
    [
        IncomeLine("small", "Sample small", "Monthly", 200),
        IncomeLine("big", "Sample big", "Monthly", 1500),
        IncomeLine("tie", "Sample tie", "Quarterly", 200),
    ],
    period,
    1900,
)
_expense_rows(
    [
        ExpenseLine("card_small", "Sample card small", "transactions", 40, None, 0, 0, 0, None),
        ExpenseLine("card_big", "Sample card big", "transactions", 800, None, 0, 0, 0, None),
    ],
    period,
)
_expense_rows(
    [
        ExpenseLine("loan_small", "Sample loan small", "loan", 10, None, 0, 0, 0, None),
        ExpenseLine("loan_big", "Sample loan big", "loan", 5000, None, 0, 0, 0, None),
    ],
    period,
)
"""
        )
        try:
            app = AppTest.from_file(harness, default_timeout=30).run()
        finally:
            harness.unlink(missing_ok=True)
        self.assertEqual(list(app.exception), [])
        links = [link.value for link in app.page_link]
        self.assertEqual(
            links,
            [
                format_money(1500),
                format_money(200),
                format_money(200),
                "Sample card big",
                "Sample card small",
                "Sample loan big",
                "Sample loan small",
            ],
        )
        rendered = [node.value for node in app.markdown]
        income_labels = ["Sample big (Monthly)", "Sample small (Monthly)", "Sample tie (Quarterly)"]
        label_at = [rendered.index(label) for label in income_labels]
        total_at = rendered.index("**Total income**")
        self.assertEqual(label_at, sorted(label_at))
        self.assertLess(label_at[-1], total_at)
        self.assertEqual(rendered[total_at : total_at + 2], ["**Total income**", f"**{format_money(1900)}**"])
        self.assertLess(total_at, rendered.index("$800.00"))
        self.assertLess(rendered.index("$800.00"), rendered.index("$40.00"))
        self.assertLess(rendered.index("$5,000.00"), rendered.index("$10.00"))
        self.assertLess(links.index("Sample card small"), links.index("Sample loan big"))
        page = inspect.getsource(render_profit_and_loss)
        self.assertLess(page.index("_expense_rows(schedule_lines, period)"), page.index("**Total expenses**"))


class PriorMonthLabelTests(unittest.TestCase):
    def test_header_shows_a_gray_last_month_line_and_hides_it_without_data(self) -> None:
        from kirbancials.formatting import format_money
        from streamlit.proto.Metric_pb2 import Metric as MetricProto

        script = """
from datetime import date

from kirbancials.periods import resolve_period
from kirbancials.pnl import _render_summary

period = resolve_period("this-month", date(2026, 11, 12))
_render_summary(1000, 180, 820, period, date(2026, 10, 1), 100)
_render_summary(0, 50, -50, period, None, None)
"""
        app = AppTest.from_string(script, default_timeout=30).run()
        self.assertEqual(list(app.exception), [])
        shown = app.metric[1]
        hidden = app.metric[5]
        self.assertEqual(shown.label, "Total expenses")
        self.assertEqual(shown.value, format_money(180))
        self.assertEqual(shown.delta, f"Last month: {format_money(100)}")
        self.assertEqual(shown.color, MetricProto.MetricColor.GRAY)
        self.assertEqual(shown.direction, MetricProto.MetricDirection.NONE)
        self.assertEqual(hidden.label, "Total expenses")
        self.assertEqual(hidden.value, format_money(50))
        self.assertEqual(hidden.delta, "")


if __name__ == "__main__":
    unittest.main()
