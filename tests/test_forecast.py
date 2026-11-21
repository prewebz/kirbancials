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
        rendered = "\n".join(node.value for node in app.markdown)
        self.assertIn("Sample Appliance", rendered)
        self.assertIn("Sample Power", rendered)
        self.assertIn("Sample Water", rendered)
        self.assertEqual(list(app.title), [])


if __name__ == "__main__":
    unittest.main()
