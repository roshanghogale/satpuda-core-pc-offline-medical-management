"""An Online delete takes back every line of a bill, also when one medicine is on several lines.

Staging, 14 Sep (proof of round 10, runs R3 and G2 on stores 4 and 127, in the fixed, released
and pre-round engines alike): a purchase with MOLICOLD on two lines, 3 + 3 strips of 10, was
deleted and the store took back -30, not -60. The queue wrote one stock movement per LINE, all
under the one key the store knows the bill's medicine by ("purchase:<uuid>:med:<mid>:delete:v1").
The store applies a key once: the second line's -30 was skipped as already seen, and a 3 + 2
bill's -20 was refused ("already applied with qty_delta -30, pushed again with -20"). The queue
then retried, found the purchase gone, and marked the row done: the rest of the stock stayed on
the shelf and nobody was told.

Now each medicine's lines are summed into one movement. The movement is kept on the queue row
before the bill is deleted, so a retry after a delete that landed (its stock push lost to the
network) still takes the stock back -- under the same keys, so never twice -- and does not
delete a new bill that took the old one's id in between. A delete the store refuses moves nothing.

A fake store stands in for server-live: stockOperations.applyStockOperation (a key applied once;
the same key with another qty refused), syncService.upsertMedicine (a document with stock_ops
moves only by its ops) and hardDeleteDoc (a missing bill deletes as ok). Nothing reaches a server.
"""
from __future__ import annotations

import copy
import os
import tempfile
import unittest
from contextlib import ExitStack
from unittest import mock

from core import online_mutation_queue as omq  # noqa: E402
from core import server_api  # noqa: E402


def _strip_line(mid, name, strips, pack=10):
    return {"medicine_id": mid, "name": name, "type": "Tablet", "qty": float(strips),
            "free_qty": 0.0, "unit": str(pack), "quantity_value": str(pack),
            "tablets_per_stripe": pack, "rate": 12.0, "mrp": 20.0, "gst_pct": 5.0,
            "item_amount": 12.0 * strips}


class FakeStore:
    def __init__(self):
        self.purchases: dict[int, dict] = {}
        self.sales: dict[int, dict] = {}
        self.sales_returns: dict[int, dict] = {}
        self.medicines = {
            17485: {"id": 17485, "local_id": 17485, "name": "MOLICOLD", "type": "Tablet",
                    "unit": "10", "stock_qty": 300.0, "version": 7,
                    "updated_at": "2026-09-14T08:00:00.000Z"},
            17511: {"id": 17511, "local_id": 17511, "name": "ACLOTON-SP", "type": "Tablet",
                    "unit": "10", "stock_qty": 900.0, "version": 3,
                    "updated_at": "2026-09-14T08:00:00.000Z"},
        }
        self.ledger: dict[str, dict] = {}
        self.refused_ops: list[str] = []
        self.deletes: list[tuple[str, int]] = []
        self.refuse_delete = ""
        self.fail_next_medicine_push = False

    # stockOperations.applyStockOperation
    def apply_op(self, op) -> str:
        key = str(op.get("op_uuid") or "")
        qty = int(op.get("qty_delta") or 0)
        held = self.ledger.get(key)
        if held is not None:
            if qty != int(held["qty_delta"]):
                self.refused_ops.append(key)
                return "failed"
            return "skipped"
        self.ledger[key] = dict(op)
        med = self.medicines[int(op["medicine_id"])]
        med["stock_qty"] = float(med["stock_qty"]) + qty
        med["version"] = int(med["version"]) + 1
        return "applied"

    def pull_doc(self, token, collection, local_id, **kwargs):
        table = {"purchases": self.purchases, "sales": self.sales,
                 "sales_returns": self.sales_returns,
                 "medicines": self.medicines}.get(collection, {})
        row = table.get(int(local_id))
        return copy.deepcopy(row) if row else None

    def delete_doc(self, token, collection, local_id, **kwargs):
        if self.refuse_delete:
            raise server_api.ServerHttpError(409, self.refuse_delete)
        self.deletes.append((collection, int(local_id)))
        if collection == "sales_returns":  # softDeleteDoc: the row stays, marked deleted
            self.sales_returns[int(local_id)]["deleted"] = True
            return {"deleted": True, "id": int(local_id)}
        {"purchases": self.purchases, "sales": self.sales}[collection].pop(int(local_id), None)
        return {"deleted": True, "hard": True, "id": int(local_id)}

    def push_collection(self, token, collection, docs, **kwargs):
        assert collection == "medicines", collection
        if self.fail_next_medicine_push:
            self.fail_next_medicine_push = False
            raise ConnectionError("connection reset while pushing medicines")
        results = []
        for doc in docs:
            statuses = [self.apply_op(op) for op in doc.get("stock_ops") or []]
            if "failed" in statuses:
                results.append({"id": doc["id"], "status": "failed",
                                "error": "stock op was already applied with another qty_delta. "
                                         "Nothing was changed."})
            else:
                results.append({"id": doc["id"], "status": "upserted"})
        return {"results": results}


class _Deletes(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        folder = tempfile.mkdtemp(prefix="omq-delete-")
        self.queue_file = os.path.join(folder, "queue.json")
        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch.object(omq, "_queue_path", return_value=self.queue_file),
            mock.patch.object(omq, "kick_flush", return_value=None),
            mock.patch.object(omq, "_recompute_supplier_balance_online", return_value=None),
            mock.patch.object(omq, "_recompute_customer_balance_online", return_value=None),
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.server_api.pull_doc", side_effect=self.store.pull_doc),
            mock.patch("core.server_api.delete_doc", side_effect=self.store.delete_doc),
            mock.patch("core.server_api.push_collection", side_effect=self.store.push_collection),
            mock.patch("core.server_crud._token", return_value="t"),
            mock.patch("core.server_crud._device_id", return_value="pc-a"),
            mock.patch("core.online_guard.ensure_can_mutate", return_value=None),
            mock.patch("core.online_guard._emit_status", return_value=None),
            mock.patch("core.online_catalog.medicine_by_id", return_value=None),
            mock.patch("core.online_catalog.patch_docs", return_value=None),
            mock.patch("core.store_live_refresh.emit", return_value=None),
        ):
            stack.enter_context(patch)

    def queue_delete(self, collection, bill_id):
        key = "purchase_id" if collection == "purchases" else "sale_id"
        omq.enqueue(collection=collection, op="delete", payload={key: bill_id}, local_id=bill_id)

    def flush(self):
        """One pass of the flush loop over the head row, as _flush_loop runs it."""
        [row] = omq.pending_rows()
        try:
            omq._flush_one(row)
        except Exception:
            raise
        omq._mark(str(row.get("id")), omq._STATUS_DONE)

    def stock(self, mid):
        return self.store.medicines[mid]["stock_qty"]

    def moves(self, mid):
        return {k: int(op["qty_delta"]) for k, op in self.store.ledger.items()
                if int(op.get("medicine_id") or 0) == mid}

    def purchase(self, pid, cu, lines):
        self.store.purchases[pid] = {"id": pid, "local_id": pid, "client_uuid": cu,
                                     "supplier_id": 297, "purchase_no": "111/FY2026-27",
                                     "items": copy.deepcopy(lines), "version": 3}

    def sale(self, sid, cu, lines):
        self.store.sales[sid] = {"id": sid, "local_id": sid, "client_uuid": cu,
                                 "customer_id": 15097, "bill_no": "SCB1412/FY2026-27",
                                 "items": copy.deepcopy(lines), "version": 4}


class APurchaseWithOneMedicineOnSeveralLines(_Deletes):
    def test_two_equal_lines_are_both_taken_back(self):
        self.purchase(3026, "fe8449df", [_strip_line(17485, "MOLICOLD", 3),
                                         _strip_line(17485, "MOLICOLD", 3)])
        self.queue_delete("purchases", 3026)
        self.flush()
        self.assertEqual(self.stock(17485), 240.0, "the second line's 30 stayed on the shelf")
        self.assertEqual(self.moves(17485), {"purchase:fe8449df:med:17485:delete:v1": -60})
        self.assertNotIn(3026, self.store.purchases)

    def test_two_different_lines_are_not_refused(self):
        self.purchase(3027, "gd-3027", [_strip_line(17485, "MOLICOLD", 3),
                                        _strip_line(17485, "MOLICOLD", 2)])
        self.queue_delete("purchases", 3027)
        self.flush()
        self.assertEqual(self.store.refused_ops, [], "the store refused the second line's movement")
        self.assertEqual(self.stock(17485), 250.0)
        self.assertEqual(self.moves(17485), {"purchase:gd-3027:med:17485:delete:v1": -50})

    def test_each_medicine_gets_its_own_whole_movement(self):
        self.purchase(3028, "p-3028", [_strip_line(17485, "MOLICOLD", 3),
                                       _strip_line(17511, "ACLOTON-SP", 6),
                                       _strip_line(17485, "MOLICOLD", 1)])
        self.queue_delete("purchases", 3028)
        self.flush()
        self.assertEqual((self.stock(17485), self.stock(17511)), (260.0, 840.0))
        self.assertEqual(len(self.store.ledger), 2)


class ADeleteWhoseStockPushIsLost(_Deletes):
    def test_the_retry_still_takes_the_stock_back_once(self):
        self.purchase(3026, "fe8449df", [_strip_line(17485, "MOLICOLD", 3),
                                         _strip_line(17485, "MOLICOLD", 2)])
        self.queue_delete("purchases", 3026)
        self.store.fail_next_medicine_push = True
        with self.assertRaises(ConnectionError):
            self.flush()
        self.assertNotIn(3026, self.store.purchases, "the bill was deleted before the stock push")
        self.assertEqual(self.stock(17485), 300.0)
        omq._bump_attempt(str(omq.pending_rows()[0]["id"]))
        self.flush()  # the queue's retry: the purchase is gone now
        self.assertEqual(self.stock(17485), 250.0, "the retry found the bill gone and moved nothing")
        self.flush_again_changes_nothing()

    def flush_again_changes_nothing(self):
        omq.enqueue(collection="purchases", op="delete", payload={"purchase_id": 3026},
                    local_id=3026)
        self.flush()
        self.assertEqual(self.stock(17485), 250.0)

    def test_a_new_bill_that_took_the_id_meanwhile_is_left_alone(self):
        self.purchase(3026, "old-3026", [_strip_line(17485, "MOLICOLD", 3),
                                         _strip_line(17485, "MOLICOLD", 3)])
        self.queue_delete("purchases", 3026)
        self.store.fail_next_medicine_push = True
        with self.assertRaises(ConnectionError):
            self.flush()
        # The server hard-deletes purchases, so the next purchase saved reuses id 3026.
        self.purchase(3026, "new-3026", [_strip_line(17511, "ACLOTON-SP", 2)])
        self.store.medicines[17511]["stock_qty"] = 920.0
        self.flush()
        self.assertIn(3026, self.store.purchases, "the retry deleted another bill")
        self.assertEqual(self.store.purchases[3026]["client_uuid"], "new-3026")
        self.assertEqual(self.stock(17485), 240.0, "the old bill's stock was not taken back")
        self.assertEqual(self.stock(17511), 920.0, "the new bill's stock was taken back")


class ADeleteTheStoreRefuses(_Deletes):
    def test_it_moves_no_stock(self):
        self.purchase(2997, "cu-2997", [_strip_line(17485, "MOLICOLD", 3),
                                        _strip_line(17485, "MOLICOLD", 3)])
        self.queue_delete("purchases", 2997)
        self.store.refuse_delete = "Cannot delete purchase - MOLICOLD was sold after it"
        with self.assertRaises(server_api.ServerHttpError):
            self.flush()
        self.assertTrue(omq._is_permanent_refusal(server_api.ServerHttpError(409, "x")))
        self.assertEqual(self.stock(17485), 300.0)
        self.assertEqual(self.store.ledger, {})
        self.assertIn(2997, self.store.purchases)


class ASaleWithOneMedicineOnSeveralLines(_Deletes):
    def test_every_line_goes_back_on_the_shelf(self):
        self.sale(36478, "s-36478", [{"medicine_id": 17485, "name": "MOLICOLD", "qty": 2},
                                     {"medicine_id": 17485, "name": "MOLICOLD", "qty": 3}])
        self.queue_delete("sales", 36478)
        self.flush()
        self.assertEqual(self.store.refused_ops, [])
        self.assertEqual(self.stock(17485), 305.0, "only the first line went back on the shelf")
        self.assertEqual(self.moves(17485), {"sale:s-36478:med:17485:delete:v1": 5})

    def test_a_retry_after_a_lost_stock_push_gives_it_back_once(self):
        self.sale(36478, "s-36478", [{"medicine_id": 17485, "name": "MOLICOLD", "qty": 2},
                                     {"medicine_id": 17485, "name": "MOLICOLD", "qty": 2}])
        self.queue_delete("sales", 36478)
        self.store.fail_next_medicine_push = True
        with self.assertRaises(ConnectionError):
            self.flush()
        self.flush()
        self.assertEqual(self.stock(17485), 304.0)
        self.assertEqual(self.moves(17485), {"sale:s-36478:med:17485:delete:v1": 4})


class AReturnWithOneMedicineOnSeveralLines(_Deletes):
    def test_cancelling_it_takes_every_line_back_off_the_shelf(self):
        self.store.sales_returns[812] = {
            "id": 812, "local_id": 812, "client_uuid": "sr-812", "customer_id": 15097,
            "items": [{"medicine_id": 17485, "name": "MOLICOLD", "qty": 2},
                      {"medicine_id": 17485, "name": "MOLICOLD", "qty": 1}],
        }
        omq.enqueue(collection="sales_returns", op="delete", payload={"id": 812}, local_id=812)
        self.flush()
        self.assertEqual(self.store.refused_ops, [])
        self.assertEqual(self.stock(17485), 297.0, "only the first line was taken back")
        self.assertEqual(self.moves(17485), {"sales_returns:sr-812:med:17485:delete:v1": -3})


if __name__ == "__main__":
    unittest.main()
