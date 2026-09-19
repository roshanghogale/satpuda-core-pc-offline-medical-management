"""Scenario tests for ConflictResolver + bootstrap / soft-delete behaviors."""
from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from core.conflict_resolver import (
    ConflictDecision,
    SyncMeta,
    meta_from_mapping,
    never_auto_delete,
    resolve,
)


def _ts(offset_sec: int = 0) -> datetime:
    return datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_sec)


class ConflictResolverTests(unittest.TestCase):
    def test_higher_version_wins(self):
        local = SyncMeta(1, _ts(0), None, "pc", False)
        remote = SyncMeta(2, _ts(-10), None, "android", False)
        self.assertEqual(
            resolve(local, remote, collection="sales", doc_id="1"),
            ConflictDecision.APPLY_REMOTE,
        )

    def test_newer_updated_at_when_version_equal(self):
        local = SyncMeta(1, _ts(5), None, "pc", False)
        remote = SyncMeta(1, _ts(1), None, "android", False)
        self.assertEqual(
            resolve(local, remote, collection="customers", doc_id="2"),
            ConflictDecision.KEEP_LOCAL,
        )

    def test_same_second_device_id_tiebreaker(self):
        t = _ts(0)
        local = SyncMeta(1, t, None, "device-z", False)
        remote = SyncMeta(1, t, None, "device-a", False)
        self.assertEqual(
            resolve(local, remote, collection="sales", doc_id="9"),
            ConflictDecision.KEEP_LOCAL,
        )
        self.assertEqual(
            resolve(remote, local, collection="customers", doc_id="9"),
            ConflictDecision.APPLY_REMOTE,
        )

    def test_identical_meta_skips(self):
        meta = SyncMeta(1, _ts(0), None, "same", False)
        self.assertEqual(
            resolve(meta, meta, collection="suppliers", doc_id="3"),
            ConflictDecision.SKIP,
        )

    def test_bills_never_auto_delete_on_absence(self):
        self.assertTrue(never_auto_delete("sales"))
        self.assertTrue(never_auto_delete("purchases"))
        self.assertFalse(never_auto_delete("customers"))

    def test_soft_delete_flag_wins_with_higher_version(self):
        local = SyncMeta(1, _ts(0), None, "pc", False)
        remote = SyncMeta(2, _ts(0), None, "pc", True)
        self.assertEqual(
            resolve(local, remote, collection="sales_returns", doc_id="4"),
            ConflictDecision.APPLY_SOFT_DELETE,
        )


class BootstrapLocalOnlyTests(unittest.TestCase):
    def test_bootstrap_push_local_only_sales(self):
        from core import server_entity_sync as fb

        conn = sqlite3.connect(":memory:")
        conn.execute(
            """
            CREATE TABLE sales (
                id INTEGER PRIMARY KEY,
                bill_no TEXT, customer_id INTEGER, bill_date TEXT,
                total_amount REAL, discount REAL, discount_pct REAL, rounding REAL,
                amount_paid REAL, cash_paid REAL, online_paid REAL,
                previous_due REAL, previous_credit REAL, due_amount REAL,
                credit_amount REAL, total_due REAL, paid_due REAL,
                bill_cleared INTEGER, account_cleared INTEGER,
                doctor_name TEXT, created_at TEXT,
                updated_at TEXT, version INTEGER DEFAULT 1,
                device_id TEXT, deleted INTEGER DEFAULT 0, sync_status TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE sales_items (
                id INTEGER PRIMARY KEY, sale_id INTEGER, medicine_id INTEGER,
                qty INTEGER, rate REAL, gst_percent REAL, amount REAL,
                item_discount REAL, cost_price REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE customers (
                id INTEGER PRIMARY KEY, name TEXT, phone TEXT, address TEXT,
                total_due REAL, total_credit REAL, created_at TEXT,
                updated_at TEXT, version INTEGER DEFAULT 1,
                device_id TEXT, deleted INTEGER DEFAULT 0, sync_status TEXT
            )
            """
        )
        # The bootstrap doc builder joins medicines, so the fixture needs that
        # table with the columns it reads (m.type among them).
        conn.execute(
            """
            CREATE TABLE medicines (
                id INTEGER PRIMARY KEY, name TEXT, batch_no TEXT, expiry_date TEXT,
                stock_qty REAL, mrp REAL, rate REAL, gst_percent REAL, hsn_code TEXT,
                content_drug TEXT, manufacturer TEXT, schedule TEXT, type TEXT,
                location TEXT, is_hidden INTEGER DEFAULT 0, created_at TEXT,
                updated_at TEXT, version INTEGER DEFAULT 1, device_id TEXT,
                deleted INTEGER DEFAULT 0, sync_status TEXT, client_uuid TEXT
            )
            """
        )
        conn.execute("INSERT INTO customers (id, name) VALUES (1, 'Offline Cust')")
        conn.execute(
            """
            INSERT INTO sales (
                id, bill_no, customer_id, bill_date, total_amount,
                discount, discount_pct, rounding, amount_paid, cash_paid, online_paid,
                previous_due, previous_credit, due_amount, credit_amount, total_due,
                paid_due, bill_cleared, account_cleared, created_at, updated_at, version, device_id
            ) VALUES (
                101, 'OFF-1', 1, '2026-07-12', 100,
                0, 0, 0, 100, 100, 0,
                0, 0, 0, 0, 0,
                0, 1, 0, '2026-07-12T10:00:00+00:00', '2026-07-12T10:00:00+00:00', 1, 'pc'
            )
            """
        )
        conn.commit()
        # Bootstrap pushes go through push_docs_batch, not push_sale.
        pushed = []

        def fake_batch(writes, timeout=None):
            pushed.extend(writes)
            return True

        with mock.patch.object(fb, "push_docs_batch", side_effect=fake_batch):
            n = fb._bootstrap_push_local_only_and_pending(
                conn,
                seen_ids={"sales": set()},
                pending_push=[],
            )
        self.assertGreaterEqual(n, 1)
        sale_ids = [str(doc_id) for col, doc_id, _ in pushed if col == "sales"]
        self.assertIn("101", sale_ids)

    def test_conflict_keeps_local_newer_sale_during_pull(self):
        from core import server_entity_sync as fb

        conn = sqlite3.connect(":memory:")
        conn.execute(
            """
            CREATE TABLE sales (
                id INTEGER PRIMARY KEY, bill_no TEXT, customer_id INTEGER,
                bill_date TEXT, total_amount REAL DEFAULT 0,
                updated_at TEXT, version INTEGER DEFAULT 1,
                device_id TEXT, deleted INTEGER DEFAULT 0, created_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO sales (id, bill_no, customer_id, bill_date, total_amount,
                               updated_at, version, device_id)
            VALUES (7, 'LOCAL', 1, '2026-07-12', 50,
                    '2026-07-12T12:00:05+00:00', 2, 'pc')
            """
        )
        conn.commit()
        cloud = {
            "bill_no": "CLOUD-OLD",
            "updated_at": "2026-07-12T11:00:00+00:00",
            "version": 1,
            "device_id": "android",
            "total_amount": 1,
            "customer_id": 1,
            "items": [],
        }
        decision = fb._resolve_pull_decision(conn, "sales", "7", cloud)
        self.assertEqual(decision, ConflictDecision.KEEP_LOCAL)


class MedicineDualEditTests(unittest.TestCase):
    def test_different_medicines_both_survive_via_independent_meta(self):
        a = meta_from_mapping(
            {"version": 2, "updated_at": "2026-07-12T12:00:00+00:00", "device_id": "pc"}
        )
        b = meta_from_mapping(
            {"version": 2, "updated_at": "2026-07-12T12:00:01+00:00", "device_id": "android"}
        )
        self.assertEqual(
            resolve(a, b, collection="medicines", doc_id="10"),
            ConflictDecision.APPLY_REMOTE,
        )
        self.assertEqual(
            resolve(b, a, collection="medicines", doc_id="11"),
            ConflictDecision.KEEP_LOCAL,
        )


class AndroidSinglePushContractTests(unittest.TestCase):
    def test_purchase_viewmodel_does_not_double_call_after_purchase_saved(self):
        root = Path(__file__).resolve().parents[1]
        vm = root.parent.parent / "Satpuda Core (2)" / "Satpuda Core" / (
            "app/src/main/java/com/selling/satpudacore/screens/purchase/PurchaseViewModel.kt"
        )
        if not vm.is_file():
            self.skipTest(f"Android ViewModel not found at {vm}")
        text = vm.read_text(encoding="utf-8")
        self.assertNotIn(
            "afterPurchaseSaved(",
            text,
            "PurchaseViewModel must not call afterPurchaseSaved (double-push)",
        )

    def test_billing_service_syncs_sale(self):
        root = Path(__file__).resolve().parents[1]
        billing = root.parent.parent / "Satpuda Core (2)" / "Satpuda Core" / (
            "app/src/main/java/com/selling/satpudacore/core/services/BillingService.kt"
        )
        if not billing.is_file():
            self.skipTest("BillingService not found")
        bt = billing.read_text(encoding="utf-8")
        self.assertIn("ServerSyncHelper.syncSale", bt)


class ReturnDeleteWireTests(unittest.TestCase):
    def test_sales_return_page_has_delete_history_hook(self):
        path = Path(__file__).resolve().parents[1] / "ui" / "returns" / "sales_return.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("after_sales_return_deleted", text)
        self.assertIn("_delete_history_return", text)

    def test_purchase_return_page_has_delete_history_hook(self):
        path = Path(__file__).resolve().parents[1] / "ui" / "returns" / "purchase_return.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("after_purchase_return_deleted", text)
        self.assertIn("_delete_history_return", text)




class SyncGapCloseTests(unittest.TestCase):
    def test_bump_row_meta_increments_version(self):
        from core.sync_meta_bump import bump_row_meta

        conn = sqlite3.connect(":memory:")
        conn.execute(
            """CREATE TABLE sales (
                id INTEGER PRIMARY KEY,
                bill_no TEXT,
                updated_at TEXT,
                version INTEGER DEFAULT 1,
                device_id TEXT,
                sync_status TEXT,
                deleted INTEGER DEFAULT 0
            )"""
        )
        conn.execute(
            "INSERT INTO sales (id, bill_no, updated_at, version, device_id, sync_status) "
            "VALUES (1, 'SCB1', '2026-07-01T00:00:00+00:00', 1, 'pc', 'synced')"
        )
        conn.commit()
        bump_row_meta(conn, "sales", 1)
        row = conn.execute(
            "SELECT version, sync_status, updated_at FROM sales WHERE id=1"
        ).fetchone()
        self.assertEqual(row[0], 2)
        self.assertEqual(row[1], "pending")
        self.assertTrue(row[2])
        bump_row_meta(conn, "sales", 1)
        self.assertEqual(
            conn.execute("SELECT version FROM sales WHERE id=1").fetchone()[0],
            3,
        )

    def test_soft_deleted_sale_resolves_apply_soft_delete(self):
        local = SyncMeta(1, _ts(0), None, "pc", False)
        remote = SyncMeta(2, _ts(0), None, "android", True)
        self.assertEqual(
            resolve(local, remote, collection="sales", doc_id="55"),
            ConflictDecision.APPLY_SOFT_DELETE,
        )

    def test_desktop_sale_delete_hard_wires(self):
        root = Path(__file__).resolve().parents[1]
        actions = (root / "ui" / "sales" / "sales_history_actions.py").read_text(encoding="utf-8")
        coord = (root / "core" / "sync_coordinator.py").read_text(encoding="utf-8")
        self.assertIn("after_sale_deleted", actions)
        self.assertIn("DELETE FROM sales", actions)
        self.assertIn("delete_remote", coord)
        self.assertIn("def after_sale_deleted", coord)

if __name__ == "__main__":
    unittest.main()
