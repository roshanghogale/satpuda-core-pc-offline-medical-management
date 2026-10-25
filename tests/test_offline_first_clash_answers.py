"""What this PC sends and does for the clashes only the server can see (server clashRules.js).

  * every sales / sales-return event that changes a record the server has carries
    ``base_lines``: the lines the change started from (the server's last copy known here, or
    this PC's own earlier change still waiting to go up), so the server can put stock straight
    when another device's copy was replaced meanwhile;
  * a push answer's ``stock_fix`` sends those medicines' stock to be taken from the server again;
    ``kept_deleted`` marks the return deleted here too (the server holds it deleted: its bill
    was deleted on another device);
  * a sales / purchase return changed here after another PC deleted it stays deleted, and the
    change's own stock movement goes back (core/offline_first/deleted_ahead.py).

Two PCs on temp store files and tests/fake_sync_server.FakeV2Server -- no real server.
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from core.offline_first import outbox
from tests.fake_sync_server import FakeV2Server
from tests.test_offline_first_deleted_bill_stays_deleted import DAY, line, make_store, till


class ClashAnswers(unittest.TestCase):
    def setUp(self):
        import os

        from core.offline_first import worker

        self.server = FakeV2Server()
        self.pa, self.pb = make_store(3, "pc-a"), make_store(4, "pc-b")
        self.a, self.b = till(self.pa), till(self.pb)
        self.ca, self.cb = worker.SyncCycle(self.pa), worker.SyncCycle(self.pb)
        for p in (mock.patch("core.sync_prefs.is_online_mode", return_value=False),
                  mock.patch("core.sync_prefs.is_offline_first", return_value=True),
                  mock.patch("core.offline_first.worker.client.stock", lambda ids=None: {"stock": []})):
            p.start()
            self.addCleanup(p.stop)

        def cleanup():
            for c in (self.ca, self.cb, self.a, self.b):
                c.close()
            for p in (self.pa, self.pb):
                for s in ("", "-wal", "-shm"):
                    try:
                        os.remove(p + s)
                    except OSError:
                        pass

        self.addCleanup(cleanup)
        self.ram = self.a.execute("INSERT INTO customers (name, phone) VALUES ('RAM', '9000000001')").lastrowid
        self.sup = self.a.execute("INSERT INTO suppliers (name) VALUES ('MAHAVIR AGENCIES')").lastrowid
        self.m = self.a.execute(
            "INSERT INTO medicines (name, type, stock_qty, unit, mrp, rate, batch_no, gst_percent) "
            "VALUES ('DOLO','Syrup',100,'1',25,20,'B1',12)").lastrowid
        self.a.commit()
        self.sync("a")
        self.sync("b")

    def sync(self, who: str):
        cyc = self.ca if who == "a" else self.cb
        with self.server.bind("pc-a" if who == "a" else "pc-b"):
            cyc.run_once()

    def sell(self, qty):
        from core.billing_service import save_new_bill

        _no, sid = save_new_bill(self.a, self.ram, [line(self.m, qty)], 0, 0, qty * 20.0, 0, "", "", 0,
                                 bill_date=DAY, customer_name="RAM")
        return int(sid)

    def edit(self, sid, qty):
        from core.billing_service import update_existing_bill

        update_existing_bill(self.a, sid, [line(self.m, qty)], 0, 0, qty * 20.0, 0, "RAM", "9000000001", "", 0,
                             bill_date=DAY)

    def events(self, conn, collection):
        return [json.loads(p) for (p,) in conn.execute(
            "SELECT payload FROM of_events WHERE collection=? ORDER BY seq", (collection,))]

    def journal(self, conn) -> float:
        return float(conn.execute("SELECT COALESCE(SUM(delta),0) FROM of_stock_journal").fetchone()[0])

    def sales_return(self, conn, sid, cid, qty):
        from core import desktop_returns_service as ret

        r = ret.save_sales_return(conn, {"sale_id": sid, "customer_id": cid, "settle_mode": "ledger",
                                         "items": [{"medicine_id": self.m, "qty": qty, "rate": 20.0}]})
        self.assertTrue(r.get("ok"), r)
        return int(r["return_id"])

    # -- base_lines ---------------------------------------------------------------------
    def test_an_edit_carries_the_lines_it_started_from(self):
        sid = self.sell(2)
        outbox.build_events(self.a, "pc-a")
        self.a.commit()
        new = self.events(self.a, "sales")[-1]
        self.assertEqual(new["base_version"], 0)
        self.assertNotIn("base_lines", new)          # a new bill: nothing to start from
        self.sync("a")
        self.edit(sid, 5)
        outbox.build_events(self.a, "pc-a")          # made while the server was out of reach
        self.a.commit()
        first = self.events(self.a, "sales")[-1]
        self.assertGreater(first["base_version"], 0)
        self.assertEqual(first["base_lines"], [[self.m, 2]])    # the server's copy
        self.edit(sid, 7)
        outbox.build_events(self.a, "pc-a")
        self.a.commit()
        second = self.events(self.a, "sales")[-1]
        self.assertEqual(second["base_lines"], [[self.m, 5]])   # its own change still waiting
        self.sync("a")
        self.assertEqual(self.a.execute("SELECT COUNT(*) FROM of_events WHERE status='pending'").fetchone()[0], 0)

    def test_a_return_edit_carries_its_lines_too(self):
        sid = self.sell(4)
        self.sync("a")
        rid = self.sales_return(self.a, sid, self.ram, 1)
        self.sync("a")
        self.a.execute("UPDATE sales_returns SET reason='x' WHERE id=?", (rid,))
        self.a.commit()
        outbox.build_events(self.a, "pc-a")
        self.a.commit()
        ev = self.events(self.a, "sales_returns")[-1]
        self.assertEqual(ev["base_lines"], [[self.m, 1]])

    # -- the push answer ------------------------------------------------------------------
    def test_kept_deleted_marks_the_return_deleted_and_takes_its_stock_from_the_server(self):
        sid = self.sell(5)
        self.sync("a")
        party = "SELECT total_due, total_credit FROM customers WHERE id=?"
        before = self.a.execute(party, (self.ram,)).fetchone()
        rid = self.sales_return(self.a, sid, self.ram, 2)
        self.assertNotEqual(self.a.execute(party, (self.ram,)).fetchone(), before)
        outbox.build_events(self.a, "pc-a")
        self.a.commit()
        seq = int(self.a.execute("SELECT seq FROM of_events WHERE collection='sales_returns' AND status='pending'")
                  .fetchone()[0])
        answer = {"last_seq": seq, "results": [{
            "seq": seq, "outcome": "flagged", "flag_code": "bill_deleted",
            "flag_detail": "bill was deleted on another device", "kept_deleted": True,
            "stock_fix": [{"medicine_id": self.m, "qty_delta": -2}]}]}
        res = outbox.apply_push_answer(self.a, answer)
        self.a.commit()
        self.assertEqual(res["refresh_medicines"], {self.m})
        self.assertEqual([k["action"] for k in res["kept_deleted"]], ["kept_deleted"])
        self.assertEqual(int(self.a.execute("SELECT deleted FROM sales_returns WHERE id=?", (rid,)).fetchone()[0]), 1)
        # Marked deleted without a delete event of its own (the server holds it deleted already).
        self.assertIsNone(self.a.execute(
            "SELECT 1 FROM of_dirty WHERE collection='sales_returns' AND local_id=?", (rid,)).fetchone())
        # The refund is off the customer's account again.
        self.assertEqual(self.a.execute(party, (self.ram,)).fetchone(), before)
        self.assertEqual(outbox.counts(self.a)["flagged"], 1)        # "Sync: 1 to check"

    def test_an_answer_without_the_new_fields_is_read_as_before(self):
        self.sell(2)
        outbox.build_events(self.a, "pc-a")
        self.a.commit()
        seqs = [r[0] for r in self.a.execute("SELECT seq FROM of_events WHERE status='pending' ORDER BY seq")]
        res = outbox.apply_push_answer(self.a, {"last_seq": seqs[-1], "results": [
            {"seq": s, "outcome": "applied"} for s in seqs]})
        self.assertEqual((res["refresh_medicines"], res["kept_deleted"], res["renumber_from"]), (set(), [], None))

    # -- a return changed after the other PC deleted it ----------------------------------
    def test_a_sales_return_changed_after_the_other_pc_deleted_it_stays_deleted(self):
        from core import desktop_returns_service as ret

        sid = self.sell(5)
        rid = self.sales_return(self.a, sid, self.ram, 2)
        self.sync("a")
        self.sync("b")
        d = ret.delete_sales_return(self.b, {"id": rid})
        self.assertTrue(d.get("ok"), d)
        self.sync("b")
        # A (not pulled yet) changes the return: 2 -> 3 back on the shelf.
        self.a.execute("UPDATE sales_return_items SET qty=3 WHERE return_id=?", (rid,))
        self.a.execute("UPDATE medicines SET stock_qty=stock_qty+1 WHERE id=?", (self.m,))
        self.a.execute("UPDATE sales_returns SET reason='one more' WHERE id=?", (rid,))
        self.a.commit()
        self.sync("a")
        self.assertTrue(self.server.docs[("sales_returns", rid)].get("deleted"), "the deleted return came back")
        self.assertEqual(int(self.a.execute("SELECT deleted FROM sales_returns WHERE id=?", (rid,)).fetchone()[0]), 1)
        self.assertEqual([e for e in self.server.pushed if e.get("collection") == "sales_returns"
                          and e.get("op") == "upsert" and (e.get("doc") or {}).get("reason") == "one more"], [])
        # Stock: sold 5, returned 2 (A), B took the 2 back, A's +1 went back: 95 out of 100.
        self.assertEqual(self.journal(self.a) + self.journal(self.b) - 100, -5.0)
        self.assertEqual(self.a.execute("SELECT COUNT(*) FROM of_events WHERE status='pending'").fetchone()[0], 0)
        self.assertEqual(self.a.execute("SELECT COUNT(*) FROM of_dirty WHERE collection='sales_returns'")
                         .fetchone()[0], 0)

    def test_a_purchase_return_changed_after_the_other_pc_deleted_it_stays_deleted(self):
        from core import desktop_returns_service as ret

        pid = self.a.execute(
            "INSERT INTO purchases (supplier_id, purchase_date, bill_number, total_amount) VALUES (?,?,?,?)",
            (self.sup, DAY, "INV-1", 100.0)).lastrowid
        self.a.execute("INSERT INTO purchase_items (purchase_id, medicine_id, qty, rate) VALUES (?,?,?,?)",
                       (pid, self.m, 10, 10.0))
        self.a.commit()
        r = ret.save_purchase_return(self.a, {"purchase_id": pid, "supplier_id": self.sup, "settle_mode": "ledger",
                                              "items": [{"medicine_id": self.m, "qty": 3, "rate": 10.0}]})
        self.assertTrue(r.get("ok"), r)
        rid = int(r["return_id"])
        self.sync("a")
        self.sync("b")
        d = ret.delete_purchase_return(self.b, {"id": rid})
        self.assertTrue(d.get("ok"), d)
        self.sync("b")
        # A changes the return 3 -> 4 (one more off the shelf).
        self.a.execute("UPDATE purchase_return_items SET qty=4, stock_units=4 WHERE return_id=?", (rid,))
        self.a.execute("UPDATE medicines SET stock_qty=stock_qty-1 WHERE id=?", (self.m,))
        self.a.execute("UPDATE purchase_returns SET reason='one more' WHERE id=?", (rid,))
        self.a.commit()
        self.sync("a")
        self.assertTrue(self.server.docs[("purchase_returns", rid)].get("deleted"))
        self.assertEqual(int(self.a.execute("SELECT deleted FROM purchase_returns WHERE id=?", (rid,)).fetchone()[0]), 1)
        # Stock: returned 3 (A), B put the 3 back, A's -1 went back: nothing moved in all.
        self.assertEqual(self.journal(self.a) + self.journal(self.b) - 100, 0.0)


if __name__ == "__main__":
    unittest.main()
