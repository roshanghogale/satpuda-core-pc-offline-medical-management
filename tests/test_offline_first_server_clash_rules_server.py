"""Clashes only the SERVER can see, on two offline PCs, through a REHEARSAL server.

Runs only when OF_TEST_BASE is set (an ssh tunnel to the rehearsal instance, never live):
    OF_TEST_BASE=http://127.0.0.1:3999 OF_TEST_KEY=SC-V2TEST01 \
        python -m pytest -q tests/test_offline_first_server_clash_rules_server.py

The server's rules (server services/clashRules.js):
  1. two offline edits of one sales bill: the later push wins and the server puts back the stock
     the replaced copy moved (the PC sends ``base_lines``), so stock ends on the winning lines;
  2. returns adding up to more than the bill sold: applied (money is never dropped), flagged;
  3. a return of a bill another PC deleted: kept deleted on the server, its stock put back, and
     marked deleted here; a return changed here after the other PC deleted it stays deleted;
  4. one supplier bill saved on two offline PCs: the second is applied and flagged.
Uses the two-PC harness of test_offline_first_audit_server.py.
"""
from __future__ import annotations

import unittest
from unittest import mock

from core.offline_first import outbox
from tests import test_offline_first_audit_server as audit
from tests.test_offline_first_audit_server import _item


@unittest.skipUnless(audit.BASE, "set OF_TEST_BASE to a rehearsal server")
class ServerClashRules(audit.Audit):

    def flags(self, pc, collection):
        return pc.q("SELECT op, outcome, flag_code, flag_detail FROM of_events "
                    "WHERE collection=? AND outcome IN ('flagged','quarantined') ORDER BY seq", collection)

    def server_stock(self, pc, mid):
        with pc.acting():
            got = audit.client.stock([mid]).get("stock") or []
        return int(got[0][1]) if got else None

    def lines(self, pc, sid):
        return pc.q("SELECT medicine_id, qty FROM sales_items WHERE sale_id=? ORDER BY medicine_id", sid)

    def problems_but(self, *allowed):
        out = []
        for pc in (self.a, self.b):
            for p in self.events_problems(pc):
                if not any(a in p for a in allowed):
                    out.append(p)
        return out

    # 1 ------------------------------------------------------------------------------------
    def test_two_offline_edits_of_one_bill_leave_stock_on_the_winning_lines(self):
        res = self.sell(self.a, [_item(self.m1, "S", 5, 10.0)], payment_mode="Due", pay_full=False)
        self.settle()
        sid = res["sale_id"]
        # Both PCs offline: A edits 5 -> 3; B edits 5 -> 4 and adds 1 of m2.
        self.sell(self.a, [_item(self.m1, "S", 3, 10.0)], editing_sale_id=sid, payment_mode="Due", pay_full=False)
        self.sell(self.b, [_item(self.m1, "S", 4, 10.0), _item(self.m2, "T", 1, 20.0)], editing_sale_id=sid,
                  payment_mode="Due", pay_full=False)
        self.a.sync()          # A's edit reaches the server first,
        self.b.sync()          # B's later one wins
        self.settle()
        self.assertEqual(self.problems_but("sales/upsert flagged concurrent_edit"), [])
        flagged = self.flags(self.b, "sales")
        self.assertEqual([f[2] for f in flagged], ["concurrent_edit"], flagged)
        self.assertIn("stock was put back", flagged[0][3])
        for pc in (self.a, self.b):
            self.assertEqual(self.lines(pc, sid), [(self.m1, 4), (self.m2, 1)], pc.name)
            self.assertEqual(self.stock(pc, self.m1), 96, pc.name)
            self.assertEqual(self.stock(pc, self.m2), 99, pc.name)
            self.assertEqual(self.party(pc), (60.0, 0.0), pc.name)
        self.assertEqual(self.server_stock(self.a, self.m1), 96)
        self.assertEqual(self.server_stock(self.a, self.m2), 99)
        self.assertEqual(self.server_mismatches(self.a), [])
        self.assertEqual(self.server_mismatches(self.b), [])

    def test_an_older_pc_without_base_lines_is_taken_as_before(self):
        res = self.sell(self.a, [_item(self.m1, "S", 5, 10.0)], payment_mode="Due", pay_full=False)
        self.settle()
        sid = res["sale_id"]
        self.sell(self.a, [_item(self.m1, "S", 3, 10.0)], editing_sale_id=sid, payment_mode="Due", pay_full=False)
        self.sell(self.b, [_item(self.m1, "S", 4, 10.0)], editing_sale_id=sid, payment_mode="Due", pay_full=False)
        self.a.sync()
        with mock.patch("core.offline_first.deleted_ahead.base_lines", lambda *a, **k: None):
            self.b.sync()      # B behaves like a build from before base_lines
        self.settle()
        self.assertEqual(self.problems_but("sales/upsert flagged concurrent_edit"), [])
        # Taken, flagged, nothing blocked -- and the stock is what it always was (A's movement stays).
        self.assertNotIn("stock was put back", self.flags(self.b, "sales")[0][3])
        self.assertEqual(self.server_stock(self.a, self.m1), 98)   # 100 - 5 + 2 (A) + 1 (B): not 96
        for pc in (self.a, self.b):
            self.assertEqual(self.lines(pc, sid), [(self.m1, 4)], pc.name)
            self.assertEqual(pc.one("SELECT COUNT(*) FROM of_events WHERE status='pending'"), 0)

    # 2 ------------------------------------------------------------------------------------
    def test_two_returns_of_the_remaining_quantity_are_kept_and_flagged(self):
        from core import desktop_returns_service as returns_svc

        s1 = self.sell(self.a, [_item(self.m1, "S", 4, 10.0)], payment_mode="Due", pay_full=False)
        self.settle()
        for pc in (self.a, self.b):          # both offline, each returns all 4
            with pc.acting():
                r = returns_svc.save_sales_return(pc.till, {
                    "sale_id": s1["sale_id"], "customer_id": self.cid(pc), "settle_mode": "ledger",
                    "items": [{"medicine_id": self.m1, "qty": 4, "rate": 10.0}]})
            self.assertTrue(r.get("ok"), r)
        self.a.sync()
        self.b.sync()
        self.settle()
        self.assertEqual(self.problems_but("sales_returns/upsert flagged over_return"), [])
        flagged = self.flags(self.b, "sales_returns")
        self.assertEqual([f[2] for f in flagged], ["over_return"], flagged)
        self.assertIn("sold 4, returned before 4, this return 4", flagged[0][3])
        # Both refunds are kept on every copy (the owner deletes the duplicate).
        for pc in (self.a, self.b):
            self.assertEqual(pc.one("SELECT COUNT(*) FROM sales_returns WHERE COALESCE(deleted,0)=0"), 2, pc.name)
            self.assertEqual(pc.one("SELECT COUNT(*) FROM of_events WHERE status='pending'"), 0, pc.name)
        self.assertEqual(outbox.counts(self.b.till)["flagged"], 1)
        self.assertEqual(self.server_mismatches(self.a), [])

    # 3 ------------------------------------------------------------------------------------
    def test_a_return_of_a_bill_the_other_pc_deleted_is_kept_deleted(self):
        from core import desktop_returns_service as returns_svc
        from core import desktop_sales_service as sales_svc

        s1 = self.sell(self.a, [_item(self.m1, "S", 5, 10.0)], payment_mode="Due", pay_full=False)
        self.settle()
        with self.b.acting():
            out = sales_svc.delete_saved_sale(self.b.till, {"sale_id": s1["sale_id"]})
        self.assertTrue(out.get("ok"), out)
        self.b.sync()
        with self.a.acting():          # A has not pulled the delete: it returns 2
            r = returns_svc.save_sales_return(self.a.till, {
                "sale_id": s1["sale_id"], "customer_id": self.cid(self.a), "settle_mode": "ledger",
                "items": [{"medicine_id": self.m1, "qty": 2, "rate": 10.0}]})
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(self.stock(self.a, self.m1), 97)
        self.a.sync()
        self.settle()
        self.assertEqual(self.problems_but("sales_returns/upsert flagged bill_deleted"), [])
        flagged = self.flags(self.a, "sales_returns")
        self.assertEqual([f[2] for f in flagged], ["bill_deleted"], flagged)
        rid = r["return_id"]
        with self.a.acting():
            doc = audit.api.pull_doc(self.a.token, "sales_returns", int(rid))
        self.assertTrue(doc.get("deleted"), doc)
        for pc in (self.a, self.b):
            self.assertEqual(pc.one("SELECT COUNT(*) FROM sales_returns WHERE COALESCE(deleted,0)=0"), 0, pc.name)
            self.assertEqual(pc.q("SELECT id FROM sales WHERE COALESCE(deleted,0)=0"), [], pc.name)
            self.assertEqual(self.stock(pc, self.m1), 100, pc.name)
            self.assertEqual(self.party(pc), (0.0, 0.0), pc.name)
        self.assertEqual(self.server_stock(self.a, self.m1), 100)
        self.assertEqual(self.server_mismatches(self.a), [])

    def test_a_return_changed_after_the_other_pc_deleted_it_stays_deleted(self):
        from core import desktop_returns_service as returns_svc

        s1 = self.sell(self.a, [_item(self.m1, "S", 5, 10.0)], payment_mode="Due", pay_full=False)
        self.settle()
        with self.a.acting():
            r = returns_svc.save_sales_return(self.a.till, {
                "sale_id": s1["sale_id"], "customer_id": self.cid(self.a), "settle_mode": "ledger",
                "items": [{"medicine_id": self.m1, "qty": 2, "rate": 10.0}]})
        self.assertTrue(r.get("ok"), r)
        self.settle()
        rid = r["return_id"]
        with self.b.acting():
            d = returns_svc.delete_sales_return(self.b.till, {"id": rid})
        self.assertTrue(d.get("ok"), d)
        self.b.sync()
        # A (not pulled yet) changes the return: one more bottle back on the shelf.
        self.a.till.execute("UPDATE sales_return_items SET qty=3 WHERE return_id=?", (rid,))
        self.a.till.execute("UPDATE sales_returns SET reason='one more' WHERE id=?", (rid,))
        self.a.till.execute("UPDATE medicines SET stock_qty=stock_qty+1 WHERE id=?", (self.m1,))
        self.a.till.commit()
        self.settle()
        self.assertEqual(self.problems_but(), [])
        with self.a.acting():
            doc = audit.api.pull_doc(self.a.token, "sales_returns", int(rid))
        self.assertTrue(doc.get("deleted"), doc)
        for pc in (self.a, self.b):
            self.assertEqual(pc.one("SELECT COUNT(*) FROM sales_returns WHERE COALESCE(deleted,0)=0"), 0, pc.name)
            self.assertEqual(self.stock(pc, self.m1), 95, pc.name)
            self.assertEqual(self.party(pc), (50.0, 0.0), pc.name)
        self.assertEqual(self.server_stock(self.a, self.m1), 95)

    # 4 ------------------------------------------------------------------------------------
    def test_one_supplier_bill_saved_on_two_offline_pcs_is_flagged(self):
        bill = f"INV{self.run_id}"
        self.buy(self.a, bill, [self.pline(self.m1, "S", 10)])
        self.buy(self.b, bill, [self.pline(self.m2, "T", 10)])   # another line: only the bill clashes
        self.a.sync()
        self.b.sync()
        self.settle()
        self.assertEqual(self.problems_but("purchases/upsert flagged duplicate_supplier_bill"), [])
        flagged = self.flags(self.b, "purchases")
        self.assertEqual([f[2] for f in flagged], ["duplicate_supplier_bill"], flagged)
        self.assertIn(bill, flagged[0][3])
        for pc in (self.a, self.b):         # neither is dropped
            self.assertEqual(pc.one("SELECT COUNT(*) FROM purchases WHERE COALESCE(deleted,0)=0 AND bill_number=?",
                                    bill), 2, pc.name)
        self.assertEqual(outbox.counts(self.b.till)["flagged"], 1)


# The harness's own tests run from their own file.
for _name in [n for n in dir(audit.Audit) if n.startswith("test_")]:
    setattr(ServerClashRules, _name, None)


if __name__ == "__main__":
    unittest.main()
