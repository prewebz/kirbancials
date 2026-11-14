"""Run rate, expected income, and dashboard period ranges.

Fixtures are fake round numbers. They are not account exports.
"""

from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

from kirbancials.income import load_income, resolve_income
from kirbancials.loans import estimated_interest
from kirbancials.overview import (
    credit_card_balance_total,
    estimated_loan_interest_this_month,
    expected_income_next_month,
    expense_coverage_note,
    expense_data_start,
    income_expense_by_month,
    monthly_run_rate,
    recent_month_periods,
)
from kirbancials.periods import PERIOD_KEYS, PERIOD_LABELS, Period, resolve_period
from kirbancials.sources import IncomeSource
from kirbancials.spend import expense_lines

TODAY = date(2026, 11, 12)

SOURCES = (
    IncomeSource("rent", "Sample Rent", "Monthly"),
    IncomeSource("tax", "Sample Quarterly", "Quarterly"),
    IncomeSource("gift", "Sample Gifts", "Irregular"),
    IncomeSource("bonus", "Sample Bonus", "IRREGULAR"),
    IncomeSource("other", "Sample Other", ""),
)


def _deposits(text: str) -> pd.DataFrame:
    origin = resolve_income({"FINANCE_INCOME_CSV": text})
    assert origin is not None
    return load_income(origin)


class PeriodRangeTests(unittest.TestCase):
    def test_picker_lists_quarter_to_date_and_defaults_to_this_month(self) -> None:
        self.assertEqual(
            PERIOD_KEYS,
            ("this-month", "last-month", "qtd", "ytd", "last-12-months"),
        )
        self.assertEqual(PERIOD_LABELS["qtd"], "Quarter to date")
        self.assertEqual(PERIOD_KEYS[0], "this-month")
        self.assertNotIn("all-time", PERIOD_KEYS)
        self.assertEqual(resolve_period("all-time", TODAY).start, None)
        self.assertEqual(resolve_period("missing", TODAY).key, "this-month")

    def test_quarter_to_date_uses_the_calendar_quarter(self) -> None:
        november = resolve_period("qtd", TODAY)
        self.assertEqual((november.start, november.end), (date(2026, 10, 1), TODAY))
        self.assertTrue(november.contains(date(2026, 10, 1)))
        self.assertFalse(november.contains(date(2026, 9, 30)))
        self.assertFalse(november.contains(date(2026, 11, 13)))

        self.assertEqual(
            (resolve_period("qtd", date(2026, 1, 15)).start, resolve_period("qtd", date(2026, 1, 15)).end),
            (date(2026, 1, 1), date(2026, 1, 15)),
        )
        self.assertEqual(
            (resolve_period("qtd", date(2026, 4, 1)).start, resolve_period("qtd", date(2026, 4, 1)).end),
            (date(2026, 4, 1), date(2026, 4, 1)),
        )
        first_quarter = resolve_period("qtd", date(2026, 3, 31))
        self.assertEqual((first_quarter.start, first_quarter.end), (date(2026, 1, 1), date(2026, 3, 31)))
        self.assertEqual(
            (resolve_period("qtd", date(2026, 12, 31)).start, resolve_period("qtd", date(2026, 12, 31)).end),
            (date(2026, 10, 1), date(2026, 12, 31)),
        )

    def test_recent_months_end_today_and_cover_six_calendar_months(self) -> None:
        months = recent_month_periods(TODAY)
        self.assertEqual(
            [(period.start, period.end) for period in months],
            [
                (date(2026, 6, 1), date(2026, 6, 30)),
                (date(2026, 7, 1), date(2026, 7, 31)),
                (date(2026, 8, 1), date(2026, 8, 31)),
                (date(2026, 9, 1), date(2026, 9, 30)),
                (date(2026, 10, 1), date(2026, 10, 31)),
                (date(2026, 11, 1), TODAY),
            ],
        )
        january = recent_month_periods(date(2026, 1, 15), 2)
        self.assertEqual(
            [(period.start, period.end) for period in january],
            [
                (date(2025, 12, 1), date(2025, 12, 31)),
                (date(2026, 1, 1), date(2026, 1, 15)),
            ],
        )


class RunRateTests(unittest.TestCase):
    def test_short_period_scales_to_thirty_days(self) -> None:
        this_month = resolve_period("this-month", TODAY)
        rate = monthly_run_rate(120, this_month)
        self.assertEqual(rate.label, "Scaled to 30 days")
        self.assertTrue(rate.scaled_to_30_days)
        self.assertAlmostEqual(rate.amount, 300)

        loss = monthly_run_rate(-60, this_month)
        self.assertAlmostEqual(loss.amount, -150)
        self.assertEqual(loss.label, "Scaled to 30 days")

        march = resolve_period("this-month", date(2026, 3, 14))
        scaled = monthly_run_rate(140, march)
        self.assertAlmostEqual(scaled.amount, 300)
        self.assertNotAlmostEqual(scaled.amount, 140 / (14 / 31))

        quarter_day = resolve_period("qtd", date(2026, 4, 1))
        self.assertAlmostEqual(monthly_run_rate(10, quarter_day).amount, 300)

    def test_full_months_average_net_per_calendar_month(self) -> None:
        october = resolve_period("this-month", date(2026, 10, 31))
        full = monthly_run_rate(310, october)
        self.assertEqual(full.label, "Average net per month")
        self.assertFalse(full.scaled_to_30_days)
        self.assertAlmostEqual(full.amount, 310)

        february = Period("custom", "February", date(2026, 2, 1), date(2026, 2, 28))
        self.assertAlmostEqual(monthly_run_rate(280, february).amount, 280)

        quarter = resolve_period("qtd", TODAY)
        self.assertAlmostEqual(monthly_run_rate(140, quarter).amount, 100)

        year = resolve_period("ytd", TODAY)
        self.assertAlmostEqual(monthly_run_rate(1040, year).amount, 100)

        trailing = resolve_period("last-12-months", TODAY)
        self.assertAlmostEqual(monthly_run_rate(1200, trailing).amount, 100)

        closed_quarter = resolve_period("qtd", date(2026, 3, 31))
        self.assertAlmostEqual(monthly_run_rate(300, closed_quarter).amount, 100)

    def test_open_period_has_no_run_rate(self) -> None:
        rate = monthly_run_rate(100, resolve_period("all-time", TODAY))
        self.assertIsNone(rate.amount)
        self.assertEqual(rate.label, "No fixed period")


class ExpectedIncomeTests(unittest.TestCase):
    def test_monthly_quarterly_and_irregular_rules(self) -> None:
        deposits = _deposits(
            """Transaction ID,Date,Source,Description,Amount
rent-oct,2026-10-01,rent,October,1000
rent-nov,2026-11-02,rent,November,1100
rent-later,2026-11-20,rent,After today,9999
tax-sep,2026-09-15,tax,Due in December,500
gift-nov,2026-11-01,gift,Left out,50
bonus-nov,2026-11-01,bonus,Also left out,80
other-nov,2026-11-01,other,No schedule,25
"""
        )
        expected = expected_income_next_month(deposits, TODAY, SOURCES)
        self.assertAlmostEqual(expected.total, 1600)
        self.assertEqual(expected.irregular_names, ("Sample Gifts", "Sample Bonus"))
        self.assertEqual(expected.note, "Irregular excluded: Sample Gifts, Sample Bonus")
        self.assertNotIn("Sample Other", expected.note)

    def test_quarterly_is_included_only_when_the_next_due_is_next_month(self) -> None:
        included = _deposits(
            """Transaction ID,Date,Source,Description,Amount
tax,2026-09-01,tax,Due December 1,400
"""
        )
        due = expected_income_next_month(included, TODAY, SOURCES)
        self.assertAlmostEqual(due.total, 400)

        excluded = _deposits(
            """Transaction ID,Date,Source,Description,Amount
tax,2026-11-05,tax,Due in February,500
rent,2026-11-01,rent,Monthly,20
"""
        )
        later = expected_income_next_month(excluded, TODAY, SOURCES)
        self.assertAlmostEqual(later.total, 20)

        clamped = _deposits(
            """Transaction ID,Date,Source,Description,Amount
tax,2026-08-31,tax,Clamped to November 30,70
"""
        )
        november = expected_income_next_month(clamped, date(2026, 10, 20), SOURCES)
        self.assertAlmostEqual(november.total, 70)
        not_yet = expected_income_next_month(
            _deposits(
                """Transaction ID,Date,Source,Description,Amount
tax,2026-09-01,tax,Due December,70
"""
            ),
            date(2026, 10, 20),
            SOURCES,
        )
        self.assertAlmostEqual(not_yet.total, 0)

    def test_same_day_amounts_sum_and_future_payments_are_ignored(self) -> None:
        deposits = _deposits(
            """Transaction ID,Date,Source,Description,Amount
a,2026-10-01,rent,Earlier,100
b,2026-11-02,rent,Same day,1000
c,2026-11-02,rent,Same day extra,100
d,2026-11-13,rent,After the sample today,9999
"""
        )
        expected = expected_income_next_month(deposits, TODAY, (IncomeSource("rent", "Sample Rent", "monthly"),))
        self.assertAlmostEqual(expected.total, 1100)
        self.assertEqual(expected.note, "")

    def test_missing_payments_are_zero(self) -> None:
        expected = expected_income_next_month(pd.DataFrame(), TODAY, SOURCES)
        self.assertEqual(expected.total, 0)
        self.assertIn("Sample Gifts", expected.note)
        none = expected_income_next_month(None, TODAY, (IncomeSource("rent", "Sample Rent", "Monthly"),))
        self.assertEqual(none.total, 0)
        self.assertEqual(none.note, "")


class SnapshotAndCoverageTests(unittest.TestCase):
    def test_card_balances_skip_loans_and_keep_the_latest_row(self) -> None:
        balances = pd.DataFrame(
            {
                "Account": [
                    "Citi AAdvantage card ending 7690",
                    "Citi AAdvantage card ending 7690",
                    "Wells Fargo Auto Loan ending 7635",
                    "Sample card ending 0000",
                ],
                "As Of": [
                    "2026-11-01T00:00:00Z",
                    "2026-11-12T00:00:00Z",
                    "2026-11-12T00:00:00Z",
                    "2026-11-12T00:00:00Z",
                ],
                "Current Balance": [10, 400, 10000, 25],
            }
        )
        self.assertEqual(credit_card_balance_total(balances), 425)
        self.assertEqual(credit_card_balance_total(pd.DataFrame()), 0)
        self.assertEqual(credit_card_balance_total(None), 0)

    def test_loan_interest_matches_the_profit_and_loss_estimate(self) -> None:
        account = "Wells Fargo Auto Loan ending 7635"
        balances = pd.DataFrame(
            {
                "Account": [account],
                "As Of": ["2026-11-12T08:00:00Z"],
                "Current Balance": [10000],
            }
        )
        interest = estimated_loan_interest_this_month(balances, TODAY)
        self.assertAlmostEqual(interest, estimated_interest(10000, 7.49, 12))
        lines = expense_lines(pd.DataFrame(), pd.DataFrame(), resolve_period("this-month", TODAY), balances)
        loan_total = sum(line.total for line in lines if line.data_type == "loan")
        self.assertAlmostEqual(interest, loan_total)

    def test_months_before_the_transaction_window_are_not_zero(self) -> None:
        transactions = pd.DataFrame(
            [
                {
                    "Account": "Citi AAdvantage card ending 7690",
                    "Date": date(2026, 10, 5),
                    "Amount": 80,
                    "Pending": False,
                    "Category": "Shopping",
                    "Merchant": "Sample Store",
                    "Description": "goods",
                }
            ]
        )
        monthly = pd.DataFrame(
            [
                {
                    "Account": "Nordstrom card ending 5488",
                    "Month": "2026-07",
                    "Statement Closing Date": "2026-07-20",
                    "Net Spend": 200,
                    "Interest": 0,
                    "Fees": 0,
                }
            ]
        )
        deposits = _deposits(
            """Transaction ID,Date,Source,Description,Amount
jun,2026-06-03,chipotle_nnn,June sample,50
"""
        )
        available = expense_data_start(transactions)
        self.assertEqual(available, date(2026, 10, 5))
        self.assertEqual(
            expense_coverage_note(resolve_period("ytd", TODAY).start, available),
            "Expense data available from 2026-10-05",
        )
        self.assertIsNone(expense_coverage_note(resolve_period("this-month", TODAY).start, available))
        self.assertEqual(
            expense_coverage_note(None, available),
            "Expense data available from 2026-10-05",
        )

        frame = income_expense_by_month(transactions, monthly, deposits, pd.DataFrame(), TODAY)
        by_month = {row.month: row for row in frame.itertuples(index=False)}
        june = by_month["Jun 2026"]
        self.assertEqual(june.income, 50)
        self.assertTrue(pd.isna(june.expenses))
        self.assertTrue(pd.isna(june.net))
        july = by_month["Jul 2026"]
        self.assertEqual(july.expenses, 200)
        self.assertEqual(july.net, -200)
        october = by_month["Oct 2026"]
        self.assertEqual(october.expenses, 80)
        self.assertEqual(october.net, -80)
        november = by_month["Nov 2026"]
        self.assertEqual(november.expenses, 0)
        self.assertEqual(november.income, 0)


if __name__ == "__main__":
    unittest.main()
