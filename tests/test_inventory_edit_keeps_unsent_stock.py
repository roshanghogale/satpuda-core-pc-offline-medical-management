"""Inventory edit (Offline / offline-first local copy): what the edit does not send stays put.

The dialog now sends stock only when the user changed it, so a sale taken off by another
device (arriving by sync pull while the dialog was open) is not put back by an HSN fix.
The local path must then leave stock_qty (and expiry) alone instead of writing 0 / blank.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import db_setup


class EditKeepsUnsentStock(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db", prefix="inv_edit_")
        os.close(fd)
        self.conn = sqlite3.connect(self.path)
        db_setup.initialise(self.conn)
        self.conn.execute(
            "INSERT INTO medicines (id, name, type, stock_qty, unit, mrp, rate, batch_no, expiry_date) "
            "VALUES (7, 'PARA 500', 'Tablet', 80, '10', 50, 30, 'B7', '2030-12-01')"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def _edit(self, **fields):
        from core.desktop_inventory_service import update_medicine

        body = {"id": 7, "name": "PARA 500", "type": "Tablet", "unit": "10", "mrp": 50,
                "rate": 30, "batch": "B7", "hsn_code": "3004", **fields}
        with mock.patch("core.sync_prefs.is_online_mode", return_value=False), \
                mock.patch("core.online_guard.ensure_can_mutate", return_value=None):
            return update_medicine(self.conn, body)

    def _row(self):
        return self.conn.execute(
            "SELECT stock_qty, expiry_date, hsn_code FROM medicines WHERE id=7"
        ).fetchone()

    def test_edit_without_stock_keeps_stock_and_expiry(self):
        # Another device's sale arrived by pull after the dialog opened.
        self.conn.execute("UPDATE medicines SET stock_qty=60 WHERE id=7")
        self.conn.commit()
        out = self._edit()
        self.assertTrue(out["ok"], out)
        stock, expiry, hsn = self._row()
        self.assertEqual(stock, 60)
        self.assertEqual(expiry, "2030-12-01")
        self.assertEqual(hsn, "3004")
        self.assertEqual(out["stock_qty"], 60)

    def test_explicit_stock_still_saves(self):
        out = self._edit(stock_strips=3, stock_qty=3, extra_tablets=5)
        self.assertTrue(out["ok"], out)
        self.assertEqual(self._row()[0], 35)

    def test_dialog_sends_stock_only_when_changed(self):
        src = (Path(__file__).resolve().parents[1] / "desktop" / "src" / "pages"
               / "MedicineEditDialog.tsx").read_text(encoding="utf-8")
        self.assertIn("loadedStock.current", src)
        self.assertIn("...stockFields", src)
        self.assertNotIn("        stock_qty: Number(form.stock_strips) || 0,\n        extra_tablets", src)


if __name__ == "__main__":
    unittest.main()


class RepairFromPurchasesIsOnlineOnly(unittest.TestCase):
    def test_offline_first_refuses_and_touches_no_server(self):
        from core import purchase_service

        with mock.patch("core.sync_prefs.is_online_mode", return_value=False), \
                mock.patch("core.server_crud.upsert_medicine_online") as up:
            out = purchase_service.repair_purchased_medicines_online()
        self.assertFalse(out["ok"])
        up.assert_not_called()
