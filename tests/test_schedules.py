"""Amortization schedules. Fixtures are fake round numbers, not loan exports."""

from __future__ import annotations

import os
import unittest
from datetime import date

import pandas as pd

from kirbancials.income import net_profit
from kirbancials.loans import estimated_interest
from kirbancials.overview import (
    estimated_loan_interest_this_month,
    income_expense_by_month,
    monthly_run_rate,
)
from kirbancials.periods import Period, resolve_period
from kirbancials.pnl import loan_label_for
from kirbancials.schedules import (
    LOAN_SCHEDULE_ENV,
    SCHEDULE_COLUMNS,
    loan_snapshots,
    remaining_balance,
    resolve_loan_schedule,
    rows_for_loan,
    schedule_payment_table,
    scheduled_balance_total,
    snapshot_line,
)
from kirbancials.spend import expense_lines, line_for_source_id

TODAY = date(2026, 11, 12)
HEADER = (
    "Loan ID,Lender,Loan Name,Collateral,Rate,Payment Number,"
    "Payment Date,Payment,Interest,Principal,Remaining Balance"
)

# Fake schedule. Payment 5 is after TODAY, so This month excludes it.
FAKE_SCHEDULE = f"""{HEADER}
sample-note,Sample Lender,Sample cash-out,Sample property,5.9,0,2025-12-01,7.00,7.00,0.00,1000.00
sample-note,Sample Lender,Sample cash-out,Sample property,5.9,1,2026-01-15,15.00,5.00,10.00,990.00
sample-note,Sample Lender,Sample cash-out,Sample property,5.9,2,2026-06-15,15.00,4.50,10.50,979.50
sample-note,Sample Lender,Sample cash-out,Sample property,5.9,3,2026-10-15,15.00,4.00,11.00,968.50
sample-note,Sample Lender,Sample cash-out,Sample property,5.9,4,2026-11-01,15.00,3.50,11.50,957.00
sample-note,Sample Lender,Sample cash-out,Sample property,5.9,5,2026-11-13,15.00,3.25,11.75,945.25
sample-note,Sample Lender,Sample cash-out,Sample property,5.9,6,2026-12-01,15.00,3.00,12.00,933.25
other-note,Sample Credit Union,Sample second loan,Other collateral,4.5,1,2026-11-05,40.00,8.00,32.00,500.00
other-note,Sample Credit Union,Sample second loan,Other collateral,4.5,2,2026-12-05,40.00,7.50,32.50,467.50
"""


def _schedule(text: str = FAKE_SCHEDULE) -> pd.DataFrame:
    frame, warning = resolve_loan_schedule({LOAN_SCHEDULE_ENV: text})
    assert warning is None
    return frame


class ScheduleParsingTests(unittest.TestCase):
    def test_header_and_env_name(self) -> None:
        self.assertEqual(LOAN_SCHEDULE_ENV, "FINANCE_LOAN_SCHEDULE_CSV")
        self.assertEqual(",".join(SCHEDULE_COLUMNS), HEADER)

    def test_plain_csv_keeps_payment_zero_and_rates(self) -> None:
        frame = _schedule("\ufeff" + FAKE_SCHEDULE)
        self.assertEqual(frame["loan_id"].nunique(), 2)
        prepaid = frame.loc[frame["payment_number"] == 0]
        self.assertEqual(len(prepaid), 1)
        self.assertAlmostEqual(float(prepaid.iloc[0]["interest"]), 7)
        self.assertAlmostEqual(float(prepaid.iloc[0]["principal"]), 0)
        self.assertAlmostEqual(float(frame.iloc[0]["rate"]), 5.9)
        self.assertEqual(pd.to_datetime(frame.iloc[0]["payment_date"]).date(), date(2025, 12, 1))
        self.assertTrue(pd.api.types.is_numeric_dtype(frame["interest"]))
        self.assertTrue(pd.api.types.is_numeric_dtype(frame["remaining_balance"]))

        table = schedule_payment_table(rows_for_loan(frame, "sample-note"), resolve_period("this-month", TODAY))
        self.assertEqual(
            list(table.columns),
            ["Date", "Payment", "Interest", "Principal", "Remaining Balance"],
        )
        self.assertEqual(table["Date"].tolist(), ["2026-11-01"])
        self.assertEqual(table["Interest"].tolist(), [3.5])
        self.assertEqual(table["Principal"].tolist(), [11.5])
        self.assertEqual(table["Payment"].tolist(), [15.0])
        for column in ("Payment", "Interest", "Principal", "Remaining Balance"):
            self.assertTrue(pd.api.types.is_numeric_dtype(table[column]))

    def test_header_only_csv_has_no_loans(self) -> None:
        frame, warning = resolve_loan_schedule({LOAN_SCHEDULE_ENV: HEADER + "\n"})
        self.assertIsNone(warning)
        self.assertTrue(frame.empty)


class SchedulePeriodTests(unittest.TestCase):
    def test_interest_follows_the_payment_date_and_skips_principal(self) -> None:
        schedule = _schedule()
        periods = {
            "this-month": resolve_period("this-month", TODAY),
            "last-month": resolve_period("last-month", TODAY),
            "qtd": resolve_period("qtd", TODAY),
            "ytd": resolve_period("ytd", TODAY),
            "last-12-months": resolve_period("last-12-months", TODAY),
        }
        expected = {
            "this-month": {"schedule:sample-note": 3.5, "schedule:other-note": 8.0},
            "last-month": {"schedule:sample-note": 4.0, "schedule:other-note": 0.0},
            "qtd": {"schedule:sample-note": 7.5, "schedule:other-note": 8.0},
            "ytd": {"schedule:sample-note": 17.0, "schedule:other-note": 8.0},
            "last-12-months": {"schedule:sample-note": 24.0, "schedule:other-note": 8.0},
        }
        for key, period in periods.items():
            lines = expense_lines(pd.DataFrame(), pd.DataFrame(), period, schedule=schedule)
            scheduled = {line.source_id: line for line in lines if line.data_type == "schedule"}
            self.assertEqual(set(scheduled), set(expected[key]))
            for source_id, interest in expected[key].items():
                self.assertAlmostEqual(scheduled[source_id].total, interest, msg=key)
                self.assertAlmostEqual(scheduled[source_id].interest, interest, msg=key)
            # October's payment is 15 and its interest is 4. Principal is not spend.
            if key == "last-month":
                self.assertNotAlmostEqual(scheduled["schedule:sample-note"].total, 15)

        december = Period("custom", "December", date(2025, 12, 1), date(2025, 12, 31))
        prepaid = line_for_source_id(
            "schedule:sample-note",
            pd.DataFrame(),
            pd.DataFrame(),
            december,
            schedule=schedule,
        )
        assert prepaid is not None
        self.assertAlmostEqual(prepaid.total, 7)
        self.assertEqual(
            loan_label_for(prepaid),
            "Sample Lender - Sample cash-out (5.9% sched.)",
        )
        second = line_for_source_id(
            "schedule:other-note",
            pd.DataFrame(),
            pd.DataFrame(),
            periods["this-month"],
            schedule=schedule,
        )
        assert second is not None
        self.assertEqual(
            loan_label_for(second),
            "Sample Credit Union - Sample second loan (4.5% sched.)",
        )
        self.assertIsNone(
            line_for_source_id(
                "schedule:missing",
                pd.DataFrame(),
                pd.DataFrame(),
                periods["this-month"],
                schedule=schedule,
            )
        )

    def test_interest_flows_into_totals_run_rate_chart_and_snapshot(self) -> None:
        schedule = _schedule()
        this_month = resolve_period("this-month", TODAY)
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
        lines = expense_lines(transactions, pd.DataFrame(), this_month, schedule=schedule)
        self.assertEqual(
            [line.source_id for line in lines if line.data_type == "loan"],
            [
                "wf_auto_7635",
                "wf_auto_8492",
                "truist_auto_1001",
                "jeep_0001",
                "ks_statebank_805_loc_1279",
            ],
        )
        expenses = sum(line.total for line in lines)
        self.assertAlmostEqual(expenses, 11.5)
        sample = next(line for line in lines if line.source_id == "schedule:sample-note")
        self.assertEqual(sample.data_through, date(2026, 11, 1))
        net = net_profit(0, expenses)
        self.assertAlmostEqual(net, -11.5)
        rate = monthly_run_rate(net, this_month)
        self.assertEqual(rate.label, "Scaled to 30 days")
        self.assertAlmostEqual(rate.amount, -11.5 * 30 / 12)

        balances = pd.DataFrame(
            {
                "Account": ["Wells Fargo Auto Loan ending 7635"],
                "As Of": ["2026-11-12T08:00:00Z"],
                "Current Balance": [10000],
            }
        )
        interest = estimated_loan_interest_this_month(balances, TODAY, schedule)
        self.assertAlmostEqual(interest, estimated_interest(10000, 7.49, 12) + 11.5)
        self.assertAlmostEqual(
            estimated_loan_interest_this_month(balances, TODAY),
            estimated_interest(10000, 7.49, 12),
        )

        frame = income_expense_by_month(
            transactions,
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            TODAY,
            schedule=schedule,
        )
        by_month = {row.month: row for row in frame.itertuples(index=False)}
        june = by_month["Jun 2026"]
        self.assertAlmostEqual(june.expenses, 4.5)
        self.assertAlmostEqual(june.net, -4.5)
        july = by_month["Jul 2026"]
        self.assertTrue(pd.isna(july.expenses))
        october = by_month["Oct 2026"]
        self.assertAlmostEqual(october.expenses, 84)
        november = by_month["Nov 2026"]
        self.assertAlmostEqual(november.expenses, 11.5)

        snapshots = loan_snapshots(schedule, TODAY)
        self.assertEqual(
            [item.label for item in snapshots],
            [
                "Sample Lender - Sample cash-out (5.9% sched.)",
                "Sample Credit Union - Sample second loan (4.5% sched.)",
            ],
        )
        self.assertAlmostEqual(scheduled_balance_total(snapshots), 1457)
        self.assertEqual(
            snapshot_line(snapshots[0]),
            "Sample Lender - Sample cash-out (5.9% sched.): $957.00 remaining. "
            "Next payment 2026-11-13 for $15.00.",
        )
        self.assertEqual(
            snapshot_line(snapshots[1]),
            "Sample Credit Union - Sample second loan (4.5% sched.): $500.00 remaining. "
            "Next payment 2026-12-05 for $40.00.",
        )


class ScheduleBalanceTests(unittest.TestCase):
    def test_balance_uses_the_latest_payment_on_or_before_today(self) -> None:
        schedule = _schedule()
        sample = rows_for_loan(schedule, "sample-note")
        other = rows_for_loan(schedule, "other-note")
        self.assertAlmostEqual(remaining_balance(sample, TODAY), 957)
        self.assertAlmostEqual(remaining_balance(sample, date(2026, 11, 1)), 957)
        self.assertAlmostEqual(remaining_balance(sample, date(2026, 10, 20)), 968.5)
        self.assertAlmostEqual(remaining_balance(other, TODAY), 500)
        self.assertAlmostEqual(remaining_balance(other, date(2026, 11, 1)), 532)
        # Before every row, the balance is remaining plus principal of the first row.
        self.assertAlmostEqual(remaining_balance(sample, date(2025, 11, 1)), 1000)
        self.assertAlmostEqual(remaining_balance(sample, date(2025, 12, 1)), 1000)

    def test_same_day_rows_keep_the_later_payment_number(self) -> None:
        text = f"""{HEADER}
same,Sample Bank,Same day,None,6,1,2026-11-01,10,1,9,100
same,Sample Bank,Same day,None,6,2,2026-11-01,10,1,9,91
"""
        rows = rows_for_loan(_schedule(text), "same")
        self.assertAlmostEqual(remaining_balance(rows, date(2026, 11, 1)), 91)
        self.assertAlmostEqual(remaining_balance(rows, date(2026, 10, 1)), 109)
        snapshots = loan_snapshots(_schedule(text), date(2026, 11, 2))
        self.assertAlmostEqual(snapshots[0].balance, 91)
        self.assertIsNone(snapshots[0].next_date)
        self.assertIn("No further scheduled payments.", snapshot_line(snapshots[0]))


class MissingScheduleTests(unittest.TestCase):
    def test_missing_or_blank_env_adds_nothing(self) -> None:
        period = resolve_period("this-month", TODAY)
        for environ in (
            {},
            {LOAN_SCHEDULE_ENV: ""},
            {LOAN_SCHEDULE_ENV: "  \n"},
            {"FINANCE_DATA_CSV": FAKE_SCHEDULE},
            {"FINANCE_INCOME_CSV": FAKE_SCHEDULE},
            {"APP_PASSWORD": "secret"},
        ):
            frame, warning = resolve_loan_schedule(environ)
            self.assertIsNone(warning)
            self.assertTrue(frame.empty)
        bare = expense_lines(pd.DataFrame(), pd.DataFrame(), period)
        empty, warning = resolve_loan_schedule({})
        self.assertIsNone(warning)
        with_empty = expense_lines(pd.DataFrame(), pd.DataFrame(), period, schedule=empty)
        self.assertEqual([line.source_id for line in bare], [line.source_id for line in with_empty])
        self.assertEqual([line.total for line in bare], [line.total for line in with_empty])
        self.assertFalse(any(line.data_type == "schedule" for line in bare))
        self.assertEqual(estimated_loan_interest_this_month(pd.DataFrame(), TODAY, empty), 0)

        previous = os.environ.get(LOAN_SCHEDULE_ENV)
        os.environ[LOAN_SCHEDULE_ENV] = FAKE_SCHEDULE
        try:
            untouched = expense_lines(pd.DataFrame(), pd.DataFrame(), period)
        finally:
            if previous is None:
                os.environ.pop(LOAN_SCHEDULE_ENV, None)
            else:
                os.environ[LOAN_SCHEDULE_ENV] = previous
        self.assertFalse(any(line.data_type == "schedule" for line in untouched))


class MalformedScheduleTests(unittest.TestCase):
    def test_malformed_csv_warns_and_does_not_crash(self) -> None:
        period = resolve_period("this-month", TODAY)
        cases = [
            "Loan ID,Lender\nnote,Sample Bank\n",
            '"unterminated',
            HEADER + "\nsample-note,Sample Lender,Sample cash-out,Secret property,5.9,1,bad-date,15,3.5,11.5,957.25\n",
            HEADER + "\nsample-note,Sample Lender,Sample cash-out,Sample property,5.9,1,2026-11-01,15,nope,11.5,957\n",
        ]
        for text in cases:
            frame, warning = resolve_loan_schedule({LOAN_SCHEDULE_ENV: text})
            self.assertTrue(frame.empty)
            self.assertIsNotNone(warning)
            assert warning is not None
            self.assertIn("loan schedule", warning.lower())
            self.assertNotIn("957.25", warning)
            self.assertNotIn("sample-note", warning)
            self.assertNotIn("Secret", warning)
            lines = expense_lines(pd.DataFrame(), pd.DataFrame(), period, schedule=frame)
            self.assertFalse(any(line.data_type == "schedule" for line in lines))
            self.assertEqual(estimated_loan_interest_this_month(None, TODAY, frame), 0)
            self.assertEqual(loan_snapshots(frame, TODAY), [])


if __name__ == "__main__":
    unittest.main()
