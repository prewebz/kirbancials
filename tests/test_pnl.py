"""Payment detection, period boundaries, and sample profit-and-loss totals."""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from kirbancials.periods import add_months, format_period, period_choice, resolve_period
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

    def test_charges_and_refunds_are_not_payments(self) -> None:
        self.assertFalse(is_card_payment(100, "Groceries", "Sample Grocery", "groceries"))
        self.assertFalse(is_card_payment(15, "Shopping", "Sample Bookstore", "pending book"))
        self.assertFalse(is_card_payment(-15, "Shopping", "Sample Return", "refund"))
        self.assertFalse(is_card_payment(0, "Payment", "PAYMENT THANK YOU", ""))
        self.assertFalse(is_card_payment(None, "Loan Payments", "PAYMENT", "PAYMENT"))


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
        self.assertEqual(by_name["Citibank CC"].total, 140)
        self.assertEqual(by_name["Citibank CC"].pending_charges, 1)
        self.assertEqual(by_name["Citibank CC"].data_through, date(2026, 11, 10))
        self.assertEqual(by_name["Nordstrom CC"].total, 0)
        self.assertEqual(by_name["Nordstrom CC"].latest_closing, date(2026, 1, 20))
        self.assertEqual(by_name["Sample card ending 0000"].total, 40)
        self.assertNotIn("Sample household expenses", by_name)
        self.assertEqual(sum(line.total for line in this_month), 180)
        self.assertEqual(total_income(), 0)
        self.assertIn("Citibank CC includes 1 pending charge.", caveats(this_month))
        self.assertIn(
            "Nordstrom CC from monthly statements, latest statement closed 2026-01-20.",
            caveats(this_month),
        )

        last_month = expense_lines(tx, monthly, resolve_period("last-month", TODAY))
        last_by_name = {line.name: line for line in last_month}
        self.assertEqual(last_by_name["Citibank CC"].total, 100)
        self.assertEqual(last_by_name["Citibank CC"].data_through, date(2026, 10, 31))
        self.assertNotIn("Sample card ending 0000", last_by_name)

        all_time = expense_lines(tx, monthly, resolve_period("all-time", TODAY))
        all_by_name = {line.name: line for line in all_time}
        self.assertEqual(all_by_name["Nordstrom CC"].total, 3100)
        self.assertEqual(all_by_name["Nordstrom CC"].interest, 100)
        self.assertEqual(all_by_name["Nordstrom CC"].fees, 100)
        self.assertEqual(all_by_name["Sample household expenses"].total, 700)


if __name__ == "__main__":
    unittest.main()
