"""Unit / white-box tests for reorder service and medicine visibility."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.db_setup import initialise as db_initialise
from core.reorder_service import (
    _as_str,
    collect_reorder_candidates,
    create_supplier_grouped_draft_orders,
    current_stock_for_medicine,
    fetch_pending_orders,
)
from core.medicine_visibility import hide_medicine, hide_zero_stock_medicines


class ReorderServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        db_initialise(self.conn)
        cur = self.conn.cursor()
        cur.execute("INSERT INTO suppliers (name, phone) VALUES ('Alpha Pharma', '111')")
        cur.execute("INSERT INTO suppliers (name, phone) VALUES ('Beta Medical', '222')")
        sid_a = cur.lastrowid - 1
        sid_b = cur.lastrowid
        cur.execute(
            "INSERT INTO medicines (name, type, stock_qty, unit, rate, batch_no) "
            "VALUES ('Med-A', 'Tablets', 2, '10', 5.0, 'B1')"
        )
        mid_a = cur.lastrowid
        cur.execute(
            "INSERT INTO medicines (name, type, stock_qty, unit, rate, batch_no) "
            "VALUES ('Med-B', 'Tablets', 0, '10', 8.0, 'B2')"
        )
        cur.execute(
            "INSERT INTO settings (name, value) VALUES ('low_stock_tablets', '10')"
        )
        cur.execute(
            "INSERT INTO medicine_suppliers (medicine_name, supplier_id, last_rate) "
            "VALUES ('Med-A', ?, 5.0)",
            (sid_a,),
        )
        cur.execute(
            "INSERT INTO medicine_suppliers (medicine_name, supplier_id, last_rate) "
            "VALUES ('Med-B', ?, 8.0)",
            (sid_b,),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_as_str_coerces_int(self):
        self.assertEqual(_as_str(10), "10")
        self.assertEqual(_as_str(None), "")

    def test_current_stock_with_int_pack(self):
        stock = current_stock_for_medicine(self.conn, "Med-A", 10)
        self.assertEqual(stock, 2.0)

    def test_collect_candidates_not_empty(self):
        items = collect_reorder_candidates(self.conn)
        names = {i["medicine_name"] for i in items}
        self.assertIn("Med-A", names)

    def test_supplier_grouped_drafts(self):
        result = create_supplier_grouped_draft_orders(self.conn)
        self.assertGreaterEqual(result["total"], 1)
        pending = fetch_pending_orders(self.conn, status="draft")
        self.assertGreaterEqual(len(pending), 1)

    def test_hide_medicine_soft_delete(self):
        cur = self.conn.cursor()
        cur.execute("SELECT id FROM medicines WHERE name='Med-A' LIMIT 1")
        mid = int(cur.fetchone()[0])
        hide_medicine(self.conn, mid)
        cur.execute("SELECT COALESCE(is_hidden,0) FROM medicines WHERE id=?", (mid,))
        self.assertEqual(int(cur.fetchone()[0]), 1)

    def test_hide_zero_stock(self):
        n = hide_zero_stock_medicines(self.conn)
        self.assertGreaterEqual(n, 1)


class PerfSmokeTests(unittest.TestCase):
    """Smoke timing — not a formal load test but catches regressions."""

    def test_db_init_under_5s(self):
        t0 = time.perf_counter()
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        try:
            db_initialise(conn)
        finally:
            conn.close()
            os.unlink(tmp.name)
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 5.0, f"db_initialise took {elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main(verbosity=2)
