"""Vehicles page. Fixtures are fake round numbers, not account exports."""

from __future__ import annotations

import csv
import io
import os
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from kirbancials.activity import load_balances, resolve_balances
from kirbancials.assets import ASSET_COLUMNS, ASSET_HEADER, ASSETS_ENV, NO_ASSETS_NOTE, resolve_assets
from kirbancials.formatting import MISSING
from kirbancials.vehicles import (
    NO_VEHICLES,
    USAGE_NOTE,
    VEHICLES_CATEGORY,
    VEHICLES_LINK_CAPTION,
    VEHICLES_PAGE,
    build_vehicle_rows,
    format_usage,
    loan_balance_for_mask,
    parse_vehicle_name,
    parse_vehicle_notes,
    vehicle_frame,
    vehicle_total,
)

ROOT = Path(__file__).resolve().parents[1]

BALANCES = """Account,As Of,Current Balance,Available Credit,Credit Limit,Account Type
Wells Fargo Auto Loan ending 7635,2026-11-01T00:00:00Z,1000,,,loan
Wells Fargo Auto Loan ending 7635,2026-11-12T00:00:00Z,6400,,,loan
Wells Fargo Auto Loan ending 8492,2026-11-12T00:00:00Z,1800,,,loan
Truist Auto Loan ending 1001,2026-11-12T00:00:00Z,2100,,,
Stellantis Jeep Loan ending 0001,2026-11-12T00:00:00Z,900,,,loan
Sample card ending 7635,2026-11-12T00:00:00Z,9999,100,500,credit
Sample Checking,2026-11-12T00:00:00Z,500,,,checking
"""

HAULER_NOTES = "42,300 miles (owner, Nov 22, 2026); VIN AB12CD; Loan: Wells Fargo ending 7635"
WAGON_NOTES = "~17,500 miles estimate; Loan: Truist ending 1001"
BOAT_NOTES = "120 hours"
SKIFF_NOTES = "kept in the sample garage"


def _csv(rows: list[dict[str, str]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=ASSET_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _asset(**overrides: str) -> dict[str, str]:
    row = {column: "" for column in ASSET_COLUMNS}
    row.update(overrides)
    return row


def _balances(text: str = BALANCES) -> pd.DataFrame:
    origin = resolve_balances({"FINANCE_BALANCES_CSV": text})
    assert origin is not None
    return load_balances(origin).rows


def _sample_rows() -> list[dict[str, str]]:
    return [
        _asset(
            **{
                "Asset ID": "shop-1",
                "Asset Name": "Sample Shop",
                "Category": "Real Estate",
                "Market Value": "100000",
                "Valuation Date": "2026-06-01",
            }
        ),
        _asset(
            **{
                "Asset ID": "hauler",
                "Asset Name": "2021 Sample Hauler EX",
                "Category": "Vehicles",
                "Market Value": "18000",
                "Valuation Date": "2026-11-01",
                "Notes": HAULER_NOTES,
            }
        ),
        _asset(
            **{
                "Asset ID": "wagon",
                "Asset Name": "2019 Sample Wagon LX",
                "Category": "Vehicles",
                "Market Value": "9500",
                "Valuation Date": "2026-10-01",
                "Notes": WAGON_NOTES,
            }
        ),
        _asset(
            **{
                "Asset ID": "boat",
                "Asset Name": "2016 Sample Whaler 170",
                "Category": "Vehicles",
                "Market Value": "22000",
                "Valuation Date": "2026-08-01",
                "Notes": BOAT_NOTES,
            }
        ),
        _asset(
            **{
                "Asset ID": "skiff",
                "Asset Name": "Sample Skiff",
                "Category": "Vehicles",
                "Market Value": "4000",
                "Valuation Date": "2026-01-15",
                "Notes": SKIFF_NOTES,
            }
        ),
        _asset(
            **{
                "Asset ID": "blank",
                "Asset Name": "2022 Sample Mystery",
                "Category": "vehicles",
                "Market Value": "1",
                "Notes": "42,300 miles",
            }
        ),
    ]


class NameParserTests(unittest.TestCase):
    def test_year_make_model_trim(self) -> None:
        parsed = parse_vehicle_name("2021 Sample Hauler EX")
        self.assertTrue(parsed.parsed)
        self.assertEqual((parsed.year, parsed.make, parsed.model_trim), ("2021", "Sample", "Hauler EX"))

    def test_two_word_make_stays_together(self) -> None:
        parsed = parse_vehicle_name("  2019   Land Rover   Range Rover HSE  ")
        self.assertTrue(parsed.parsed)
        self.assertEqual(parsed.year, "2019")
        self.assertEqual(parsed.make, "Land Rover")
        self.assertEqual(parsed.model_trim, "Range Rover HSE")
        boat = parse_vehicle_name("2016 Boston Whaler 170 Montauk")
        self.assertEqual((boat.make, boat.model_trim), ("Boston Whaler", "170 Montauk"))

    def test_year_and_make_without_a_model(self) -> None:
        parsed = parse_vehicle_name("2020 Sample")
        self.assertTrue(parsed.parsed)
        self.assertEqual(parsed.model_trim, MISSING)

    def test_name_without_a_year_keeps_the_raw_text(self) -> None:
        parsed = parse_vehicle_name("Sample Skiff")
        self.assertFalse(parsed.parsed)
        self.assertEqual(parsed.year, MISSING)
        self.assertEqual(parsed.make, MISSING)
        self.assertEqual(parsed.model_trim, "Sample Skiff")

    def test_blank_name(self) -> None:
        parsed = parse_vehicle_name("   ")
        self.assertFalse(parsed.parsed)
        self.assertEqual(parsed.model_trim, MISSING)


class NotesParserTests(unittest.TestCase):
    def test_miles_with_an_owner_parenthetical(self) -> None:
        parsed = parse_vehicle_notes("42,300 miles (owner, Nov 22, 2026)")
        self.assertFalse(parsed.show_raw)
        self.assertAlmostEqual(parsed.miles or 0, 42300)
        self.assertFalse(parsed.miles_tilde)
        self.assertFalse(parsed.miles_word)
        self.assertEqual(format_usage(parsed), "42,300")
        self.assertIsNone(parsed.hours)

    def test_tilde_and_estimate_mark_the_miles(self) -> None:
        parsed = parse_vehicle_notes("~17,500 miles estimate")
        self.assertFalse(parsed.show_raw)
        self.assertAlmostEqual(parsed.miles or 0, 17500)
        self.assertTrue(parsed.miles_tilde)
        self.assertTrue(parsed.miles_word)
        self.assertEqual(format_usage(parsed), "~17,500 est.")

    def test_estimate_words_without_a_tilde(self) -> None:
        about = parse_vehicle_notes("about 17,500 miles")
        self.assertEqual(format_usage(about), "17,500 est.")
        approx = parse_vehicle_notes("approx. 80 miles")
        self.assertEqual(format_usage(approx), "80 est.")
        paren = parse_vehicle_notes("10 miles (estimate)")
        self.assertEqual(format_usage(paren), "10 est.")
        self.assertFalse(paren.show_raw)

    def test_hours_for_a_boat_and_an_estimate(self) -> None:
        hours = parse_vehicle_notes("120 hours")
        self.assertFalse(hours.show_raw)
        self.assertIsNone(hours.miles)
        self.assertAlmostEqual(hours.hours or 0, 120)
        self.assertEqual(format_usage(hours), "120 hours")
        engine = parse_vehicle_notes("~80 engine hours estimate")
        self.assertEqual(format_usage(engine), "~80 hours est.")
        labeled = parse_vehicle_notes("engine hours: 40")
        self.assertEqual(format_usage(labeled), "40 hours")
        self.assertFalse(labeled.show_raw)

    def test_miles_win_when_hours_are_also_present(self) -> None:
        parsed = parse_vehicle_notes("42,300 miles; 12 hours")
        self.assertEqual(format_usage(parsed), "42,300")
        self.assertAlmostEqual(parsed.hours or 0, 12)
        self.assertFalse(parsed.show_raw)

    def test_vin_last_six(self) -> None:
        short = parse_vehicle_notes("VIN ab12cd")
        self.assertEqual(short.vin, "AB12CD")
        self.assertFalse(short.show_raw)
        labeled = parse_vehicle_notes("VIN last 6: XY98ZW")
        self.assertEqual(labeled.vin, "XY98ZW")
        self.assertFalse(labeled.show_raw)

    def test_loan_line_is_lender_ending_four_digits(self) -> None:
        expected = (
            ("Loan: Wells Fargo ending 7635", "Wells Fargo ending 7635", "7635"),
            ("Loan: Wells Fargo ending 8492", "Wells Fargo ending 8492", "8492"),
            ("Loan: Truist ending 1001", "Truist ending 1001", "1001"),
            ("Loan: Stellantis ending 0001", "Stellantis ending 0001", "0001"),
        )
        for text, loan, mask in expected:
            parsed = parse_vehicle_notes(text)
            self.assertFalse(parsed.show_raw, text)
            self.assertFalse(parsed.loan_unparsed, text)
            self.assertEqual(parsed.loan, loan, text)
            self.assertEqual(parsed.loan_mask, mask, text)
            self.assertIsNone(parsed.miles)

    def test_a_note_without_a_loan_line_is_not_a_loan(self) -> None:
        parsed = parse_vehicle_notes("120 hours")
        self.assertEqual(parsed.loan, "")
        self.assertEqual(parsed.loan_mask, "")
        self.assertFalse(parsed.loan_unparsed)
        self.assertFalse(parsed.show_raw)

    def test_combined_note_from_the_asset_file(self) -> None:
        parsed = parse_vehicle_notes(HAULER_NOTES)
        self.assertFalse(parsed.show_raw)
        self.assertAlmostEqual(parsed.miles or 0, 42300)
        self.assertEqual(parsed.vin, "AB12CD")
        self.assertEqual(parsed.loan, "Wells Fargo ending 7635")
        self.assertEqual(parsed.loan_mask, "7635")
        self.assertEqual(format_usage(parsed), "42,300")

    def test_unparsed_note_is_shown_raw(self) -> None:
        parsed = parse_vehicle_notes("kept in the sample garage")
        self.assertTrue(parsed.show_raw)
        self.assertEqual(parsed.raw, "kept in the sample garage")
        self.assertIsNone(parsed.miles)
        self.assertEqual(parsed.loan, "")
        self.assertEqual(format_usage(parsed), MISSING)

    def test_partial_note_keeps_the_miles_and_the_raw_text(self) -> None:
        parsed = parse_vehicle_notes("42,300 miles and a ding on the bumper")
        self.assertTrue(parsed.show_raw)
        self.assertAlmostEqual(parsed.miles or 0, 42300)
        self.assertEqual(format_usage(parsed), "42,300")
        self.assertIn("ding", parsed.raw)

    def test_two_mile_readings_are_not_treated_as_clean(self) -> None:
        parsed = parse_vehicle_notes("10 miles; 12 miles")
        self.assertTrue(parsed.show_raw)
        self.assertAlmostEqual(parsed.miles or 0, 10)

    def test_blank_notes(self) -> None:
        parsed = parse_vehicle_notes("  ")
        self.assertFalse(parsed.show_raw)
        self.assertEqual(format_usage(parsed), MISSING)
        self.assertEqual(parsed.raw, "")

    def test_two_masks_are_not_guessed(self) -> None:
        parsed = parse_vehicle_notes("Loan: Sample ending 1111 ending 2222")
        self.assertTrue(parsed.show_raw)
        self.assertTrue(parsed.loan_unparsed)
        self.assertEqual(parsed.loan, "")
        self.assertEqual(parsed.loan_mask, "")

    def test_a_loan_line_without_four_digits_stays_raw(self) -> None:
        parsed = parse_vehicle_notes("Loan: paid off")
        self.assertTrue(parsed.show_raw)
        self.assertTrue(parsed.loan_unparsed)
        self.assertEqual(parsed.loan, "")
        self.assertEqual(parsed.loan_mask, "")


class LoanBalanceTests(unittest.TestCase):
    def test_mask_matches_the_loan_and_ignores_a_card_with_the_same_digits(self) -> None:
        balances = _balances()
        expected = {
            "Loan: Wells Fargo ending 7635": 6400,
            "Loan: Wells Fargo ending 8492": 1800,
            "Loan: Truist ending 1001": 2100,
            "Loan: Stellantis ending 0001": 900,
        }
        for text, amount in expected.items():
            parsed = parse_vehicle_notes(text)
            found = loan_balance_for_mask(parsed.loan_mask, parsed.loan, balances)
            self.assertAlmostEqual(found or 0, amount, msg=text)
        card = loan_balance_for_mask("7635", "Wells Fargo ending 7635", balances)
        self.assertAlmostEqual(card or 0, 6400)
        self.assertNotAlmostEqual(card or 0, 9999)

    def test_contradictory_lender_does_not_match(self) -> None:
        balances = _balances()
        amount = loan_balance_for_mask("7635", "Truist ending 7635", balances)
        self.assertIsNone(amount)

    def test_ambiguous_mask_is_left_blank(self) -> None:
        text = """Account,As Of,Current Balance,Account Type
Alpha Motors Loan ending 4242,2026-11-12T00:00:00Z,100,loan
Beta Motors Loan ending 4242,2026-11-12T00:00:00Z,300,loan
"""
        balances = _balances(text)
        self.assertIsNone(loan_balance_for_mask("4242", "Motors ending 4242", balances))
        self.assertAlmostEqual(
            loan_balance_for_mask("4242", "Alpha Motors ending 4242", balances) or 0,
            100,
        )

    def test_blank_mask_and_missing_balances(self) -> None:
        self.assertIsNone(loan_balance_for_mask("", "Wells Fargo", _balances()))
        self.assertIsNone(loan_balance_for_mask("7635", "Wells Fargo ending 7635", pd.DataFrame()))


class VehicleTableTests(unittest.TestCase):
    def setUp(self) -> None:
        loaded = resolve_assets({ASSETS_ENV: _csv(_sample_rows())})
        self.assertIsNone(loaded.warning)
        self.rows = build_vehicle_rows(loaded.assets, _balances())

    def test_only_the_vehicles_category_and_value_order(self) -> None:
        self.assertEqual(
            [row.model_trim for row in self.rows],
            ["Whaler 170", "Hauler EX", "Wagon LX", "Sample Skiff"],
        )
        self.assertEqual([row.market_value for row in self.rows], [22000.0, 18000.0, 9500.0, 4000.0])
        self.assertAlmostEqual(vehicle_total(self.rows), 53500)
        self.assertNotIn("Sample Shop", [row.model_trim for row in self.rows])

    def test_miles_loan_balance_and_equity(self) -> None:
        boat, hauler, wagon, skiff = self.rows
        self.assertEqual(boat.miles, "120 hours")
        self.assertEqual(boat.loan, "")
        self.assertIsNone(boat.loan_balance)
        self.assertAlmostEqual(boat.equity or 0, 22000)
        self.assertEqual(boat.year, "2016")
        self.assertEqual(boat.make, "Sample")

        self.assertEqual(hauler.miles, "42,300")
        self.assertEqual(hauler.vin, "AB12CD")
        self.assertEqual(hauler.loan, "Wells Fargo ending 7635")
        self.assertAlmostEqual(hauler.loan_balance or 0, 6400)
        self.assertAlmostEqual(hauler.equity or 0, 11600)
        self.assertEqual(hauler.valuation_date, date(2026, 11, 1))
        self.assertFalse(hauler.show_raw)

        self.assertEqual(wagon.miles, "~17,500 est.")
        self.assertAlmostEqual(wagon.loan_balance or 0, 2100)
        self.assertAlmostEqual(wagon.equity or 0, 7400)

        self.assertEqual(skiff.miles, MISSING)
        self.assertEqual(skiff.year, MISSING)
        self.assertEqual(skiff.model_trim, "Sample Skiff")
        self.assertTrue(skiff.show_raw)
        self.assertEqual(skiff.notes, SKIFF_NOTES)
        self.assertAlmostEqual(skiff.equity or 0, 4000)

    def test_frame_columns_money_and_the_raw_note(self) -> None:
        frame = vehicle_frame(self.rows)
        self.assertEqual(
            list(frame.columns),
            [
                "Year",
                "Make",
                "Model/Trim",
                "VIN",
                "Miles",
                "Est. Value",
                "Loan",
                "Loan Balance",
                "Equity",
                "Valuation Date",
                "Notes",
            ],
        )
        self.assertTrue(pd.api.types.is_numeric_dtype(frame["Est. Value"]))
        self.assertTrue(pd.api.types.is_numeric_dtype(frame["Loan Balance"]))
        self.assertTrue(pd.api.types.is_numeric_dtype(frame["Equity"]))
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(frame["Valuation Date"]))
        self.assertEqual(frame["Miles"].tolist(), ["120 hours", "42,300", "~17,500 est.", MISSING])
        self.assertEqual(frame["Notes"].iloc[-1], SKIFF_NOTES)
        self.assertEqual(frame["Notes"].iloc[0], MISSING)
        self.assertTrue(pd.isna(frame["Loan Balance"].iloc[0]))
        self.assertAlmostEqual(frame["Loan Balance"].iloc[1], 6400)

    def test_vin_and_notes_columns_stay_off_when_unused(self) -> None:
        text = (
            ASSET_HEADER
            + "\nboat,2016 Sample Whaler 170,Vehicles,,,22000,2026-08-01,Sample,,,120 hours\n"
        )
        loaded = resolve_assets({ASSETS_ENV: text})
        rows = build_vehicle_rows(loaded.assets, pd.DataFrame())
        frame = vehicle_frame(rows)
        self.assertNotIn("VIN", frame.columns)
        self.assertNotIn("Notes", frame.columns)
        self.assertEqual(frame["Miles"].iloc[0], "120 hours")
        self.assertAlmostEqual(frame["Equity"].iloc[0], 22000)

    def test_named_loan_without_a_balance_leaves_equity_blank(self) -> None:
        text = (
            ASSET_HEADER
            + '\ncar,2020 Sample Hauler,Vehicles,,,100,2026-01-01,Sample,,,"Loan: Sample Bank ending 4242"\n'
            + "\nboat,2016 Sample Skiff,Vehicles,,,80,2026-01-01,Sample,,,120 hours\n"
        )
        loaded = resolve_assets({ASSETS_ENV: text})
        self.assertIsNone(loaded.warning)
        rows = build_vehicle_rows(loaded.assets, _balances())
        by_model = {row.model_trim: row for row in rows}
        unmatched = by_model["Hauler"]
        self.assertEqual(unmatched.loan, "Sample Bank ending 4242")
        self.assertIsNone(unmatched.loan_balance)
        self.assertIsNone(unmatched.equity)
        owned = by_model["Skiff"]
        self.assertEqual(owned.loan, "")
        self.assertIsNone(owned.loan_balance)
        self.assertAlmostEqual(owned.equity or 0, 80)

    def test_a_loan_line_that_is_not_the_mask_format_stays_raw(self) -> None:
        text = (
            ASSET_HEADER
            + '\ncar,2020 Sample Hauler,Vehicles,,,100,2026-01-01,Sample,,,"Loan: paid off"\n'
        )
        loaded = resolve_assets({ASSETS_ENV: text})
        rows = build_vehicle_rows(loaded.assets, _balances())
        self.assertEqual(rows[0].loan, "")
        self.assertTrue(rows[0].show_raw)
        self.assertEqual(rows[0].notes, "Loan: paid off")
        self.assertIsNone(rows[0].loan_balance)
        self.assertIsNone(rows[0].equity)

    def test_missing_market_value_sorts_last_and_is_left_out_of_the_total(self) -> None:
        text = (
            ASSET_HEADER
            + "\npriced,2020 Sample Hauler,Vehicles,,,50,2026-01-01,Sample,,,\n"
            + "open,2021 Sample Wagon,Vehicles,,,,2026-01-01,Sample,,,\n"
        )
        loaded = resolve_assets({ASSETS_ENV: text})
        self.assertIsNone(loaded.warning)
        rows = build_vehicle_rows(loaded.assets, None)
        self.assertEqual([row.model_trim for row in rows], ["Hauler", "Wagon"])
        self.assertIsNone(rows[1].market_value)
        self.assertIsNone(rows[1].equity)
        self.assertAlmostEqual(vehicle_total(rows), 50)


class RenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)

    def test_page_shows_the_total_and_the_sorted_table(self) -> None:
        script = f"""
import os
os.environ[{ASSETS_ENV!r}] = {_csv(_sample_rows())!r}
os.environ["FINANCE_BALANCES_CSV"] = {BALANCES!r}
from kirbancials.vehicles import render_vehicles
render_vehicles()
"""
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_string(script, default_timeout=30).run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(app.metric[0].label, "Total value")
        self.assertEqual(app.metric[0].value, "$53,500.00")
        frame = app.dataframe[0].value
        self.assertEqual(frame["Model/Trim"].tolist(), ["Whaler 170", "Hauler EX", "Wagon LX", "Sample Skiff"])
        self.assertEqual(frame["Miles"].tolist(), ["120 hours", "42,300", "~17,500 est.", MISSING])
        self.assertTrue(pd.api.types.is_numeric_dtype(frame["Est. Value"]))
        self.assertAlmostEqual(frame["Est. Value"].iloc[0], 22000)
        self.assertAlmostEqual(frame["Loan Balance"].iloc[1], 6400)
        self.assertAlmostEqual(frame["Equity"].iloc[1], 11600)
        self.assertTrue(pd.isna(frame["Loan Balance"].iloc[0]))
        self.assertEqual(frame["Notes"].iloc[-1], SKIFF_NOTES)
        self.assertIn('"format": "dollar"', app.dataframe[0].proto.columns)
        captions = "\n".join(node.value for node in app.caption)
        self.assertIn(USAGE_NOTE, captions)
        self.assertEqual(list(app.warning), [])

    def test_missing_assets_use_the_shared_empty_state(self) -> None:
        script = """
import os
os.environ.pop("FINANCE_ASSETS_CSV", None)
os.environ.pop("FINANCE_BALANCES_CSV", None)
from kirbancials.vehicles import render_vehicles
render_vehicles()
"""
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_string(script, default_timeout=30).run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(list(app.metric), [])
        self.assertEqual(list(app.dataframe), [])
        captions = "\n".join(node.value for node in app.caption)
        self.assertIn(NO_ASSETS_NOTE, captions)

    def test_configured_file_with_no_vehicles(self) -> None:
        text = ASSET_HEADER + "\nshop,Sample Shop,Real Estate,,,10,2026-01-01,Sample,,,\n"
        script = f"""
import os
os.environ[{ASSETS_ENV!r}] = {text!r}
os.environ["FINANCE_BALANCES_CSV"] = {BALANCES!r}
from kirbancials.vehicles import render_vehicles
render_vehicles()
"""
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_string(script, default_timeout=30).run()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(app.metric[0].value, "$0.00")
        captions = "\n".join(node.value for node in app.caption)
        self.assertIn(NO_VEHICLES, captions)
        self.assertEqual(list(app.dataframe), [])

    def test_balance_sheet_links_to_the_vehicles_page(self) -> None:
        from streamlit.testing.v1 import AppTest

        assets = _csv(
            [
                _asset(
                    **{
                        "Asset ID": "car-1",
                        "Asset Name": "2020 Sample Hauler EX",
                        "Category": "Vehicles",
                        "Market Value": "30",
                        "Valuation Date": "2026-01-01",
                    }
                ),
                _asset(
                    **{
                        "Asset ID": "boat",
                        "Asset Name": "2016 Sample Skiff",
                        "Category": "Vehicles",
                        "Market Value": "12",
                        "Valuation Date": "2026-01-01",
                    }
                ),
            ]
        )
        harness = ROOT / "_vehicles_sheet_harness.py"
        harness.write_text(
            f"""
import os
import streamlit as st

os.environ[{ASSETS_ENV!r}] = {assets!r}
os.environ["FINANCE_BALANCES_CSV"] = ""
os.environ["FINANCE_LOAN_SCHEDULE_CSV"] = ""
os.environ["FINANCE_INCOME_CSV"] = ""

st.navigation(
    [
        st.Page("views/balance_sheet.py", title="Balance Sheet", url_path="balance-sheet"),
        st.Page("views/vehicles.py", title="Vehicles", url_path="vehicles"),
    ]
)
from kirbancials.balance_sheet import render_balance_sheet
render_balance_sheet()
"""
        )
        try:
            app = AppTest.from_file(harness, default_timeout=30).run()
        finally:
            harness.unlink(missing_ok=True)
        self.assertEqual(list(app.exception), [])
        rendered = "\n".join(node.value for node in app.markdown)
        self.assertIn("Vehicles subtotal", rendered)
        self.assertIn("$42.00", rendered)
        links = [link.value for link in app.page_link]
        self.assertIn(VEHICLES_CATEGORY, links)
        captions = "\n".join(node.value for node in app.caption)
        self.assertIn(VEHICLES_LINK_CAPTION, captions)


class NavigationTests(unittest.TestCase):
    def test_vehicles_follows_the_balance_sheet_and_login_covers_it(self) -> None:
        text = (ROOT / "app.py").read_text()
        pages = [line for line in text.splitlines() if "st.Page(" in line]
        self.assertIn("balance_sheet.py", pages[2])
        self.assertIn("vehicles.py", pages[3])
        self.assertIn('title="Vehicles"', pages[3])
        self.assertIn('url_path="vehicles"', pages[3])
        self.assertNotIn("default=True", pages[3])
        sidebar = text.split("with st.sidebar:", 1)[1]
        links = [line for line in sidebar.splitlines() if "st.page_link" in line]
        self.assertIn("balance_sheet.py", links[2])
        self.assertIn("vehicles.py", links[3])
        self.assertIn('label="Vehicles"', links[3])
        self.assertLess(text.index("require_login()"), text.index("navigation.run()"))
        view = (ROOT / "views" / "vehicles.py").read_text()
        self.assertIn('render_page_title("Vehicles")', view)
        self.assertIn("render_vehicles", view)
        self.assertNotIn("require_login", view)
        self.assertNotIn("APP_PASSWORD", view)
        self.assertNotIn("<style>", view)
        self.assertEqual(VEHICLES_PAGE, "views/vehicles.py")
