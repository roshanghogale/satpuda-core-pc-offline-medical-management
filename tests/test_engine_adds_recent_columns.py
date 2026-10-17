"""A store made by an older build gets the payment / return columns when the engine opens it.

The desktop engine opens an existing store without db_setup.initialise(), so the
allocations / settle_mode columns never reached an upgraded PC: the Payment panel said
"no such column: sp.allocations" (18 Oct 2026, found on a sandbox store from 12 Oct).
"""
import os
import sqlite3
import tempfile
import unittest

from core import db_setup


OLD_TABLES = [
    "CREATE TABLE customer_payments (id INTEGER PRIMARY KEY, customer_id INTEGER, amount REAL)",
    "CREATE TABLE supplier_payments (id INTEGER PRIMARY KEY, supplier_id INTEGER, amount REAL)",
    "CREATE TABLE sales_returns (id INTEGER PRIMARY KEY, refund_amount REAL)",
    "CREATE TABLE purchase_returns (id INTEGER PRIMARY KEY, refund_amount REAL)",
]


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


class EngineAddsRecentColumns(unittest.TestCase):
    def test_old_store_gets_the_columns(self):
        conn = sqlite3.connect(":memory:")
        for sql in OLD_TABLES:
            conn.execute(sql)
        db_setup.ensure_recent_columns(conn)
        db_setup.ensure_recent_columns(conn)  # a second open changes nothing
        self.assertIn("allocations", _cols(conn, "customer_payments"))
        self.assertIn("allocations", _cols(conn, "supplier_payments"))
        for t in ("sales_returns", "purchase_returns"):
            self.assertTrue({"allocations", "settle_mode"} <= _cols(conn, t), t)

    def test_the_engine_open_runs_it(self):
        from core import desktop_api

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            raw = sqlite3.connect(path)
            for sql in OLD_TABLES:
                raw.execute(sql)
            raw.commit()
            raw.close()
            conn = desktop_api._open_conn(path)
            try:
                self.assertIn("allocations", _cols(conn, "supplier_payments"))
                self.assertIn("settle_mode", _cols(conn, "purchase_returns"))
            finally:
                conn.close()
        finally:
            for p in (path, path + "-wal", path + "-shm"):
                try:
                    os.remove(p)
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main()
