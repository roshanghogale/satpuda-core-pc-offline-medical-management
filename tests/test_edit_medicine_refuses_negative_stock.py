"""Edit Medicine: a minus typed into Stock / Extra Tablets is refused, not saved as 0.

inventory_save_stock_qty clamps at 0, so "-5" strips quietly wiped the batch's whole stock
(and offline-first's stock journal sent that minus to every device). Every mode is refused
before anything is written; an edit that leaves stock alone is untouched.
"""
from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from core import db_setup


class NegativeStockIsRefused(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        db_setup.initialise(self.conn)
        self.conn.execute(
            "INSERT INTO medicines (id, name, type, stock_qty, unit, mrp, rate, batch_no) "
            "VALUES (7, 'PARA 500', 'Tablet', 80, '10', 50, 30, 'B7')")
        self.conn.commit()

    def _edit(self, online=False, **fields):
        from core.desktop_inventory_service import update_medicine

        body = {"id": 7, "name": "PARA 500", "type": "Tablet", "unit": "10", "mrp": 50,
                "rate": 30, "batch": "B7", **fields}
        with mock.patch("core.sync_prefs.is_online_mode", return_value=online), \
                mock.patch("core.online_guard.ensure_can_mutate", return_value=None), \
                mock.patch("core.server_crud.upsert_medicine_online") as pushed:
            return update_medicine(self.conn, body), pushed

    def stock(self):
        return self.conn.execute("SELECT stock_qty FROM medicines WHERE id=7").fetchone()[0]

    def test_minus_strips(self):
        out, _ = self._edit(stock_strips=-5, stock_qty=-5, extra_tablets=0)
        self.assertFalse(out["ok"])
        self.assertEqual(out["code"], "negative_stock")
        self.assertEqual(self.stock(), 80)

    def test_minus_loose_tablets(self):
        out, _ = self._edit(stock_strips=3, extra_tablets=-2)
        self.assertFalse(out["ok"])
        self.assertEqual(self.stock(), 80)

    def test_online_sends_nothing(self):
        out, pushed = self._edit(online=True, stock_strips=-1, extra_tablets=0)
        self.assertFalse(out["ok"])
        pushed.assert_not_called()

    def test_zero_and_up_still_save(self):
        out, _ = self._edit(stock_strips=0, extra_tablets=4)
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.stock(), 4)

    def test_an_edit_without_stock_is_untouched(self):
        self.conn.execute("UPDATE medicines SET stock_qty=-3 WHERE id=7")   # a sync left it negative
        self.conn.commit()
        out, _ = self._edit(hsn_code="3004")
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.stock(), -3)


class TheDialogSaysSoFirst(unittest.TestCase):
    def test_the_dialog_checks_a_changed_stock_and_the_inputs_stop_at_zero(self):
        src = (Path(__file__).resolve().parents[1] / "desktop" / "src" / "pages"
               / "MedicineEditDialog.tsx").read_text(encoding="utf-8")
        self.assertIn("stockTouched && (Number(form.stock_strips) < 0 || Number(form.extra_tablets) < 0)", src)
        self.assertEqual(src.count("min={0}"), 2)


if __name__ == "__main__":
    unittest.main()
