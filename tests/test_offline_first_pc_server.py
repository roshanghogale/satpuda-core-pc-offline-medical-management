"""Offline-first, two PCs against a REHEARSAL server (a copy of the live database; never live).

Runs only when OF_TEST_BASE is set, e.g. through an ssh tunnel to the rehearsal instance:
    OF_TEST_BASE=http://127.0.0.1:3999 OF_TEST_KEY=SC-V2TEST01 python -m pytest -q tests/test_offline_first_pc_server.py

Two PCs of one shop each work on their own copy, sell the same medicine while offline, edit the
same bill, and sync: no bill, stock movement or number may be lost or doubled.
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import tempfile
import unittest
import urllib.request

from core import db_setup
from core import server_api as api
from core.offline_first import client, ids, runtime, worker
from core.offline_first.schema import meta_get

BASE = os.environ.get("OF_TEST_BASE", "").rstrip("/")
KEY = os.environ.get("OF_TEST_KEY", "SC-V2TEST01")


def _post(path, body, token=None):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.loads(res.read())


class PC:
    def __init__(self, name: str, install: str):
        self.name, self.install = name, install
        fd, self.path = tempfile.mkstemp(suffix=".db", prefix=f"of_{name}_")
        os.close(fd)
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode=WAL")
        db_setup.initialise(conn)
        conn.commit()
        conn.close()
        self.token = _post("/api/auth/pair", {"android_key": KEY, "device_id": install, "device_type": "pc"})["data"]["token"]
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

    # what a till does, in the app's own tables
    def sell(self, customer_id, medicine_id, qty, rate=30.0):
        from core.offline_first.numbers import take_serial
        from core.fy_serial import encode_sales_bill_no

        serial = take_serial(self.till, "sales", 2026)
        bill_no = encode_sales_bill_no(serial, 2026)
        sid = self.till.execute(
            "INSERT INTO sales (bill_no, customer_id, bill_date, total_amount, amount_paid, cash_paid, fy_start_year, fy_serial) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (bill_no, customer_id, "2026-10-07", qty * rate, qty * rate, qty * rate, 2026, serial),
        ).lastrowid
        self.till.execute("INSERT INTO sales_items (sale_id, medicine_id, qty, rate, gst_percent, amount) VALUES (?,?,?,?,?,?)",
                          (sid, medicine_id, qty, rate, 12, qty * rate))
        self.till.execute("UPDATE medicines SET stock_qty = stock_qty - ? WHERE id=?", (qty, medicine_id))
        self.till.commit()
        return sid, bill_no


@unittest.skipUnless(BASE, "set OF_TEST_BASE to a rehearsal server")
class TwoPCsOneShop(unittest.TestCase):
    def setUp(self):
        import uuid

        run = uuid.uuid4().hex[:8]
        self.a = PC("A", f"pc-A-of-test-{run}")
        self.b = PC("B", f"pc-B-of-test-{run}")
        self.addCleanup(self.a.close)
        self.addCleanup(self.b.close)
        self.a.prepare()
        self.b.prepare()

    def stock(self, pc, mid):
        return pc.q("SELECT stock_qty FROM medicines WHERE id=?", mid)[0][0]

    def test_a_reinstalled_pc_carries_on_after_its_own_ids_and_numbers(self):
        a = self.a
        cid = a.till.execute("INSERT INTO customers (name) VALUES ('FIRST')").lastrowid
        a.till.commit()
        from core.offline_first.numbers import doc_number
        a.till.execute("INSERT INTO sales_returns (return_no, return_date, refund_amount) VALUES (?,?,?)",
                       (doc_number(a.till, "SR"), "2026-10-07", 10))
        a.till.commit()
        a.sync()
        # Same PC, empty database (reinstalled), same install id.
        again = PC("A2", a.install)
        self.addCleanup(again.close)
        again.prepare()
        cid2 = again.till.execute("INSERT INTO customers (name) VALUES ('AFTER REINSTALL')").lastrowid
        self.assertGreater(cid2, cid)
        self.assertNotEqual(doc_number(again.till, "SR"), f"SR{meta_get(a.till, 'device_no')}-1")

    def test_offline_sales_on_both_pcs_land_whole_and_stock_is_exact(self):
        a, b = self.a, self.b
        self.assertNotEqual(meta_get(a.till, "device_no"), meta_get(b.till, "device_no"))
        mid = a.till.execute(
            "INSERT INTO medicines (name, type, stock_qty, unit, mrp, rate, batch_no) VALUES ('DOLO 650','Tablet',100,'10',30,20,'B1')"
        ).lastrowid
        cid = a.till.execute("INSERT INTO customers (name, phone) VALUES ('RAM PATIL','9000000001')").lastrowid
        a.till.commit()
        a.sync()
        b.sync()
        self.assertEqual(self.stock(b, mid), 100, "B did not get A's medicine with its opening stock")

        # Both offline: each sells from its own number block.
        sa, bill_a = a.sell(cid, mid, 2)
        sb, bill_b = b.sell(cid, mid, 9)
        self.assertNotEqual(bill_a, bill_b)
        self.assertEqual(self.stock(a, mid), 98)
        self.assertEqual(self.stock(b, mid), 91)

        # Back online, in either order.
        a.sync(); b.sync(); a.sync(); b.sync()
        self.assertEqual(self.stock(a, mid), 89)
        self.assertEqual(self.stock(b, mid), 89)
        for pc in (a, b):
            got = sorted(r[0] for r in pc.q("SELECT bill_no FROM sales WHERE COALESCE(deleted,0)=0"))
            self.assertEqual(got, sorted([bill_a, bill_b]), f"{pc.name} does not hold both bills")
            self.assertEqual(pc.q("SELECT COUNT(*) FROM of_events WHERE status='pending'")[0][0], 0)
            self.assertEqual(pc.q("SELECT COUNT(*) FROM of_dirty")[0][0], 0)

        # The server holds every bill and the same stock.
        with a.acting():
            s = client.stock([mid])
        self.assertEqual(s["stock"][0][1], 89)

    def test_an_edit_on_each_pc_of_the_same_bill_is_kept_and_both_end_the_same(self):
        a, b = self.a, self.b
        mid = a.till.execute(
            "INSERT INTO medicines (name, type, stock_qty, unit, mrp, rate, batch_no) VALUES ('CIPCAL','Syrup',50,'1',90,60,'C1')"
        ).lastrowid
        cid = a.till.execute("INSERT INTO customers (name) VALUES ('SHAM')").lastrowid
        a.till.commit()
        sid, _ = a.sell(cid, mid, 2)
        a.sync(); b.sync()
        self.assertEqual(self.stock(b, mid), 48)
        # A: qty 2 -> 3; B (not having seen it): qty 2 -> 1.
        a.till.execute("UPDATE sales_items SET qty=3, amount=90 WHERE sale_id=?", (sid,))
        a.till.execute("UPDATE sales SET total_amount=90 WHERE id=?", (sid,))
        a.till.execute("UPDATE medicines SET stock_qty = stock_qty - 1 WHERE id=?", (mid,))
        a.till.commit()
        b.till.execute("UPDATE sales_items SET qty=1, amount=30 WHERE sale_id=?", (sid,))
        b.till.execute("UPDATE sales SET total_amount=30 WHERE id=?", (sid,))
        b.till.execute("UPDATE medicines SET stock_qty = stock_qty + 1 WHERE id=?", (mid,))
        b.till.commit()
        a.sync(); b.sync(); a.sync(); b.sync()
        ta = a.q("SELECT total_amount FROM sales WHERE id=?", sid)[0][0]
        tb = b.q("SELECT total_amount FROM sales WHERE id=?", sid)[0][0]
        self.assertEqual(ta, tb, "the two PCs ended with different copies of the bill")
        self.assertEqual(self.stock(a, mid), self.stock(b, mid))
        # Both movements are facts: 50 - 2 - 1 + 1 = 48
        self.assertEqual(self.stock(a, mid), 48)
        flagged = b.q("SELECT outcome, flag_code FROM of_events WHERE collection='sales' AND outcome='flagged'")
        self.assertTrue(flagged and "concurrent_edit" in (flagged[0][1] or ""), flagged)


if __name__ == "__main__":
    unittest.main()
