"""Bills, property taxes, and entity fees.

Fixtures are fake round numbers. They are not account exports.
"""

from __future__ import annotations

import io
import os
import unittest
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from streamlit.testing.v1 import AppTest

from kirbancials.activity import load_balances, resolve_balances
from kirbancials.bills import (
    ACCOUNT_MATCH,
    ACCOUNT_MATCH_NOTE,
    ALL_TYPES,
    BILL_COLUMNS,
    BILL_EXPORT,
    BILL_HEADER,
    BILL_LEGEND,
    BILL_SECTIONS,
    BILLS_ENV,
    DUE_SOON_STYLE,
    ENTITY_FEE_COLUMNS,
    ENTITY_FEE_HEADER,
    ENTITY_FEES_ENV,
    FLAG_NOTE,
    NONE_YET,
    NO_BILLS,
    NO_BILLS_FOR_TYPE,
    NO_ENTITY_FEES,
    NO_PROPERTY_TAXES,
    NO_TYPE,
    OVERDUE_STYLE,
    PROP_TAX_COLUMNS,
    PROP_TAX_EXPORT,
    PROP_TAX_HEADER,
    PROP_TAXES_ENV,
    SECTION_CAR,
    SECTION_COLUMN,
    SECTION_CREDIT,
    SECTION_LOC,
    SECTION_OTHER,
    SECTION_TAX,
    SUBTOTAL_LABEL,
    TOTAL_LABEL,
    BillRecord,
    apply_account_balances,
    bill_flag,
    bill_records,
    bill_section,
    canonical_section,
    entity_fee_records,
    filter_bills,
    infer_section,
    property_tax_records,
    resolve_bills,
    resolve_entity_fees,
    resolve_property_taxes,
    section_groups,
    sort_bills,
    table_csv,
    type_options,
    workbook_bytes,
)
from kirbancials.formatting import show_money_table

TODAY = date(2026, 11, 12)
ROOT = Path(__file__).resolve().parents[1]
HIDDEN = "HIDDEN-FIGURE-999"

BILLS = f"""{BILL_HEADER},{ACCOUNT_MATCH}
Sample Visa,Personal,"$10.00",40.00,15.00,25.00,19.99%,2026-10-31,2026-11-10,,Fake card, Sample Card 
Sample Rent,Personal,,1200.00,1200.00,0,,2026-11-01,11/1/2026,2026-11-01,Paid on time,
Sample Insurance,Business,0.00,80.00,0.00,80.00,,2026-11-15,2026-11-18,,Due soon,
Sample Loan,Other,50.00,50.00,0.00,50.00,5.5,2026-11-01,2026-12-01,,Later,
Undated,VWH,,,,,,,,,,
Sample Cash,Personal,7.00,7.00,0.00,7.00,,2026-11-01,2026-11-30,,Keep manual,Sample Checking
Sample Auto,Business,1.00,1.00,0.00,1.00,3,2026-11-01,2026-11-19,,"Loan match, really",sample auto
Sample Mystery,Other,3.00,3.00,0.00,3.00,,2026-11-01,2026-11-25,,No match,Missing Account
"""

BALANCES = """Account,As Of,Current Balance,Account Type
Sample Card,2026-11-01T00:00:00Z,5,credit
Sample Card,2026-11-12T00:00:00Z,400,credit
Sample Auto,2026-11-12T00:00:00Z,900,loan
Sample Checking,2026-11-12T00:00:00Z,25,depository
"""

TAXES = f"""{PROP_TAX_HEADER}
Sample House,200.00,100.00,100.00,100.00,Fake parcel
Sample Lot,($50.00),25.00,0.00,50.00,
"""

FEES = f"""{ENTITY_FEE_HEADER}
Sample LLC,AZ,FILE-1,Sample Agent,Active,125.00,2026-04-15,50.00,2026-04-15,Sample Checking,Fake fee
Second LLC,NV,,Other Agent,Due,75.00,4/1/2026,0.00,2027-01-15,,
"""

SORTED_NAMES = [
    "Sample Rent",
    "Sample Visa",
    "Sample Insurance",
    "Sample Auto",
    "Sample Mystery",
    "Sample Cash",
    "Sample Loan",
    "Undated",
]

# Fake round numbers. Section is optional and last. Lines of Credit has one row
# here so the grouping test can check a subtotal; the page still allows it to
# be empty.
SECTIONED = f"""{BILL_HEADER},{ACCOUNT_MATCH},{SECTION_COLUMN}
Amex Gold,Personal,10,40,10,30,19.99,2026-10-31,2026-11-10,,Inferred,,
Citi Double Cash,Personal,20,30,5,25,21,2026-11-01,2026-11-08,,Earlier card,,
Jeep Loan,Personal,100,50,20,30,6,2026-11-01,2026-11-19,,Inferred car,,
BOA Auto Loan,Business,200,80,0,80,4,2026-11-01,2026-11-03,,Brand plus loan,,
Sample LOC,Business,21,21,0,21,8,2026-11-01,2026-11-18,,Inferred line,,
Sample Property Tax,VWH,1,10,0,10,,2026-11-01,2026-11-30,,Inferred tax,,
County Property Tax,VWH,2,15,15,0,,2026-11-01,2026-11-02,2026-11-02,Paid tax,,Property Taxes
HOA Dues,Other,3,25,25,0,,2026-11-01,2026-11-04,2026-11-04,Inferred other,,
Board Fee,Other,4,7,0,7,,2026-11-01,2026-11-28,,Not boa,,
Local Services,Business,5,9,0,9,,2026-11-01,2026-12-15,,Not loc,,
Amex Override,Personal,6,11,0,11,,2026-11-01,2026-11-06,,Forced,,Other
Rent Check,Personal,7,13,0,13,,2026-11-01,2026-11-07,,Unknown section,,Loans
"""


def _bills():
    loaded = resolve_bills({BILLS_ENV: "\ufeff" + BILLS})
    assert loaded.warning is None, loaded.warning
    origin = resolve_balances({"FINANCE_BALANCES_CSV": BALANCES})
    balances = load_balances(origin).rows
    return sort_bills(apply_account_balances(loaded.records, balances))


class HeaderTests(unittest.TestCase):
    def test_headers_match_the_readme(self) -> None:
        readme = (ROOT / "README.md").read_text()
        self.assertEqual(BILLS_ENV, "FINANCE_BILLS_CSV")
        self.assertEqual(PROP_TAXES_ENV, "FINANCE_PROP_TAXES_CSV")
        self.assertEqual(ENTITY_FEES_ENV, "FINANCE_ENTITY_FEES_CSV")
        self.assertEqual(
            BILL_HEADER,
            "Description,Type,Current Balance,Statement Balance,Amount Paid,"
            "Amount Still Due,Interest Rate,Statement Ending Date,Due Date,Paid Date,Notes",
        )
        self.assertEqual(
            PROP_TAX_HEADER,
            "Property,Total Amount Due,1st Half,Amount Paid,Remaining Due,Notes",
        )
        self.assertEqual(
            ENTITY_FEE_HEADER,
            "LLC,State,Tax File Number,Registered Agent,Status,Reg Agent Fee,"
            "Reg Agent Due Date,Franchise Tax,Franchise Tax Due Date,Paid From,Notes",
        )
        self.assertEqual(ACCOUNT_MATCH, "Account Match")
        for header in (BILL_HEADER, PROP_TAX_HEADER, ENTITY_FEE_HEADER):
            self.assertIn(f"`{header}`", readme)
        self.assertIn("`Account Match`", readme)
        self.assertIn("`Section`", readme)
        self.assertNotIn(SECTION_COLUMN, BILL_COLUMNS)
        self.assertEqual(BILL_COLUMNS[0], "Description")
        self.assertEqual(PROP_TAX_COLUMNS[2], "1st Half")
        self.assertEqual(ENTITY_FEE_COLUMNS[-2], "Paid From")


class BillFileTests(unittest.TestCase):
    def test_plain_amounts_dates_and_rates(self) -> None:
        bills = _bills()
        self.assertEqual([bill.description for bill in bills], SORTED_NAMES)
        visa = bills[1]
        self.assertEqual(visa.bill_type, "Personal")
        self.assertAlmostEqual(visa.current_balance or 0, 400)
        self.assertTrue(visa.balance_from_account)
        self.assertAlmostEqual(visa.statement_balance or 0, 40)
        self.assertAlmostEqual(visa.amount_paid or 0, 15)
        self.assertAlmostEqual(visa.amount_still_due or 0, 25)
        self.assertAlmostEqual(visa.interest_rate or 0, 19.99)
        self.assertEqual(visa.statement_ending, date(2026, 10, 31))
        self.assertEqual(visa.due_date, date(2026, 11, 10))
        self.assertIsNone(visa.paid_date)
        rent = bills[0]
        self.assertEqual(rent.due_date, date(2026, 11, 1))
        self.assertEqual(rent.paid_date, date(2026, 11, 1))
        self.assertIsNone(rent.current_balance)
        self.assertFalse(rent.balance_from_account)
        self.assertIsNone(bills[-1].due_date)

    def test_account_match_uses_cards_and_loans_only(self) -> None:
        by_name = {bill.description: bill for bill in _bills()}
        self.assertAlmostEqual(by_name["Sample Auto"].current_balance or 0, 900)
        self.assertTrue(by_name["Sample Auto"].balance_from_account)
        self.assertAlmostEqual(by_name["Sample Cash"].current_balance or 0, 7)
        self.assertFalse(by_name["Sample Cash"].balance_from_account)
        self.assertAlmostEqual(by_name["Sample Mystery"].current_balance or 0, 3)
        self.assertFalse(by_name["Sample Mystery"].balance_from_account)

    def test_header_only_is_configured_and_empty(self) -> None:
        loaded = resolve_bills({BILLS_ENV: BILL_HEADER + "\n"})
        self.assertTrue(loaded.configured)
        self.assertIsNone(loaded.warning)
        self.assertEqual(loaded.records, ())

    def test_missing_or_blank_env_has_no_warning(self) -> None:
        for loaded in (
            resolve_bills({}),
            resolve_bills({BILLS_ENV: " \n"}),
            resolve_property_taxes({}),
            resolve_property_taxes({PROP_TAXES_ENV: ""}),
            resolve_entity_fees({}),
            resolve_entity_fees({ENTITY_FEES_ENV: "\n"}),
        ):
            self.assertFalse(loaded.configured)
            self.assertIsNone(loaded.warning)
            self.assertEqual(loaded.records, ())

    def test_optional_account_match_column_may_be_omitted(self) -> None:
        text = BILL_HEADER + "\nSample Rent,Personal,,10,10,0,,2026-11-01,2026-11-01,2026-11-01,\n"
        loaded = resolve_bills({BILLS_ENV: text})
        self.assertIsNone(loaded.warning)
        self.assertEqual(loaded.records[0].account_match, "")
        self.assertEqual(loaded.records[0].section, "")
        self.assertEqual(apply_account_balances(loaded.records, None)[0].current_balance, None)

    def test_section_column_is_optional_and_stays_off_the_table(self) -> None:
        omitted = resolve_bills({BILLS_ENV: BILL_HEADER + "\nAmex Gold,Personal,1,2,0,2,5,2026-11-01,2026-11-01,,note\n"})
        self.assertIsNone(omitted.warning)
        self.assertEqual(omitted.records[0].section, "")
        self.assertEqual(bill_section(omitted.records[0]), SECTION_CREDIT)
        self.assertNotIn(SECTION_COLUMN, bill_records(omitted.records)[0])

        swapped = (
            f"{BILL_HEADER},{SECTION_COLUMN},{ACCOUNT_MATCH}\n"
            "Rent Check,Personal,1,2,0,2,5,2026-11-01,2026-11-01,,note,Loans,Sample Card\n"
        )
        loaded = resolve_bills({BILLS_ENV: swapped})
        self.assertIsNone(loaded.warning, loaded.warning)
        bill = loaded.records[0]
        self.assertEqual(bill.section, "Loans")
        self.assertEqual(bill.account_match, "Sample Card")
        self.assertEqual(bill_section(bill), SECTION_OTHER)

    def test_blank_type_is_its_own_filter(self) -> None:
        text = BILL_HEADER + "\nSample, ,1,1,0,1,,2026-11-01,2026-11-01,,\n"
        loaded = resolve_bills({BILLS_ENV: text})
        self.assertEqual(loaded.records[0].bill_type, "")
        self.assertEqual(type_options(loaded.records), [ALL_TYPES, NO_TYPE])
        self.assertEqual(filter_bills(loaded.records, NO_TYPE), loaded.records)

    def test_malformed_csv_warns_and_hides_cells(self) -> None:
        samples = [
            BILL_HEADER + f"\n{HIDDEN},Personal,not-money,1,1,1,5,2026-11-01,2026-11-01,,note\n",
            BILL_HEADER + f"\n{HIDDEN},Personal,1,1,1,1,5,not-a-date,2026-11-01,,note\n",
            f"Description,Type\n{HIDDEN},Personal\n",
            '"unterminated',
            BILL_HEADER + f"\n,Personal,1,1,1,1,5,2026-11-01,2026-11-01,,{HIDDEN}\n",
        ]
        for text in samples:
            loaded = resolve_bills({BILLS_ENV: text})
            self.assertTrue(loaded.configured, text)
            self.assertIsNotNone(loaded.warning)
            self.assertNotIn(HIDDEN, loaded.warning or "")
            self.assertNotIn("not-money", loaded.warning or "")
            self.assertEqual(loaded.records, ())


class FlagAndTotalTests(unittest.TestCase):
    def test_flags_and_filtered_totals(self) -> None:
        bills = _bills()
        by_name = {bill.description: bill for bill in bills}
        self.assertEqual(bill_flag(by_name["Sample Visa"], TODAY), "overdue")
        self.assertEqual(bill_flag(by_name["Sample Rent"], TODAY), "")
        self.assertEqual(bill_flag(by_name["Sample Insurance"], TODAY), "due_soon")
        self.assertEqual(bill_flag(by_name["Sample Auto"], TODAY), "due_soon")
        self.assertEqual(bill_flag(by_name["Sample Cash"], TODAY), "")
        self.assertEqual(bill_flag(by_name["Undated"], TODAY), "")
        due_today = by_name["Sample Rent"]
        due_today = type(due_today)(**{**due_today.__dict__, "due_date": TODAY, "amount_still_due": 1})
        self.assertEqual(bill_flag(due_today, TODAY), "due_soon")

        self.assertEqual(type_options(bills), [ALL_TYPES, "Business", "Other", "Personal", "VWH"])
        personal = filter_bills(bills, "Personal")
        self.assertEqual([bill.description for bill in personal], ["Sample Rent", "Sample Visa", "Sample Cash"])
        totals = _totals(bill_records(personal), BILL_EXPORT)
        self.assertAlmostEqual(totals["Current Balance"], 407)
        self.assertAlmostEqual(totals["Statement Balance"], 1247)
        self.assertAlmostEqual(totals["Amount Paid"], 1215)
        self.assertAlmostEqual(totals["Amount Still Due"], 32)
        everyone = _totals(bill_records(bills), BILL_EXPORT)
        self.assertAlmostEqual(everyone["Current Balance"], 1360)
        self.assertAlmostEqual(everyone["Statement Balance"], 1381)
        self.assertAlmostEqual(everyone["Amount Paid"], 1215)
        self.assertAlmostEqual(everyone["Amount Still Due"], 166)
        self.assertEqual(filter_bills(bills, "Missing"), ())


class OtherTabTests(unittest.TestCase):
    def test_property_tax_totals_keep_file_order(self) -> None:
        loaded = resolve_property_taxes({PROP_TAXES_ENV: TAXES})
        self.assertIsNone(loaded.warning)
        self.assertEqual([row.property_name for row in loaded.records], ["Sample House", "Sample Lot"])
        self.assertAlmostEqual(loaded.records[1].total_amount_due or 0, -50)
        totals = _totals(property_tax_records(loaded.records), PROP_TAX_EXPORT)
        self.assertAlmostEqual(totals["Total Amount Due"], 150)
        self.assertAlmostEqual(totals["1st Half"], 125)
        self.assertAlmostEqual(totals["Amount Paid"], 100)
        self.assertAlmostEqual(totals["Remaining Due"], 150)

    def test_entity_fee_dates_and_fee_totals(self) -> None:
        loaded = resolve_entity_fees({ENTITY_FEES_ENV: FEES})
        self.assertIsNone(loaded.warning)
        first, second = loaded.records
        self.assertEqual(first.reg_agent_due, date(2026, 4, 15))
        self.assertEqual(first.franchise_tax_due, date(2026, 4, 15))
        self.assertEqual(second.reg_agent_due, date(2026, 4, 1))
        self.assertEqual(second.franchise_tax_due, date(2027, 1, 15))
        self.assertEqual(first.paid_from, "Sample Checking")
        self.assertEqual(second.paid_from, "")
        totals = _totals(entity_fee_records(loaded.records), (
            ("LLC", "text"),
            ("Reg Agent Fee", "money"),
            ("Franchise Tax", "money"),
        ))
        self.assertAlmostEqual(totals["Reg Agent Fee"], 200)
        self.assertAlmostEqual(totals["Franchise Tax"], 50)

    def test_bad_property_tax_and_fee_files_hide_cells(self) -> None:
        tax = resolve_property_taxes({PROP_TAXES_ENV: PROP_TAX_HEADER + f"\n{HIDDEN},nope,1,1,1,note\n"})
        fee = resolve_entity_fees({ENTITY_FEES_ENV: "LLC,State\n" + HIDDEN + ",AZ\n"})
        for loaded in (tax, fee):
            self.assertTrue(loaded.configured)
            self.assertIsNotNone(loaded.warning)
            self.assertNotIn(HIDDEN, loaded.warning or "")
            self.assertEqual(loaded.records, ())


class ExportTests(unittest.TestCase):
    def test_csv_follows_the_type_filter_and_adds_a_total(self) -> None:
        personal = filter_bills(_bills(), "Personal")
        text = table_csv(bill_records(personal), BILL_EXPORT)
        self.assertTrue(text.startswith(BILL_HEADER + "\n"))
        self.assertNotIn(ACCOUNT_MATCH, text.splitlines()[0])
        self.assertNotIn(SECTION_COLUMN, text.splitlines()[0])
        self.assertNotIn("Sample Insurance", text)
        self.assertIn("400.00", text)
        rows = list(pd.read_csv(io.StringIO(text)).itertuples(index=False))
        self.assertEqual(rows[-1][0], TOTAL_LABEL)
        self.assertAlmostEqual(rows[-1][2], 407)
        self.assertEqual(rows[0][8], "2026-11-01")

    def test_workbook_has_three_sheets_with_formats(self) -> None:
        bills = _bills()
        taxes = resolve_property_taxes({PROP_TAXES_ENV: TAXES}).records
        fees = resolve_entity_fees({ENTITY_FEES_ENV: FEES}).records
        blob = workbook_bytes(bills, taxes, fees, TODAY)
        book = load_workbook(io.BytesIO(blob))
        self.assertEqual(book.sheetnames, ["Bills", "Property Taxes", "Entity Fees"])

        sheet = book["Bills"]
        self.assertEqual([cell.value for cell in sheet[1]], BILL_COLUMNS)
        self.assertEqual([sheet.cell(row, 1).value for row in range(2, 10)], SORTED_NAMES)
        self.assertEqual(sheet.cell(10, 1).value, TOTAL_LABEL)
        self.assertTrue(sheet.cell(10, 1).font.bold)
        self.assertEqual(sheet.cell(10, 3).value, 1360)
        self.assertEqual(sheet["C3"].number_format, "$#,##0.00")
        self.assertEqual(sheet["C3"].value, 400)
        self.assertIsNone(sheet["C2"].value)
        self.assertIsInstance(sheet["I2"].value, datetime)
        self.assertEqual(sheet["I2"].value.date(), date(2026, 11, 1))
        self.assertEqual(sheet["I2"].number_format, "YYYY-MM-DD")
        self.assertAlmostEqual(sheet["G3"].value, 0.1999)
        self.assertEqual(sheet["G3"].number_format, "0.00%")
        self.assertEqual(_fill(sheet["A3"]), "F4C7C3")
        self.assertEqual(_fill(sheet["A4"]), "FCE8B2")
        self.assertEqual(_fill(sheet["A5"]), "FCE8B2")
        self.assertIsNone(_fill(sheet["A2"]))
        self.assertEqual(_fill(sheet["A10"]), "EEEEEE")
        self.assertEqual(sheet.auto_filter.ref, "A1:K9")

        tax_sheet = book["Property Taxes"]
        self.assertEqual([cell.value for cell in tax_sheet[1]], PROP_TAX_COLUMNS)
        self.assertEqual(tax_sheet["A4"].value, TOTAL_LABEL)
        self.assertEqual(tax_sheet["B4"].value, 150)
        self.assertEqual(tax_sheet["B2"].number_format, "$#,##0.00")
        self.assertEqual(tax_sheet["B3"].value, -50)

        fee_sheet = book["Entity Fees"]
        self.assertEqual([cell.value for cell in fee_sheet[1]], ENTITY_FEE_COLUMNS)
        self.assertEqual(fee_sheet["G2"].value.date(), date(2026, 4, 15))
        self.assertEqual(fee_sheet["G3"].value.date(), date(2026, 4, 1))
        self.assertEqual(fee_sheet["G2"].number_format, "YYYY-MM-DD")
        self.assertEqual(fee_sheet["F4"].value, 200)
        self.assertEqual(fee_sheet["H4"].value, 50)
        self.assertIsNone(fee_sheet["I4"].value)
        self.assertEqual(fee_sheet["A4"].value, TOTAL_LABEL)

    def test_formula_text_stays_text(self) -> None:
        text = BILL_HEADER + "\n=1+1,Personal,1,1,1,1,5,2026-11-01,2026-11-01,,note\n"
        loaded = resolve_bills({BILLS_ENV: text})
        book = load_workbook(io.BytesIO(workbook_bytes(loaded.records, (), (), TODAY)))
        cell = book["Bills"]["A2"]
        self.assertEqual(cell.value, "=1+1")
        self.assertNotEqual(cell.data_type, "f")

    def test_empty_workbook_still_has_three_headers(self) -> None:
        book = load_workbook(io.BytesIO(workbook_bytes((), (), (), TODAY)))
        self.assertEqual(book.sheetnames, ["Bills", "Property Taxes", "Entity Fees"])
        self.assertEqual([cell.value for cell in book["Bills"][1]], BILL_COLUMNS)
        self.assertIsNone(book["Bills"]["A2"].value)


class RenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)

    def test_page_shows_sorted_money_and_the_type_filter(self) -> None:
        script = _page_script(BILLS, TAXES, FEES, BALANCES)
        app = AppTest.from_string(script, default_timeout=30).run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(app.tabs[0].label, "Bills")
        self.assertEqual(app.tabs[1].label, "Property Taxes")
        self.assertEqual(app.tabs[2].label, "Entity Fees")
        self.assertEqual([node.value for node in app.subheader], list(BILL_SECTIONS))
        self.assertEqual(sum(node.value == NONE_YET for node in app.caption), 4)
        frame = app.dataframe[0].value
        self.assertEqual(frame["Description"].tolist(), SORTED_NAMES)
        self.assertNotIn(SECTION_COLUMN, list(frame.columns))
        self.assertTrue(pd.api.types.is_numeric_dtype(frame["Current Balance"]))
        self.assertTrue(pd.api.types.is_numeric_dtype(frame["Interest Rate"]))
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(frame["Due Date"]))
        self.assertEqual(list(frame.columns), BILL_COLUMNS)
        self.assertAlmostEqual(frame.loc[frame["Description"] == "Sample Visa", "Current Balance"].iloc[0], 400)
        subtotal = app.dataframe[1].value
        self.assertEqual(subtotal["Description"].iloc[0], SUBTOTAL_LABEL)
        self.assertAlmostEqual(subtotal["Current Balance"].iloc[0], 1360)
        totals = app.dataframe[2].value
        self.assertEqual(totals["Description"].iloc[0], TOTAL_LABEL)
        self.assertAlmostEqual(totals["Current Balance"].iloc[0], 1360)
        self.assertEqual(_column_frame(app, "Property")["Property"].tolist(), ["Sample House", "Sample Lot"])
        self.assertEqual(_column_frame(app, "LLC")["LLC"].tolist(), ["Sample LLC", "Second LLC"])
        labels = [button.label for button in app.download_button]
        self.assertIn("Export to Excel", labels)
        self.assertEqual(labels.count("Download CSV"), 3)
        rendered = "\n".join(node.value for node in app.caption)
        self.assertIn(FLAG_NOTE, rendered)
        self.assertIn(ACCOUNT_MATCH_NOTE, rendered)
        self.assertIn('"format": "dollar"', app.dataframe[0].proto.columns)
        self.assertIn("rgba(198, 40, 40, 0.22)", str(app.dataframe[0].proto))
        _assert_bold_columns(self, app.dataframe[0].proto, BILL_COLUMNS)
        _assert_bold_columns(self, app.dataframe[1].proto, BILL_COLUMNS)
        _assert_bold_columns(self, app.dataframe[2].proto, BILL_COLUMNS)
        tax = next(node for node in app.dataframe if "Property" in node.value.columns)
        fee = next(node for node in app.dataframe if "LLC" in node.value.columns)
        self.assertNotIn("font-weight", str(tax.proto))
        self.assertNotIn("font-weight", str(fee.proto))

        app.selectbox[0].set_value("Business").run()
        self.assertEqual(list(app.exception), [])
        shown = app.dataframe[0].value
        self.assertEqual(shown["Description"].tolist(), ["Sample Insurance", "Sample Auto"])
        self.assertEqual(app.dataframe[1].value["Description"].iloc[0], SUBTOTAL_LABEL)
        self.assertAlmostEqual(app.dataframe[1].value["Amount Still Due"].iloc[0], 81)
        self.assertEqual(app.dataframe[2].value["Description"].iloc[0], TOTAL_LABEL)
        self.assertAlmostEqual(app.dataframe[2].value["Amount Still Due"].iloc[0], 81)
        self.assertEqual(_column_frame(app, "Property")["Property"].tolist(), ["Sample House", "Sample Lot"])

    def test_missing_env_is_an_empty_state(self) -> None:
        script = """
import os
for key in (
    "FINANCE_BILLS_CSV",
    "FINANCE_PROP_TAXES_CSV",
    "FINANCE_ENTITY_FEES_CSV",
    "FINANCE_BALANCES_CSV",
):
    os.environ.pop(key, None)
from kirbancials.bills import render_bills
render_bills()
"""
        app = AppTest.from_string(script, default_timeout=30).run()
        self.assertEqual(list(app.exception), [])
        messages = [node.value for node in app.info]
        self.assertIn(NO_BILLS, messages)
        self.assertIn(NO_PROPERTY_TAXES, messages)
        self.assertIn(NO_ENTITY_FEES, messages)
        self.assertEqual(list(app.dataframe), [])
        self.assertEqual([button.label for button in app.download_button], ["Export to Excel"])
        self.assertNotIn(HIDDEN, "\n".join(messages))

    def test_bad_bill_file_warns_without_the_cell(self) -> None:
        script = _page_script(BILL_HEADER + f"\n{HIDDEN},Personal,not-money,1,1,1,5,2026-11-01,2026-11-01,,note\n", "", "", "")
        app = AppTest.from_string(script, default_timeout=30).run()
        self.assertEqual(list(app.exception), [])
        warnings = [node.value for node in app.warning]
        self.assertTrue(warnings)
        self.assertNotIn(HIDDEN, "\n".join(warnings))
        self.assertNotIn(NO_BILLS_FOR_TYPE, warnings)
        self.assertEqual(list(app.dataframe), [])

    def test_unreadable_balances_do_not_block_the_page(self) -> None:
        script = _page_script(BILLS, TAXES, FEES, '"unterminated')
        app = AppTest.from_string(script, default_timeout=30).run()
        self.assertEqual(list(app.exception), [])
        frame = app.dataframe[0].value
        visa = frame.loc[frame["Description"] == "Sample Visa", "Current Balance"].iloc[0]
        self.assertAlmostEqual(visa, 10)

    def test_sections_subtotal_and_keep_the_other_tabs(self) -> None:
        script = _page_script(SECTIONED, TAXES, FEES, "")
        app = AppTest.from_string(script, default_timeout=30).run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual([node.value for node in app.subheader], list(BILL_SECTIONS))
        self.assertNotIn(NONE_YET, [node.value for node in app.caption])
        self.assertEqual(app.tabs[1].label, "Property Taxes")
        self.assertEqual(app.tabs[2].label, "Entity Fees")
        descriptions = [
            node.value["Description"].tolist()
            for node in app.dataframe
            if "Description" in node.value.columns and node.value["Description"].iloc[0] != SUBTOTAL_LABEL
            and node.value["Description"].iloc[0] != TOTAL_LABEL
        ]
        self.assertEqual(
            descriptions,
            [
                ["Citi Double Cash", "Amex Gold"],
                ["Sample LOC"],
                ["BOA Auto Loan", "Jeep Loan"],
                ["County Property Tax", "Sample Property Tax"],
                ["HOA Dues", "Amex Override", "Rent Check", "Board Fee", "Local Services"],
            ],
        )
        self.assertNotIn(SECTION_COLUMN, list(app.dataframe[0].value.columns))
        self.assertEqual(app.dataframe[1].value["Description"].iloc[0], SUBTOTAL_LABEL)
        self.assertAlmostEqual(app.dataframe[1].value["Statement Balance"].iloc[0], 70)
        self.assertAlmostEqual(app.dataframe[1].value["Amount Still Due"].iloc[0], 55)
        labels = [
            node.value["Description"].iloc[0]
            for node in app.dataframe
            if "Description" in node.value.columns
        ]
        self.assertEqual(labels.count(SUBTOTAL_LABEL), 5)
        self.assertEqual(labels.count(TOTAL_LABEL), 1)
        grand = app.dataframe[10].value
        self.assertEqual(grand["Description"].iloc[0], TOTAL_LABEL)
        self.assertAlmostEqual(grand["Statement Balance"].iloc[0], 311)
        self.assertAlmostEqual(grand["Amount Still Due"].iloc[0], 236)
        self.assertAlmostEqual(grand["Amount Paid"].iloc[0], 75)
        self.assertIn("rgba(198, 40, 40, 0.22)", str(app.dataframe[0].proto))
        paid = app.dataframe[6].value
        self.assertEqual(paid["Description"].iloc[0], "County Property Tax")
        self.assertEqual(paid["Paid Date"].iloc[0].date(), date(2026, 11, 2))
        self.assertEqual(_column_frame(app, "Property")["Property"].tolist(), ["Sample House", "Sample Lot"])
        self.assertEqual(_column_frame(app, "LLC")["LLC"].iloc[0], "Sample LLC")
        self.assertEqual(_column_frame(app, "Reg Agent Fee")["Reg Agent Fee"].sum(), 200)

        app.selectbox[0].set_value("Personal").run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(sum(node.value == NONE_YET for node in app.caption), 2)
        personal = [
            node.value["Description"].tolist()
            for node in app.dataframe
            if "Description" in node.value.columns
            and node.value["Description"].iloc[0] not in {SUBTOTAL_LABEL, TOTAL_LABEL}
        ]
        self.assertEqual(
            personal,
            [
                ["Citi Double Cash", "Amex Gold"],
                ["Jeep Loan"],
                ["Amex Override", "Rent Check"],
            ],
        )
        self.assertAlmostEqual(app.dataframe[1].value["Amount Still Due"].iloc[0], 55)
        grand_rows = [
            node.value
            for node in app.dataframe
            if "Description" in node.value.columns and node.value["Description"].iloc[0] == TOTAL_LABEL
        ]
        self.assertAlmostEqual(grand_rows[-1]["Amount Still Due"].iloc[0], 109)


class SectionTests(unittest.TestCase):
    def test_inference_patterns(self) -> None:
        cases = {
            "Amex Gold": SECTION_CREDIT,
            "personal AMEX": SECTION_CREDIT,
            "Citi Double Cash": SECTION_CREDIT,
            "Citibank AAdvantage": SECTION_CREDIT,
            "City Utilities": SECTION_OTHER,
            "BOA Custom Cash": SECTION_CREDIT,
            "Board Fee": SECTION_OTHER,
            "Bank of America Visa": SECTION_CREDIT,
            "Capital One Venture": SECTION_CREDIT,
            "Nordstrom card": SECTION_CREDIT,
            "Neiman Marcus": SECTION_CREDIT,
            "Marriott Bonvoy": SECTION_CREDIT,
            "Auto Loan": SECTION_CAR,
            "Jeep Loan": SECTION_CAR,
            "jeep loans": SECTION_CAR,
            "Sample Auto": SECTION_OTHER,
            "BOA Auto Loan": SECTION_CAR,
            "Capital One Auto Loan": SECTION_CAR,
            "Line of Credit": SECTION_LOC,
            "LOC": SECTION_LOC,
            "loc payment": SECTION_LOC,
            "(LOC)": SECTION_LOC,
            "Local Services": SECTION_OTHER,
            "HELOC": SECTION_OTHER,
            "Property Tax": SECTION_TAX,
            "Property Taxes": SECTION_TAX,
            "County property tax bill": SECTION_TAX,
            "Nordstrom Property Tax": SECTION_TAX,
            "Sample Rent": SECTION_OTHER,
            "Sample Insurance": SECTION_OTHER,
            "Sample Visa": SECTION_OTHER,
        }
        for description, section in cases.items():
            self.assertEqual(infer_section(description), section, description)

    def test_explicit_section_wins_over_the_description(self) -> None:
        self.assertEqual(canonical_section("  credit cards "), SECTION_CREDIT)
        self.assertEqual(canonical_section("LINES OF CREDIT"), SECTION_LOC)
        self.assertEqual(canonical_section("Car  Payments"), SECTION_CAR)
        self.assertIsNone(canonical_section(""))
        self.assertIsNone(canonical_section("Loans"))
        self.assertIsNone(canonical_section("Credit Card"))

        forced = _bill("Amex Gold", section="Other", due_date=date(2026, 11, 6))
        blank = _bill("Amex Gold", section="  ", due_date=date(2026, 11, 2))
        unknown = _bill("Rent Check", section="Loans", due_date=date(2026, 11, 7))
        self.assertEqual(bill_section(forced), SECTION_OTHER)
        self.assertEqual(bill_section(blank), SECTION_CREDIT)
        self.assertEqual(bill_section(unknown), SECTION_OTHER)
        groups = dict(section_groups([forced, blank, unknown, _bill("Sample LOC", due_date=None)]))
        self.assertEqual([name for name, _rows in section_groups([forced])], list(BILL_SECTIONS))
        self.assertEqual([bill.description for bill in groups[SECTION_CREDIT]], ["Amex Gold"])
        self.assertEqual([bill.description for bill in groups[SECTION_OTHER]], ["Amex Gold", "Rent Check"])
        self.assertEqual([bill.description for bill in groups[SECTION_LOC]], ["Sample LOC"])
        self.assertEqual(groups[SECTION_CAR], ())
        self.assertEqual(groups[SECTION_TAX], ())

    def test_rows_group_and_subtotal_by_section(self) -> None:
        loaded = resolve_bills({BILLS_ENV: SECTIONED})
        self.assertIsNone(loaded.warning, loaded.warning)
        groups = dict(section_groups(loaded.records))
        self.assertEqual(
            [bill.description for bill in groups[SECTION_CREDIT]],
            ["Citi Double Cash", "Amex Gold"],
        )
        self.assertEqual([bill.description for bill in groups[SECTION_LOC]], ["Sample LOC"])
        self.assertEqual(
            [bill.description for bill in groups[SECTION_CAR]],
            ["BOA Auto Loan", "Jeep Loan"],
        )
        self.assertEqual(
            [bill.description for bill in groups[SECTION_TAX]],
            ["County Property Tax", "Sample Property Tax"],
        )
        self.assertEqual(
            [bill.description for bill in groups[SECTION_OTHER]],
            ["HOA Dues", "Amex Override", "Rent Check", "Board Fee", "Local Services"],
        )
        self.assertEqual(groups[SECTION_TAX][0].paid_date, date(2026, 11, 2))
        self.assertEqual(bill_flag(groups[SECTION_TAX][0], TODAY), "")
        self.assertEqual(bill_flag(groups[SECTION_CREDIT][1], TODAY), "overdue")
        self.assertEqual(loaded.records[0].section, "")
        self.assertEqual(groups[SECTION_OTHER][1].section, "Other")
        self.assertEqual(groups[SECTION_OTHER][2].section, "Loans")

        credit = _totals(bill_records(groups[SECTION_CREDIT]), BILL_EXPORT)
        self.assertAlmostEqual(credit["Statement Balance"], 70)
        self.assertAlmostEqual(credit["Amount Still Due"], 55)
        self.assertEqual(credit["Description"], TOTAL_LABEL)
        other = _totals(bill_records(groups[SECTION_OTHER]), BILL_EXPORT)
        self.assertAlmostEqual(other["Statement Balance"], 65)
        self.assertAlmostEqual(other["Amount Still Due"], 40)
        everyone = _totals(bill_records(sort_bills(loaded.records)), BILL_EXPORT)
        self.assertAlmostEqual(everyone["Current Balance"], 379)
        self.assertAlmostEqual(everyone["Statement Balance"], 311)
        self.assertAlmostEqual(everyone["Amount Paid"], 75)
        self.assertAlmostEqual(everyone["Amount Still Due"], 236)
        text = table_csv(bill_records(loaded.records), BILL_EXPORT)
        self.assertNotIn(SECTION_COLUMN, text.splitlines()[0])
        self.assertNotIn(ACCOUNT_MATCH, text.splitlines()[0])

    def test_a_file_without_section_still_sorts_inside_other(self) -> None:
        groups = dict(section_groups(_bills()))
        self.assertEqual([bill.description for bill in groups[SECTION_OTHER]], SORTED_NAMES)
        for name in (SECTION_CREDIT, SECTION_LOC, SECTION_CAR, SECTION_TAX):
            self.assertEqual(groups[name], ())


class NavigationTests(unittest.TestCase):
    def test_bills_is_its_own_page_and_login_covers_it(self) -> None:
        text = (ROOT / "app.py").read_text()
        pages = [line for line in text.splitlines() if "st.Page(" in line]
        self.assertIn("balance_sheet.py", pages[1])
        self.assertIn("bills.py", pages[2])
        self.assertIn('title="Bills"', pages[2])
        self.assertIn('url_path="bills"', pages[2])
        self.assertNotIn("default=True", pages[2])
        sidebar = text.split("with st.sidebar:", 1)[1]
        links = [line for line in sidebar.splitlines() if "st.page_link" in line]
        self.assertIn("bills.py", links[2])
        self.assertIn('label="Bills"', links[2])
        self.assertLess(text.index("require_login()"), text.index("navigation.run()"))
        view = (ROOT / "views" / "bills.py").read_text()
        self.assertIn('render_page_title("Bills", legend=BILL_LEGEND)', view)
        self.assertIn("render_bills", view)
        self.assertNotIn("require_login", view)
        self.assertNotIn("APP_PASSWORD", view)
        self.assertNotIn("<style>", view)

    def test_color_legend_is_the_title_line_and_only_real_colors(self) -> None:
        self.assertEqual(
            BILL_LEGEND,
            (
                (OVERDUE_STYLE, "Past due, still owed"),
                (DUE_SOON_STYLE, "Due within 7 days"),
            ),
        )
        source = (ROOT / "kirbancials" / "bills.py").read_text()
        self.assertEqual(source.count("row_styles="), 1)
        script = """
from kirbancials.bills import BILL_LEGEND
from kirbancials.page_title import render_page_title

render_page_title("Bills", legend=BILL_LEGEND)
"""
        app = AppTest.from_string(script, default_timeout=30).run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(len(list(app.html)), 1)
        body = app.html[0].value
        self.assertIn('class="page-heading"', body)
        self.assertIn('class="page-title">Bills</p>', body)
        self.assertIn('class="page-legend"', body)
        self.assertLess(body.index('class="page-title"'), body.index('class="page-legend"'))
        self.assertIn("Past due, still owed", body)
        self.assertIn("Due within 7 days", body)
        self.assertIn(OVERDUE_STYLE, body)
        self.assertIn(DUE_SOON_STYLE, body)
        self.assertIn("flex-wrap: nowrap", body)
        self.assertNotIn("Paid", body)
        plain = AppTest.from_string(
            'from kirbancials.page_title import render_page_title\nrender_page_title("Profit & Loss")\n',
            default_timeout=30,
        ).run()
        self.assertNotIn("page-legend", plain.html[0].value)
        self.assertNotIn("page-heading", plain.html[0].value)


class MoneyTableTests(unittest.TestCase):
    def test_row_styles_keep_numeric_columns(self) -> None:
        script = """
import pandas as pd
from kirbancials.formatting import show_money_table

frame = pd.DataFrame(
    {
        "Description": ["Sample due", "Sample later"],
        "Amount": [25.0, 5.0],
        "Due Date": pd.to_datetime(["2026-11-10", "2026-12-01"]),
    }
)
show_money_table(
    frame,
    ["Amount"],
    row_styles=["background-color: rgba(198, 40, 40, 0.22)", ""],
)
"""
        app = AppTest.from_string(script, default_timeout=30).run()
        self.assertEqual(list(app.exception), [])
        shown = app.dataframe[0].value
        self.assertTrue(pd.api.types.is_numeric_dtype(shown["Amount"]))
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(shown["Due Date"]))
        self.assertIn("rgba(198, 40, 40, 0.22)", str(app.dataframe[0].proto))
        # The default path, with no row styles, is what the other pages use.
        self.assertTrue(callable(show_money_table))

    def test_bold_columns_keep_the_row_color_and_the_value_types(self) -> None:
        script = """
import pandas as pd
from kirbancials.formatting import show_money_table

frame = pd.DataFrame(
    {
        "Description": ["Sample due", "Sample later"],
        "Amount Still Due": [25.0, 5.0],
        "Due Date": pd.to_datetime(["2026-11-10", "2026-12-01"]),
        "Notes": ["owed", "later"],
    }
)
show_money_table(
    frame,
    ["Amount Still Due"],
    row_styles=["background-color: rgba(198, 40, 40, 0.22)", ""],
    bold_columns=["Amount Still Due", "Due Date", "Missing"],
)
"""
        app = AppTest.from_string(script, default_timeout=30).run()
        self.assertEqual(list(app.exception), [])
        shown = app.dataframe[0].value
        self.assertTrue(pd.api.types.is_numeric_dtype(shown["Amount Still Due"]))
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(shown["Due Date"]))
        self.assertIn("rgba(198, 40, 40, 0.22)", str(app.dataframe[0].proto))
        _assert_bold_columns(self, app.dataframe[0].proto, list(shown.columns))


def _assert_bold_columns(test, proto, columns) -> None:
    import re

    match = re.search(r'styles: "(.*?)"', str(proto), re.DOTALL)
    test.assertIsNotNone(match)
    styles = match.group(1)
    bold_indexes = {columns.index("Amount Still Due"), columns.index("Due Date")}
    seen = set()
    for rule in styles.split("}"):
        if "font-weight: 700" not in rule:
            continue
        selectors = rule.split("{", 1)[0]
        for piece in selectors.split(","):
            column = int(piece.strip().rsplit("_col", 1)[1])
            test.assertIn(column, bold_indexes)
            seen.add(column)
    test.assertEqual(seen, bold_indexes)


def _totals(records, columns):
    from kirbancials.bills import totals_record

    return totals_record(records, columns, columns[0][0])


def _bill(description: str, section: str = "", due_date: date | None = None) -> BillRecord:
    return BillRecord(
        description=description,
        bill_type="Personal",
        current_balance=None,
        statement_balance=None,
        amount_paid=None,
        amount_still_due=1,
        interest_rate=None,
        statement_ending=None,
        due_date=due_date,
        paid_date=None,
        notes="",
        section=section,
    )


def _column_frame(app, column: str):
    for node in app.dataframe:
        if column in node.value.columns:
            return node.value
    raise AssertionError(column)


def _fill(cell) -> str | None:
    color = getattr(cell.fill.fgColor, "rgb", None)
    if not color or cell.fill.patternType is None:
        return None
    return str(color)[-6:].upper()


def _page_script(bills: str, taxes: str, fees: str, balances: str) -> str:
    import json

    return f"""
import os
os.environ["FINANCE_BILLS_CSV"] = {json.dumps(bills)}
os.environ["FINANCE_PROP_TAXES_CSV"] = {json.dumps(taxes)}
os.environ["FINANCE_ENTITY_FEES_CSV"] = {json.dumps(fees)}
os.environ["FINANCE_BALANCES_CSV"] = {json.dumps(balances)}
from kirbancials.bills import render_bills
render_bills()
"""
