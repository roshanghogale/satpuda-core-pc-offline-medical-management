"""Owner audit, 25 Oct 2026: the two return-delete bugs.

1. A purchase with a live purchase return is not deleted. Deleting it took every line's stock
   off the shelf, the returned units a second time (they had already gone back to the
   supplier) -- in every mode. Refused like the sales bill: "This purchase has purchase return
   <no>. Delete the return first, then the purchase." Online the store's returns are read (and
   this PC's queued ones); a list that cannot be read refuses with nothing changed.

2. Online, deleting a sales / purchase return asks the store first (strict) and the PC changes
   nothing before its yes. A return the store never had (still queued here) has its queued
   upload dropped -- it never lands, its stock never moves, nothing is reversed for it.

No server is reached: the server client and the queue are patched.
"""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import ExitStack
from unittest import mock

from core import db_setup
from core import desktop_purchase_service as pur
from core import desktop_returns_service as ret
from core import online_mutation_queue as omq

SENTENCE = "This purchase has purchase return PR3. Delete the return first, then the purchase."


def _shop() -> sqlite3.Connection:
    """A purchase of 10 bottles with a purchase return of 3 of them (shelf: 7)."""
    conn = sqlite3.connect(":memory:")
    db_setup.initialise(conn)
    conn.execute("INSERT INTO suppliers (id, name) VALUES (4, 'RDT AGENCY')")
    conn.execute(
        "INSERT INTO medicines (id, name, type, unit, stock_qty) VALUES (9, 'RDT SYRUP', 'Syrup', '1', 7)"
    )
    conn.execute("INSERT INTO purchases (id, supplier_id, deleted) VALUES (5, 4, 0)")
    conn.execute(
        "INSERT INTO purchase_items (purchase_id, medicine_id, qty, free_qty, type, stock_units) "
        "VALUES (5, 9, 10, 0, 'Syrup', 10)"
    )
    conn.execute(
        "INSERT INTO purchase_returns (id, return_no, purchase_id, supplier_id, refund_amount, deleted) "
        "VALUES (3, 'PR3', 5, 4, 120, 0)"
    )
    conn.execute(
        "INSERT INTO purchase_return_items (return_id, medicine_id, qty, stock_units, rate, amount) "
        "VALUES (3, 9, 3, 3, 40, 120)"
    )
    conn.commit()
    return conn


def _stock(conn) -> float:
    return float(conn.execute("SELECT stock_qty FROM medicines WHERE id=9").fetchone()[0])


class Offline(unittest.TestCase):
    def test_the_sentence(self):
        self.assertEqual(pur.purchase_return_blocks_delete("PR3"), SENTENCE)

    def test_refused_until_the_return_goes_then_the_shelf_ends_where_it_started(self):
        conn = _shop()
        res = pur.delete_saved_purchase(conn, {"purchase_id": 5})
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], SENTENCE)
        self.assertEqual(res["code"], "purchase_has_return")
        self.assertIsNotNone(conn.execute("SELECT 1 FROM purchases WHERE id=5").fetchone())
        self.assertEqual(_stock(conn), 7.0, "nothing moved")

        self.assertTrue(ret.delete_purchase_return(conn, {"id": 3}).get("ok"))
        self.assertEqual(_stock(conn), 10.0)
        res2 = pur.delete_saved_purchase(conn, {"purchase_id": 5})
        self.assertTrue(res2.get("ok"), res2)
        self.assertEqual(_stock(conn), 0.0, "the returned units came off once, not twice")

    def test_a_deleted_return_does_not_block(self):
        conn = _shop()
        conn.execute("UPDATE purchase_returns SET deleted=1")
        conn.execute("UPDATE medicines SET stock_qty=10")
        conn.commit()
        self.assertIsNone(pur.purchase_return_on_purchase(conn, 5))
        self.assertTrue(pur.delete_saved_purchase(conn, {"purchase_id": 5}).get("ok"))


class _Online(unittest.TestCase):
    """Online: the sqlite here is an empty shell; the store and the queue are patched."""

    store_rows: list[dict] = []
    pending: list[dict] = []
    list_error: Exception | None = None

    def setUp(self):
        self.queued: list[dict] = []
        self.cancelled: list[dict] = []
        self.conn = sqlite3.connect(":memory:")
        stack = ExitStack()
        self.addCleanup(stack.close)

        def _list(collection, floor=""):
            self.assertEqual(collection, "purchase_returns")
            if self.list_error is not None:
                raise self.list_error
            return [dict(r) for r in self.store_rows]

        for p in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.online_guard.ensure_can_mutate", return_value=None),
            mock.patch("core.server_crud.get_doc",
                       return_value={"id": 5, "purchase_date": "2026-10-20"}),
            mock.patch("core.desktop_returns_service._list_returns_complete", side_effect=_list),
            mock.patch.object(omq, "pending_rows",
                              side_effect=lambda collection=None: [
                                  r for r in self.pending
                                  if collection is None or r["collection"] == collection]),
            mock.patch.object(omq, "enqueue",
                              side_effect=lambda **kw: self.queued.append(kw) or {"local_id": kw.get("local_id")}),
            mock.patch.object(omq, "cancel_matching",
                              side_effect=lambda **kw: self.cancelled.append(kw) or 1),
        ):
            stack.enter_context(p)


class OnlineStoreReturn(_Online):
    store_rows = [{"id": 3, "return_no": "PR3", "purchase_id": 5, "return_date": "2026-10-21"}]

    def test_a_store_return_refuses_and_nothing_is_queued(self):
        res = pur.delete_saved_purchase(self.conn, {"purchase_id": 5})
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], SENTENCE)
        self.assertEqual(self.queued, [])
        self.assertEqual(self.cancelled, [])


class OnlineReturnOfAnotherPurchaseOrDeleted(_Online):
    store_rows = [
        {"id": 3, "return_no": "PR3", "purchase_id": 6, "return_date": "2026-10-21"},
        {"id": 4, "return_no": "PR4", "purchase_id": 5, "return_date": "2026-10-21", "deleted": True},
    ]

    def test_the_delete_goes_ahead(self):
        res = pur.delete_saved_purchase(self.conn, {"purchase_id": 5})
        self.assertTrue(res.get("ok"), res)
        self.assertEqual([(q["collection"], q["op"]) for q in self.queued], [("purchases", "delete")])


class OnlineQueuedReturn(_Online):
    pending = [{"collection": "purchase_returns", "op": "upsert", "local_id": 77,
                "payload": {"purchase_id": 5, "return_no": "PR77"}}]

    def test_a_return_still_in_the_queue_counts(self):
        res = pur.delete_saved_purchase(self.conn, {"purchase_id": 5})
        self.assertFalse(res["ok"])
        self.assertIn("purchase return PR77", res["error"])
        self.assertEqual(self.queued, [])


class OnlineQueuedReturnOnAQueuedPurchase(_Online):
    pending = [{"collection": "purchase_returns", "op": "upsert", "local_id": 78,
                "payload": {"purchase_id": -1000000123, "return_no": "PR78"}}]

    def test_refused_too(self):
        res = pur.delete_saved_purchase(self.conn, {"purchase_id": -1000000123})
        self.assertFalse(res["ok"])
        self.assertEqual(self.cancelled, [], "the queued purchase was not dropped")


class OnlineUnreadable(_Online):
    list_error = ret.ReturnsUnreadable("A store return has no readable date.")

    def test_an_unreadable_list_refuses_and_changes_nothing(self):
        res = pur.delete_saved_purchase(self.conn, {"purchase_id": 5})
        self.assertFalse(res["ok"])
        self.assertIn("NOT deleted", res["error"])
        self.assertIn("could not be read", res["error"])
        self.assertEqual(self.queued, [])
        self.assertEqual(self.cancelled, [])


# ---------------------------------------------------------------------- 2. Online return delete


class _StoreFirst(unittest.TestCase):
    """delete_return_now against a patched store."""

    def setUp(self):
        self.calls: list[tuple] = []
        self.queued: list[dict] = []
        self.cancelled: list[dict] = []
        self.doc: dict | None = {
            "id": 3, "client_uuid": "pr-3", "purchase_id": 5,
            "items": [{"medicine_id": 9, "name": "RDT SYRUP", "qty": 3}],
        }
        self.delete_error: Exception | None = None
        self.upsert_error: Exception | None = None
        stack = ExitStack()
        self.addCleanup(stack.close)

        def _pull(_tok, col, rid):
            self.calls.append(("pull", col, rid))
            return dict(self.doc) if self.doc is not None else None

        def _delete(col, rid):
            self.calls.append(("delete", col, rid))
            if self.delete_error is not None:
                raise self.delete_error
            return {}

        def _upsert(col, docs):
            self.calls.append(("stock", col, [d["stock_ops"][0]["qty_delta"] for d in docs]))
            if self.upsert_error is not None:
                raise self.upsert_error
            return {}

        med = {"id": 9, "local_id": 9, "name": "RDT SYRUP", "type": "Syrup", "unit": "1",
               "stock_qty": 7.0, "version": 3}
        for p in (
            mock.patch("core.server_api.pull_doc", side_effect=_pull),
            mock.patch("core.server_crud._token", return_value="tok"),
            mock.patch("core.server_crud.delete_entity", side_effect=_delete),
            mock.patch("core.server_crud.upsert_docs", side_effect=_upsert),
            mock.patch("core.server_crud.get_doc", return_value=dict(med)),
            mock.patch("core.server_crud.bump_meta", side_effect=lambda d: dict(d)),
            mock.patch("core.server_crud._device_id", return_value="pc"),
            mock.patch("core.online_catalog.medicine_by_id", return_value=dict(med)),
            mock.patch("core.online_catalog.patch_docs", return_value=None),
            mock.patch.object(omq, "enqueue",
                              side_effect=lambda **kw: self.queued.append(kw) or {"local_id": kw.get("local_id")}),
            mock.patch.object(omq, "cancel_matching",
                              side_effect=lambda **kw: self.cancelled.append(kw) or 1),
        ):
            stack.enter_context(p)


class StoreFirstOrder(_StoreFirst):
    def test_the_store_deletes_first_then_the_stock_goes_back(self):
        out = omq.delete_return_now("purchase_returns", 3)
        self.assertEqual(out, {"store_had": True, "warning": ""})
        self.assertEqual(
            self.calls,
            [("pull", "purchase_returns", 3), ("delete", "purchase_returns", 3),
             ("stock", "medicines", [3])],
        )
        self.assertEqual(self.queued, [])

    def test_a_refusal_raises_and_moves_nothing(self):
        self.delete_error = RuntimeError("Delete purchase_returns/3 failed")
        with self.assertRaises(RuntimeError):
            omq.delete_return_now("purchase_returns", 3)
        self.assertNotIn("stock", [c[0] for c in self.calls])
        self.assertEqual(self.queued, [])
        self.assertEqual(self.cancelled, [], "a refused delete keeps whatever is queued")

    def test_a_store_that_cannot_be_read_raises_before_anything(self):
        with mock.patch("core.server_api.pull_doc", side_effect=OSError("no route to host")):
            with self.assertRaises(OSError):
                omq.delete_return_now("purchase_returns", 3)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.cancelled, [])

    def test_a_return_the_store_never_had_drops_its_queued_upload_and_reverses_nothing(self):
        self.doc = None
        out = omq.delete_return_now("purchase_returns", 3)
        self.assertEqual(out["store_had"], False)
        self.assertEqual(self.calls, [("pull", "purchase_returns", 3)])
        self.assertEqual(self.cancelled, [{"collection": "purchase_returns", "local_id": 3}])

    def test_a_failed_stock_push_is_queued_with_its_ops_and_replayed_alone(self):
        self.upsert_error = OSError("timeout")
        out = omq.delete_return_now("purchase_returns", 3)
        self.assertTrue(out["store_had"])
        self.assertIn("will be sent automatically", out["warning"])
        [row] = self.queued
        self.assertEqual((row["collection"], row["op"], row["local_id"]), ("purchase_returns", "delete", 3))
        ops = row["payload"]["_delete_reversal"]
        self.assertEqual([o["qty_delta"] for o in ops], [3])
        self.assertEqual(ops[0]["op_uuid"], "purchase_returns:pr-3:med:9:delete:v1")

        # The queue's replay: the store is not asked to delete again, only the stock is sent.
        self.calls.clear()
        self.upsert_error = None
        omq._flush_return_delete("purchase_returns", row["payload"], 3)
        self.assertEqual(self.calls, [("stock", "medicines", [3])])


class ReturnsScreenOnline(unittest.TestCase):
    """desktop_returns_service: the Returns screen's delete in Online mode."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        db_setup.initialise(self.conn)
        self.conn.execute(
            "INSERT INTO purchase_returns (id, return_no, purchase_id, supplier_id, deleted) "
            "VALUES (3, 'PR3', 5, 4, 0)"
        )
        self.conn.commit()
        self.queued: list[dict] = []
        self.cancelled: list[dict] = []
        self.pending: list[dict] = []
        stack = ExitStack()
        self.addCleanup(stack.close)
        for p in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.online_guard.ensure_can_mutate", return_value=None),
            mock.patch.object(omq, "pending_rows",
                              side_effect=lambda collection=None: [
                                  r for r in self.pending
                                  if collection is None or r["collection"] == collection]),
            mock.patch.object(omq, "enqueue",
                              side_effect=lambda **kw: self.queued.append(kw) or {"local_id": kw.get("local_id")}),
            mock.patch.object(omq, "cancel_matching",
                              side_effect=lambda **kw: self.cancelled.append(kw) or 1),
        ):
            stack.enter_context(p)

    def _hidden(self) -> bool:
        return bool(self.conn.execute("SELECT deleted FROM purchase_returns WHERE id=3").fetchone()[0])

    def test_a_refusal_is_shown_and_nothing_changes_here(self):
        with mock.patch.object(omq, "delete_return_now", side_effect=RuntimeError("HTTP 500: boom")):
            res = ret.delete_purchase_return(self.conn, {"id": 3})
        self.assertFalse(res["ok"])
        self.assertIn("NOT deleted", res["error"])
        self.assertIn("boom", res["error"])
        self.assertFalse(self._hidden(), "the PC let go of a return the store still has")
        self.assertEqual(self.queued, [], "nothing is queued behind the shop's back")

    def test_the_yes_comes_first_then_the_pc_follows(self):
        seen: list[bool] = []

        def _now(col, rid):
            seen.append(self._hidden())
            return {"store_had": True, "warning": ""}

        with mock.patch.object(omq, "delete_return_now", side_effect=_now):
            res = ret.delete_purchase_return(self.conn, {"id": 3})
        self.assertTrue(res["ok"], res)
        self.assertNotIn("queued", res, "the store has answered; nothing is waiting")
        self.assertEqual(seen, [False], "the PC changed its copy before the store said yes")
        self.assertTrue(self._hidden())
        self.assertEqual(self.queued, [])

    def test_a_stock_warning_reaches_the_screen(self):
        with mock.patch.object(omq, "delete_return_now",
                               return_value={"store_had": True, "warning": "stock later"}):
            res = ret.delete_purchase_return(self.conn, {"id": 3})
        self.assertEqual(res.get("warning"), "stock later")

    def test_a_queued_sales_return_drops_its_queued_refund_payout(self):
        self.pending = [
            {"collection": "sales_returns", "op": "upsert", "local_id": 41, "client_uuid": "sr-41",
             "payload": {"return_no": "SR41", "customer_id": 7, "return_date": "2026-10-25"}},
            {"collection": "customer_payments", "op": "upsert", "local_id": 90, "client_uuid": "pay-90",
             "payload": {"reference_no": "SR41", "amount": -400.0, "customer_id": 7}},
            {"collection": "customer_payments", "op": "upsert", "local_id": 91, "client_uuid": "pay-91",
             "payload": {"reference_no": "", "amount": 250.0, "customer_id": 7}},
        ]
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                omq, "delete_return_now", return_value={"store_had": False, "warning": ""}))
            stack.enter_context(mock.patch(
                "core.store_query_client.list_customer_payments", return_value={"rows": []}))
            stack.enter_context(mock.patch("core.server_crud.get_doc", return_value=None))
            res = ret.delete_sales_return(self.conn, {"id": 41})
        self.assertTrue(res["ok"], res)
        self.assertFalse(res["store_had"])
        self.assertEqual(self.cancelled, [{"client_uuid": "pay-90"}])
        self.assertNotIn("warning", res)


if __name__ == "__main__":
    unittest.main()
