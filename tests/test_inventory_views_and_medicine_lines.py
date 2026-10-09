"""Inventory views (Active / Hidden / Out of stock / Expired / All) and a medicine's details
flowing into its old bills (owner, 9 Oct 2026). Offline path, on a temp store file; no server.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

from core import db_setup, medicine_lines
from core.inventory_views import count_local, normalize_view


def make_store() -> tuple[sqlite3.Connection, str]:
    fd, path = tempfile.mkstemp(suffix=".db", prefix="inv_views_")
    os.close(fd)
    conn = sqlite3.connect(path)
    db_setup.initialise(conn)
    conn.commit()
    return conn, path


def add_med(conn, mid, name, *, stock=10, hidden=0, expiry="2030-12-01", schedule="", mtype="Tablet"):
    conn.execute(
        "INSERT INTO medicines (id, name, type, stock_qty, unit, mrp, rate, batch_no, expiry_date, "
        "schedule, manufacturer, is_hidden) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (mid, name, mtype, stock, "10", 50, 30, f"B{mid}", expiry, schedule, "ACME", hidden),
    )


class Views(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_store()
        last_month = (date.today().replace(day=1) - timedelta(days=1)).replace(day=1).isoformat()
        this_month = date.today().replace(day=1).isoformat()
        add_med(self.conn, 1, "ALPHA")                              # active, in stock
        add_med(self.conn, 2, "BETA", stock=0)                      # out of stock
        add_med(self.conn, 3, "GAMMA", hidden=1)                    # hidden
        add_med(self.conn, 4, "DELTA", expiry=last_month)           # expired
        add_med(self.conn, 5, "EPSILON", expiry=this_month)         # month-end grace: not expired
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def test_counts_use_the_month_end_rule(self):
        self.assertEqual(
            count_local(self.conn),
            {"active": 4, "hidden": 1, "out_of_stock": 1, "expired": 1, "all": 5},
        )

    def test_views_list_the_right_medicines(self):
        from core import desktop_pages_service as pages

        def names(show):
            with mock.patch("core.sync_prefs.is_online_mode", return_value=False):
                out = pages.list_inventory(self.conn, show=show)
            return sorted(r[0] for r in out["rows"]), out

        got, out = names("")
        self.assertEqual(got, ["ALPHA", "BETA", "DELTA", "EPSILON"])  # today's default, unchanged
        self.assertEqual(out["view"], "active")
        self.assertEqual(out["view_counts"]["hidden"], 1)
        self.assertEqual(names("hidden")[0], ["GAMMA"])
        self.assertEqual(names("out_of_stock")[0], ["BETA"])
        self.assertEqual(names("expired")[0], ["DELTA"])
        self.assertEqual(names("all")[0], ["ALPHA", "BETA", "DELTA", "EPSILON", "GAMMA"])

    def test_unhide_brings_a_hidden_medicine_back(self):
        from core.desktop_inventory_service import unhide_medicine

        with mock.patch("core.sync_prefs.is_online_mode", return_value=False),                 mock.patch("core.sync_coordinator.is_online_mode", return_value=False),                 mock.patch("core.online_guard.ensure_can_mutate", return_value=None):
            out = unhide_medicine(self.conn, {"id": 3})
            missing = unhide_medicine(self.conn, {"id": 999})
        self.assertTrue(out["ok"], out)
        self.assertFalse(missing["ok"])
        self.assertEqual(self.conn.execute("SELECT is_hidden FROM medicines WHERE id=3").fetchone()[0], 0)
        self.assertEqual(count_local(self.conn)["hidden"], 0)

    def test_view_names(self):
        self.assertEqual(normalize_view("Out of stock"), "out_of_stock")
        self.assertEqual(normalize_view("nonsense"), "active")


class OldBillsFollow(unittest.TestCase):
    def setUp(self):
        self.conn, self.path = make_store()
        add_med(self.conn, 7, "PARA 500")
        add_med(self.conn, 8, "OTHER")
        c = self.conn
        c.execute("UPDATE medicines SET hsn_code='3004' WHERE id=7")
        c.execute("INSERT INTO sales (id, bill_no, customer_id, bill_date, total_amount) VALUES (1,'SCB1',1,'2026-10-01',50)")
        c.execute("INSERT INTO sales (id, bill_no, customer_id, bill_date, total_amount) VALUES (2,'SCB2',1,'2026-10-02',50)")
        c.execute("INSERT INTO sales_items (sale_id, medicine_id, qty, rate, amount) VALUES (1,7,10,5,50)")
        c.execute("INSERT INTO sales_items (sale_id, medicine_id, qty, rate, amount) VALUES (2,7,10,5,50)")
        c.execute("INSERT INTO sales_items (sale_id, medicine_id, qty, rate, amount) VALUES (2,8,1,5,5)")
        c.execute(
            "INSERT INTO purchases (id, purchase_no, supplier_id, purchase_date, total_amount) VALUES (1,'1',1,'2026-09-01',300)"
        )
        c.execute(
            "INSERT INTO purchase_items (purchase_id, medicine_id, qty, rate, mrp, gst_pct, schedule, hsn_code, type, manufacturer) "
            "VALUES (1,7,10,30,50,12,'','3004','Tablet','ACME')"
        )
        c.commit()

    def tearDown(self):
        self.conn.close()
        os.remove(self.path)

    def _edit(self, **fields):
        from core.desktop_inventory_service import update_medicine

        body = {"id": 7, "name": "PARA 500", "type": "Tablet", "unit": "10", "mrp": 50, "rate": 30,
                "manufacturer": "ACME", "schedule": "", "hsn_code": "3004", **fields}
        with mock.patch("core.sync_prefs.is_online_mode", return_value=False), \
                mock.patch("core.online_guard.ensure_can_mutate", return_value=None):
            return update_medicine(self.conn, body)

    def test_schedule_reaches_old_purchase_lines_and_reports_old_sales(self):
        out = self._edit(schedule="H1")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["lines_note"], "Schedule changed: 2 old sales, 1 purchase updated")
        row = self.conn.execute("SELECT schedule, rate, gst_pct FROM purchase_items WHERE medicine_id=7").fetchone()
        self.assertEqual(row, ("H1", 30.0, 12.0))  # rate and GST as billed
        # sales lines carry no copy on the PC: the register reads the medicine, now H1
        self.assertEqual(self.conn.execute("SELECT schedule FROM medicines WHERE id=7").fetchone()[0], "H1")

    def test_price_edit_touches_no_old_line(self):
        out = self._edit(mrp=60, rate=35)
        self.assertEqual(out["lines_note"], "")
        self.assertEqual(self.conn.execute("SELECT rate FROM purchase_items WHERE medicine_id=7").fetchone()[0], 30.0)

    def test_pulled_medicine_updates_lines_but_a_missing_field_blanks_nothing(self):
        before = medicine_lines.snapshot(self.conn, 7)
        self.conn.execute("UPDATE medicines SET hsn_code='300490' WHERE id=7")
        medicine_lines.after_pulled_medicine(self.conn, before, {"id": 7, "hsn_code": "300490"})
        row = self.conn.execute("SELECT hsn_code, manufacturer, type FROM purchase_items WHERE medicine_id=7").fetchone()
        self.assertEqual(row, ("300490", "ACME", "Tablet"))

    def test_batch_expiry_and_gst_follow_but_never_blank_and_amounts_stay(self):
        out = self._edit(batch="B7X", expiry="11/28")
        self.assertTrue(out["ok"], out)
        self.assertIn("Batch", out["lines_note"])
        self.assertIn("Expiry", out["lines_note"])
        exp = self.conn.execute("SELECT expiry_date FROM medicines WHERE id=7").fetchone()[0]
        row = self.conn.execute("SELECT batch_no, expiry_date, rate FROM purchase_items WHERE medicine_id=7").fetchone()
        self.assertEqual(row, ("B7X", exp, 30.0))
        # GST % arriving from another device: sale and purchase lines take the rate, not the amount
        self.conn.execute("UPDATE sales_items SET gst_percent=12")
        before = medicine_lines.snapshot(self.conn, 7)
        medicine_lines.after_pulled_medicine(self.conn, before, {"id": 7, "gst_percent": 5, "batch_no": ""})
        self.assertEqual(
            self.conn.execute("SELECT DISTINCT gst_percent, amount FROM sales_items WHERE medicine_id=7").fetchall(),
            [(5.0, 50.0)],
        )
        self.assertEqual(
            self.conn.execute("SELECT gst_pct, batch_no FROM purchase_items WHERE medicine_id=7").fetchone(), (5.0, "B7X")
        )

    def test_note_wording(self):
        self.assertEqual(medicine_lines.note({"fields": ["schedule"], "sales": 37}), "Schedule changed: 37 old sales updated")
        self.assertEqual(medicine_lines.note({"fields": ["schedule"], "sales": 0, "purchases": 0}), "")
        self.assertEqual(
            medicine_lines.note_from_push({"medicines": {"results": [{"id": 7, "lines_note": "Schedule changed: 3 old sales updated"}]}}),
            "Schedule changed: 3 old sales updated",
        )


if __name__ == "__main__":
    unittest.main()
