"""Live-sync gate verification for Hybrid A (plan todo live-sync-gate).

Simulates reconnect flush, stock_ops once (stable op_uuid), catalog during drop,
and peer-wins cancel — without requiring Android UI or mutating production bills.
"""
from __future__ import annotations

import importlib
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


class LiveSyncGateTests(unittest.TestCase):
    """Plan verification §1–6 (Android peer = cancel_matching simulation)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        appdata = self._tmpdir.name

        self.p_appdata = mock.patch(
            "core.license_manager._appdata_dir", lambda: appdata
        )
        self.p_store = mock.patch(
            "core.store_manager.get_active_store_key",
            return_value="GateStore",
        )
        self.p_appdata.start()
        self.p_store.start()
        self.addCleanup(self.p_appdata.stop)
        self.addCleanup(self.p_store.stop)

        import core.online_mutation_queue as q
        import core.online_catalog as cat

        importlib.reload(q)
        importlib.reload(cat)
        self.q = q
        self.cat = cat
        path = q._queue_path()
        if os.path.isfile(path):
            os.remove(path)
        with cat._lock:
            cat._cache.clear()
            cat._medicine_by_id = {}
            cat._customer_index = {}

    # ── 1. Instant save + overlay ─────────────────────────────────────────

    def test_01_sale_save_under_200ms_and_overlay(self):
        from core import billing_service as bs

        t0 = time.perf_counter()
        with mock.patch("core.sync_prefs.is_online_mode", return_value=True):
            with mock.patch(
                "core.online_catalog.find_customer_by_id",
                return_value={
                    "id": 1,
                    "name": "GATE CUST",
                    "total_due": 0,
                    "total_credit": 0,
                },
            ):
                with mock.patch.object(self.q, "kick_flush"):
                    bill_no, sale_id = bs.save_new_bill(
                        None,
                        1,
                        [
                            {
                                "id": 10,
                                "qty": 2,
                                "rate": 50,
                                "amount": 100,
                                "name": "GATE MED",
                            }
                        ],
                        0,
                        0,
                        100,
                        0,
                        "",
                        "",
                        0,
                        bill_date="2026-08-14",
                    )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        self.assertEqual(bill_no, "PENDING")
        self.assertLess(sale_id, 0)
        self.assertLess(
            elapsed_ms,
            200,
            f"save_new_bill took {elapsed_ms:.1f}ms (want <200ms)",
        )
        overlays = self.q.overlay_sales_dicts()
        self.assertEqual(len(overlays), 1)
        self.assertTrue(overlays[0].get("pending"))
        self.assertEqual(overlays[0].get("bill_no"), "PENDING")

    # ── 2–3. Reconnect flush + op_uuid once on retry ──────────────────────

    def test_02_reconnect_flush_stock_ops_once(self):
        """Enqueue while unreachable → kick_flush when reachable → one push."""
        with mock.patch.object(self.q, "kick_flush"):
            row = self.q.enqueue(
                collection="sales",
                op="upsert",
                payload={
                    "customer_id": 1,
                    "medicines": [
                        {"id": 10, "qty": 1, "rate": 20, "amount": 20, "name": "M"}
                    ],
                    "discount_pct": 0,
                    "rounding": 0,
                    "cash_paid": 20,
                    "online_paid": 0,
                    "doctor_name": "",
                    "doctor_phone": "",
                    "previous_due": 0,
                    "bill_date": "2026-08-14",
                    "customer_name": "GATE CUST",
                    "total_amount": 20,
                },
                client_uuid="gate-reconnect-uuid",
            )

        push_calls = []

        def fake_save(**kwargs):
            push_calls.append(kwargs)
            return ("SCB999", 999)

        # Simulate connectivity restore → flush loop body
        with mock.patch("core.sync_prefs.is_online_mode", return_value=True):
            with mock.patch(
                "core.online_guard.is_cloud_reachable", return_value=True
            ):
                with mock.patch(
                    "core.server_crud.save_new_sale_online",
                    side_effect=fake_save,
                ):
                    self.q._flush_one(row)
                    self.q._mark(str(row["id"]), self.q._STATUS_DONE)

        self.assertEqual(len(push_calls), 1)
        self.assertEqual(push_calls[0].get("client_uuid"), "gate-reconnect-uuid")
        self.assertEqual(len(self.q.pending_rows()), 0)

    def test_03_stable_op_uuid_same_on_double_flush_payload(self):
        """Same client_uuid + medicine → identical op_uuid (server apply-once)."""
        from core.server_crud import save_new_sale_online

        captured = []

        def capture_push(bundle):
            captured.append(bundle)
            return {"ok": True}

        kwargs = dict(
            customer_id=1,
            medicines=[{"id": 10, "qty": 3, "rate": 10, "amount": 30, "name": "M"}],
            discount_pct=0,
            rounding=0,
            cash_paid=30,
            online_paid=0,
            doctor_name="",
            doctor_phone="",
            previous_due=0,
            bill_date="2026-08-14",
            client_uuid="stable-op-uuid-1",
        )
        med = {
            "id": 10,
            "local_id": 10,
            "name": "M",
            "stock_qty": 100,
            "version": 1,
        }
        cust = {"id": 1, "name": "C", "total_due": 0, "total_credit": 0}
        fy = {
            "fy_start_year": 2025,
            "fy_serial": 1,
            "bill_no": "SCB1/FY25-26",
        }

        with mock.patch("core.online_guard.ensure_can_mutate"):
            with mock.patch("core.server_crud._token", return_value="tok"):
                with mock.patch("core.server_api.allocate_fy", return_value=fy):
                    with mock.patch(
                        "core.server_crud.allocate_ids_map",
                        return_value={"sales": 501},
                    ):
                        with mock.patch(
                            "core.server_crud.allocate_id", return_value=501
                        ):
                            with mock.patch(
                                "core.server_crud.get_doc", return_value=dict(med)
                            ):
                                with mock.patch(
                                    "core.online_catalog.find_customer_by_id",
                                    return_value=cust,
                                ):
                                    with mock.patch(
                                        "core.online_catalog.medicine_by_id",
                                        return_value=dict(med),
                                    ):
                                        with mock.patch(
                                            "core.online_catalog.patch_docs"
                                        ):
                                            with mock.patch(
                                                "core.server_crud.push_bundle",
                                                side_effect=capture_push,
                                            ):
                                                save_new_sale_online(**kwargs)
                                                save_new_sale_online(**kwargs)

        self.assertEqual(len(captured), 2)
        ops1 = captured[0]["medicines"][0]["stock_ops"][0]["op_uuid"]
        ops2 = captured[1]["medicines"][0]["stock_ops"][0]["op_uuid"]
        self.assertEqual(ops1, ops2)
        self.assertEqual(ops1, "sale:stable-op-uuid-1:med:10:v1")

    # ── 4. Dropdowns during drop from snapshot ────────────────────────────

    def test_04_catalog_snapshot_during_drop(self):
        # Seed the catalog directly. This used to go through patch_docs, which
        # would happily build a whole catalog out of one row -- the defect that
        # made the medicine picker show only the medicines from the bill that
        # had just been saved. patch_docs now merges into a list that exists and
        # never invents one, so a test about SNAPSHOTS seeds the cache itself.
        import time as _time

        with self.cat._lock:
            meds_seed = [
                {"id": 77, "local_id": 77, "name": "CACHED MED", "stock_qty": 12}
            ]
            cust_seed = [
                {
                    "id": 3,
                    "local_id": 3,
                    "name": "CACHED CUST",
                    "total_due": 0,
                    "total_credit": 0,
                }
            ]
            self.cat._cache["medicines_inventory"] = (_time.time(), meds_seed)
            self.cat._cache["customers"] = (_time.time(), cust_seed)
            self.cat._apply_list_indexes("medicines_inventory", meds_seed)
            self.cat._apply_list_indexes("customers", cust_seed)
        # Force persist + clear memory, then load via snapshot path
        with self.cat._lock:
            # Ensure cache keys match snapshot keys
            meds = self.cat._cache.get("medicines_inventory")
            custs = self.cat._cache.get("customers")
            self.assertIsNotNone(meds)
            self.assertIsNotNone(custs)
        self.cat._persist_snapshot()
        with self.cat._lock:
            self.cat._cache.clear()
            self.cat._medicine_by_id = {}

        # Loader fails (network drop) → snapshot used
        def boom():
            raise RuntimeError("no network")

        with mock.patch.object(
            self.cat,
            "_cached_list",
            wraps=self.cat._cached_list,
        ):
            # Directly exercise snapshot load
            snap_meds = self.cat._load_snapshot_key("medicines_inventory")
            snap_cust = self.cat._load_snapshot_key("customers")
        self.assertTrue(any(m.get("name") == "CACHED MED" for m in snap_meds))
        self.assertTrue(any(c.get("name") == "CACHED CUST" for c in snap_cust))

        # _cached_list falls back to snapshot on loader error
        rows = self.cat._cached_list("medicines_inventory", boom, force=True)
        self.assertTrue(any(m.get("name") == "CACHED MED" for m in rows))

    # ── 5–6. Peer hint cancel (Android peer simulated) ────────────────────

    def test_05_peer_edit_cancels_matching_pending(self):
        with mock.patch.object(self.q, "kick_flush"):
            self.q.enqueue(
                collection="sales",
                op="upsert",
                payload={"customer_id": 1, "medicines": []},
                local_id=5555,
                client_uuid="peer-bill-x",
            )
        self.assertEqual(len(self.q.pending_rows()), 1)
        # Simulate sync_engine peer hint for same local_id
        n = self.q.cancel_matching(collection="sales", local_id=5555)
        self.assertEqual(n, 1)
        self.assertEqual(len(self.q.pending_rows()), 0)

    def test_06_peer_client_uuid_cancels_stale_create(self):
        with mock.patch.object(self.q, "kick_flush"):
            self.q.enqueue(
                collection="sales",
                op="upsert",
                payload={"customer_id": 1, "medicines": []},
                client_uuid="stale-create-uuid",
            )
        n = self.q.cancel_matching(client_uuid="stale-create-uuid")
        self.assertEqual(n, 1)
        self.assertEqual(len(self.q.pending_rows()), 0)

    def test_07_due_paid_fields_in_enqueue_payload(self):
        from core import billing_service as bs

        with mock.patch("core.sync_prefs.is_online_mode", return_value=True):
            with mock.patch(
                "core.online_catalog.find_customer_by_id",
                return_value={
                    "id": 1,
                    "name": "DUE CUST",
                    "total_due": 50,
                    "total_credit": 0,
                },
            ):
                with mock.patch.object(self.q, "kick_flush"):
                    bs.save_new_bill(
                        None,
                        1,
                        [
                            {
                                "id": 10,
                                "qty": 1,
                                "rate": 100,
                                "amount": 100,
                                "name": "M",
                            }
                        ],
                        0,
                        0,
                        0,  # unpaid → due
                        0,
                        "",
                        "",
                        50,
                    )
        p = self.q.pending_rows()[0]["payload"]
        self.assertGreater(float(p.get("due_amount") or 0), 0)
        self.assertEqual(int(p.get("bill_cleared") or 0), 0)

    def test_08_sync_engine_peer_cancel_wired(self):
        src = (ROOT / "core" / "sync_engine.py").read_text(encoding="utf-8")
        self.assertIn("cancel_matching", src)
        self.assertIn("peer cancel_matching", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
