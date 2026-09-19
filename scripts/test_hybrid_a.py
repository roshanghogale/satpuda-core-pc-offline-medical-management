"""Hybrid A smoke tests — queue, overlay, coalesce, flush routing (no live server)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class HybridAQueueTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        appdata = self._tmpdir.name

        def _appdata_dir():
            return appdata

        self.p_appdata = mock.patch(
            "core.license_manager._appdata_dir", _appdata_dir
        )
        self.p_store = mock.patch(
            "core.store_manager.get_active_store_key", return_value="TestStore"
        )
        self.p_appdata.start()
        self.p_store.start()
        self.addCleanup(self.p_appdata.stop)
        self.addCleanup(self.p_store.stop)

        # Import after patches so queue path uses temp dir
        import importlib
        import core.online_mutation_queue as q

        importlib.reload(q)
        self.q = q
        # Clear any file
        path = q._queue_path()
        if os.path.isfile(path):
            os.remove(path)

    def test_temp_id_stable(self):
        a = self.q.temp_id_from_uuid("abc-123")
        b = self.q.temp_id_from_uuid("abc-123")
        self.assertEqual(a, b)
        self.assertLess(a, 0)

    def test_enqueue_coalesce_same_uuid(self):
        with mock.patch.object(self.q, "kick_flush"):
            r1 = self.q.enqueue(
                collection="sales",
                op="upsert",
                payload={"customer_id": 1, "total_amount": 10, "medicines": []},
                client_uuid="same-uuid",
            )
            r2 = self.q.enqueue(
                collection="sales",
                op="upsert",
                payload={"customer_id": 1, "total_amount": 99, "medicines": [{"id": 1}]},
                client_uuid="same-uuid",
            )
        pending = self.q.pending_rows(collection="sales")
        self.assertEqual(len(pending), 1)
        self.assertEqual(float(pending[0]["payload"]["total_amount"]), 99)
        self.assertEqual(r1["client_uuid"], r2["client_uuid"])

    def test_overlay_and_merge_fingerprint(self):
        with mock.patch.object(self.q, "kick_flush"):
            self.q.enqueue(
                collection="sales",
                op="upsert",
                payload={
                    "customer_id": 5,
                    "customer_name": "RAM",
                    "bill_date": "2026-08-14",
                    "total_amount": 150.0,
                    "medicines": [],
                },
                client_uuid="fp-uuid",
            )
        overlays = self.q.overlay_sales_dicts()
        self.assertEqual(len(overlays), 1)
        self.assertTrue(overlays[0].get("pending"))

        # Server row without client_uuid but matching fingerprint → no overlay
        server = [
            {
                "id": 9001,
                "bill_date": "2026-08-14",
                "customer_name": "RAM",
                "total_amount": 150.0,
            }
        ]
        merged = self.q.merge_server_rows(
            server, overlays, collection="sales"
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(int(merged[0]["id"]), 9001)

    def test_peer_cancel_by_local_id(self):
        with mock.patch.object(self.q, "kick_flush"):
            row = self.q.enqueue(
                collection="sales",
                op="upsert",
                payload={"customer_id": 1, "medicines": []},
                local_id=4242,
                client_uuid="peer-cu",
            )
        self.assertEqual(len(self.q.pending_rows()), 1)
        n = self.q.cancel_matching(collection="sales", local_id=4242)
        self.assertEqual(n, 1)
        self.assertEqual(len(self.q.pending_rows()), 0)

    def test_flush_sale_create_routing(self):
        with mock.patch.object(self.q, "kick_flush"):
            self.q.enqueue(
                collection="sales",
                op="upsert",
                payload={
                    "customer_id": 7,
                    "medicines": [{"id": 1, "qty": 1, "rate": 10}],
                    "discount_pct": 0,
                    "rounding": 0,
                    "cash_paid": 10,
                    "online_paid": 0,
                    "doctor_name": "",
                    "doctor_phone": "",
                    "previous_due": 0,
                    "bill_date": "2026-08-14",
                },
                client_uuid="flush-create",
            )
        row = self.q.pending_rows()[0]
        called = {}

        def fake_save(**kwargs):
            called.update(kwargs)
            return ("SCB1", 55)

        with mock.patch(
            "core.server_crud.save_new_sale_online", side_effect=fake_save
        ):
            with mock.patch(
                "core.sync_prefs.is_online_mode", return_value=True
            ):
                self.q._flush_one(row)
        self.assertEqual(called.get("customer_id"), 7)
        self.assertEqual(called.get("client_uuid"), "flush-create")

    def test_flush_sale_update_routing(self):
        with mock.patch.object(self.q, "kick_flush"):
            self.q.enqueue(
                collection="sales",
                op="upsert",
                payload={
                    "customer_id": 7,
                    "medicines": [{"id": 1, "qty": 2, "rate": 10}],
                    "discount_pct": 0,
                    "rounding": 0,
                    "cash_paid": 20,
                    "online_paid": 0,
                    "customer_name": "A",
                    "customer_phone": "",
                    "doctor_name": "",
                    "previous_due": 0,
                },
                local_id=88,
                client_uuid="flush-upd",
            )
        row = self.q.pending_rows()[0]
        called = {}

        def fake_upd(*args, **kwargs):
            called["args"] = args
            called["kwargs"] = kwargs

        with mock.patch(
            "core.billing_service.update_existing_bill_online_now",
            side_effect=fake_upd,
        ):
            with mock.patch(
                "core.sync_prefs.is_online_mode", return_value=True
            ):
                self.q._flush_one(row)
        self.assertEqual(called["args"][0], 88)

    def test_queue_health_warn(self):
        with mock.patch.object(self.q, "kick_flush"):
            row = self.q.enqueue(
                collection="sales",
                op="upsert",
                payload={"customer_id": 1, "medicines": []},
            )
        # Backdate
        path = self.q._queue_path()
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
        rows[0]["created_at"] = time.time() - 700
        Path(path).write_text(json.dumps(rows), encoding="utf-8")
        health = self.q.queue_health()
        self.assertTrue(health["warn"])
        self.assertGreaterEqual(health["oldest_sec"], 600)

    def test_save_new_bill_online_enqueues(self):
        from core import billing_service as bs

        with mock.patch("core.sync_prefs.is_online_mode", return_value=True):
            with mock.patch(
                "core.online_catalog.find_customer_by_id",
                return_value={"id": 1, "name": "C", "total_due": 0, "total_credit": 0},
            ):
                with mock.patch.object(self.q, "kick_flush"):
                    # Patch enqueue used inside billing via import path
                    with mock.patch(
                        "core.online_mutation_queue.enqueue",
                        wraps=self.q.enqueue,
                    ) as enq:
                        bill_no, sale_id = bs.save_new_bill(
                            None,
                            1,
                            [{"id": 10, "qty": 1, "rate": 5, "amount": 5, "name": "M"}],
                            0,
                            0,
                            5,
                            0,
                            "",
                            "",
                            0,
                        )
        self.assertEqual(bill_no, "PENDING")
        self.assertLess(int(sale_id), 0)
        self.assertTrue(enq.called)

    def test_op_uuid_no_nameerror_in_source(self):
        src = (ROOT / "core" / "server_crud.py").read_text(encoding="utf-8")
        self.assertNotIn("sale.get('client_uuid', sale_id) if False", src)
        self.assertIn('f"sale:{cu}:med:{mid}:v1"', src)


class HybridACatalogTests(unittest.TestCase):
    def test_patch_docs_and_medicine_index(self):
        """A patch updates a catalog that exists; it does not become one.

        This used to seed the whole inventory from whatever a save had just
        touched, so after a bill was saved the medicine picker showed only that
        bill's medicines until the app was restarted. With no cached list a
        patch is now a no-op and the next read loads the real one
        (medicine_by_id falls back to medicines() on an index miss).
        """
        import importlib
        import time
        import core.online_catalog as cat

        importlib.reload(cat)
        with cat._lock:
            cat._cache.clear()
            cat._medicine_by_id = {}

        # Cold cache: a patch must not invent a catalog.
        cat.patch_docs(
            "medicines",
            [{"id": 101, "local_id": 101, "name": "PARA", "stock_qty": 5}],
        )
        with cat._lock:
            self.assertIsNone(cat._cache.get("medicines_inventory"))

        # Warm cache: the patch merges and the index follows it.
        shelf = [
            {"id": 101, "local_id": 101, "name": "PARA", "stock_qty": 5},
            {"id": 202, "local_id": 202, "name": "AMOXY", "stock_qty": 12},
        ]
        with cat._lock:
            cat._cache["medicines_inventory"] = (time.time(), shelf)
            cat._apply_list_indexes("medicines_inventory", shelf)
        m = cat.medicine_by_id(101)
        self.assertIsNotNone(m)
        self.assertEqual(m.get("name"), "PARA")
        cat.patch_docs(
            "medicines",
            [{"id": 101, "local_id": 101, "name": "PARA", "stock_qty": 3}],
        )
        m2 = cat.medicine_by_id(101)
        self.assertEqual(float(m2.get("stock_qty") or 0), 3.0)
        self.assertIsNotNone(
            cat.medicine_by_id(202), "the rest of the shelf survives a patch"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
