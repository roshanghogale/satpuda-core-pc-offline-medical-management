"""A medicine pulled from the store keeps the day it came in.

Which batches a back-dated bill may use depends on when each batch came in
(core.sale_availability). The pull wrote every medicine without its created_at, so the
row took the moment of the pull, and a batch another device bought in January looked new.
The store's date is kept now, and an existing row keeps whichever date is earlier.
Runs on an in-memory database.
"""
from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from core import db_setup, server_entity_sync  # noqa: E402


class APulledMedicineKeepsWhenItCameIn(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        db_setup.initialise(self.conn)
        self.conn.execute(
            "INSERT INTO medicines (id, name, batch_no, stock_qty, created_at, version) "
            "VALUES (9, 'PARA', 'B9', 1, '2026-01-01 00:00:00', 1)"
        )
        self.conn.commit()

    def pull(self, doc_id: int, created_at, version: int = 5):
        data = {
            "id": doc_id, "name": "PARA", "batch_no": f"B{doc_id}", "stock_qty": 2,
            "version": version, "updated_at": "2026-09-13T06:00:00.000Z",
            "created_at": created_at,
        }
        with mock.patch("core.sync_prefs.is_online_mode", return_value=True):
            result = server_entity_sync.sync_down_doc(self.conn, "medicines", str(doc_id), data)
        row = self.conn.execute(
            "SELECT created_at FROM medicines WHERE id=?", (doc_id,)
        ).fetchone()
        return result, str(row[0]) if row else None

    def test_a_new_row_takes_the_stores_date(self):
        result, created = self.pull(10, "2026-02-02T05:00:00.000Z", version=1)
        self.assertEqual(result, "applied")
        self.assertTrue(created.startswith("2026-02-02"), created)

    def test_an_existing_row_keeps_the_earlier_date(self):
        result, created = self.pull(9, "2026-05-01T00:00:00.000Z")
        self.assertEqual(result, "applied")
        self.assertEqual(created, "2026-01-01 00:00:00")

    def test_an_earlier_date_from_the_store_moves_it_back(self):
        _, created = self.pull(9, "2025-12-01T00:00:00.000Z")
        self.assertTrue(created.startswith("2025-12-01"), created)


if __name__ == "__main__":
    unittest.main()
