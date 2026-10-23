"""Online -> Offline / offline-first: the store copy is fast, complete and the same as before.

Owner, 22 Oct 2026: Jagdamba (61,561 medicines) sat on "checking for correction of the
stock" for hours. The stock check paged the medicine list with after_id alone, which the
server answers with the FIRST page again -- on any shop with more than 5,000 medicines it
never ended. The copy itself took minutes more: plain (not gzip) JSON, each list downloaded
whole before a record was written, the table's columns looked up for every record.

These run the real download_store_for_offline against a local stand-in for the server
(tests/fake_sync_server.py) with pages of 7, so every list is paged.
"""
from __future__ import annotations

import contextlib
import gzip
import http.server
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fake_sync_server  # noqa: E402
import store_copy_compare  # noqa: E402

PAGE = 7


def _ts(i: int) -> str:
    return f"2026-10-{1 + (i // 3600) % 20:02d}T{(i // 60) % 24:02d}:{i % 60:02d}:{(i * 7) % 60:02d}.{(i * 37) % 1000:03d}Z"


def store_data() -> dict:
    """A small shop with every kind of record the copy writes."""
    meds = []
    for i in range(1, 41):
        d = {
            "id": i, "name": f"MED {i:02d}", "type": "Tablet" if i % 3 else "Syrup",
            "unit": "10" if i % 3 else "100 ml", "stock_qty": (i * 13) % 50, "gst_percent": 12,
            "mrp": 10.0 + i, "rate": 8.0 + i, "manufacturer": "ACME", "batch_no": f"B{i}",
            "expiry_date": "2027-06-30", "hsn_code": "3004", "schedule": "H" if i % 5 == 0 else "",
            "location": "", "content_drug": "", "is_hidden": i in (3, 5, 30), "deleted": i == 7,
            "version": 1 + i % 3, "device_id": "pc-1", "created_at": "2026-01-01T00:00:00.000Z",
            "updated_at": "2026-09-01T10:00:00.000Z" if i <= 20 else _ts(1000 + i),
            "synced_at": None, "client_uuid": (f"u-{i}" if i % 4 else None),
        }
        if i in (11, 12):
            d["client_uuid"] = "same-uuid"
        if i % 6 == 0:
            d.update({"purchase_rate": 7.5, "rate_a": 9.0, "rate_b": 9.5, "rate_c": 9.9})
        meds.append(d)
    customers = [{"id": i, "name": f"CUST {i}", "phone": f"98{i:08d}", "address": "Sonala",
                  "total_due": float(i % 3) * 10, "total_credit": 0, "created_at": _ts(i),
                  "updated_at": _ts(2000 + i), "version": 1, "device_id": "pc-1", "deleted": False}
                 for i in range(1, 11)]
    suppliers = [{"id": i, "name": f"SUP {i}", "phone": "", "address": "", "gstin": "",
                  "total_due": 100.0 * i, "total_credit": 0, "updated_at": _ts(3000 + i), "version": 1,
                  "device_id": "pc-1", "deleted": False} for i in range(1, 4)]
    doctors = [{"id": i, "name": f"DR {i}", "phone": "", "registration_number": "",
                "updated_at": _ts(3100 + i), "version": 1, "deleted": False} for i in range(1, 3)]
    sales = []
    for i in range(1, 16):
        items = []
        for k in range(1 + i % 3):
            mid = 99 if (i == 6 and k == 0) else (7 if (i == 8 and k == 0) else 1 + (i * 5 + k * 11) % 40)
            items.append({"medicine_id": mid, "name": f"MED {mid}", "type": "Tablet", "batch_no": f"B{mid}",
                          "expiry_date": "2027-06-30", "hsn_code": "3004", "schedule": "", "manufacturer": "ACME",
                          "qty": 2 + k, "rate": 10.0, "gst_percent": 12, "amount": 20.0 + k, "item_discount": 0,
                          "cost_price": 8.0})
        total = sum(it["amount"] for it in items)
        sales.append({
            "id": i, "bill_no": f"SCB{i}", "customer_id": 1 + i % 10, "bill_date": "2026-10-0%d" % (1 + i % 9),
            "total_amount": total, "discount": 0, "discount_pct": 0, "rounding": 0,
            "amount_paid": total if i % 4 else 0, "cash_paid": total if i % 4 else 0, "online_paid": 0,
            "previous_due": 0, "previous_credit": 0, "due_amount": 0 if i % 4 else total,
            "credit_amount": 0, "total_due": 0 if i % 4 else total, "paid_due": 0,
            "bill_cleared": bool(i % 4), "account_cleared": bool(i % 4), "doctor_name": "DR 1",
            "is_autosave": 1 if i == 5 else 0, "fy_start_year": 2026, "fy_serial": i if i % 2 else None,
            "customer_name": f"CUST {1 + i % 10}", "created_at": _ts(i), "updated_at": _ts(4000 + i),
            "version": 1, "device_id": "pc-1", "deleted": i == 4, "items": items,
            "client_uuid": f"s-{i}",
        })
    purchases = []
    for i in range(1, 9):
        items = [{"medicine_id": 98 if (i == 2 and k == 0) else 1 + (i * 3 + k * 7) % 40, "name": "X",
                  "type": "Tablet" if k % 2 == 0 else "Syrup", "qty": 3 + k, "free_qty": k % 2,
                  "rate": 5.0, "mrp": 12.0, "gst_pct": 12, "batch_no": "PB", "expiry_date": "2027-01-31",
                  "item_amount": 15.0, "hsn_code": "3004", "discount_pct": 0, "taxable": 15.0,
                  "gst_amt": 1.8, "unit": "10", "rate_a": 6.0}
                 for k in range(2)]
        purchases.append({
            "id": i, "purchase_no": f"P{i}", "supplier_id": 1 + i % 3, "purchase_date": "2026-09-1%d" % (i % 9),
            "bill_number": f"INV{i}", "subtotal": 30, "total_gst": 3.6, "cgst": 1.8, "sgst": 1.8,
            "total_amount": 33.6, "final_amount": 33.6, "amount_paid": 0 if i % 3 == 0 else 33.6,
            "amount_paid_at_entry": 0, "cash_paid_at_entry": 0, "online_paid_at_entry": 0,
            "overall_discount": 0, "rounding": 0, "need_to_pay": 33.6, "previous_due": 0,
            "previous_credit": 0, "due": 33.6 if i % 3 == 0 else 0, "due_amount": 33.6 if i % 3 == 0 else 0,
            "current_credit": 0, "credit_amount": 0, "total_due": 33.6 if i % 3 == 0 else 0, "paid_due": 0,
            "bill_cleared": i % 3 != 0, "account_cleared": i % 3 != 0, "expenditure": 0,
            "fy_start_year": 2026, "fy_serial": i, "created_at": _ts(i), "updated_at": _ts(5000 + i),
            "version": 1, "device_id": "pc-1", "deleted": False, "items": items,
        })
    sales_returns = [{"id": i, "return_no": f"SR{i}", "sale_id": i, "customer_id": 1 + i % 10,
                      "return_date": "2026-10-05", "refund_amount": 10.0, "discount": 0, "reason": "",
                      "settle_mode": "ledger" if i == 1 else "cash", "allocations": None,
                      "created_at": _ts(i), "updated_at": _ts(6000 + i), "version": 1, "deleted": False,
                      "items": [{"medicine_id": 1 + i, "qty": 1, "rate": 10.0, "amount": 10.0}]}
                     for i in (1, 2)]
    purchase_returns = [{"id": i, "return_no": f"PR{i}", "purchase_id": i, "supplier_id": 1,
                         "return_date": "2026-10-06", "refund_amount": 5.0, "discount": 0,
                         "reason": "Expired" if i == 1 else "Damaged", "created_at": _ts(i),
                         "updated_at": _ts(6100 + i), "version": 1, "deleted": False,
                         "items": [{"medicine_id": 20 + i, "qty": 1, "rate": 5.0, "amount": 5.0}]}
                        for i in (1, 2)]
    cpay = [{"id": i, "customer_id": i, "payment_date": "2026-10-07", "amount": 15.0, "payment_mode": "cash",
             "cash_amount": 15.0, "online_amount": 0, "reference_no": "", "note": "",
             "allocations": [{"sale_id": i, "amount": 15.0}], "created_at": _ts(i),
             "updated_at": _ts(7000 + i), "version": 1, "deleted": False} for i in range(1, 4)]
    spay = [{"id": i, "payment_no": f"SP{i}", "supplier_id": i, "payment_date": "2026-10-08", "amount": 20.0,
             "mode": "Cash", "reference": "", "due_before": 100, "due_after": 80, "allocations": None,
             "created_at": _ts(i), "updated_at": _ts(7100 + i), "version": 1, "deleted": False}
            for i in range(1, 3)]
    return {
        "medicines": meds, "customers": customers, "suppliers": suppliers, "doctors": doctors,
        "sales": sales, "purchases": purchases, "sales_returns": sales_returns,
        "purchase_returns": purchase_returns, "customer_payments": cpay, "supplier_payments": spay,
        "racks": [{"id": 1, "name": "R1", "updated_at": _ts(8001), "deleted": False}],
        "sections": [{"id": 1, "rack_id": 1, "name": "S1", "updated_at": _ts(8002), "deleted": False}],
        "boxes": [{"id": 1, "section_id": 1, "name": "BX1", "updated_at": _ts(8003), "deleted": False}],
        "_settings_kv": [{"name": "regular_meds:1", "value": "[1, 2]"},
                         {"name": "customer_gst:2", "value": "27ABCDE1234F1Z5"}],
    }


class _Copy(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="fastcopy_")
        self.addCleanup(shutil.rmtree, self.d, True)

    @contextlib.contextmanager
    def server(self, srv, key="Store_Fast_Copy_Test"):
        cfg = os.path.join(self.d, "cfg")
        os.makedirs(cfg, exist_ok=True)
        patches = [
            mock.patch("core.license_manager._appdata_dir", lambda: cfg),
            mock.patch("core.server_api.pull_collection", srv.pull_collection),
            mock.patch("core.server_api._request", srv.request),
            mock.patch("core.sync_prefs.get_sync_mode", lambda: "online"),
            mock.patch("core.sync_prefs.is_online_mode", lambda: True),
            mock.patch("core.store_manager.get_active_store_key", lambda: key),
            mock.patch("core.server_live._token", lambda *a, **k: "t"),
            mock.patch("core.server_crud._token", lambda *a, **k: "t"),
            mock.patch("core.server_api.store_token_for_active", lambda force_pair=False: "t"),
            mock.patch("core.server_live.start_poller", lambda *a, **k: None),
            mock.patch("core.server_live._PULL_PAGE", PAGE),
        ]
        for p in patches:
            p.start()
        try:
            yield
        finally:
            for p in reversed(patches):
                p.stop()

    def copy(self, name, *, fast=True, data=None, srv=None, progress=None):
        from core import online_migrate as om

        srv = srv or fake_sync_server.FakeServer(data or store_data())
        path = os.path.join(self.d, name)
        with self.server(srv):
            res = om.download_store_for_offline(db_path=path, fast_copy=fast, progress_cb=progress)
        return path, res, srv


class FastCopyIsTheSameCopy(_Copy):
    def test_fast_and_old_copies_hold_the_same_rows_and_values(self):
        old, r1, _ = self.copy("old.db", fast=False)
        new, r2, _ = self.copy("new.db", fast=True)
        self.assertTrue(r1["ok"], r1)
        self.assertTrue(r2["ok"], r2)
        self.assertEqual(r1["rows"], r2["rows"])
        self.assertEqual([], store_copy_compare.compare(old, new, run_started="2026-10-22T00:00:00"))
        counts = store_copy_compare.summary(new)
        # 39 live + the stubs bills name (98, 99) + no. 7: a bill names it, the server deleted it
        self.assertEqual(42, counts["medicines"])
        self.assertEqual(14, counts["sales"])               # the deleted bill is not copied
        self.assertEqual(8, counts["purchases"])

    def test_stock_and_hidden_are_the_servers(self):
        path, res, _ = self.copy("s.db")
        self.assertTrue(res["ok"], res)
        c = sqlite3.connect(path)
        server = {d["id"]: d for d in store_data()["medicines"] if not d["deleted"]}
        got = dict((r[0], (r[1], r[2])) for r in c.execute("SELECT id, stock_qty, is_hidden FROM medicines"))
        for mid, d in server.items():
            self.assertEqual((d["stock_qty"], 1 if d["is_hidden"] else 0), got[mid], mid)
        c.close()

    def test_the_medicine_list_is_downloaded_once(self):
        _, res, srv = self.copy("o.db")
        self.assertTrue(res["ok"], res)
        live = [k for k in srv.calls if k[0] == "medicines" and k[3] is False]
        self.assertEqual([], live)  # no second pass for the stock


class PagingAlwaysMovesOn(_Copy):
    def test_old_stock_check_ends_on_a_list_longer_than_a_page(self):
        """The loop that never ended: after_id alone gave the first page back forever."""
        from core import online_migrate as om

        srv = fake_sync_server.FakeServer(store_data())
        path = os.path.join(self.d, "r.db")
        c = sqlite3.connect(path)
        c.execute("CREATE TABLE medicines (id INTEGER PRIMARY KEY, stock_qty INTEGER, is_hidden INTEGER)")
        c.executemany("INSERT INTO medicines VALUES (?, 0, 0)", [(i,) for i in range(1, 41)])
        with self.server(srv):
            fixed = om._restore_server_stock(c)
        self.assertEqual(39, fixed)
        self.assertEqual(13, c.execute("SELECT stock_qty FROM medicines WHERE id=1").fetchone()[0])
        c.close()

    def test_server_count_pages_by_time_and_id(self):
        from core import online_migrate as om

        srv = fake_sync_server.FakeServer(store_data())
        with self.server(srv):
            self.assertEqual(39, om._server_collection_count("t", "medicines"))

    def _bulk_stamped(self):
        data = store_data()
        for d in data["medicines"]:  # one bulk statement: same microsecond, sent to the millisecond
            d["updated_at"] = "2026-10-22T15:08:51.256Z"
            d["_us"] = "2026-10-22T15:08:51.256972Z"
        return data

    def test_a_list_the_server_cannot_page_stops_the_copy_instead_of_cutting_it_short(self):
        from core.server_live import PagingStuck

        with self.assertRaises(PagingStuck) as cm:
            self.copy("p.db", data=self._bulk_stamped())
        self.assertIn("medicines list could not be downloaded in full", str(cm.exception))
        from core import online_migrate as om

        self.assertFalse(om.has_complete_mirror(os.path.join(self.d, "p.db")))

    def test_the_old_way_also_stops_instead_of_cutting_it_short(self):
        from core.server_live import PagingStuck

        with self.assertRaises(PagingStuck):
            self.copy("q.db", data=self._bulk_stamped(), fast=False)

    def test_a_server_that_sends_the_full_stamp_pages_through_it(self):
        srv = fake_sync_server.FakeServer(self._bulk_stamped(), sync_cursor=True)
        path, res, _ = self.copy("ok.db", srv=srv)
        self.assertTrue(res["ok"], res)
        self.assertEqual(42, store_copy_compare.summary(path)["medicines"])  # as in the test above

    def test_a_short_copy_is_refused(self):
        data = store_data()
        srv = fake_sync_server.FakeServer(data)
        real = srv.request

        def more_on_the_server(method, path, **kw):
            res = real(method, path, **kw)
            if path.startswith("/api/store/inventory/counts"):
                res["data"]["all"] = 500
            return res

        srv.request = more_on_the_server
        with self.assertRaises(RuntimeError) as cm:
            self.copy("short.db", srv=srv)
        self.assertIn("of the server's 500 medicines arrived", str(cm.exception))


class CopyWhateverTheMode(_Copy):
    """The copy writes the server's records whatever this PC's sync mode is: the Retail |
    Wholesale switch copies the other store while the PC is already offline-first, and every
    record used to be thrown away ("skipped_offline"; Shree Gajanan, 23 Oct 2026)."""

    def offline_first_copy(self, name, *, fast=True, srv=None):
        with mock.patch("core.sync_prefs.is_online_mode", lambda: False),                 mock.patch("core.sync_prefs.get_sync_mode", lambda: "offline_first"):
            return self.copy(name, fast=fast, srv=srv)

    def test_an_offline_first_pc_gets_the_whole_copy(self):
        online, r1, _ = self.copy("online.db")
        for fast in (True, False):
            path, res, _ = self.offline_first_copy(f"of-{fast}.db", fast=fast)
            self.assertTrue(res["ok"], res)
            counts = store_copy_compare.summary(path)
            self.assertEqual((42, 14, 8), (counts["medicines"], counts["sales"], counts["purchases"]))
            self.assertEqual([], store_copy_compare.compare(online, path, run_started="2026-10-22T00:00:00"))

    def test_the_mode_check_still_holds_outside_the_copy(self):
        from core import server_entity_sync as fb

        with mock.patch("core.sync_prefs.is_online_mode", lambda: False):
            self.assertEqual("skipped_offline", fb.sync_down_doc(None, "medicines", "1", {"id": 1}))

    def test_a_list_that_arrives_empty_is_refused(self):
        srv = fake_sync_server.FakeServer(store_data())
        real = srv.pull_collection

        def no_bills(token, collection, **kw):
            return ([], {}) if collection == "sales" else real(token, collection, **kw)

        srv.pull_collection = no_bills
        with self.assertRaises(RuntimeError) as cm:
            self.copy("nobills.db", srv=srv)
        self.assertIn("None of the server's 14 bills arrived", str(cm.exception))


class RealProgress(_Copy):
    def test_the_bar_moves_with_the_records_and_ends_full(self):
        from core import copy_progress

        seen = []
        _, res, _ = self.copy("pr.db", progress=seen.append)
        self.assertTrue(res["ok"], res)
        self.assertTrue(any(m.startswith("Copying medicines:") and " of 39" in m for m in seen), seen)
        pcts = [int(m.split("(")[-1].split("%")[0]) for m in seen if m.startswith("Copying ") and "% done" in m]
        self.assertEqual(pcts, sorted(pcts))
        snap = copy_progress.current()
        self.assertEqual(100, snap["pct"])
        self.assertTrue(seen[-1].startswith("Offline copy ready"))

    def test_time_left_and_figures(self):
        from core.copy_progress import CopyProgress

        out = []
        p = CopyProgress(out.append, {"medicines": 1000, "sales": 100}, tail_share=0.0, min_interval=0)
        p.begin("sales")
        p.first_write = p.started - 10  # ten seconds of copying so far
        p.advance("sales", 100)
        p.begin("medicines")
        p.advance("medicines", 400)
        snap = p.snapshot()
        self.assertEqual(("medicines", 400, 1000), (snap["collection"], snap["done"], snap["total"]))
        self.assertTrue(30 <= snap["pct"] <= 70, snap)
        self.assertIsNotNone(snap["eta_s"])
        self.assertIn("Copying medicines: 400 of 1,000", out[-1])
        self.assertIn("left", out[-1])


class GzipAnswers(unittest.TestCase):
    def test_a_gzip_answer_is_unzipped(self):
        body = gzip.compress(json.dumps({"ok": True, "data": ["x"] * 1000}).encode())
        seen = {}

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen["enc"] = self.headers.get("Accept-Encoding")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            from core import server_api as api

            res = api._request("GET", "/x", base=f"http://127.0.0.1:{srv.server_address[1]}", timeout=10)
        finally:
            srv.shutdown()
        self.assertEqual(1000, len(res["data"]))
        self.assertIn("gzip", seen["enc"])


if __name__ == "__main__":
    unittest.main()
