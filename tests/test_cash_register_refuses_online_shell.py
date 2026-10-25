"""Cash & Expenses in Online mode: the engine's connection there is a throwaway :memory:
database, so an expense or opening typed in Online mode "saved" and was gone after the next
restart (and never reached another PC). The write routes now refuse it with a clear message;
Offline / Offline-first keep working.
"""
from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from core import cash_register as cr
from core import db_setup


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    db_setup.initialise(conn)
    cr.ensure_tables(conn)
    return conn


class CashRegisterOnlineShell(unittest.TestCase):
    def test_online_writes_are_refused_and_nothing_is_written(self):
        conn = _conn()
        with mock.patch("core.sync_prefs.is_online_mode", return_value=True):
            for path, body in (
                ("/api/cash/opening", {"date": "2026-10-25", "opening_cash": 500}),
                ("/api/cash/expense", {"date": "2026-10-25", "amount": 20, "description": "Tea"}),
                ("/api/cash/close", {"date": "2026-10-25", "counted_cash": 480}),
                ("/api/cash/expense/delete", {"id": 1}),
            ):
                status, out = cr.handle_post(path, body, conn)
                self.assertEqual(status, 409, path)
                self.assertFalse(out["ok"])
                self.assertIn("Offline-first", out["error"])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM cash_expenses").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM cash_days").fetchone()[0], 0)

    def test_offline_and_offline_first_still_save(self):
        conn = _conn()
        with mock.patch("core.sync_prefs.is_online_mode", return_value=False):
            status, out = cr.handle_post(
                "/api/cash/expense", {"date": "2026-10-25", "amount": 20, "description": "Tea"}, conn)
        self.assertEqual(status, 200)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM cash_expenses").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
