"""Settings the shop saves and the app then ignores.

An audit went through the Settings screen one control at a time asking the same
question of each: does anything actually read this? Forty failed. These are the
engine-side halves of the fixes -- the ones a test can pin. The rest are React
and are covered by review and a build.

The shapes they fell into, all represented below:
  * saved and never applied -- the value round-trips and nothing renders with it
  * written to the wrong key -- saved under a name the reader does not look for
  * one screen only -- the alert list honours it, the shelf does not
  * overwritten by a default -- the app writes its own answer back over the shop's
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import (  # noqa: E402
    bill_config,
    column_config,
    db_setup,
    desktop_pages_service as pages,
    layout_config as lc,
    sync_prefs,
)


def iso(days):
    return (date.today() + timedelta(days=days)).strftime("%Y-%m-%d")


class ThresholdsReachTheShelf(unittest.TestCase):
    """S11-S14: every row was judged at the built-in 10 units and 3 months."""

    def setUp(self):
        self._online = sync_prefs.is_online_mode
        sync_prefs.is_online_mode = lambda *a, **k: False
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db_setup.initialise(self.conn)
        self.conn.execute("DELETE FROM medicines")
        for name, typ, stock, days in (
            ("TAB-30", "Tablet", 30, 900),
            ("TAB-90", "Tablet", 90, 900),
            ("INJ-150", "Injection", 90, 150),
        ):
            self.conn.execute(
                "INSERT INTO medicines (name,type,stock_qty,unit,mrp,rate,batch_no,expiry_date)"
                " VALUES (?,?,?,'10',100,80,'B1',?)",
                (name, typ, stock, iso(days)),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        sync_prefs.is_online_mode = self._online

    def _set(self, name, value):
        self.conn.execute(
            "INSERT OR REPLACE INTO settings (name, value) VALUES (?, ?)",
            (name, str(value)),
        )
        self.conn.commit()

    def _status(self, med):
        p = pages.list_inventory(self.conn)
        ni, si = p["columns"].index("Name"), p["columns"].index("Status")
        return {str(r[ni]): str(r[si] or "") for r in p["rows"]}[med]

    def test_the_low_stock_minimum_is_the_shops_own(self):
        self.assertEqual(self._status("TAB-30"), "")
        self._set("low_stock_tablet", 50)
        self.assertEqual(self._status("TAB-30"), "Low Stock")

    def test_a_medicine_above_the_minimum_is_still_clean(self):
        self._set("low_stock_tablet", 50)
        self.assertEqual(self._status("TAB-90"), "")

    def test_the_near_expiry_window_is_the_shops_own(self):
        """The near map was loaded and thrown away with an underscore."""
        self.assertEqual(self._status("INJ-150"), "")
        self._set("near_expiry_injection", 6)
        self.assertEqual(self._status("INJ-150"), "Near Expiry")

    def test_the_summary_tiles_agree_with_the_rows(self):
        self._set("low_stock_tablet", 50)
        self._set("near_expiry_injection", 6)
        s = pages.list_inventory(self.conn)["summary"]
        self.assertEqual(s["low_stock"], 1)
        self.assertEqual(s["near_expiry"], 1)

    def test_the_filters_agree_too(self):
        self._set("low_stock_tablet", 50)
        self._set("near_expiry_injection", 6)
        low = pages.list_inventory(self.conn, stock_status="low stock")["rows"]
        near = pages.list_inventory(self.conn, expiry_status="near expiry")["rows"]
        self.assertEqual([r[0] for r in low], ["TAB-30"])
        self.assertEqual([r[0] for r in near], ["INJ-150"])

    def test_the_reorder_minimum_looks_under_the_key_it_was_saved_under(self):
        """S14: it read the raw "Tablet"; every writer stores it lowercased."""
        from core.reorder_service import min_stock_level

        self._set("low_stock_tablet", 50)
        self.assertEqual(min_stock_level(self.conn, "Tablet"), 50.0)
        self.assertEqual(min_stock_level(self.conn, "Syrup"), 10.0)


class ColumnTicksDecideColumns(unittest.TestCase):
    """S27, S29, S30: ticks that could not hide, and ticks for nothing."""

    def setUp(self):
        self._online = sync_prefs.is_online_mode
        sync_prefs.is_online_mode = lambda *a, **k: False
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db_setup.initialise(self.conn)
        self.conn.execute("INSERT INTO shelf_settings (show_location) VALUES (1)")
        self.conn.commit()
        self.base = dict(lc.load_layout())

    def tearDown(self):
        self.conn.close()
        sync_prefs.is_online_mode = self._online

    def _with(self, vis, fn):
        cfg = dict(self.base)
        cfg["column_visibility"] = vis
        with mock.patch.object(lc, "load_layout", return_value=cfg), mock.patch.object(
            column_config, "load_layout", return_value=cfg
        ):
            return fn(self.conn)["columns"]

    def test_location_can_be_hidden_even_when_the_shelf_offers_it(self):
        on = self._with({"inventory": {"Location": True}}, pages.list_inventory)
        off = self._with({"inventory": {"Location": False}}, pages.list_inventory)
        self.assertIn("Location", on)
        self.assertNotIn("Location", off, "it was force-included whatever the tick said")

    def test_purchase_history_status_can_be_hidden(self):
        off = self._with({"purchase_history": {"Status": False}}, pages.list_purchase_history)
        on = self._with({"purchase_history": {}}, pages.list_purchase_history)
        self.assertNotIn("Status", off, "Status was appended unconditionally")
        self.assertIn("Status", on)

    def test_inventory_status_is_never_lost(self):
        """It is synthesised and has no registry entry; it must survive."""
        cols = self._with({"inventory": {"Status": False}}, pages.list_inventory)
        self.assertIn("Status", cols)

    def test_there_is_no_checkbox_for_a_column_the_screen_cannot_draw(self):
        offered = {p: {n for n, _ in cols}
                   for p, cols in column_config.desktop_table_columns().items()}
        for page, fn in (("sales_history", pages.list_sales_history),
                         ("purchase_history", pages.list_purchase_history)):
            with self.subTest(page=page):
                registry = {n for n, _ in column_config.TABLE_COLUMNS[page]}
                cfg = dict(self.base)
                cfg["column_visibility"] = {page: {n: True for n in registry}}
                with mock.patch.object(lc, "load_layout", return_value=cfg), \
                     mock.patch.object(column_config, "load_layout", return_value=cfg):
                    rendered = set(fn(self.conn)["columns"])
                self.assertFalse(
                    offered[page] - rendered,
                    "a tick with nothing on the other end",
                )

    def test_the_tk_registry_is_left_alone(self):
        # The Tk trees still render these; only the desktop list is trimmed.
        self.assertIn("Schedule", {n for n, _ in column_config.TABLE_COLUMNS["sales_history"]})
        self.assertIn("Returns", {n for n, _ in column_config.TABLE_COLUMNS["purchase_history"]})


class ExportTicksAreStoredWhereTheyAreRead(unittest.TestCase):
    """S28: written flat, read nested -- so the ticks were discarded."""

    def setUp(self):
        self.base = dict(lc.load_layout())

    def _visibility(self, stored, report):
        cfg = dict(self.base)
        cfg["export_column_visibility"] = stored
        with mock.patch.object(column_config, "load_layout", return_value=cfg):
            return column_config.get_export_column_visibility("sales_history", report)

    def test_the_shape_the_panel_now_writes_is_read_back(self):
        reports = column_config.EXPORT_REPORTS["sales_history"]
        stored = {"sales_history": {rk: {"Bill No": False} for rk in reports}}
        for rk in ("sales_register", "schedule_report"):
            with self.subTest(report=rk):
                self.assertIs(self._visibility(stored, rk)["Bill No"], False)

    def test_the_old_mixed_shape_lost_the_tick(self):
        """Why this needed fixing at all."""
        mixed = {"sales_history": {"sales_register": {"Bill No": True}, "Bill No": False}}
        self.assertIs(
            self._visibility(mixed, "sales_register")["Bill No"],
            True,
            "a flat tick beside a nested one was thrown away",
        )

    def test_the_panel_is_told_which_reports_a_page_has(self):
        from core.desktop_settings_service import get_options

        reports = get_options()["export_reports"]
        self.assertIn("sales_history", reports)
        self.assertIn("schedule_report", reports["sales_history"])


class DeletingABuiltInMakesItStayDeleted(unittest.TestCase):
    """S40: the built-ins were merged back in on read AND written back to disk."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "layout_config.txt")

    def _run(self, data):
        json.dump(data, open(self.path, "w"))
        with mock.patch.object(lc, "_get_config_path", return_value=self.path):
            lc.repair_layout_config_file()
            loaded = lc.load_layout()
            schedules = lc.get_layout_schedules()
        return loaded, json.load(open(self.path)), schedules

    def test_a_deleted_medicine_type_does_not_come_back(self):
        keep = [t for t in lc._DEFAULT_MED_TYPES if t != "Cream"]
        loaded, on_disk, _ = self._run({"med_types": keep})
        self.assertNotIn("Cream", loaded["med_types"])
        self.assertNotIn("Cream", on_disk.get("med_types", []),
                         "the start-up repair wrote it back to the file")

    def test_a_deleted_schedule_does_not_come_back(self):
        loaded, on_disk, _ = self._run({"schedules": ["", "H", "H1"]})
        self.assertNotIn("X", loaded["schedules"])
        self.assertNotIn("X", on_disk.get("schedules", []))

    def test_the_non_scheduled_option_survives_whatever_is_deleted(self):
        _, _, schedules = self._run({"schedules": ["H"]})
        self.assertIn("", schedules, "the blank entry is what 'Non-Scheduled' means")

    def test_a_first_run_still_gets_every_built_in(self):
        loaded, _, _ = self._run({})
        self.assertEqual(len(loaded["med_types"]), len(lc._DEFAULT_MED_TYPES))
        for s in lc._DEFAULT_SCHEDULES:
            self.assertIn(s, loaded["schedules"])


class QuickSaleOffersTheShopsOwnTypes(unittest.TestCase):
    """S08: it read a key load_layout has never emitted, so it never saw them."""

    def test_the_types_are_the_shops_types(self):
        from core.desktop_sales_service import _medicine_types

        self.assertEqual(list(_medicine_types()), list(lc.get_med_types()))
        self.assertGreater(len(_medicine_types()), 6, "not the six built-in fallbacks")

    def test_the_fallback_names_a_type_that_actually_exists(self):
        from core.desktop_sales_service import _medicine_types

        with mock.patch.object(lc, "get_med_types", side_effect=RuntimeError):
            fallback = _medicine_types()
        self.assertIn("Others", fallback)
        self.assertNotIn("Other", fallback, "'Other' is not one of the app's types")

    def test_the_pack_default_follows_the_type(self):
        from core.quick_sale_medicine import default_pack_for_type

        # A strip type gets a strip count; a bottle gets one.
        self.assertEqual(default_pack_for_type("Capsule"), "10")
        self.assertEqual(default_pack_for_type("Bolus"), "1")


class DefaultQtyIsStoredUnderTheKeyItIsReadFrom(unittest.TestCase):
    """S05: written as qty_<type>, read as typeqty_<type> -- so it vanished."""

    def test_it_survives_the_round_trip(self):
        from core import desktop_settings_service as svc

        saved = {}
        with mock.patch.object(lc, "save_layout", side_effect=lambda l: saved.update(l)):
            svc.save_layout_lists({"units": {"Tablet": {"unit": "10", "default_qty": 7}}})
        self.assertEqual(saved.get("typeqty_Tablet"), 7)

        merged = dict(lc.load_layout())
        merged.update(saved)
        with mock.patch.object(lc, "load_layout", return_value=merged):
            back = svc.get_layout_lists()["units"]["Tablet"]
        self.assertEqual(back["default_qty"], 7, "load_layout only re-emits typeqty_")


class PrintSlotsDecideWhatComesOut(unittest.TestCase):
    """S19, S21, S22: slot settings that were dropped or that overrode wrongly."""

    def test_a_full_size_slot_inherits_the_shops_bill_size(self):
        settings = dict(bill_config.DEFAULT_BILL_PRINT_SETTINGS)
        settings["bill_size_mode"] = "dot_matrix"
        merged = bill_config.get_print_slot_settings(settings, 1)
        self.assertEqual(
            merged["bill_size_mode"], "dot_matrix",
            "slot 1 shipped saying 'normal' and overrode Paper & Copies",
        )

    def test_an_explicit_dot_matrix_slot_still_wins(self):
        settings = dict(bill_config.DEFAULT_BILL_PRINT_SETTINGS)
        self.assertEqual(
            bill_config.get_print_slot_settings(settings, 2)["bill_size_mode"],
            "dot_matrix",
        )

    def test_a_slots_half_position_applies_on_a5_too(self):
        settings = dict(bill_config.DEFAULT_BILL_PRINT_SETTINGS)
        settings["a5_single_copy_position"] = "top"
        settings["print_slot_1"] = dict(settings["print_slot_1"])
        settings["print_slot_1"]["a5_single_copy_position"] = "bottom"
        merged = bill_config.get_print_slot_settings(settings, 1)
        self.assertEqual(merged["a5_single_copy_position"], "bottom")

    def test_no_checkbox_is_offered_for_a_field_no_template_draws(self):
        offered = {k for _group, opts in bill_config.BILL_PRINT_FIELD_OPTIONS for k, _ in opts}
        self.assertNotIn("show_terms", offered)
        self.assertNotIn("show_pay_mode", offered)


class TheFssaiTickIsRealWithoutLosingTodaysBills(unittest.TestCase):
    """S20: honouring it would have stripped a licence number from live bills."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        db_setup.initialise(self.conn)
        self.conn.execute("DELETE FROM pharmacy_profile")
        self.conn.execute(
            "INSERT INTO pharmacy_profile (name, fssai_number, show_fssai_on_bill)"
            " VALUES ('WITH', '1234567890', 0)"
        )
        self.conn.execute(
            "INSERT INTO pharmacy_profile (name, fssai_number, show_fssai_on_bill)"
            " VALUES ('WITHOUT', '', 0)"
        )
        self.conn.execute("DELETE FROM settings WHERE name='fssai_print_flag_migrated'")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _flag(self, name):
        return self.conn.execute(
            "SELECT show_fssai_on_bill FROM pharmacy_profile WHERE name=?", (name,)
        ).fetchone()[0]

    def _migrate(self):
        db_setup._migrate_fssai_print_flag(self.conn.cursor())
        self.conn.commit()

    def test_a_shop_already_printing_fssai_keeps_printing_it(self):
        self._migrate()
        self.assertEqual(self._flag("WITH"), 1)

    def test_a_shop_with_no_number_is_untouched(self):
        self._migrate()
        self.assertEqual(self._flag("WITHOUT"), 0)

    def test_the_shop_can_then_turn_it_off_for_good(self):
        self._migrate()
        self.conn.execute("UPDATE pharmacy_profile SET show_fssai_on_bill=0 WHERE name='WITH'")
        self.conn.commit()
        self._migrate()
        self.assertEqual(self._flag("WITH"), 0, "the one-shot must not fire twice")


class AppearanceFlagsReachTheScreensThatDrawThem(unittest.TestCase):
    """S01, S02: both round-tripped perfectly and nothing rendered with them."""

    def test_the_dashboard_payload_carries_them(self):
        import core.desktop_api as api

        self.assertIsInstance(api._home_banner_width(), int)
        self.assertGreaterEqual(api._home_banner_width(), 200)
        sections = api._dashboard_sections_payload()
        self.assertIn("home_dashboard", sections)

    def test_the_list_pages_are_told_too(self):
        from core.desktop_settings_service import get_layout_lists

        sections = get_layout_lists()["dashboard_sections"]
        for key in ("inventory_summary", "sales_summary", "purchase_summary"):
            self.assertIn(key, sections)


class ExportingIsNotChoosingADefault(unittest.TestCase):
    """S34: a one-off export wrote its format over the shop's saved default."""

    def test_running_an_export_does_not_save_a_format(self):
        from core import desktop_settings_service as svc

        src = open(svc.__file__, encoding="utf-8").read()
        block = src[src.index('    if action == "export":'):][:600]
        self.assertNotIn("save_default_export_format", block)
        self.assertIn("load_default_export_format", block)



class ThingsTheAdversarialPassFound(unittest.TestCase):
    """Second-order defects the fixes themselves introduced."""

    def test_an_online_shop_keeps_printing_its_fssai_number(self):
        """The one-shot migration only ever ran against the local database.

        In Online mode the engine's SQLite is an empty :memory: shell and the
        real profile comes from the store server, which has never carried this
        key -- so gating on a False default would have removed the licence line
        from every online shop's bills on day one.
        """
        from core.pharmacy_profile_io import _normalize

        self.assertIs(_normalize({"fssai_number": "123"})["show_fssai_on_bill"], True)
        self.assertIs(
            _normalize({"fssai_number": "123", "show_fssai_on_bill": 0})[
                "show_fssai_on_bill"
            ],
            False,
            "a shop that actually unticked it is still obeyed",
        )

    def test_the_export_grid_offers_the_export_reports_own_columns(self):
        """Trimming the SCREEN list took two working export ticks with it."""
        from core.desktop_settings_service import get_options

        opts = get_options()
        purchase = {c["key"] for c in opts["export_columns"]["purchase_history"]}
        sales = {c["key"] for c in opts["export_columns"]["sales_history"]}
        self.assertIn("Returns", purchase, "a real column of the Purchase Register")
        self.assertIn("Schedule", sales, "a real column of the Schedule Report")
        # ...while the on-screen grid stays trimmed.
        self.assertNotIn(
            "Returns", {c["key"] for c in opts["table_columns"]["purchase_history"]}
        )

    def test_no_purchase_tick_is_offered_for_a_column_that_table_omits(self):
        from core import column_config as cc

        offered = {n for n, _ in cc.desktop_table_columns()["purchase"]}
        for dead in ("Pack", "MRP/Tab", "Rate/Tab"):
            self.assertNotIn(dead, offered)
        self.assertIn("Medicine", offered)

    def test_a_slot_can_still_force_full_size(self):
        """Making "normal" inert would have trapped a dot-matrix shop."""
        settings = dict(bill_config.DEFAULT_BILL_PRINT_SETTINGS)
        settings["bill_size_mode"] = "dot_matrix"
        settings["print_slot_1"] = dict(settings["print_slot_1"])
        settings["print_slot_1"]["bill_size_mode"] = "normal"
        self.assertEqual(
            bill_config.get_print_slot_settings(settings, 1)["bill_size_mode"],
            "normal",
        )

    def test_an_absent_slot_size_still_inherits(self):
        settings = dict(bill_config.DEFAULT_BILL_PRINT_SETTINGS)
        settings["bill_size_mode"] = "dot_matrix"
        self.assertNotIn("bill_size_mode", settings["print_slot_1"])
        self.assertEqual(
            bill_config.get_print_slot_settings(settings, 1)["bill_size_mode"],
            "dot_matrix",
        )

    def test_the_preview_reads_bill_copies_not_page_copies(self):
        """They are different numbers, and the preview was reading the wrong one."""
        merged = bill_config.get_print_slot_settings(
            dict(bill_config.DEFAULT_BILL_PRINT_SETTINGS), 1
        )
        self.assertEqual(merged.get("copies"), 1, "page copies")
        self.assertEqual(merged.get("bill_copies"), 2, "the slot's bill copies")
        laid_out = bill_config.apply_print_bill_layout(
            dict(merged), print_slot_copies=int(merged.get("bill_copies") or 1)
        )
        self.assertEqual(laid_out.get("pdf_save_layout"), "two_copies")


if __name__ == "__main__":
    unittest.main(verbosity=2)
