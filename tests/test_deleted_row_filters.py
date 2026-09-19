"""Deleted rows and autosave drafts must not reach user-facing money figures.

A record deleted on another device arrives here as deleted=1 rather than being
removed (soft_delete_local in core/server_entity_sync.py), and an unfinished
bill sits in `sales` with is_autosave=1. Several local-path queries counted
both, so the home dashboard's collection totals included deleted payments and
the customer-dues alert chased people over deleted or half-typed bills.

The online path already filtered these; only the local path was affected.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import alert_monitoring_service as ams  # noqa: E402
from core import db_setup, home_dashboard, sync_prefs  # noqa: E402

TODAY = date.today().strftime("%Y-%m-%d")


def _ins(conn, table, **vals):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    use = {k: v for k, v in vals.items() if k in cols}
    return conn.execute(
        f"INSERT INTO {table} ({','.join(use)}) "
        f"VALUES ({','.join('?' * len(use))})",
        list(use.values()),
    ).lastrowid


def _store(with_dead_rows):
    """A store with one real sale and payment, optionally plus dead rows."""
    conn = sqlite3.connect(tempfile.mktemp(suffix=".db"))
    conn.row_factory = sqlite3.Row
    db_setup.initialise(conn)
    cid = _ins(conn, "customers", name="C1", phone="9")
    _ins(conn, "sales", customer_id=cid, bill_no="REAL", bill_date=TODAY,
         total_amount=1000, amount_paid=400, due_amount=600)
    _ins(conn, "customer_payments", customer_id=cid, payment_date=TODAY,
         amount=500, payment_mode="Cash")
    if with_dead_rows:
        _ins(conn, "customer_payments", customer_id=cid, payment_date=TODAY,
             amount=900, payment_mode="Cash", deleted=1)
        _ins(conn, "sales", customer_id=cid, bill_no="DELBILL",
             bill_date=TODAY, total_amount=800, amount_paid=0,
             due_amount=800, deleted=1)
        _ins(conn, "sales", customer_id=cid, bill_no="DRAFT", bill_date=TODAY,
             total_amount=700, amount_paid=0, due_amount=700, is_autosave=1)
    conn.commit()
    return conn


class DeadRowsExcludedTests(unittest.TestCase):
    def setUp(self):
        self._online = sync_prefs.is_online_mode
        sync_prefs.is_online_mode = lambda *a, **k: False

    def tearDown(self):
        sync_prefs.is_online_mode = self._online

    def test_dashboard_ignores_dead_rows(self):
        """Every dashboard figure must read the same either way.

        Before the fix the deleted 900 payment pushed today's collection
        from 1,000 to 1,800.
        """
        clean = home_dashboard.query_dashboard_stats(_store(False))["values"]
        dirty = home_dashboard.query_dashboard_stats(_store(True))["values"]
        self.assertEqual(clean, dirty)

    def test_due_alerts_skip_deleted_and_draft_bills(self):
        rows = ams.fetch_customer_due_bills(_store(True))
        self.assertEqual([r[2] for r in rows], ["REAL"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
