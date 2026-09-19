"""A shop can put the stock it already had on the shelf without inventing a bill.

Before this, the only ways in were a purchase -- which needs a supplier and a
bill number the shop never received -- or the phone loader. The owner asked for
the third way: the rows the data-loading app produces, typed or pasted into
Settings, landing straight in Inventory.

Also covered here, because the same code carries it: the Online half. Online
mode keeps nothing in the engine's SQLite, so the old import matched every row
against an empty table and pushed all of them as NEW medicines -- a loader
import could double a shop's catalogue instead of filling in its stock. Online
now matches against the shop's own server catalogue and updates under its id.

In-memory store, patched server client. Nothing reaches a server.
"""
from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from core import db_setup, desktop_settings_service as settings  # noqa: E402
from core import opening_stock_service as opening  # noqa: E402

SHEET = (
    "name,batch,expiry,type,unit,stock,extra,mrp,rate,gst\n"
    "AMOXYCILLIN 500MG,B1204,08/2027,Tablet,1x10,12,4,85.50,68.40,12\n"
    "CALCIUM LIQUID 500ML,C77,03/2028,Liquid,500ML,6,0,240,190,12\n"
)


class _Offline(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        db_setup.initialise(self.conn)
        p = mock.patch("core.sync_prefs.is_online_mode", return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def apply(self, text=SHEET):
        return settings.import_action(self.conn, {"action": "opening_stock_apply", "csv": text})

    def stock_of(self, name):
        row = self.conn.execute(
            "SELECT stock_qty FROM medicines WHERE name=?", (name,)
        ).fetchone()
        return row[0] if row else None


class TheShelfArrivesWithNoSupplierAndNoBill(_Offline):
    def test_the_rows_land_in_inventory(self):
        out = self.apply()
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["inserted"], 2)
        self.assertEqual(self.stock_of("AMOXYCILLIN 500MG"), 124, "12 strips of 10 plus 4 loose")
        self.assertEqual(self.stock_of("CALCIUM LIQUID 500ML"), 6)

    def test_nothing_else_is_created(self):
        self.apply()
        for table in ("suppliers", "purchases", "purchase_items", "supplier_payments"):
            self.assertEqual(
                self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0,
                f"the import invented rows in {table}",
            )

    def test_the_same_sheet_twice_does_not_double_the_shelf(self):
        self.apply()
        out = self.apply()
        self.assertEqual(out["updated"], 2)
        self.assertEqual(out["inserted"], 0)
        self.assertEqual(self.stock_of("AMOXYCILLIN 500MG"), 124)

    def test_a_row_with_no_name_is_reported_not_imported(self):
        out = self.apply(SHEET + ",B9,01/2029,Tablet,1x10,5,0,10,8,5\n")
        self.assertEqual(out["inserted"], 2)
        self.assertTrue(any("no medicine name" in p for p in out.get("problems", [])))

    def test_a_blank_line_in_a_pasted_sheet_is_not_a_problem(self):
        out = settings.import_action(
            self.conn, {"action": "opening_stock_preview", "csv": SHEET + ",,,,,,,,,\n"}
        )
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["problems"], [])

    def test_the_loader_json_is_accepted_as_it_is(self):
        payload = (
            '{"export_type":"medicines","medicines":['
            '{"name":"VITAMIN B COMPLEX","batch_no":"V1","type":"Tablet",'
            '"unit":"1x15","stock_qty":3,"extra_medicine":2,"mrp":60,"rate":48}]}'
        )
        out = settings.import_action(self.conn, {"action": "opening_stock_apply", "json": payload})
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.stock_of("VITAMIN B COMPLEX"), 47, "3 strips of 15 plus 2 loose")

    def test_a_preview_writes_nothing(self):
        out = settings.import_action(self.conn, {"action": "opening_stock_preview", "csv": SHEET})
        self.assertEqual(out["count"], 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM medicines").fetchone()[0], 0)

    def test_a_sheet_with_no_column_line_is_refused_with_the_shape_it_wants(self):
        out = settings.import_action(
            self.conn, {"action": "opening_stock_preview", "csv": "AMOXY,B1,5\n"}
        )
        self.assertFalse(out["ok"])
        self.assertIn("name,batch_no", out["error"])

    def test_the_template_names_the_columns_and_the_rules(self):
        out = settings.import_action(self.conn, {"action": "opening_stock_template"})
        self.assertIn("name", out["columns"])
        self.assertIn("extra_medicine", out["columns"])
        self.assertTrue(any("No supplier" in n for n in out["notes"]))


class TheOnlineShopIsFilledInNotDuplicated(unittest.TestCase):
    """Online mode holds the medicines on the server, not in the engine's SQLite."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        db_setup.initialise(self.conn)
        self.written: list[dict] = []
        self.server = [
            {"id": 41, "name": "AMOXYCILLIN 500MG", "batch_no": "B1204",
             "expiry_date": "2027-08-01", "stock_qty": 0, "type": "Tablet", "unit": "1x10"},
            {"id": 42, "name": "OLD DELETED", "batch_no": "X", "deleted": True},
        ]
        for patch in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.online_catalog.medicines", side_effect=lambda **k: list(self.server)),
            mock.patch("core.server_crud.upsert_medicine_online",
                       side_effect=lambda doc: self.written.append(dict(doc)) or int(doc.get("id") or 99)),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_medicine_the_shop_already_has_is_updated_under_its_own_id(self):
        out = settings.import_action(self.conn, {"action": "opening_stock_apply", "csv": SHEET})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["updated"], 1)
        self.assertEqual(out["inserted"], 1, "the medicine it does not have is the new one")
        known = [d for d in self.written if d["name"] == "AMOXYCILLIN 500MG"]
        self.assertEqual(len(known), 1)
        self.assertEqual(known[0]["id"], 41, "a second copy would have been created on the server")
        self.assertEqual(known[0]["stock_qty"], 124)
        fresh = [d for d in self.written if d["name"] == "CALCIUM LIQUID 500ML"]
        self.assertEqual(len(fresh), 1)
        self.assertNotIn("id", fresh[0], "a new medicine must not claim an id of its own")

    def test_nothing_is_written_to_the_engines_own_database(self):
        settings.import_action(self.conn, {"action": "opening_stock_apply", "csv": SHEET})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM medicines").fetchone()[0], 0)

    def test_a_deleted_medicine_on_the_server_is_not_matched(self):
        settings.import_action(
            self.conn,
            {"action": "opening_stock_apply", "csv": "name,batch,stock\nOLD DELETED,X,5\n"},
        )
        self.assertNotIn(42, [d.get("id") for d in self.written])


class TheScreenIsWiredToIt(unittest.TestCase):
    def test_settings_has_the_section_and_the_panel(self):
        config = open("desktop/src/pages/settings/settingsConfig.ts", encoding="utf-8").read()
        self.assertIn("{ id: 'opening_stock', label: 'Opening Stock' }", config)
        panel = open("desktop/src/pages/settings/ImportDataPanels.tsx", encoding="utf-8").read()
        self.assertIn("sectionId === 'opening_stock'", panel)
        self.assertIn("opening_stock_apply", panel)
        self.assertIn("opening_stock_preview", panel)

    def test_the_columns_the_screen_shows_are_the_ones_the_engine_reads(self):
        panel = open("desktop/src/pages/settings/ImportDataPanels.tsx", encoding="utf-8").read()
        for column in ("name", "batch_no", "expiry_date", "stock_qty", "extra_medicine"):
            self.assertIn(column, panel)
            self.assertIn(column, opening.COLUMNS)


if __name__ == "__main__":
    unittest.main()
