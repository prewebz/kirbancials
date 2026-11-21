"""Month-end card forecast. Fixtures are fake round numbers, not account exports."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

import pandas as pd
from streamlit.proto.Metric_pb2 import Metric as MetricProto
from streamlit.testing.v1 import AppTest

from kirbancials.forecast import (
    ONE_TIME_MEDIAN_MULTIPLE,
    ONE_TIME_MIN_AMOUNT,
    RECURRING_AMOUNT_TOLERANCE,
    RECURRING_DAY_TOLERANCE,
    TRAILING_PACE_DAYS,
    month_end_forecast,
)
from kirbancials.formatting import format_money

CARD = "Citi AAdvantage card ending 7690"
AS_OF = date(2026, 11, 10)


def _row(
    when: date,
    amount: float,
    merchant: str = "Sample Shop",
    *,
    pending: bool = False,
    transaction_id: str = "",
    category: str = "Shopping",
    description: str = "goods",
    account: str = CARD,
) -> dict[str, object]:
    return {
        "Transaction ID": transaction_id,
        "Date": when,
        "Account": account,
        "Merchant": merchant,
        "Description": description,
        "Amount": amount,
        "Pending": pending,
        "Category": category,
    }


def _frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _daily(start: date, days: int, amount: float, merchant: str = "Sample Shop") -> list[dict[str, object]]:
    """One charge a day. Each day gets its own merchant so the series is not a bill."""
    return [
        _row(
            start + timedelta(days=offset),
            amount,
            f"{merchant} {offset}",
            transaction_id=f"d-{(start + timedelta(days=offset)).isoformat()}-{amount}",
        )
        for offset in range(days)
    ]


class ThresholdTests(unittest.TestCase):
    def test_named_thresholds(self) -> None:
        self.assertEqual(ONE_TIME_MIN_AMOUNT, 1000.0)
        self.assertEqual(ONE_TIME_MEDIAN_MULTIPLE, 3.0)
        self.assertEqual(RECURRING_AMOUNT_TOLERANCE, 0.15)
        self.assertEqual(RECURRING_DAY_TOLERANCE, 5)
        self.assertEqual(TRAILING_PACE_DAYS, 90)


class OneTimeTests(unittest.TestCase):
    def test_large_charge_is_counted_once_and_not_paced(self) -> None:
        history_start = date(2026, 11, 1) - timedelta(days=TRAILING_PACE_DAYS)
        rows = _daily(history_start, TRAILING_PACE_DAYS, 10)
        rows.append(_row(date(2026, 11, 2), 1500, "Sample Appliance", transaction_id="big"))
        rows.extend(_daily(date(2026, 11, 1), 10, 10, "Sample Grocery"))
        forecast = month_end_forecast(_frame(rows), AS_OF)

        self.assertEqual(forecast.one_time, 1500)
        self.assertEqual(forecast.everyday_to_date, 100)
        self.assertEqual(forecast.days_elapsed, 10)
        self.assertEqual(forecast.days_remaining, 20)
        self.assertAlmostEqual(forecast.everyday_pace, 10)
        self.assertAlmostEqual(forecast.total, 1500 + 100 + 10 * 20)
        paced_everything = (1500 + 100) / 10 * 30
        self.assertNotAlmostEqual(forecast.total, paced_everything)
        self.assertEqual([charge.merchant for charge in forecast.one_time_charges], ["Sample Appliance"])

    def test_three_times_median_daily_spend_is_one_time_below_the_dollar_bar(self) -> None:
        history_start = date(2026, 11, 1) - timedelta(days=TRAILING_PACE_DAYS)
        rows = _daily(history_start, TRAILING_PACE_DAYS, 20)
        rows.append(_row(date(2026, 11, 3), 70, "Sample Unusual", transaction_id="odd"))
        rows.append(_row(date(2026, 11, 4), 50, "Sample Usual", transaction_id="usual"))
        forecast = month_end_forecast(_frame(rows), AS_OF)
        self.assertEqual(forecast.one_time, 70)
        self.assertEqual(forecast.everyday_to_date, 50)
        self.assertEqual([charge.merchant for charge in forecast.one_time_charges], ["Sample Unusual"])


class RecurringTests(unittest.TestCase):
    def test_unposted_bill_is_still_to_come_and_a_posted_bill_is_not_paced(self) -> None:
        rows = [
            _row(date(2026, 8, 10), 80, "Sample Power", transaction_id="aug"),
            _row(date(2026, 9, 11), 80, "Sample Power", transaction_id="sep"),
            _row(date(2026, 10, 10), 84, "Sample Power", transaction_id="oct"),
        ]
        early = month_end_forecast(_frame(rows), date(2026, 11, 4))
        self.assertEqual(early.recurring_posted, 0)
        self.assertEqual(early.bills_still_to_come, 80)
        self.assertEqual(early.one_time, 0)
        self.assertAlmostEqual(early.total, 80)
        self.assertEqual(early.expected_bills[0].merchant, "Sample Power")

        late = month_end_forecast(_frame(rows), date(2026, 11, 20))
        self.assertEqual(late.bills_still_to_come, 0)
        self.assertEqual(late.total, 0)

        posted_rows = rows + [_row(date(2026, 11, 10), 80, "Sample Power", transaction_id="nov")]
        posted = month_end_forecast(_frame(posted_rows), date(2026, 11, 20))
        self.assertEqual(posted.recurring_posted, 80)
        self.assertEqual(posted.bills_still_to_come, 0)
        self.assertEqual(posted.everyday_to_date, 0)
        self.assertAlmostEqual(posted.total, 80)
        self.assertEqual(posted.days_remaining, 10)

    def test_one_prior_month_is_not_yet_a_bill(self) -> None:
        rows = [_row(date(2026, 10, 10), 80, "Sample Power", transaction_id="oct")]
        forecast = month_end_forecast(_frame(rows), date(2026, 11, 4))
        self.assertEqual(forecast.bills_still_to_come, 0)
        self.assertEqual(forecast.expected_bills, ())

    def test_a_finished_month_does_not_invent_a_missed_bill(self) -> None:
        rows = [
            _row(date(2026, 8, 10), 80, "Sample Power", transaction_id="aug"),
            _row(date(2026, 9, 10), 80, "Sample Power", transaction_id="sep"),
            _row(date(2026, 10, 10), 80, "Sample Power", transaction_id="oct"),
        ]
        forecast = month_end_forecast(_frame(rows), date(2026, 11, 30))
        self.assertEqual(forecast.days_remaining, 0)
        self.assertEqual(forecast.bills_still_to_come, 0)
        self.assertEqual(forecast.total, 0)


class PaceWeightTests(unittest.TestCase):
    def test_blend_shifts_toward_this_month_as_the_month_goes_on(self) -> None:
        history_start = date(2026, 11, 1) - timedelta(days=TRAILING_PACE_DAYS)
        rows = _daily(history_start, TRAILING_PACE_DAYS, 10, "Sample History")
        # Four $29 charges stay under 3x a $10 median day, and total $116 a day.
        for day in range(3):
            for copy in range(4):
                rows.append(
                    _row(
                        date(2026, 11, 1) + timedelta(days=day),
                        29,
                        "Sample This Month",
                        transaction_id=f"m-{day}-{copy}",
                    )
                )
        early = month_end_forecast(_frame(rows), date(2026, 11, 3))
        self.assertAlmostEqual(early.everyday_pace, 0.9 * 10 + 0.1 * 116)

        late_rows = list(rows)
        for day in range(3, 27):
            for copy in range(4):
                late_rows.append(
                    _row(
                        date(2026, 11, 1) + timedelta(days=day),
                        29,
                        "Sample This Month",
                        transaction_id=f"l-{day}-{copy}",
                    )
                )
        late = month_end_forecast(_frame(late_rows), date(2026, 11, 27))
        self.assertAlmostEqual(late.everyday_pace, 0.1 * 10 + 0.9 * 116)


class PendingCopyTests(unittest.TestCase):
    def test_pending_and_posted_copies_count_once(self) -> None:
        rows = [
            _row(date(2026, 11, 3), 25, "Sample Cafe", pending=False, transaction_id="posted-cafe"),
            _row(date(2026, 11, 3), 25, "Sample Cafe", pending=True, transaction_id="pending-cafe"),
            _row(date(2026, 11, 4), -40, "PAYMENT THANK YOU", category="Payment", description="thank you"),
        ]
        forecast = month_end_forecast(_frame(rows), AS_OF)
        self.assertEqual(forecast.everyday_to_date, 25)
        self.assertEqual(forecast.one_time, 0)

    def test_same_transaction_id_keeps_the_posted_row(self) -> None:
        rows = [
            _row(date(2026, 11, 3), 25, "Sample Cafe", pending=True, transaction_id="same"),
            _row(date(2026, 11, 4), 25, "Sample Cafe", pending=False, transaction_id="same"),
        ]
        forecast = month_end_forecast(_frame(rows), AS_OF)
        self.assertEqual(forecast.everyday_to_date, 25)
        self.assertEqual(len(forecast.one_time_charges) + len(forecast.recurring_charges), 0)


class LastMonthTests(unittest.TestCase):
    def test_last_month_actual_is_card_spend_and_hidden_when_absent(self) -> None:
        rows = [
            _row(date(2026, 10, 5), 40, "Sample October", transaction_id="oct"),
            _row(date(2026, 11, 2), 10, "Sample November", transaction_id="nov"),
        ]
        forecast = month_end_forecast(_frame(rows), AS_OF)
        self.assertEqual(forecast.last_month_actual, 40)

        empty = month_end_forecast(_frame([_row(date(2026, 11, 2), 10)]), AS_OF)
        self.assertIsNone(empty.last_month_actual)


class TotalExpenseForecastTests(unittest.TestCase):
    """Fake balances and statements. The forecast stays at or above month-to-date."""

    def test_interest_and_statements_keep_the_forecast_above_month_to_date(self) -> None:
        from kirbancials.loans import estimated_interest
        from kirbancials.periods import Period
        from kirbancials.spend import expense_lines

        account = "Wells Fargo Auto Loan ending 7635"
        as_of = date(2026, 11, 10)
        transactions = _frame(
            [
                _row(date(2026, 11, 2), 100, "Sample Grocery", transaction_id="groceries"),
            ]
        )
        balances = pd.DataFrame(
            {
                "Account": [account],
                "As Of": ["2026-11-10T08:00:00Z"],
                "Current Balance": [10000.0],
            }
        )
        monthly = pd.DataFrame(
            [
                {
                    "Account": "Nordstrom card ending 5488",
                    "Month": "2026-11",
                    "Statement Closing Date": "2026-11-28",
                    "Net Spend": 75,
                    "Interest": 5,
                    "Fees": 0,
                }
            ]
        )
        forecast = month_end_forecast(transactions, as_of, monthly, balances)
        self.assertGreaterEqual(forecast.total, forecast.month_to_date)
        self.assertEqual(forecast.other_expenses, 80)
        self.assertNotAlmostEqual(
            forecast.other_expenses,
            estimated_interest(10000, 7.49, 30) + 80,
        )
        self.assertEqual(forecast.month_to_date, 100)

    def test_a_negative_pace_does_not_pull_the_forecast_below_month_to_date(self) -> None:
        from kirbancials.periods import Period
        from kirbancials.spend import expense_lines

        as_of = date(2026, 11, 5)
        transactions = _frame([_row(date(2026, 11, 2), -40, "Sample Refund", transaction_id="refund")])
        forecast = month_end_forecast(transactions, as_of)
        mtd = sum(
            line.total
            for line in expense_lines(
                transactions,
                pd.DataFrame(),
                Period("mtd", "Month to date", date(2026, 11, 1), as_of),
            )
        )
        self.assertLess(forecast.everyday_pace, 0)
        self.assertGreaterEqual(forecast.total, mtd)
        self.assertGreaterEqual(forecast.total, forecast.month_to_date)

    def test_pending_copy_does_not_make_the_forecast_smaller_than_total_expenses(self) -> None:
        from kirbancials.periods import Period
        from kirbancials.spend import expense_lines

        as_of = date(2026, 11, 10)
        transactions = _frame(
            [
                _row(date(2026, 11, 3), 25, "Sample Cafe", pending=False, transaction_id="posted-cafe"),
                _row(date(2026, 11, 3), 25, "Sample Cafe", pending=True, transaction_id="pending-cafe"),
            ]
        )
        forecast = month_end_forecast(transactions, as_of)
        mtd = sum(
            line.total
            for line in expense_lines(
                transactions,
                pd.DataFrame(),
                Period("mtd", "Month to date", date(2026, 11, 1), as_of),
            )
        )
        self.assertEqual(mtd, 50)
        self.assertEqual(forecast.everyday_to_date, 25)
        self.assertEqual(forecast.other_card_spend, 25)
        self.assertGreaterEqual(forecast.total, mtd)

    def test_last_month_line_includes_statement_expenses(self) -> None:
        transactions = _frame([_row(date(2026, 11, 2), 10, "Sample November", transaction_id="nov")])
        monthly = pd.DataFrame(
            [
                {
                    "Account": "Nordstrom card ending 5488",
                    "Month": "2026-10",
                    "Statement Closing Date": "2026-10-20",
                    "Net Spend": 60,
                    "Interest": 0,
                    "Fees": 0,
                }
            ]
        )
        forecast = month_end_forecast(transactions, AS_OF, monthly)
        self.assertEqual(forecast.last_month_actual, 60)


class DiscretionaryScopeTests(unittest.TestCase):
    def test_financing_transfers_and_loan_payments_stay_out(self) -> None:
        from kirbancials.discretionary import (
            DISCRETIONARY_EXCLUDED_DATA_TYPES,
            DISCRETIONARY_EXCLUDED_SOURCE_IDS,
            discretionary_total,
            is_financing_expense,
        )
        from kirbancials.periods import Period
        from kirbancials.spend import ExpenseLine

        self.assertIn("wf_auto_7635", DISCRETIONARY_EXCLUDED_SOURCE_IDS)
        self.assertIn("wf_auto_8492", DISCRETIONARY_EXCLUDED_SOURCE_IDS)
        self.assertIn("truist_auto_1001", DISCRETIONARY_EXCLUDED_SOURCE_IDS)
        self.assertIn("jeep_0001", DISCRETIONARY_EXCLUDED_SOURCE_IDS)
        self.assertIn("ks_statebank_805_loc_1279", DISCRETIONARY_EXCLUDED_SOURCE_IDS)
        self.assertIn("morpho_loan", DISCRETIONARY_EXCLUDED_SOURCE_IDS)
        self.assertIn("schedule", DISCRETIONARY_EXCLUDED_DATA_TYPES)
        ffb = ExpenseLine("schedule:chipotle", "First Fidelity Bank - Chipotle", "schedule", 90, None, 0, 90, 0, None)
        self.assertTrue(is_financing_expense(ffb))
        rows = [
            _row(date(2026, 11, 3), 500, "Sample Lender", category="Loan Payments", transaction_id="loan"),
            _row(date(2026, 11, 4), -20, "Sample Transfer", category="Transfer Out", transaction_id="xfer"),
            _row(date(2026, 11, 5), 15, "Sample Air", category="Travel", transaction_id="trip"),
        ]
        period = Period("mtd", "Month to date", date(2026, 11, 1), date(2026, 11, 10))
        self.assertEqual(discretionary_total(_frame(rows), pd.DataFrame(), period), 15)

    def test_travel_is_flagged_when_it_jumps_above_the_three_month_average(self) -> None:
        rows = []
        for month in (8, 9, 10):
            rows.append(
                _row(
                    date(2026, month, 10),
                    30,
                    "Sample Market",
                    category="Groceries",
                    transaction_id=f"g-{month}",
                )
            )
            rows.append(
                _row(
                    date(2026, month, 10),
                    10,
                    "Sample Air",
                    category="Travel",
                    transaction_id=f"t-{month}",
                )
            )
        rows.append(_row(date(2026, 11, 4), 30, "Sample Market", category="Groceries", transaction_id="g-11"))
        rows.append(_row(date(2026, 11, 4), 100, "Sample Air", category="Travel", transaction_id="t-11"))
        forecast = month_end_forecast(_frame(rows), date(2026, 11, 10))
        by_name = {item.category: item for item in forecast.categories}
        travel = by_name["Travel"]
        groceries = by_name["Groceries"]
        self.assertEqual(travel.trailing_average, 10)
        self.assertGreaterEqual(travel.forecast, travel.month_to_date)
        self.assertTrue(travel.trending)
        self.assertEqual(forecast.categories[0].category, "Travel")
        self.assertGreater(travel.increase, groceries.increase)
        self.assertEqual(travel.transactions[0].merchant, "Sample Air")
        self.assertGreaterEqual(forecast.total, forecast.month_to_date)


class ForecastDisplayTests(unittest.TestCase):
    def test_header_shows_the_forecast_breakdown_and_gray_last_month(self) -> None:
        script = """
from datetime import date

from kirbancials.forecast import ExpenseForecast, FlaggedCharge
from kirbancials.periods import resolve_period
from kirbancials.pnl import _render_summary

period = resolve_period("this-month", date(2026, 11, 12))
forecast = ExpenseForecast(
    total=430,
    one_time=200,
    recurring_posted=80,
    bills_still_to_come=50,
    everyday_to_date=70,
    everyday_pace=5,
    days_elapsed=10,
    days_remaining=20,
    last_month_actual=300,
    one_time_charges=(FlaggedCharge(date(2026, 11, 2), "Sample card", "Sample Appliance", 200, "one-time"),),
    recurring_charges=(FlaggedCharge(date(2026, 11, 10), "Sample card", "Sample Power", 80, "recurring"),),
    expected_bills=(FlaggedCharge(date(2026, 11, 18), "Sample card", "Sample Water", 50, "expected"),),
)
_render_summary(0, 180, -180, period, date(2026, 10, 1), 100, forecast)
"""
        app = AppTest.from_string(script, default_timeout=30).run()
        self.assertEqual(list(app.exception), [])
        metric = app.metric[3]
        self.assertEqual(metric.label, "Month-end forecast")
        self.assertEqual(metric.value, format_money(430))
        self.assertEqual(metric.delta, f"Last month: {format_money(300)}")
        self.assertEqual(metric.color, MetricProto.MetricColor.GRAY)
        self.assertEqual(metric.direction, MetricProto.MetricDirection.NONE)
        captions = [node.value for node in app.caption]
        breakdown = next(text for text in captions if text.startswith("One-time so far"))
        self.assertIn(format_money(200), breakdown)
        self.assertIn(format_money(50), breakdown)
        self.assertIn(f"{format_money(5)}/day", breakdown)
        self.assertIn("Statement cards", breakdown)
        self.assertTrue(any(text.startswith("Forecast of discretionary spending") for text in captions))
        self.assertIn("Excludes loans and LOC interest (see Bills)", captions)
        rendered = "\n".join(node.value for node in app.markdown)
        self.assertIn("Sample Appliance", rendered)
        self.assertIn("Sample Power", rendered)
        self.assertIn("Sample Water", rendered)
        self.assertEqual(list(app.title), [])


if __name__ == "__main__":
    unittest.main()
