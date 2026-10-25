"""A sales return Online gives the same refund, cash payout and stock as offline-first / Offline
(owner, 25 Oct 2026: "offline-first must give the same numbers").

The bill of tests/test_offline_first_returns_payments_numbers_server.py: 25 tablets x Rs 5 and
4 syrups x Rs 10, Rs 16.50 off (10%), Rs 50 paid -- Rs 98.50 due. Return 1: 12 tablets (a strip
and 2 loose) + 1 syrup to the account; return 2: the rest, in cash. The Online path (the request
it queues for the server) and a plain local store must both say 63 / 0 then 85.50 / 50, with
the units back exactly as returned.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import desktop_returns_service as drs  # noqa: E402

TAB, SYR = 11, 12
SALE = {"id": 5, "local_id": 5, "bill_no": "SCB5", "bill_date": "2026-10-25", "customer_id": 3,
        "total_amount": 148.5, "discount": 16.5,
        "items": [{"medicine_id": TAB, "name": "TAB", "qty": 25, "rate": 5.0, "amount": 125.0},
                  {"medicine_id": SYR, "name": "SYR", "qty": 4, "rate": 10.0, "amount": 40.0}]}


def body(tab, syr, settle):
    items = []
    if tab:
        items.append({"medicine_id": TAB, "name": "TAB", "qty": tab, "rate": 5.0, "orig_qty": 25,
                      "orig_amount": 125.0, "type": "Tablet", "is_tablet": True})
    if syr:
        items.append({"medicine_id": SYR, "name": "SYR", "qty": syr, "rate": 10.0, "orig_qty": 4,
                      "orig_amount": 40.0, "type": "Syrup", "is_tablet": False})
    return {"sale_id": 5, "customer_id": 3, "customer_name": "RAM", "bill_no": "SCB5", "items": items,
            "discount": 10.0, "reason": "", "settle_mode": settle}


class OnlineSalesReturn(unittest.TestCase):
    def setUp(self):
        self.queued = []
        self.cust = {"id": 3, "name": "RAM", "total_due": 98.5, "total_credit": 0.0}
        self.returned = {}

        def enqueue(**kw):
            self.queued.append(kw)
            p = kw.get("payload") or {}
            if kw.get("collection") == "sales_returns":
                for it in p.get("items") or []:
                    self.returned[it["medicine_id"]] = self.returned.get(it["medicine_id"], 0) + it["qty"]
                if p.get("_customers"):
                    c = p["_customers"][0]
                    self.cust.update(total_due=c["total_due"], total_credit=c["total_credit"])
            return {"local_id": -len(self.queued)}

        patches = [
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.server_crud.get_doc",
                       side_effect=lambda col, lid: dict(SALE) if (col, int(lid)) == ("sales", 5) else None),
            mock.patch.object(drs, "_online_returned_map", side_effect=lambda *a, **k: dict(self.returned)),
            mock.patch.object(drs, "_pending_returned_qty", return_value=0.0),
            mock.patch.object(drs, "_return_id_for", side_effect=lambda c, u: (-100 - len(self.queued), True)),
            mock.patch("core.online_mutation_queue.enqueue", side_effect=enqueue),
            mock.patch("core.online_catalog.find_customer_by_id", side_effect=lambda cid: dict(self.cust)),
            mock.patch("core.online_catalog.medicine_by_id", return_value=None),
            mock.patch("core.online_catalog.patch_docs", return_value=None),
            mock.patch("core.online_catalog.patch_customer_cache", return_value=None),
            mock.patch.object(drs, "_notify_return_saved", return_value=None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)

    def _stock_deltas(self, start):
        out = {}
        for q in self.queued[start:]:
            for m in (q.get("payload") or {}).get("_medicines") or []:
                for op in m.get("stock_ops") or []:
                    out[op["medicine_id"]] = out.get(op["medicine_id"], 0) + op["qty_delta"]
        return out

    def test_online_refund_payout_and_units_match_offline(self):
        r1 = drs.save_sales_return(self.conn, body(12, 1, "ledger"))
        self.assertTrue(r1["ok"], r1)
        self.assertEqual((r1["refund_amount"], r1["refund_payout"]), (63.0, 0.0))
        self.assertEqual(self._stock_deltas(0), {TAB: 12, SYR: 1})
        self.assertEqual((self.cust["total_due"], self.cust["total_credit"]), (35.5, 0.0))
        n = len(self.queued)
        r2 = drs.save_sales_return(self.conn, body(13, 3, "cash"))
        self.assertTrue(r2["ok"], r2)
        self.assertEqual((r2["refund_amount"], r2["refund_payout"]), (85.5, 50.0))
        self.assertEqual(self._stock_deltas(n), {TAB: 13, SYR: 3})
        self.assertEqual((self.cust["total_due"], self.cust["total_credit"]), (0.0, 0.0))
        refunds = [q["payload"] for q in self.queued if q.get("collection") == "customer_payments"]
        self.assertEqual([(p["amount"], p["cash_amount"]) for p in refunds], [(-50.0, -50.0)])
        # the same two returns on a local store (Offline / offline-first save the same way)
        self.assertEqual(self._local(), [(63.0, 0.0), (85.5, 50.0), (200, 100), (0.0, 0.0)])

    def _local(self):
        from core import db_setup

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        c = sqlite3.connect(path)
        self.addCleanup(c.close)
        db_setup.initialise(c)
        db_setup.ensure_recent_columns(c)
        c.execute("INSERT INTO customers (id, name) VALUES (3, 'RAM')")
        c.execute("INSERT INTO medicines (id, name, type, unit, stock_qty, mrp) VALUES (?, 'TAB', 'Tablet', '10', 175, 50)", (TAB,))
        c.execute("INSERT INTO medicines (id, name, type, unit, stock_qty, mrp) VALUES (?, 'SYR', 'Syrup', '1', 96, 10)", (SYR,))
        c.execute("INSERT INTO sales (id, bill_no, bill_date, customer_id, total_amount, discount, amount_paid, "
                  "cash_paid, due_amount) VALUES (5, 'SCB5', '2026-10-25', 3, 148.5, 16.5, 50, 50, 98.5)")
        c.execute("INSERT INTO sales_items (sale_id, medicine_id, qty, rate, amount) VALUES (5, ?, 25, 5, 125)", (TAB,))
        c.execute("INSERT INTO sales_items (sale_id, medicine_id, qty, rate, amount) VALUES (5, ?, 4, 10, 40)", (SYR,))
        c.commit()
        from core.customer_service import get_customer_due, recalculate_customer_due

        out = []
        with mock.patch("core.sync_prefs.is_online_mode", return_value=False), \
                mock.patch("core.sync_prefs.get_sync_mode", return_value="offline"):
            recalculate_customer_due(c, 3)
            for b in (body(12, 1, "ledger"), body(13, 3, "cash")):
                r = drs.save_sales_return(c, b)
                self.assertTrue(r["ok"], r)
                out.append((r["refund_amount"], r["refund_payout"]))
            out.append(tuple(int(x) for (x,) in c.execute("SELECT stock_qty FROM medicines ORDER BY id")))
            out.append(tuple(round(v, 2) for v in get_customer_due(c, 3)))
        return out


if __name__ == "__main__":
    unittest.main()
