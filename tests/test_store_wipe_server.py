""""Delete all data" with two offline-first PCs against a REHEARSAL server (never live), on a
THROWAWAY store only (its store_id must start with "wipetest_").

    WIPE_TEST_BASE=http://127.0.0.1:3999 WIPE_TEST_KEY=SC-WIPET025 \
    WIPE_TEST_ADMIN_TOKEN_FILE=<rehearsal admin JWT> python -m pytest -q tests/test_store_wipe_server.py

PC A deletes all data; PC B, which still holds the old copy and an unsent edit and bill, learns
of it on its next sync, clears its copy and drops the unsent work; what both make afterwards
reaches the other, and bill numbers start again at 1.
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import tempfile
import unittest
import urllib.request
import uuid

from core import db_setup
from core import server_api as api
from core.offline_first import client, ids, runtime, worker
from core.offline_first.schema import meta_get, unsent_work

BASE = os.environ.get("WIPE_TEST_BASE", "").rstrip("/")
KEY = os.environ.get("WIPE_TEST_KEY", "")
_TOKEN_FILE = os.environ.get("WIPE_TEST_ADMIN_TOKEN_FILE", "")
ADMIN = open(_TOKEN_FILE, encoding="utf-8").read().strip() if _TOKEN_FILE else ""
READY = bool(BASE and KEY and ADMIN and ":3000" not in BASE and "satpudacore.online" not in BASE)


def _post(path, body, token=None):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.loads(res.read())


def _get(path, token):
    req = urllib.request.Request(BASE + path, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.loads(res.read())


class PC:
    def __init__(self, name: str, install: str):
        self.name, self.install = name, install
        fd, self.path = tempfile.mkstemp(suffix=".db", prefix=f"wipe_{name}_")
        os.close(fd)
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode=WAL")
        db_setup.initialise(conn)
        conn.commit()
        conn.close()
        pair = _post("/api/auth/pair", {"android_key": KEY, "device_id": install, "device_type": "pc"})["data"]
        self.token = pair["token"]
        self.store = pair["store"]
        self.till = None
        self.cycle = worker.SyncCycle(self.path)

    @contextlib.contextmanager
    def acting(self):
        saved = (client._token, client.install_id, api.api_base)
        client._token = lambda: self.token
        client.install_id = lambda: self.install
        api.api_base = lambda: BASE
        try:
            yield
        finally:
            client._token, client.install_id, api.api_base = saved

    def prepare(self):
        with self.acting():
            conn = sqlite3.connect(self.path)
            runtime.register_and_prepare(conn, head_revision=client.head_revision())
            conn.close()
        self.till = sqlite3.connect(self.path, check_same_thread=False, timeout=30, factory=ids.OfConnection)

    def sync(self, rounds=2):
        with self.acting():
            for _ in range(rounds):
                self.cycle.run_once()

    def q(self, sql, *a):
        return self.till.execute(sql, a).fetchall()

    def close(self):
        self.cycle.close()
        if self.till:
            self.till.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + s)
            except OSError:
                pass

    def add_party_and_medicine(self, tag):
        cid = self.till.execute("INSERT INTO customers (name, phone) VALUES (?, ?)", (f"Cust {tag}", "9000000000")).lastrowid
        mid = self.till.execute("INSERT INTO medicines (name, mrp, rate, stock_qty) VALUES (?, 40, 30, 0)",
                                (f"Med {tag}",)).lastrowid
        self.till.commit()
        return cid, mid

    def sell(self, customer_id, medicine_id, qty, rate=30.0):
        from core.fy_serial import encode_sales_bill_no
        from core.offline_first.numbers import take_serial

        serial = take_serial(self.till, "sales", 2026)
        bill_no = encode_sales_bill_no(serial, 2026)
        sid = self.till.execute(
            "INSERT INTO sales (bill_no, customer_id, bill_date, total_amount, amount_paid, cash_paid, fy_start_year, fy_serial) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (bill_no, customer_id, "2026-10-25", qty * rate, qty * rate, qty * rate, 2026, serial),
        ).lastrowid
        self.till.execute("INSERT INTO sales_items (sale_id, medicine_id, qty, rate, gst_percent, amount) VALUES (?,?,?,?,?,?)",
                          (sid, medicine_id, qty, rate, 12, qty * rate))
        self.till.commit()
        return sid, serial


@unittest.skipUnless(READY, "set WIPE_TEST_BASE / WIPE_TEST_KEY / WIPE_TEST_ADMIN_TOKEN_FILE (rehearsal only)")
class DeleteAllDataReachesEveryPC(unittest.TestCase):
    def setUp(self):
        run = uuid.uuid4().hex[:8]
        self.a = PC("A", f"pc-A-wipe-test-{run}")
        self.b = PC("B", f"pc-B-wipe-test-{run}")
        self.addCleanup(self.a.close)
        self.addCleanup(self.b.close)
        self.assertTrue(str(self.a.store.get("store_id") or "").startswith("wipetest_"),
                        "this test only ever wipes a throwaway store")
        self.a.prepare()
        self.b.prepare()

    def test_wipe_on_a_reaches_b_and_unsent_work_is_dropped(self):
        a, b = self.a, self.b
        cid, mid = a.add_party_and_medicine("before")
        a.sell(cid, mid, 2)
        a.sync()
        b.sync()
        self.assertEqual(len(b.q("SELECT id FROM sales")), 1)
        self.assertEqual(len(b.q("SELECT id FROM customers WHERE id=?", cid)), 1)

        # B: an edit and a bill it has not sent
        b.till.execute("UPDATE customers SET phone='9111111111' WHERE id=?", (cid,))
        b.till.commit()
        b.sell(cid, mid, 1)
        b_conn = sqlite3.connect(b.path)
        self.assertGreater(unsent_work(b_conn), 0)
        b_conn.close()

        # A: Delete all data (what the Settings action runs after the two confirmations)
        from core import desktop_settings_service as dss

        with a.acting():
            work = sqlite3.connect(a.path, timeout=30)
            res = dss._wipe_store_everywhere(work, mode="offline_first", admin_token=ADMIN, also_profile=False,
                                             request_id=uuid.uuid4().hex, progress=None)
            work.close()
        self.assertTrue(res["ok"], res)
        gen = int(res["server"]["wipe_generation"])
        for t in ("sales", "sales_items", "customers", "medicines"):
            self.assertEqual(a.q(f"SELECT COUNT(*) FROM {t}")[0][0], 0, f"A {t}")
        prev = _get("/api/sync/v2/store-wipe/preview", a.token)["data"]
        self.assertEqual(prev["counts"]["sales"], 0)
        self.assertEqual(prev["counts"]["customers"], 0)

        # an Online PC of the store: its next status answer drops its caches, once
        from unittest import mock
        from core import store_wipe

        seen_dir = tempfile.mkdtemp()
        with mock.patch.object(store_wipe, "_seen_path", lambda: os.path.join(seen_dir, "seen.json")),                 mock.patch.object(store_wipe, "forget_online_caches") as forget:
            store_wipe.remember_generation(gen - 1)
            with a.acting():
                status = api.get_sync_status(a.token)
            self.assertTrue(store_wipe.check_online_wipe(status))
            self.assertFalse(store_wipe.check_online_wipe(status))
            self.assertEqual(forget.call_count, 1)

        # B syncs: its push is refused, its copy and its unsent work go
        b.sync()
        for t in ("sales", "sales_items", "customers", "medicines"):
            self.assertEqual(b.q(f"SELECT COUNT(*) FROM {t}")[0][0], 0, f"B {t}")
        b_conn = sqlite3.connect(b.path)
        self.assertEqual(unsent_work(b_conn), 0)
        self.assertEqual(int(meta_get(b_conn, "wipe_generation")), gen)
        b_conn.close()
        prev = _get("/api/sync/v2/store-wipe/preview", a.token)["data"]
        self.assertEqual(prev["counts"]["sales"], 0, "B's old bill must never reach the server")

        # life goes on: B's next bill reaches A, numbered from 1 again
        cid2, mid2 = b.add_party_and_medicine("after")
        _sid, serial = b.sell(cid2, mid2, 1)
        self.assertEqual(serial, 1)
        b.sync()
        a.sync()
        self.assertEqual([r[0] for r in a.q("SELECT id FROM customers")], [cid2])
        self.assertEqual(len(a.q("SELECT id FROM sales")), 1)
        prev = _get("/api/sync/v2/store-wipe/preview", a.token)["data"]
        self.assertEqual(prev["counts"]["sales"], 1)
        self.assertEqual(prev["counts"]["customers"], 1)


if __name__ == "__main__":
    unittest.main()
