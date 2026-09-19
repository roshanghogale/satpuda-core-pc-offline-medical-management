"""The 500-return blind spot: "already returned" on a bill, on a busy shop.

"Already returned" is worked out by listing the store's returns and keeping the
ones for that bill. Online the list was read as ONE page of 500 rows, newest
first, so an old bill's earlier returns sat past row 500, were never counted, and
the bill came up returnable AGAIN: a second refund and a second stock-in.

The live route (routes/storeQuery.js:148-194) reads only limit (capped at 5000),
from and to (inclusive, on the DATE return_date) and sorts return_date DESC,
local_id DESC; `q` is not read. FakeReturnsList below is that route. The lookup
now walks back by date to the bill's own date and never takes a full page as the
end; where the old cap was not hit it makes the old request and nothing else.
An unreadable list is not "nothing returned".
"""
import os
import sqlite3
import sys
import unittest
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import desktop_returns_service as drs  # noqa: E402

TODAY = date.today()


def day(n: int) -> str:
    return (TODAY + timedelta(days=n)).isoformat()


class FakeReturnsList:
    """GET /api/store/returns/{sales,purchases}, as storeQuery.js serves it."""

    def __init__(self, rows):
        self.rows = sorted(rows, key=lambda r: (r["return_date"], r["id"]), reverse=True)
        self.calls = []

    def __call__(self, *, limit=2000, from_date="", to_date=""):
        self.calls.append((int(limit), from_date or "", to_date or ""))
        lim = min(int(limit) or 200, 5000)
        hit = [
            r for r in self.rows
            if (not from_date or r["return_date"] >= str(from_date)[:10])
            and (not to_date or r["return_date"] <= str(to_date)[:10])
        ]
        page = [dict(r) for r in hit[:lim]]
        return {"rows": page, "total": len(page)}


def busy_shop(parent_key, old_parent, old_returns, medicine_id=7, recent=1500, per_day=5):
    """`recent` returns on other bills over the last recent/per_day days, plus
    `old_returns` [(days_back, qty)] of `medicine_id` on the old bill."""
    rows, docs = [], {}
    rid = 0
    for i in range(recent):
        rid += 1
        rows.append({"id": rid, parent_key: 900000 + i, "return_date": day(-(i // per_day))})
        docs[rid] = {"items": [{"medicine_id": 99, "qty": 1}]}
    for back, qty in old_returns:
        rid += 1
        rows.append({"id": rid, parent_key: old_parent, "return_date": day(back)})
        docs[rid] = {"items": [{"medicine_id": medicine_id, "qty": qty}]}
    return rows, docs


SALE = {
    "id": 5, "bill_no": "S-5", "bill_date": day(-400), "created_at": day(-400) + "T10:00:00Z",
    "customer_id": 3, "customer_name": "Ram",
    "items": [{"medicine_id": 7, "medicine_name": "AMOXY 250", "qty": 10, "rate": 5, "amount": 50}],
}
PURCHASE = {
    "id": 77, "bill_number": "B-77", "purchase_date": day(-500), "supplier_id": 4,
    "supplier_name": "ZZ SUPPLIER",
    "items": [{"medicine_id": 7, "medicine_name": "AMOXY 250", "batch_no": "A1", "qty": 60,
               "rate": 7.5, "amount": 450, "type": "Capsule", "unit": "10"}],
}


def sale_body(qty):
    return {"sale_id": 5, "customer_id": 3, "customer_name": "Ram", "bill_no": "S-5",
            "discount": 0, "reason": "", "settle_mode": "ledger",
            "items": [{"medicine_id": 7, "name": "AMOXY 250", "qty": qty, "rate": 5}]}


class _Online(unittest.TestCase):
    """Online: the engine sqlite is :memory:, bill and returns come from the server."""

    returns_list = "core.store_query_client.list_sales_returns"

    def start(self, rows, docs, bills, list_side_effect=None, pending=None):
        self.server = FakeReturnsList(rows)

        def get_doc(col, lid):
            if col in ("sales", "purchases"):
                b = bills.get((col, int(lid)))
                return dict(b) if b else None
            return docs.get(int(lid))

        patches = [
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.server_crud.get_doc", side_effect=get_doc),
            mock.patch(self.returns_list, side_effect=list_side_effect or self.server),
            mock.patch("core.online_catalog.medicine_by_id", return_value={}),
            mock.patch("core.online_catalog.find_customer_by_id", return_value={}),
            mock.patch("core.purchase_service.get_supplier_due", return_value=(0.0, 0.0)),
            mock.patch("core.online_mutation_queue.pending_by_local_id",
                       side_effect=lambda col, lid: pending),
            mock.patch.object(drs, "_pending_returned_qty", return_value=0.0),
            mock.patch("core.online_mutation_queue.enqueue",
                       side_effect=AssertionError("a refused return was queued")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)


class AnOldSaleOnABusyShop(_Online):
    def setUp(self):
        rows, docs = busy_shop("sale_id", 5, [(-390, 4), (-380, 3)])
        self.start(rows, docs, {("sales", 5): SALE})

    def test_the_old_single_page_never_held_this_bills_returns(self):
        first = self.server(limit=500)["rows"]
        self.assertEqual(len(first), 500)
        self.assertEqual([r for r in first if r["sale_id"] == 5], [])

    def test_every_earlier_return_on_the_bill_is_counted(self):
        got = drs._online_returned_map(
            "sales_returns", "sale_id", 5, floor=drs._returns_floor(SALE["bill_date"]))
        self.assertEqual(got, {7: 7.0})

    def test_the_bill_loads_with_only_what_is_left(self):
        res = drs.load_sales_bill_for_return(self.conn, 5)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["items"][0]["remaining_qty"], 3.0)

    def test_a_second_return_of_the_same_strips_is_refused_at_save(self):
        why = drs._sales_return_request_error(self.conn, 5, sale_body(4), online=True)
        self.assertIn("Cannot return more than 3", why)
        res = drs.save_sales_return(self.conn, sale_body(4))
        self.assertFalse(res["ok"])
        self.assertIn("Cannot return more than 3", res["error"])

    def test_what_is_left_may_still_go_back(self):
        self.assertEqual(drs._sales_return_request_error(self.conn, 5, sale_body(3), True), "")


class AnOldPurchaseOnABusyShop(_Online):
    returns_list = "core.store_query_client.list_purchase_returns"

    def setUp(self):
        rows, docs = busy_shop("purchase_id", 77, [(-450, 20), (-420, 25)])
        self.start(rows, docs, {("purchases", 77): PURCHASE})

    def test_the_bill_loads_with_only_what_is_left(self):
        res = drs.load_purchase_for_return(self.conn, 77)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["items"][0]["remaining_qty"], 15.0)


class WhereTheOldCapWasNotHit(_Online):
    def setUp(self):
        rows = [{"id": i, "sale_id": 5 if i <= 2 else 6, "return_date": day(-i)}
                for i in range(1, 11)]
        docs = {i: {"items": [{"medicine_id": 7, "qty": 1}]} for i in range(1, 11)}
        self.start(rows, docs, {("sales", 5): SALE})

    def test_it_is_the_one_request_the_old_code_made(self):
        got = drs._online_returned_map(
            "sales_returns", "sale_id", 5, floor=drs._returns_floor(SALE["bill_date"]))
        self.assertEqual(got, {7: 2.0})
        self.assertEqual(self.server.calls, [(500, "", "")])


class TheWalkStopsAtTheBill(_Online):
    def test_a_recent_bill_does_not_read_the_whole_history(self):
        recent = dict(SALE, bill_date=day(-10), created_at=day(-10))
        rows, docs = busy_shop("sale_id", 5, [(-5, 2)], recent=12000, per_day=10)
        self.start(rows, docs, {("sales", 5): recent})
        res = drs.load_sales_bill_for_return(self.conn, 5)
        self.assertEqual(res["items"][0]["remaining_qty"], 8.0)
        self.assertEqual(len(self.server.calls), 1)

    def test_an_old_bill_on_a_huge_shop_is_read_all_the_way_back(self):
        rows, docs = busy_shop("sale_id", 5, [(-1300, 6)], recent=12000, per_day=10)
        old = dict(SALE, bill_date=day(-1310), created_at=day(-1310))
        self.start(rows, docs, {("sales", 5): old})
        res = drs.load_sales_bill_for_return(self.conn, 5)
        self.assertEqual(res["items"][0]["remaining_qty"], 4.0)
        self.assertGreater(len(self.server.calls), 2)
        self.assertEqual(self.server.calls[0], (500, "", ""))
        self.assertTrue(all(limit == 5000 for limit, _f, _t in self.server.calls[1:]))


class AFullPageIsNeverTheEnd(_Online):
    def setUp(self):
        # 5,001 returns on one day: no page the server will serve can hold them.
        rows = [{"id": i, "sale_id": 6, "return_date": day(-1)} for i in range(1, 5002)]
        rows.append({"id": 9000, "sale_id": 5, "return_date": day(-300)})
        docs = {9000: {"items": [{"medicine_id": 7, "qty": 9}]}}
        self.start(rows, docs, {("sales", 5): SALE})

    def test_it_is_unreadable_not_zero(self):
        with self.assertRaises(drs.ReturnsUnreadable):
            drs._online_returned_map("sales_returns", "sale_id", 5)
        res = drs.load_sales_bill_for_return(self.conn, 5)
        self.assertFalse(res["ok"])
        self.assertIn("earlier returns", res["error"])
        self.assertIn("earlier returns",
                      drs._sales_return_request_error(self.conn, 5, sale_body(1), True))


class AnUnreadableServerIsNotNothingReturned(_Online):
    def test_the_bill_does_not_load_as_fully_returnable(self):
        self.start([], {}, {("sales", 5): SALE},
                   list_side_effect=RuntimeError("timed out"))
        res = drs.load_sales_bill_for_return(self.conn, 5)
        self.assertFalse(res["ok"])
        res = drs.save_sales_return(self.conn, sale_body(1))
        self.assertFalse(res["ok"])
        self.assertIn("earlier returns", res["error"])

    def test_a_return_document_that_cannot_be_read_is_not_zero_either(self):
        rows = [{"id": 1, "sale_id": 5, "return_date": day(-10)}]
        self.start(rows, {}, {("sales", 5): SALE})  # no document for return 1
        self.assertFalse(drs.load_sales_bill_for_return(self.conn, 5)["ok"])

    def test_a_bill_still_in_the_upload_queue_still_loads(self):
        # Not on the server yet, so the server can hold no return against it.
        self.start([], {}, {}, list_side_effect=RuntimeError("timed out"),
                   pending={"payload": dict(SALE)})
        res = drs.load_sales_bill_for_return(self.conn, 5)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["items"][0]["remaining_qty"], 10.0)


if __name__ == "__main__":
    unittest.main()
