"""Cash income parsing, periods, net, and unknown source ids.

Fixtures are fake round numbers. They are not account exports.
"""

from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

from kirbancials.data import FinanceDataError
from kirbancials.income import (
    deposits_for_source,
    income_lines,
    income_table,
    load_income,
    net_profit,
    resolve_income,
)
from kirbancials.periods import resolve_period
from kirbancials.sources import INCOME_SOURCES, income_source_by_id
from kirbancials.spend import expense_lines, income_for_source, total_income

TODAY = date(2026, 11, 12)

# Clearly fake deposits. November 13 is after TODAY, so This month excludes it.
FAKE_INCOME = """Transaction ID,Date,Source,Description,Amount
fake-chip-oct,2026-10-03,chipotle_nnn,October sample rent,1000
fake-chip-nov,2026-11-01,chipotle_nnn,November sample rent,1000
fake-chip-today,2026-11-12,chipotle_nnn,November sample adjustment,25
fake-chip-later,2026-11-13,chipotle_nnn,After the sample today,999
fake-water-nov,2026-11-05,waterleaf_pem,Sample quarterly deposit,500
fake-side-nov,2026-11-06,side_gig,Sample side deposit,40
fake-side-sep,2026-09-01,side_gig,Old sample side deposit,7
"""


def _deposits(text: str = FAKE_INCOME) -> pd.DataFrame:
    origin = resolve_income({"FINANCE_INCOME_CSV": text})
    assert origin is not None
    return load_income(origin)


class IncomeRegistryTests(unittest.TestCase):
    def test_registry_lists_chipotle_and_waterleaf(self) -> None:
        self.assertEqual(
            [(source.id, source.name, source.frequency) for source in INCOME_SOURCES],
            [
                ("chipotle_nnn", "Chipotle NNN", "Monthly"),
                ("waterleaf_pem", "Waterleaf Apts (PEM Residential)", "Quarterly"),
                ("mf3distro", "MF3Distro", "Irregular"),
            ],
        )
        chipotle = income_source_by_id("chipotle_nnn")
        waterleaf = income_source_by_id("waterleaf_pem")
        mf3distro = income_source_by_id("mf3distro")
        self.assertIsNotNone(chipotle)
        self.assertIsNotNone(waterleaf)
        self.assertIsNotNone(mf3distro)
        assert chipotle is not None and waterleaf is not None and mf3distro is not None
        self.assertEqual(chipotle.frequency, "Monthly")
        self.assertEqual(waterleaf.frequency, "Quarterly")
        self.assertEqual(mf3distro.frequency, "Irregular")
        self.assertEqual(mf3distro.name, "MF3Distro")
        self.assertIsNone(income_source_by_id("side_gig"))


class IncomeParsingTests(unittest.TestCase):
    def test_plain_csv_parses_amounts_descriptions_and_duplicate_ids(self) -> None:
        text = """Transaction ID,Date,Source,Description,Amount
id-1,2026-11-03,chipotle_nnn,"Sample, rent",1500.50
id-1,2026-11-04,chipotle_nnn,Ignored duplicate,9
,2026-11-03,waterleaf_pem,Blank id kept,2
,2026-11-03,waterleaf_pem,Second blank id,3
id-blank-source,2026-11-03,,Dropped blank source,10
id-bad-date,not-a-date,chipotle_nnn,Dropped bad date,10
id-bad-amount,2026-11-03,chipotle_nnn,Dropped bad amount,nope
"""
        frame = _deposits("\ufeff" + text)
        self.assertEqual(
            list(zip(frame["Source"], frame["Description"], frame["Amount"], strict=True)),
            [
                ("chipotle_nnn", "Sample, rent", 1500.50),
                ("waterleaf_pem", "Blank id kept", 2.0),
                ("waterleaf_pem", "Second blank id", 3.0),
            ],
        )
        self.assertEqual(frame["Date"].dt.strftime("%Y-%m-%d").tolist(), ["2026-11-03"] * 3)
        self.assertTrue(pd.api.types.is_numeric_dtype(frame["Amount"]))

        table = income_table(frame)
        self.assertEqual(list(table.columns), ["Date", "Description", "Amount"])
        self.assertTrue(pd.api.types.is_numeric_dtype(table["Amount"]))
        self.assertEqual(table["Description"].tolist()[0], "Sample, rent")

    def test_missing_column_raises(self) -> None:
        origin = resolve_income({"FINANCE_INCOME_CSV": "Date,Amount\n2026-11-01,10\n"})
        self.assertIsNotNone(origin)
        with self.assertRaises(FinanceDataError):
            load_income(origin)

    def test_header_only_csv_has_no_deposits(self) -> None:
        frame = _deposits("Transaction ID,Date,Source,Description,Amount\n")
        self.assertTrue(frame.empty)
        period = resolve_period("this-month", TODAY)
        self.assertEqual(total_income(frame, period), 0)


class MissingIncomeEnvTests(unittest.TestCase):
    def test_missing_or_blank_env_is_zero(self) -> None:
        self.assertIsNone(resolve_income({}))
        self.assertIsNone(resolve_income({"FINANCE_INCOME_CSV": ""}))
        self.assertIsNone(resolve_income({"FINANCE_INCOME_CSV": "  \n"}))
        self.assertIsNone(resolve_income({"OTHER": "Transaction ID,Date,Source,Description,Amount\n"}))

        frame = load_income(None)
        self.assertTrue(frame.empty)
        period = resolve_period("this-month", TODAY)
        lines = income_lines(frame, period)
        self.assertEqual(
            [line.source_id for line in lines],
            ["chipotle_nnn", "waterleaf_pem", "mf3distro"],
        )
        self.assertEqual([line.total for line in lines], [0.0, 0.0, 0.0])
        self.assertEqual(total_income(frame, period), 0)
        self.assertEqual(total_income(), 0)
        self.assertEqual(income_for_source("chipotle_nnn"), 0)
        self.assertEqual(net_profit(total_income(), 180), -180)


class IncomePeriodTests(unittest.TestCase):
    def test_totals_follow_the_cash_date(self) -> None:
        deposits = _deposits()
        this_month = resolve_period("this-month", TODAY)
        last_month = resolve_period("last-month", TODAY)
        ytd = resolve_period("ytd", TODAY)
        all_time = resolve_period("all-time", TODAY)

        this_lines = {line.source_id: line for line in income_lines(deposits, this_month)}
        self.assertEqual(this_lines["chipotle_nnn"].label, "Chipotle NNN (Monthly)")
        self.assertEqual(this_lines["chipotle_nnn"].total, 1025)
        self.assertEqual(
            this_lines["waterleaf_pem"].label,
            "Waterleaf Apts (PEM Residential) (Quarterly)",
        )
        self.assertEqual(this_lines["waterleaf_pem"].total, 500)
        self.assertEqual(this_lines["side_gig"].total, 40)
        self.assertEqual(total_income(deposits, this_month), 1565)
        self.assertEqual(income_for_source("chipotle_nnn", deposits, this_month), 1025)

        chipotle_rows = deposits_for_source(deposits, "chipotle_nnn", this_month)
        self.assertEqual(
            income_table(chipotle_rows)["Date"].tolist(),
            ["2026-11-12", "2026-11-01"],
        )
        self.assertEqual(chipotle_rows["Amount"].tolist(), [25, 1000])
        self.assertNotIn("2026-11-13", income_table(chipotle_rows)["Date"].tolist())

        last_lines = {line.source_id: line for line in income_lines(deposits, last_month)}
        self.assertEqual(last_lines["chipotle_nnn"].total, 1000)
        self.assertEqual(last_lines["waterleaf_pem"].total, 0)
        self.assertNotIn("side_gig", last_lines)
        self.assertEqual(total_income(deposits, last_month), 1000)

        self.assertEqual(total_income(deposits, ytd), 2572)
        self.assertEqual(total_income(deposits, all_time), 3571)

    def test_unknown_source_id_is_kept_under_the_raw_id(self) -> None:
        deposits = _deposits()
        period = resolve_period("this-month", TODAY)
        lines = income_lines(deposits, period)
        self.assertEqual(
            [line.source_id for line in lines],
            ["chipotle_nnn", "waterleaf_pem", "mf3distro", "side_gig"],
        )
        unknown = lines[-1]
        self.assertEqual(unknown.name, "side_gig")
        self.assertEqual(unknown.frequency, "")
        self.assertEqual(unknown.label, "side_gig")
        self.assertEqual(unknown.total, 40)
        self.assertEqual(total_income(deposits, period), sum(line.total for line in lines))
        self.assertEqual(income_for_source("side_gig", deposits, period), 40)

        ytd_ids = [line.source_id for line in income_lines(deposits, resolve_period("ytd", TODAY))]
        self.assertEqual(ytd_ids.count("side_gig"), 1)


class IncomeNetTests(unittest.TestCase):
    def test_net_is_income_minus_expenses_and_spend_is_unchanged(self) -> None:
        deposits = _deposits()
        period = resolve_period("this-month", TODAY)
        transactions = pd.DataFrame(
            [
                {
                    "Account": "Citi AAdvantage card ending 7690",
                    "Date": date(2026, 11, 2),
                    "Amount": 80,
                    "Pending": False,
                    "Category": "Shopping",
                    "Merchant": "Sample Store",
                    "Description": "goods",
                }
            ]
        )
        before = expense_lines(transactions, pd.DataFrame(), period)
        income = total_income(deposits, period)
        after = expense_lines(transactions, pd.DataFrame(), period)
        self.assertEqual([line.total for line in before], [line.total for line in after])
        expenses = sum(line.total for line in after)
        self.assertEqual(expenses, 80)
        citi = next(line for line in after if line.source_id == "citi")
        self.assertEqual(citi.total, 80)
        self.assertEqual(income, 1565)
        self.assertEqual(net_profit(income, expenses), 1485)
        self.assertEqual(net_profit(0, expenses), -80)


if __name__ == "__main__":
    unittest.main()
