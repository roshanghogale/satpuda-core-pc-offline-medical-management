"""Inventory > Merge medicines (owner, 30 Oct 2026): rows of one batch kept under two names.

A shop held "DEMO DOXY CAP 10'S" and "DEMO-DOXY CAP" on one batch: bills took stock off one
row, the shelf showed on the other. ``core.medicine_merge`` lists such rows and puts the ones the
owner picked into the row kept: every line moves to it, stock is the sum (or the count typed),
the other row is hidden and deleted. Offline-first sends the bills and the two stock movements.
Made-up medicines; nothing reaches a server.
"""
from __future__ import annotations

import os
import sqlite3
import unittest
from unittest import mock

from core import db_setup, medicine_merge
from tests.test_offline_first_pc_local import make_store, till


def _offline():
    return mock.patch("core.sync_prefs.is_online_mode", return_value=False)


def _seed(conn):
    conn.execute("INSERT INTO medicines (id, name, batch_no, stock_qty, mrp, type, unit) VALUES "
                 "(11, 'DEMO DOXY CAP 10''S', 'DX26', 12, 113.2, 'Capsule', '10'),"
                 "(12, 'DEMO-DOXY CAP', ' dx26 ', 36, 113.2, 'Capsule', '10'),"
                 "(13, 'DEMO DOXY CAP', 'DX27', 5, 113.2, 'Capsule', '10'),"
                 "(14, 'OTHER TAB', 'DX26', 9, 10, 'Tablet', '10')")
    conn.execute("INSERT INTO sales (id, bill_no, bill_date, total_amount) VALUES (501, 'SCB1', '2026-10-27', 60)")
    conn.execute("INSERT INTO sales_items (sale_id, medicine_id, qty, amount) VALUES (501, 12, 2, 22.6), (501, 14, 1, 1)")
    conn.execute("INSERT INTO purchases (id, purchase_no, purchase_date) VALUES (601, '1', '2026-10-01')")
    conn.execute("INSERT INTO purchase_items (purchase_id, medicine_id, qty, free_qty) VALUES (601, 12, 4, 0)")
    conn.commit()


class Offline(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        db_setup.initialise(self.conn)
        _seed(self.conn)
        p = _offline()
        p.start()
        self.addCleanup(p.stop)

    def test_the_list_pairs_one_batch_whose_names_start_alike(self):
        out = medicine_merge.candidates(self.conn)
        [g] = out["groups"]
        self.assertEqual(sorted(r["id"] for r in g["rows"]), [11, 12])     # not DX27, not OTHER TAB
        self.assertTrue(g["split_stock"])
        self.assertEqual({r["id"]: r["bill_lines"] for r in g["rows"]}, {11: 0, 12: 2})

    def test_merge_moves_every_line_and_sums_the_stock(self):
        res = medicine_merge.merge(self.conn, {"keep_id": 11, "merge_ids": [12]})
        self.assertEqual((res["ok"], res["stock"], res["lines_moved"]), (True, 48, 2))
        self.assertEqual(self.conn.execute("SELECT stock_qty, is_hidden FROM medicines WHERE id=11").fetchone(), (48, 0))
        self.assertEqual(self.conn.execute("SELECT stock_qty, is_hidden, deleted FROM medicines WHERE id=12").fetchone(),
                         (0, 1, 1))
        self.assertEqual([r[0] for r in self.conn.execute("SELECT medicine_id FROM sales_items ORDER BY id")], [11, 14])
        self.assertEqual(self.conn.execute("SELECT medicine_id FROM purchase_items").fetchone()[0], 11)
        self.assertEqual(medicine_merge.candidates(self.conn)["groups"], [])

    def test_the_count_typed_wins(self):
        res = medicine_merge.merge(self.conn, {"keep_id": 12, "merge_ids": [11], "stock_after": 40})
        self.assertEqual((res["stock"], res["stock_total"]), (40, 48))
        self.assertEqual(self.conn.execute("SELECT stock_qty FROM medicines WHERE id=12").fetchone()[0], 40)

    def test_another_batch_is_refused_and_nothing_changes(self):
        with self.assertRaises(ValueError):
            medicine_merge.merge(self.conn, {"keep_id": 11, "merge_ids": [13]})
        self.assertEqual(self.conn.execute("SELECT stock_qty, COALESCE(deleted,0) FROM medicines WHERE id=13").fetchone(), (5, 0))

    def test_nothing_picked_is_refused(self):
        for body in ({}, {"keep_id": 11, "merge_ids": []}, {"keep_id": 11, "merge_ids": [11]},
                     {"keep_id": 11, "merge_ids": [12], "stock_after": -1}):
            with self.assertRaises(ValueError):
                medicine_merge.merge(self.conn, body)


class OfflineFirst(unittest.TestCase):
    """The triggers send the moved bills, the deleted row and both stock movements."""

    def setUp(self):
        self.path = make_store()
        self.conn = till(self.path)
        self.addCleanup(self._cleanup)
        _seed(self.conn)
        self.conn.execute("DELETE FROM of_dirty")
        self.conn.execute("DELETE FROM of_stock_journal")
        self.conn.commit()
        p = _offline()
        p.start()
        self.addCleanup(p.stop)

    def _cleanup(self):
        self.conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    def test_what_goes_to_the_server(self):
        medicine_merge.merge(self.conn, {"keep_id": 11, "merge_ids": [12], "stock_after": 45})
        dirty = {(c, i) for c, i in self.conn.execute("SELECT collection, local_id FROM of_dirty")}
        self.assertIn(("sales", 501), dirty)
        self.assertIn(("purchases", 601), dirty)
        self.assertIn(("medicines", 12), dirty)                       # hidden + deleted
        journal = {m: d for m, d in self.conn.execute(
            "SELECT medicine_id, SUM(delta) FROM of_stock_journal GROUP BY medicine_id")}
        self.assertEqual(journal, {12: -36, 11: 33})                  # 12 + 36 = 48 -> counted 45


if __name__ == "__main__":
    unittest.main()
