"""Payment detection, period boundaries, and sample profit-and-loss totals."""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

import pandas as pd

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
                "nordstrom",
                "wf_auto_7635",
                "wf_auto_8492",
                "truist_auto_1001",
                "jeep_0001",
            ],
        )
        expected_accounts = {
            "citi_platinum": "Citi AAdvantage Platinum card ending 3590",
            "capital_one": "Capital One card ending 2391",
            "amex_gold": "Amex Business Gold card ending 2003",
            "morgan_stanley": "Morgan Stanley card ending 3008",
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
                "Nordstrom - 5488",
                "Wells Fargo Auto - 7635",
                "Wells Fargo Auto - 8492",
                "Truist Auto - 1001",
                "Stellantis Jeep - 0001",
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
            ["wf_auto_7635", "wf_auto_8492", "truist_auto_1001", "jeep_0001"],
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
        self.assertEqual(payments["Date"].tolist(), ["2026-11-08", "2026-11-03"])
        self.assertEqual(payments["Amount"].tolist(), [999, -400])


if __name__ == "__main__":
    unittest.main()
