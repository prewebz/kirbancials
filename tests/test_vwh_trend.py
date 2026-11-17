"""VWH trending. Fixtures are fake round numbers, not a property export."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from kirbancials.formatting import MISSING, format_money
from kirbancials.vwh import (
    VIEW_MONTH,
    VWH_ENV,
    _tax_frame,
    _trend_chart,
    available_months,
    empty_rows,
    month_window,
    resolve_vwh,
    summarize,
)
from kirbancials.vwh_trend import (
    NO_TAX_DATA,
    TAX_ENV,
    TAX_MISSING_COLUMN,
    TAX_OMITTED,
    annual_run_rate,
    available_pay_years,
    format_percent_change,
    monthly_trend,
    property_tax_table,
    resolve_vwh_tax,
    run_rate_note,
    tax_skipped_message,
)

ROOT = Path(__file__).resolve().parents[1]

PL_HEADER = "Month,Property ID,Address,Section,Account Number,Account Name,Amount,Source"
TAX_HEADER = (
    "Pay Year,Property ID,Address,Parcel,Spring Tax,Fall Tax,Annual Tax,Spring Status,Fall Status"
)

# Three portfolio months. VWH2 has no August row, so August counts as zero.
# October Kline income for VWH1 is -50 + 150. A Bank income row of 500 is on
# the chart and off the run rate. Kline expenses include 5000, 6400, and 9999.
# A Bank 5200, BANK-TAX-PY, and BANK-TAX-PEN are traps and stay out of the run rate.
# VWH1's bill is the 2026 annual of 300, not the 2024 bill of 999 and not BANK-TAX.
# VWH2 has no bill, so its tax is the BANK-TAX sum. VWH3 is on the tax roll only.
FAKE_PL = f"""{PL_HEADER}
2026-08,VWH1,1 Sample St,Income,4300,Property Income,100,Kline P&L
2026-08,VWH1,1 Sample St,Expense,6400,Property Management Expenses,10,Kline P&L
2026-08,VWH1,1 Sample St,Expense,5200,Property Repairs & Maintenance Expenses,20,Kline P&L
2026-08,VWH1,1 Sample St,Expense,BANK-INS,Property Insurance,5,Bank
2026-09,VWH1,1 Sample St,Income,4300,Property Income,200,Kline P&L
2026-09,VWH1,1 Sample St,Expense,6400,Property Management Expenses,10,Kline P&L
2026-09,VWH1,1 Sample St,Expense,BANK-INS,Property Insurance,5,Bank
2026-09,VWH1,1 Sample St,Expense,BANK-TAX,Property Tax,80,Bank
2026-09,VWH1,1 Sample St,Below the line,BANK-TAX-PEN,Tax Penalties (excluded),7,Bank
2026-10,VWH1,1 Sample St,Income,4000,Property Management Income,-50,Kline P&L
2026-10,VWH1,1 Sample St,Income,4300,Property Income,150,Kline P&L
2026-10,VWH1,1 Sample St,Income,4300,Property Income,500,Bank
2026-10,VWH1,1 Sample St,Expense,6400,Property Management Expenses,30,Kline P&L
2026-10,VWH1,1 Sample St,Expense,5200,Property Repairs & Maintenance Expenses,10,Kline P&L
2026-10,VWH1,1 Sample St,Expense,5000,Property Management Expenses,8,Kline P&L
2026-10,VWH1,1 Sample St,Expense,9999,Other Kline Expense,2,Kline P&L
2026-10,VWH1,1 Sample St,Expense,5200,Property Repairs & Maintenance Expenses,100,Bank
2026-10,VWH1,1 Sample St,Expense,BANK-INS,Property Insurance,6,Bank
2026-10,VWH1,1 Sample St,Expense,BANK-TAX-PY,Property Tax - Prior Year (delinquent),15,Bank
2026-09,VWH2,2 Sample St,Income,4300,Property Income,90,Kline P&L
2026-09,VWH2,2 Sample St,Expense,5200,Property Repairs & Maintenance Expenses,40,Kline P&L
2026-09,VWH2,2 Sample St,Expense,BANK-INS,Property Insurance,4,Bank
2026-10,VWH2,2 Sample St,Income,4300,Property Income,90,Kline P&L
2026-10,VWH2,2 Sample St,Expense,5200,Property Repairs & Maintenance Expenses,20,Kline P&L
2026-10,VWH2,2 Sample St,Expense,BANK-INS,Property Insurance,4,Bank
2026-10,VWH2,2 Sample St,Expense,BANK-TAX,Property Tax,30,Bank
"""

FAKE_TAX = f"""{TAX_HEADER}
2024,VWH1,1 Sample St,1,400,599,999,Paid,Paid
2025,VWH1,1 Sample St,1,125,125,250,Paid,Paid
2026,VWH1,1 Sample St,1,150,150,300,Paid,Unpaid
2026,VWH3,3 Sample St,3,60,60,120,Paid,Paid
"""


def _pl(text: str = FAKE_PL):
    loaded = resolve_vwh({VWH_ENV: text})
    assert loaded.warning is None
    return loaded.rows


def _tax(text: str = FAKE_TAX):
    loaded = resolve_vwh_tax({TAX_ENV: text})
    assert loaded.warning is None
    return loaded.rows


def _line(rate, property_id: str):
    found = [line for line in rate.lines if line.property_id == property_id]
    assert len(found) == 1
    return found[0]


class TaxLoadTests(unittest.TestCase):
    def test_missing_tax_file_is_empty_and_the_pl_file_is_separate(self) -> None:
        self.assertEqual(TAX_ENV, "FINANCE_VWH_TAX_CSV")
        self.assertEqual(NO_TAX_DATA, "Property tax data isn't loaded yet.")
        for env in ({}, {TAX_ENV: ""}, {TAX_ENV: " \n"}, {TAX_ENV: "\ufeff"}, {VWH_ENV: FAKE_PL}):
            loaded = resolve_vwh_tax(env)
            self.assertFalse(loaded.configured)
            self.assertIsNone(loaded.warning)
            self.assertTrue(loaded.rows.empty)
        self.assertFalse(resolve_vwh({TAX_ENV: FAKE_TAX}).configured)

    def test_bad_tax_file_does_not_quote_cells(self) -> None:
        loaded = resolve_vwh_tax({TAX_ENV: "Pay Year,Annual Tax\n2026,SECRET-CELL\n"})
        self.assertTrue(loaded.configured)
        self.assertEqual(loaded.warning, TAX_MISSING_COLUMN)
        self.assertNotIn("SECRET-CELL", loaded.warning or "")
        self.assertTrue(loaded.rows.empty)

        text = TAX_HEADER + "\n"
        text += "2026,VWH1,1 Sample St,1,10,10,20,Paid,Paid\n"
        text += "2026,VWH9,9 Sample St,9,10,10,NOPE,Paid,Paid\n"
        text += "nope,VWH9,9 Sample St,9,10,10,20,Paid,Paid\n"
        loaded = resolve_vwh_tax({TAX_ENV: text})
        self.assertEqual(loaded.warning, tax_skipped_message(2))
        self.assertNotIn("NOPE", loaded.warning or "")
        self.assertNotIn("nope", loaded.warning or "")
        table = property_tax_table(loaded.rows, "2026")
        self.assertEqual(format_money(table.lines[0].annual), "$20.00")

        with mock.patch("kirbancials.vwh_trend.pd.read_csv", side_effect=RuntimeError("SECRET")):
            loaded = resolve_vwh_tax({TAX_ENV: "Pay Year,Property ID\nSECRET,VWH1\n"})
        self.assertEqual(loaded.warning, TAX_OMITTED)
        self.assertNotIn("SECRET", loaded.warning or "")

    def test_bom_and_padded_header_still_parse(self) -> None:
        text = "\ufeff" + TAX_HEADER.replace("Annual Tax", " Annual Tax ") + "\n"
        text += "2026,VWH1,1 Sample St,1,1.5,1.5,3,Paid,Paid\n"
        loaded = resolve_vwh_tax({TAX_ENV: text})
        self.assertIsNone(loaded.warning)
        table = property_tax_table(loaded.rows, "2026")
        self.assertEqual(format_money(table.lines[0].annual), "$3.00")


class TaxTableTests(unittest.TestCase):
    def test_latest_year_change_and_a_house_with_no_prior_year(self) -> None:
        rows = _tax()
        self.assertEqual(available_pay_years(rows), ["2026", "2025", "2024"])
        table = property_tax_table(rows, "2026")
        self.assertEqual([line.property_id for line in table.lines], ["VWH1", "VWH3"])
        first, third = table.lines
        self.assertEqual(first.spring_status, "Paid")
        self.assertEqual(first.fall_status, "Unpaid")
        self.assertEqual(format_money(first.annual), "$300.00")
        self.assertEqual(format_money(first.change), "$50.00")
        self.assertEqual(first.change_pct, "+20.0%")
        self.assertIsNone(third.change)
        self.assertEqual(third.change_pct, MISSING)
        self.assertEqual(format_money(table.total_annual), "$420.00")
        self.assertEqual(format_money(table.total_spring), "$210.00")
        self.assertEqual(format_money(table.total_change), "$50.00")
        self.assertEqual(table.total_change_pct, "+20.0%")
        frame = _tax_frame(table, None)
        self.assertNotIn("Total", frame["Address"].tolist())
        self.assertTrue(pd.api.types.is_numeric_dtype(frame["Annual"]))
        self.assertTrue(pd.api.types.is_numeric_dtype(frame["vs last year %"]))
        self.assertEqual(
            frame.sort_values("Annual")["Address"].tolist(),
            ["3 Sample St", "1 Sample St"],
        )
        one_house = _tax_frame(table, "VWH1")
        self.assertEqual(one_house["Address"].tolist(), ["1 Sample St"])
        self.assertNotIn("Total", one_house["Address"].tolist())

    def test_duplicate_rows_add_and_percent_rounds_half_up(self) -> None:
        text = TAX_HEADER + "\n"
        text += "2026,VWH1,1 Sample St,1,1,1,2,Paid,Paid\n"
        text += "2026,VWH1,1 Old St,1,3,3,6,Late,Unpaid\n"
        text += "2025,VWH1,1 Sample St,1,612.40,612.40,1224.80,Paid,Paid\n"
        text += "2026,VWH2,2 Sample St,2,100,100,200,,\n"
        rows = _tax(text)
        table = property_tax_table(rows, "2026")
        first = table.lines[0]
        self.assertEqual(first.address, "1 Old St")
        self.assertEqual(format_money(first.annual), "$8.00")
        self.assertEqual(first.spring_status, "Late")
        self.assertEqual(first.fall_status, "Unpaid")
        self.assertEqual(format_percent_change(6480, 116000), "+5.6%")
        self.assertEqual(format_percent_change(-1000, 2000), "-50.0%")
        self.assertEqual(format_percent_change(0, 100), "0.0%")
        self.assertEqual(format_percent_change(50, 0), MISSING)
        self.assertEqual(format_percent_change(None, 100), MISSING)

    def test_percent_for_the_sample_bill_shape(self) -> None:
        text = TAX_HEADER + "\n"
        text += "2025,VWH1,1 Sample St,1,580,580,1160,Paid,Paid\n"
        text += "2026,VWH1,1 Sample St,1,612.40,612.40,1224.80,Paid,Paid\n"
        text += "2025,VWH2,2 Sample St,2,690,690,1380,Paid,Paid\n"
        text += "2026,VWH2,2 Sample St,2,701.10,701.10,1402.20,Paid,Unpaid\n"
        text += "2025,VWH3,3 Sample St,3,640,640,1280,Paid,Paid\n"
        text += "2026,VWH3,3 Sample St,3,655,655,1310,Paid,Paid\n"
        text += "2025,VWH4,4 Sample St,4,575,575,1150,Paid,Paid\n"
        text += "2026,VWH4,4 Sample St,4,590.25,590.25,1180.50,Paid,Paid\n"
        table = property_tax_table(_tax(text), "2026")
        self.assertEqual(
            [line.change_pct for line in table.lines],
            ["+5.6%", "+1.6%", "+2.3%", "+2.7%"],
        )
        self.assertEqual(format_money(table.total_annual), "$5,117.50")
        self.assertEqual(format_money(table.total_change), "$147.50")
        self.assertEqual(table.total_change_pct, "+3.0%")


class RunRateTests(unittest.TestCase):
    def test_run_rate_uses_sections_bills_and_the_latest_insurance(self) -> None:
        rows = _pl()
        rate = annual_run_rate(rows, _tax(), tax_configured=True)
        self.assertEqual(rate.months, ("2026-08", "2026-09", "2026-10"))
        first = _line(rate, "VWH1")
        second = _line(rate, "VWH2")
        third = _line(rate, "VWH3")
        # VWH1 Kline income 100+200-50+150 = 400 over 3 months, times 12.
        self.assertEqual(format_money(first.income), "$1,600.00")
        # Kline expense 10+20+10+30+10+8+2 = 90. Bank 5200, tax, penalties stay out.
        self.assertEqual(format_money(first.operating_expense), "$360.00")
        self.assertEqual(format_money(first.property_tax), "$300.00")
        self.assertEqual(format_money(first.insurance), "$72.00")
        self.assertEqual(format_money(first.net), "$868.00")
        self.assertEqual(format_money(first.net_per_month), "$72.33")
        # VWH2 misses August, so the 180 of income is divided by 3, not by 2.
        self.assertEqual(format_money(second.income), "$720.00")
        self.assertEqual(format_money(second.operating_expense), "$240.00")
        self.assertEqual(format_money(second.property_tax), "$30.00")
        self.assertEqual(format_money(second.insurance), "$48.00")
        self.assertEqual(format_money(second.net), "$402.00")
        self.assertEqual(format_money(second.net_per_month), "$33.50")
        self.assertEqual(format_money(third.income), "$0.00")
        self.assertEqual(format_money(third.property_tax), "$120.00")
        self.assertEqual(format_money(third.net), "-$120.00")
        self.assertEqual(format_money(third.net_per_month), "-$10.00")
        self.assertEqual([line.property_id for line in rate.lines], ["VWH1", "VWH2", "VWH3"])
        self.assertEqual(format_money(rate.total.income), "$2,320.00")
        self.assertEqual(format_money(rate.total.operating_expense), "$600.00")
        self.assertEqual(format_money(rate.total.property_tax), "$450.00")
        self.assertEqual(format_money(rate.total.insurance), "$120.00")
        self.assertEqual(format_money(rate.total.net), "$1,150.00")
        self.assertEqual(format_money(rate.total.net_per_month), "$95.83")
        note = run_rate_note(rate.months, True)
        self.assertIn("August 2026 through October 2026 (3 months)", note)
        self.assertIn("latest annual bill", note)
        self.assertNotIn("isn't loaded yet", note)

    def test_missing_tax_file_falls_back_to_the_bank_tax_sum(self) -> None:
        rows = _pl()
        rate = annual_run_rate(rows, empty_rows(), tax_configured=False)
        self.assertEqual(format_money(_line(rate, "VWH1").property_tax), "$80.00")
        self.assertEqual(format_money(_line(rate, "VWH2").property_tax), "$30.00")
        self.assertNotIn("VWH3", [line.property_id for line in rate.lines])
        note = run_rate_note(rate.months, False)
        self.assertIn("property tax data isn't loaded yet", note)
        self.assertIn("Penalties and prior-year tax are left out.", note)

    def test_a_zero_bill_is_kept_and_only_twelve_months_are_averaged(self) -> None:
        text = TAX_HEADER + "\n"
        text += "2026,VWH1,1 Sample St,1,0,0,0,Paid,Paid\n"
        months = [f"2025-{month:02d}" for month in range(10, 13)]
        months += [f"2026-{month:02d}" for month in range(1, 11)]
        self.assertEqual(len(months), 13)
        lines = [PL_HEADER]
        for index, month in enumerate(months):
            amount = 1000 if index == 0 else 1
            lines.append(f"{month},VWH1,1 Sample St,Income,4300,Property Income,{amount},Kline P&L")
        lines.append(f"{months[-1]},VWH1,1 Sample St,Expense,BANK-TAX,Property Tax,40,Bank")
        lines.append(f"{months[-2]},VWH1,1 Sample St,Expense,BANK-INS,Property Insurance,9,Bank")
        rows = _pl("\n".join(lines) + "\n")
        rate = annual_run_rate(rows, _tax(text), tax_configured=True)
        self.assertEqual(len(rate.months), 12)
        self.assertNotIn(months[0], rate.months)
        self.assertEqual(rate.months[-1], "2026-10")
        # 12 months of $1, the old $1,000 left out. The $0 bill wins over BANK-TAX.
        self.assertEqual(format_money(_line(rate, "VWH1").income), "$12.00")
        self.assertEqual(format_money(_line(rate, "VWH1").property_tax), "$0.00")
        # Latest month has no insurance, so the September premium is not reused.
        self.assertEqual(format_money(_line(rate, "VWH1").insurance), "$0.00")
        trend = monthly_trend(rows)
        self.assertEqual(len(trend), 13)
        self.assertEqual(trend.iloc[0]["Month"], months[0])
        self.assertEqual(format_money(trend.iloc[0]["Income"]), "$1,000.00")

    def test_ten_months_and_a_gap_match_the_launch_shape(self) -> None:
        lines = [PL_HEADER]
        for month in range(1, 11):
            lines.append(
                f"2026-{month:02d},VWH1,1 Sample St,Income,4300,Property Income,100,Kline P&L"
            )
            if month != 1:
                lines.append(
                    f"2026-{month:02d},VWH2,2 Sample St,Income,4300,Property Income,50,Kline P&L"
                )
        lines.append("2026-10,VWH1,1 Sample St,Expense,BANK-INS,Property Insurance,4,Bank")
        lines.append("2026-01,VWH1,1 Sample St,Expense,BANK-INS,Property Insurance,1,Bank")
        rows = _pl("\n".join(lines) + "\n")
        rate = annual_run_rate(rows, empty_rows(), tax_configured=False)
        self.assertEqual(len(rate.months), 10)
        self.assertEqual(format_money(_line(rate, "VWH1").income), "$1,200.00")
        # 50 * 9 months / 10 * 12 = 540.
        self.assertEqual(format_money(_line(rate, "VWH2").income), "$540.00")
        self.assertEqual(format_money(_line(rate, "VWH1").insurance), "$48.00")
        note = run_rate_note(rate.months, False)
        self.assertIn("January 2026 through October 2026 (10 months)", note)
        house = monthly_trend(rows, "VWH2")
        self.assertEqual(house.iloc[0]["Month"], "2026-01")
        self.assertEqual(format_money(house.iloc[0]["Income"]), "$0.00")
        self.assertEqual(format_money(house.iloc[1]["Income"]), "$50.00")

    def test_half_up_rounding_on_a_short_average(self) -> None:
        lines = [PL_HEADER]
        for month in range(1, 9):
            amount = "0.04" if month == 8 else "0"
            lines.append(f"2026-{month:02d},VWH1,1 Sample St,Income,4300,Property Income,{amount},Kline P&L")
        rate = annual_run_rate(_pl("\n".join(lines) + "\n"), empty_rows(), tax_configured=False)
        # 4 cents * 12 / 8 = 6 cents a year, and 6 / 12 rounds half up to 1 cent.
        self.assertEqual(format_money(_line(rate, "VWH1").income), "$0.06")
        self.assertEqual(format_money(_line(rate, "VWH1").net_per_month), "$0.01")


class TrendChartTests(unittest.TestCase):
    def test_chart_matches_the_statement_and_keeps_bank_amounts(self) -> None:
        rows = _pl()
        trend = monthly_trend(rows)
        self.assertEqual(trend["Month"].tolist(), ["2026-08", "2026-09", "2026-10"])
        self.assertEqual(trend["Label"].tolist(), ["Aug 2026", "Sep 2026", "Oct 2026"])
        october = trend.loc[trend["Month"] == "2026-10"].iloc[0]
        statement = summarize(rows, month_window(available_months(rows), "2026-10", VIEW_MONTH))
        self.assertEqual(format_money(october["Income"]), format_money(statement.income))
        self.assertEqual(format_money(october["Expense"]), format_money(statement.expense))
        self.assertEqual(format_money(october["Net"]), format_money(statement.net))
        # Bank income is in the chart. Penalties are not. Prior-year tax is.
        self.assertEqual(format_money(october["Income"]), "$690.00")
        self.assertEqual(format_money(october["Expense"]), "$225.00")
        self.assertEqual(format_money(october["Net"]), "$465.00")
        september = trend.loc[trend["Month"] == "2026-09"].iloc[0]
        self.assertEqual(format_money(september["Expense"]), "$139.00")
        quiet = monthly_trend(rows, "VWH2")
        august = quiet.loc[quiet["Month"] == "2026-08"].iloc[0]
        self.assertEqual(format_money(august["Income"]), "$0.00")
        self.assertEqual(format_money(august["Expense"]), "$0.00")
        chart = _trend_chart(trend)
        self.assertIsNotNone(chart)
        self.assertIsNone(_trend_chart(monthly_trend(empty_rows())))


class TrendPrivacyTests(unittest.TestCase):
    def test_password_gates_stay_in_the_entrypoint(self) -> None:
        for name in ("kirbancials/vwh.py", "kirbancials/vwh_trend.py", "views/vwh_pl.py"):
            text = (ROOT / name).read_text()
            self.assertNotIn("APP_PASSWORD", text)
            self.assertNotIn("BALANCE_SHEET_PASSWORD", text)
            self.assertNotIn("require_login", text)
