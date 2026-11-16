"""VWH portfolio totals. Fixtures are fake round numbers, not a property export."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from kirbancials.formatting import format_money, format_signed_money
from kirbancials.vwh import (
    MISSING_COLUMN,
    NO_VWH_DATA,
    OMITTED,
    VIEW_MONTH,
    VIEW_T12,
    VIEW_YTD,
    VWH_ENV,
    address_labels,
    available_months,
    describe_window,
    lookup_property,
    memo_lines_to_show,
    month_window,
    prefer_choice,
    resolve_vwh,
    skipped_message,
    summarize,
)

ROOT = Path(__file__).resolve().parents[1]

HEADER = "Month,Property ID,Address,Section,Account Number,Account Name,Amount,Source"

# Clearly fake houses. 2025-09 is outside a trailing 12 months that ends in 2026-10.
FAKE_VWH = """Month,Property ID,Address,Section,Account Number,Account Name,Amount,Source
2025-09,VWH1,1 Old St,Income,4300,Property Income,999,Kline P&L
2026-09,VWH1,1 Sample St,Income,4300,Property Income,1000,Kline P&L
2026-09,VWH1,1 Sample St,Expense,6400,Property Management Expenses,100,Kline P&L
2026-09,VWH1,1 Sample St,Expense,5200,Property Repairs & Maintenance Expenses,200,Kline P&L
2026-09,VWH1,1 Sample St,Expense,BANK-INS,Property Insurance,50,Bank
2026-09,VWH1,1 Sample St,Expense,BANK-TAX,Property Tax,80,Bank
2026-09,VWH1,1 Sample St,Below the line,BANK-TAX-PEN,Tax Penalties (excluded),7,Bank
2026-09,VWH2,2 Sample St,expense,5200,Property Repairs & Maintenance Expenses,40,Kline P&L
2026-09,VWH3,3 Sample St,below the line,BANK-TAX-PEN,Tax Penalties (excluded),3,Bank
2026-10,VWH1,1 Sample Street,Income,4000,Property Management Income,10,Kline P&L
2026-10,VWH1,1 Sample Street,Income,4300,Property Income,1100,Kline P&L
2026-10,VWH1,1 Sample Street,Expense,6400,Property Management Expenses,110,Kline P&L
2026-10,VWH2,2 Sample St,Expense,5200,Property Repairs & Maintenance Expenses,25,Kline P&L
2026-10,VWH2,2 Sample St,Expense,BANK-TAX-PY,Property Tax - Prior Year (delinquent),15,Bank
2026-10,VWH2,2 Sample St,Below the line,BANK-TAX-PEN,Tax Penalties (excluded),0,Bank
"""


def _load(text: str = FAKE_VWH):
    loaded = resolve_vwh({VWH_ENV: text})
    assert loaded.warning is None
    return loaded.rows


def _statement(text: str, selected: str, view: str):
    rows = _load(text)
    window = month_window(available_months(rows), selected, view)
    return summarize(rows, window)


def _house(statement, property_id: str):
    found = [prop for prop in statement.properties if prop.property_id == property_id]
    assert len(found) == 1
    return found[0]


class VwhEnvTests(unittest.TestCase):
    def test_missing_or_blank_env_has_no_rows(self) -> None:
        self.assertEqual(VWH_ENV, "FINANCE_VWH_PL_CSV")
        self.assertEqual(NO_VWH_DATA, "No VWH P&L data yet")
        for env in ({}, {VWH_ENV: ""}, {VWH_ENV: " \n"}, {VWH_ENV: "\ufeff"}, {"FINANCE_INCOME_CSV": FAKE_VWH}):
            loaded = resolve_vwh(env)
            self.assertFalse(loaded.configured)
            self.assertIsNone(loaded.warning)
            self.assertTrue(loaded.rows.empty)
            self.assertEqual(available_months(loaded.rows), [])

    def test_header_only_is_configured_and_empty(self) -> None:
        loaded = resolve_vwh({VWH_ENV: HEADER + "\n"})
        self.assertTrue(loaded.configured)
        self.assertIsNone(loaded.warning)
        self.assertEqual(available_months(loaded.rows), [])

    def test_bom_and_padded_header_still_parse(self) -> None:
        text = "\ufeff" + HEADER.replace("Amount", " Amount ") + "\n"
        text += "2026-10,VWH1,1 Sample St,Income,4300,Property Income,12.5,Kline P&L\n"
        loaded = resolve_vwh({VWH_ENV: text})
        self.assertIsNone(loaded.warning)
        statement = summarize(loaded.rows, month_window(available_months(loaded.rows), "2026-10", VIEW_MONTH))
        self.assertEqual(format_money(statement.income), "$12.50")

    def test_missing_column_warning_does_not_quote_the_file(self) -> None:
        loaded = resolve_vwh({VWH_ENV: "Month,Amount\n2026-10,SECRET-CELL\n"})
        self.assertTrue(loaded.configured)
        self.assertEqual(loaded.warning, MISSING_COLUMN)
        self.assertNotIn("SECRET-CELL", loaded.warning or "")
        self.assertTrue(loaded.rows.empty)

    def test_bad_row_is_skipped_without_quoting_it(self) -> None:
        text = HEADER + "\n"
        text += "2026-10,VWH9,9 Sample St,Income,4300,Property Income,5,Kline P&L\n"
        text += "2026-10,VWH9,9 Sample St,Income,4300,Property Income,NOT-A-NUMBER,Kline P&L\n"
        text += "2026-13,VWH9,9 Sample St,Income,4300,Property Income,8,Kline P&L\n"
        loaded = resolve_vwh({VWH_ENV: text})
        self.assertEqual(loaded.warning, skipped_message(2))
        self.assertNotIn("NOT-A-NUMBER", loaded.warning or "")
        self.assertNotIn("2026-13", loaded.warning or "")
        statement = summarize(loaded.rows, month_window(["2026-10"], "2026-10", VIEW_MONTH))
        self.assertEqual(format_money(statement.income), "$5.00")

    def test_unexpected_read_error_does_not_quote_the_file(self) -> None:
        with mock.patch("kirbancials.vwh.pd.read_csv", side_effect=RuntimeError("SECRET")):
            loaded = resolve_vwh({VWH_ENV: "Month,Property ID\nSECRET-CELL,VWH1\n"})
        self.assertEqual(loaded.warning, OMITTED)
        self.assertNotIn("SECRET", loaded.warning or "")
        self.assertTrue(loaded.rows.empty)


class VwhTotalTests(unittest.TestCase):
    def test_latest_month_is_first_and_penalties_stay_out_of_net(self) -> None:
        rows = _load()
        self.assertEqual(available_months(rows), ["2026-10", "2026-09", "2025-09"])
        october = _statement(FAKE_VWH, "2026-10", VIEW_MONTH)
        self.assertEqual(october.window.selected, "2026-10")
        self.assertEqual(format_money(october.income), "$1,110.00")
        self.assertEqual(format_money(october.expense), "$150.00")
        self.assertEqual(format_money(october.net), "$960.00")
        self.assertEqual(format_signed_money(october.net), ":green[$960.00]")
        self.assertEqual(memo_lines_to_show(october.memo_lines), ())
        self.assertEqual(
            [(line.account_number, line.account_name, line.source, format_money(line.amount)) for line in october.expense_lines],
            [
                ("5200", "Property Repairs & Maintenance Expenses", "Kline P&L", "$25.00"),
                ("6400", "Property Management Expenses", "Kline P&L", "$110.00"),
                ("BANK-TAX-PY", "Property Tax - Prior Year (delinquent)", "Bank", "$15.00"),
            ],
        )
        first = _house(october, "VWH1")
        second = _house(october, "VWH2")
        self.assertEqual([prop.property_id for prop in october.properties], ["VWH1", "VWH2"])
        self.assertEqual(first.address, "1 Sample Street")
        self.assertEqual(format_money(first.income), "$1,110.00")
        self.assertEqual(format_money(first.expense), "$110.00")
        self.assertEqual(format_money(first.net), "$1,000.00")
        self.assertEqual(
            [line.account_number for line in first.income_lines],
            ["4000", "4300"],
        )
        self.assertNotIn("VWH3", [prop.property_id for prop in october.properties])
        self.assertEqual(format_money(second.income), "$0.00")
        self.assertEqual(format_money(second.expense), "$40.00")
        self.assertEqual(format_signed_money(second.net), ":red[-$40.00]")
        self.assertEqual(second.income_lines, ())

    def test_vacant_house_and_memo_line_in_an_earlier_month(self) -> None:
        september = _statement(FAKE_VWH, "2026-09", VIEW_MONTH)
        self.assertEqual([prop.property_id for prop in september.properties], ["VWH1", "VWH2", "VWH3"])
        self.assertEqual(format_money(september.income), "$1,000.00")
        self.assertEqual(format_money(september.expense), "$470.00")
        self.assertEqual(format_money(september.net), "$530.00")
        shown = memo_lines_to_show(september.memo_lines)
        self.assertEqual(len(shown), 1)
        self.assertEqual(shown[0].account_number, "BANK-TAX-PEN")
        self.assertEqual(shown[0].account_name, "Tax Penalties (excluded)")
        self.assertEqual(format_money(shown[0].amount), "$10.00")
        vacant = _house(september, "VWH2")
        self.assertEqual(vacant.income_lines, ())
        self.assertEqual(format_money(vacant.income), "$0.00")
        self.assertEqual(format_signed_money(vacant.net), ":red[-$40.00]")
        memo_only = _house(september, "VWH3")
        self.assertEqual(format_money(memo_only.income), "$0.00")
        self.assertEqual(format_money(memo_only.expense), "$0.00")
        self.assertEqual(format_money(memo_only.net), "$0.00")
        self.assertEqual(format_money(memo_lines_to_show(memo_only.memo_lines)[0].amount), "$3.00")
        self.assertEqual(
            [line.account_number for line in september.expense_lines],
            ["5200", "6400", "BANK-INS", "BANK-TAX"],
        )

    def test_year_to_date_and_trailing_twelve_skip_the_old_month(self) -> None:
        ytd = _statement(FAKE_VWH, "2026-10", VIEW_YTD)
        trailing = _statement(FAKE_VWH, "2026-10", VIEW_T12)
        self.assertEqual(ytd.window.start, "2026-01")
        self.assertEqual(ytd.window.months, ("2026-09", "2026-10"))
        self.assertEqual(trailing.window.start, "2025-11")
        self.assertEqual(trailing.window.months, ("2026-09", "2026-10"))
        for statement in (ytd, trailing):
            self.assertEqual(format_money(statement.income), "$2,110.00")
            self.assertEqual(format_money(statement.expense), "$620.00")
            self.assertEqual(format_money(statement.net), "$1,490.00")
            self.assertEqual(format_money(memo_lines_to_show(statement.memo_lines)[0].amount), "$10.00")
            self.assertEqual(_house(statement, "VWH1").address, "1 Sample Street")
            self.assertIn("VWH3", [prop.property_id for prop in statement.properties])
        self.assertEqual(
            describe_window(ytd.window),
            "September 2026 through October 2026. Months with no rows count as zero.",
        )
        september = _statement(FAKE_VWH, "2026-09", VIEW_T12)
        self.assertEqual(september.window.start, "2025-10")
        self.assertEqual(september.window.months, ("2026-09",))
        self.assertEqual(format_money(september.income), "$1,000.00")
        self.assertNotIn("999", format_money(september.income))

    def test_unknown_month_uses_the_latest(self) -> None:
        rows = _load()
        window = month_window(available_months(rows), "nope", "nope")
        self.assertEqual(window.view, VIEW_MONTH)
        self.assertEqual(window.selected, "2026-10")
        self.assertEqual(describe_window(window), "October 2026")

    def test_duplicate_amounts_sum_and_a_negative_reduces_the_total(self) -> None:
        text = HEADER + "\n"
        text += "2026-10,VWH1,1 Sample St,Income,4300,Property Income,10,Kline P&L\n"
        text += "2026-10,VWH1,1 Sample St,Income,4300,Property Income,2.50,Kline P&L\n"
        text += "2026-10,VWH1,1 Sample St,Income,4300,Property Income,-1.25,Kline P&L\n"
        statement = _statement(text, "2026-10", VIEW_MONTH)
        self.assertEqual(format_money(statement.income), "$11.25")
        self.assertEqual(len(_house(statement, "VWH1").income_lines), 1)

    def test_house_outside_the_month_is_zero_and_unknown_house_is_missing(self) -> None:
        october = _statement(FAKE_VWH, "2026-10", VIEW_MONTH)
        rows = _load()
        quiet = lookup_property(october, rows, "VWH3")
        assert quiet is not None
        self.assertEqual(quiet.address, "3 Sample St")
        self.assertEqual(format_money(quiet.income), "$0.00")
        self.assertEqual(format_money(quiet.net), "$0.00")
        self.assertIsNone(lookup_property(october, rows, "VWH-missing"))

    def test_shared_address_keeps_the_house_id(self) -> None:
        text = HEADER + "\n"
        text += "2026-10,VWH1,1 Sample St,Income,4300,Property Income,10,Kline P&L\n"
        text += "2026-10,VWH2,1 Sample St,Income,4300,Property Income,4,Kline P&L\n"
        statement = _statement(text, "2026-10", VIEW_MONTH)
        labels = address_labels(statement.properties)
        self.assertEqual(labels["VWH1"], "1 Sample St (VWH1)")
        self.assertEqual(labels["VWH2"], "1 Sample St (VWH2)")

    def test_full_trailing_year_does_not_mention_missing_months(self) -> None:
        months = [f"2025-{month:02d}" for month in range(11, 13)]
        months += [f"2026-{month:02d}" for month in range(1, 11)]
        window = month_window(months, "2026-10", VIEW_T12)
        self.assertEqual(len(window.months), 12)
        self.assertEqual(describe_window(window), "November 2025 through October 2026")


class VwhChoiceTests(unittest.TestCase):
    def test_a_changed_url_wins_over_the_select_box(self) -> None:
        allowed = {"2026-09", "2026-10"}
        self.assertEqual(
            prefer_choice("2026-09", "2026-10", "2026-10", allowed, "2026-10"),
            ("2026-09", "2026-09"),
        )
        self.assertEqual(
            prefer_choice("2026-10", "2026-09", "2026-10", allowed, "2026-10"),
            ("2026-09", "2026-10"),
        )
        self.assertEqual(
            prefer_choice(None, None, None, allowed, "2026-10"),
            ("2026-10", "2026-10"),
        )
        self.assertEqual(
            prefer_choice("missing", "also-missing", "2026-10", allowed, "2026-10"),
            ("2026-10", "2026-10"),
        )


class VwhNavigationTests(unittest.TestCase):
    def test_vwh_follows_the_balance_sheet_and_login_still_covers_it(self) -> None:
        text = (ROOT / "app.py").read_text()
        pages = [line for line in text.splitlines() if "st.Page(" in line]
        self.assertIn("profit_and_loss.py", pages[0])
        self.assertIn("default=True", pages[0])
        self.assertIn("balance_sheet.py", pages[1])
        self.assertIn("vwh_pl.py", pages[2])
        self.assertIn("VWH P&L", pages[2])
        sidebar = text.split("with st.sidebar:", 1)[1]
        links = [line for line in sidebar.splitlines() if "st.page_link" in line]
        self.assertIn("profit_and_loss.py", links[0])
        self.assertIn("balance_sheet.py", links[1])
        self.assertIn("vwh_pl.py", links[2])
        self.assertIn("VWH P&L", links[2])
        self.assertLess(text.index("require_login()"), text.index("navigation.run()"))
        view = (ROOT / "views" / "vwh_pl.py").read_text()
        self.assertIn('st.title("VWH P&L")', view)
        self.assertIn("render_vwh_pl", view)
        self.assertNotIn("require_login", view)
        self.assertNotIn("APP_PASSWORD", view)
        self.assertNotIn("BALANCE_SHEET_PASSWORD", view)
