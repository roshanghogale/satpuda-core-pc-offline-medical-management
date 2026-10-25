"""A sales bill another PC deleted is never brought back by this PC's next send (offline-first).

The server applies an upsert of a record it holds as deleted, so before this fix:
  * PC A's day COUNTER SALE bill, deleted on PC B, came back with all its old lines the moment
    A added the next walk-in -- the stock of those lines had already gone back on B;
  * an edit on A of a bill B had just deleted brought the bill back the same way.

Now the worker looks at the server's changes before it pushes: the bill stays deleted; walk-ins
added to a counter bill after the server's copy become a new counter bill (they were real sales),
and any other edit's own stock movement is put back.

Two PCs on temp store files and tests/fake_sync_server.FakeV2Server -- no real server.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from core import db_setup
from core.offline_first import ids, schema, worker
from core.offline_first.schema import meta_set
from tests.fake_sync_server import FakeV2Server

DAY = "2026-10-25"


def make_store(device: int, install: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".db", prefix=f"del_{device}_")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    db_setup.initialise(conn)
    conn.commit()
    schema.ensure_schema(conn)
    base = device * 1_000_000_000
    meta_set(conn, "device_no", device)
    meta_set(conn, "id_base", base)
    meta_set(conn, "id_max", base + 999_999_999)
    meta_set(conn, "install_id", install)
    conn.execute("INSERT INTO of_number_blocks (kind, fy_start_year, from_serial, to_serial, next_serial) "
                 "VALUES ('sales', 2026, ?, ?, ?)", (device * 100 + 1, device * 100 + 99, device * 100 + 1))
    conn.commit()
    conn.close()
    return path


def till(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30, factory=ids.OfConnection)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def line(mid: int, qty: float, rate: float = 20.0) -> dict:
    return {"id": mid, "qty": qty, "rate": rate, "amount": round(qty * rate, 2), "medicine_discount": 0,
            "gst_percent": 12, "name": "DOLO", "batch": "B1", "expiry": "2027-01", "schedule": "", "unit": "1",
            "mrp": 25}


class DeletedOnTheOtherPc(unittest.TestCase):
    def setUp(self):
        self.server = FakeV2Server()
        self.pa, self.pb = make_store(3, "pc-a"), make_store(4, "pc-b")
        self.a, self.b = till(self.pa), till(self.pb)
        self.ca, self.cb = worker.SyncCycle(self.pa), worker.SyncCycle(self.pb)
        for p in (mock.patch("core.sync_prefs.is_online_mode", return_value=False),
                  mock.patch("core.sync_prefs.is_offline_first", return_value=True),
                  mock.patch("core.offline_first.worker.client.stock", lambda ids=None: {"stock": []})):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._cleanup)
        self.counter = self.a.execute("INSERT INTO customers (name) VALUES ('COUNTER SALE')").lastrowid
        self.ram = self.a.execute("INSERT INTO customers (name, phone) VALUES ('RAM', '9000000001')").lastrowid
        self.m = self.a.execute(
            "INSERT INTO medicines (name, type, stock_qty, unit, mrp, rate, batch_no, gst_percent) "
            "VALUES ('DOLO','Tablet',100,'1',25,20,'B1',12)").lastrowid
        self.a.commit()
        self.sync("a")
        self.sync("b")

    def _cleanup(self):
        for c in (self.ca, self.cb):
            c.close()
        for c in (self.a, self.b):
            c.close()
        for p in (self.pa, self.pb):
            for s in ("", "-wal", "-shm"):
                try:
                    os.remove(p + s)
                except OSError:
                    pass

    def sync(self, who: str):
        cyc = self.ca if who == "a" else self.cb
        with self.server.bind("pc-a" if who == "a" else "pc-b"):
            cyc.run_once()

    def sell(self, conn, cid, name, qty):
        from core.billing_service import save_new_bill

        _no, sid = save_new_bill(conn, cid, [line(self.m, qty)], 0, 0, qty * 20.0, 0, "", "", 0,
                                 bill_date=DAY, customer_name=name)
        return int(sid)

    def journal(self, conn) -> float:
        return float(conn.execute("SELECT COALESCE(SUM(delta),0) FROM of_stock_journal").fetchone()[0])

    def delete_on_b(self, sid):
        from core import desktop_sales_service as sales_svc

        with mock.patch("core.online_guard.ensure_can_mutate", lambda *a, **k: None):
            out = sales_svc.delete_saved_sale(self.b, {"sale_id": sid})
        self.assertTrue(out.get("ok"), out)
        self.sync("b")

    def test_walk_ins_added_after_the_delete_become_a_new_counter_bill(self):
        from core.billing_service import append_counter_sale_today

        x = self.sell(self.a, self.counter, "COUNTER SALE", 2)
        self.sync("a")
        self.sync("b")
        self.delete_on_b(x)
        # A has not pulled the delete yet: the next walk-in goes into its day bill.
        merged = append_counter_sale_today(self.a, self.counter, "COUNTER SALE", [line(self.m, 3)], 0, 0,
                                           60.0, 0, "", "", 0, bill_date=DAY)
        self.assertEqual(int(merged[1]), x)
        self.sync("a")
        self.sync("b")

        self.assertTrue(self.server.docs[("sales", x)].get("deleted"), "the deleted bill came back")
        self.assertIsNone(self.a.execute("SELECT 1 FROM sales WHERE id=?", (x,)).fetchone())
        alive = [d for (c, _i), d in self.server.docs.items() if c == "sales" and not d.get("deleted")]
        self.assertEqual(len(alive), 1, alive)
        y = alive[0]
        self.assertEqual([(int(it["medicine_id"]), float(it["qty"])) for it in y["items"]], [(self.m, 3.0)])
        self.assertEqual((float(y["total_amount"]), float(y["cash_paid"])), (60.0, 60.0))
        self.assertNotEqual(y["bill_no"], self.server.docs[("sales", x)]["bill_no"])
        for conn in (self.a, self.b):
            rows = conn.execute("SELECT id, total_amount, cash_paid FROM sales WHERE COALESCE(deleted,0)=0").fetchall()
            self.assertEqual([(int(r[0]), float(r[1]), float(r[2])) for r in rows], [(int(y["id"]), 60.0, 60.0)])
            self.assertEqual(conn.execute("SELECT qty FROM sales_items WHERE sale_id=?", (int(y["id"]),)).fetchall(),
                             [(3.0,)])
        # Stock: A sold 2 then 3, B put the 2 back -> 3 out in all, the new bill's 3.
        self.assertEqual(self.journal(self.a) + self.journal(self.b) - 100, -3.0)
        # Syncing again changes nothing; nothing is waiting.
        self.sync("a")
        self.assertEqual(self.a.execute("SELECT COUNT(*) FROM of_events WHERE status='pending'").fetchone()[0], 0)
        self.assertEqual(self.a.execute("SELECT COUNT(*) FROM of_dirty").fetchone()[0], 0)

    def test_an_edit_of_a_bill_deleted_elsewhere_keeps_it_deleted_and_puts_its_stock_back(self):
        from core.billing_service import update_existing_bill

        x = self.sell(self.a, self.ram, "RAM", 2)
        self.sync("a")
        self.sync("b")
        self.delete_on_b(x)
        update_existing_bill(self.a, x, [line(self.m, 5)], 0, 0, 100.0, 0, "RAM", "9000000001", "", 0,
                             bill_date=DAY)
        self.sync("a")
        self.assertTrue(self.server.docs[("sales", x)].get("deleted"))
        self.assertIsNone(self.a.execute("SELECT 1 FROM sales WHERE id=?", (x,)).fetchone())
        self.assertEqual([d for (c, _i), d in self.server.docs.items() if c == "sales" and not d.get("deleted")], [])
        # A sold 2, edited to 5 (3 more), the edit's 3 came back; B put the 2 back: nothing out.
        self.assertEqual(self.journal(self.a) + self.journal(self.b) - 100, 0.0)
        self.assertEqual(float(self.a.execute("SELECT stock_qty FROM medicines WHERE id=?", (self.m,)).fetchone()[0]), 98.0)

    def test_an_unsent_event_of_the_bill_is_dropped_and_the_rest_renumbered(self):
        from core.billing_service import append_counter_sale_today
        from core.offline_first import outbox

        x = self.sell(self.a, self.counter, "COUNTER SALE", 2)
        self.sync("a")
        self.sync("b")
        self.delete_on_b(x)
        append_counter_sale_today(self.a, self.counter, "COUNTER SALE", [line(self.m, 1)], 0, 0, 20.0, 0, "", "",
                                  0, bill_date=DAY)
        self.a.execute("UPDATE customers SET phone='9000000002' WHERE id=?", (self.ram,))
        self.a.commit()
        outbox.build_events(self.a, "pc-a")          # made while the server was out of reach
        self.a.commit()
        self.assertIn(x, [r[0] for r in self.a.execute(
            "SELECT local_id FROM of_events WHERE status='pending' AND collection='sales'")])
        self.sync("a")
        self.assertTrue(self.server.docs[("sales", x)].get("deleted"))
        self.assertEqual(self.server.docs[("customers", self.ram)]["phone"], "9000000002")
        self.assertEqual(self.a.execute("SELECT COUNT(*) FROM of_events WHERE status='pending'").fetchone()[0], 0)
        alive = [d for (c, _i), d in self.server.docs.items() if c == "sales" and not d.get("deleted")]
        self.assertEqual([[float(it["qty"]) for it in d["items"]] for d in alive], [[1.0]])

    def test_a_bill_nobody_deleted_is_sent_as_before(self):
        from core.billing_service import append_counter_sale_today

        x = self.sell(self.a, self.counter, "COUNTER SALE", 2)
        self.sync("a")
        append_counter_sale_today(self.a, self.counter, "COUNTER SALE", [line(self.m, 3)], 0, 0, 60.0, 0, "", "",
                                  0, bill_date=DAY)
        self.sync("a")
        doc = self.server.docs[("sales", x)]
        self.assertFalse(doc.get("deleted"))
        self.assertEqual([float(it["qty"]) for it in doc["items"]], [5.0])
        self.assertEqual(float(doc["cash_paid"]), 100.0)


if __name__ == "__main__":
    unittest.main()
