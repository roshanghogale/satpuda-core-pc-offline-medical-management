"""Purchase Form Fields (owner, 30 Oct 2026): the shop hides Purchase-screen boxes in Settings.

A hidden box counts as empty on a new line and is never asked for; the ones the master list
knows are filled from it when the line is added (PurchasePage.formValuesForAdd reads
``master`` from /api/purchase/lookup-medicine). A line with a hidden Batch is saved as
batch "NA" and a hidden Expiry empty. Made-up medicines; no network.
"""
from __future__ import annotations

import os
import sqlite3
import unittest
from unittest import mock

from core import db_setup, desktop_purchase_service, purchase_service
from tests import test_a_loose_purchase_line_stocks_its_own_pack as loose

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TheLookupCarriesTheMastersOwnValues(unittest.TestCase):
    def test_master_values_apart_from_the_shops_batch(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        conn.execute("INSERT INTO medicines (name, type, unit, schedule, hsn_code, batch_no) "
                     "VALUES ('DEMO HID TAB', 'Tablet', '10', 'G', '3004', 'B1')")
        master = {"type": "Tablet", "manufacturer": "DEMO LABS", "mrp": 55.5, "content_drug": "Demo (5mg)",
                  "pack_size": "1x15", "schedule": "H", "hsn_code": "30049099", "gst_percent": 5.0}
        with mock.patch("core.master_medicine_service.lookup_master_details", return_value=master), \
                mock.patch("core.purchase_service.lookup_medicine_details",
                           return_value={"type": "Tablet", "schedule": "G", "batch_no": "B1"}):
            res = desktop_purchase_service.lookup_medicine(conn, {"name": "DEMO HID TAB"})
        self.assertEqual(res["details"]["schedule"], "G")            # the shop's, as before
        self.assertEqual(res["master"]["schedule"], "H")             # the master's, for a hidden box
        self.assertEqual((res["master"]["pack_size"], res["master"]["hsn_code"], res["master"]["gst_percent"]),
                         ("1x15", "30049099", 5.0))

    def test_no_master_row_is_all_empty(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        with mock.patch("core.master_medicine_service.lookup_master_details", return_value={}):
            res = desktop_purchase_service.lookup_medicine(conn, {"name": "NOWHERE"})
        self.assertEqual(res["master"]["schedule"], "")
        self.assertEqual(res["master"]["mrp"], 0.0)


class AHiddenBatchAndExpiryLine(loose.OfflineALooseLine):
    def test_is_saved_with_them_empty(self):
        line = dict(self.line(2, 10), batch="NA", expiry="")
        purchase_service.save_purchase(self.conn, 1, "2026-09-12", "INV-H", self.calc(), [line])
        row = self.conn.execute("SELECT qty, COALESCE(batch_no,''), expiry_date FROM purchase_items").fetchone()
        self.assertEqual(row[:2], (2, "NA"))
        self.assertIn(row[2], (None, ""))


class OnlineAHiddenBatchAndExpiryLine(loose._Online):
    def test_is_saved_and_stocked(self):
        # "NA" is a batch of its own: a new stock row beside the medicine's other batches.
        line = dict(loose._screen_line(2, 10), batch="NA", expiry="")
        body = {"supplier_name": "STG SUP", "purchase_date": "2026-09-12", "items": [line],
                "cash_paid": 0, "online_paid": 0}
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        res = desktop_purchase_service.save_purchase_bill(conn, body)
        self.assertTrue(res.get("ok"), res)
        [doc] = self.sent
        [med] = doc["_medicines"]
        self.assertEqual((med["batch_no"], sum(int(o["qty_delta"]) for o in med["stock_ops"])), ("NA", 20))
        self.assertEqual(doc["items"][0]["batch_no"], "NA")


class TheScreen(unittest.TestCase):
    def read(self, *parts):
        return open(os.path.join(ROOT, "desktop", "src", *parts), encoding="utf-8").read()

    def test_every_listed_box_can_be_hidden(self):
        page = self.read("pages", "PurchasePage.tsx")
        keys = ["address", "phone", "gstin", "dl", "purchase_date", "bill_number", "gst_method", "type",
                "pack", "free", "hsn", "gst_pct", "mrp", "rate", "discount", "manufacturer", "batch",
                "expiry", "schedule", "content"]
        lib = self.read("pageSummaries.ts")
        for k in keys:
            self.assertIn(f"{{showBox('{k}') ? (", page, k)
            self.assertIn(f"key: '{k}'", lib, k)
        # a hidden batch / expiry is not asked for
        self.assertIn("!batch.trim() && !hidden.has('batch')", page)
        self.assertIn("if (hidden.has('batch')) v.batch = HIDDEN_BATCH", page)
        self.assertIn("!hidden.has('expiry') &&", page)
        self.assertIn("{ id: 'purchase_fields', label: 'Purchase Form Fields' }",
                      self.read("pages", "settings", "settingsConfig.ts"))


if __name__ == "__main__":
    unittest.main()
