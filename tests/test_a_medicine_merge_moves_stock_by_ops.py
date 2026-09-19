"""Merging two Online rows of one medicine moves stock by record and never writes a deleted row.

The PC merges medicines that share a name and batch. Three things went wrong in store 127:

  * the row that went was written straight to stock 0 and the row that stayed to the summed
    figure, with no stock movement logged: 36 rows lost 2,144 units with no record;
  * sale and purchase lines still pointed at the rows that were deleted;
  * four deleted rows (570, 627, 968, 1128) were written again 472 times. Each had two live
    twins. The merge sent the twin that should go without its own client_uuid, stamped the
    name+batch one instead, and the server landed that write on the deleted row that owns the
    uuid -- so the twin was never deleted and the next pass tried again.

Now stock moves by a pair of movements (off the row that goes, onto the row that stays),
lines are pointed at the row that stays before the other is deleted, a row that cannot have
all its lines moved is left alone, and each row is written under its own id and client_uuid.

The store query client and the server client are patched; nothing reaches a server.
"""
from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest import mock

from core import purchase_service  # noqa: E402
from core.client_uuid import deterministic_medicine_uuid  # noqa: E402

NAME, BATCH = "AMOXY BOLUS", "AB1"


class _Store(unittest.TestCase):
    def setUp(self):
        self.meds = {
            244: {"id": 244, "name": NAME, "batch_no": BATCH, "type": "Bolus", "stock_qty": 10.0,
                  "version": 5, "client_uuid": "cu-244", "deleted": False, "is_hidden": False},
            316: {"id": 316, "name": NAME, "batch_no": BATCH, "type": "Bolus", "stock_qty": 4.0,
                  "version": 2, "client_uuid": "", "deleted": False, "is_hidden": False},
            # Deleted earlier; it owns the name+batch client_uuid.
            570: {"id": 570, "name": NAME, "batch_no": BATCH, "type": "Bolus", "stock_qty": 0.0,
                  "version": 2, "client_uuid": deterministic_medicine_uuid(NAME, BATCH),
                  "deleted": True, "is_hidden": True},
            1: {"id": 1, "name": "OTHER TAB", "batch_no": "O1", "type": "Tablet", "stock_qty": 7.0,
                "version": 1, "client_uuid": "cu-1", "deleted": False, "is_hidden": False},
        }
        self.sales = {900: {"id": 900, "bill_no": "SCB44/FY2026-27", "total_amount": 60.0,
                            "customer_id": 7, "version": 3, "client_uuid": "s-900",
                            "items": [{"medicine_id": 316, "name": NAME, "batch_no": BATCH,
                                       "qty": 2, "rate": 20.0, "gst_percent": 5.0, "amount": 40.0},
                                      {"medicine_id": 1, "name": "OTHER TAB", "batch_no": "O1",
                                       "qty": 1, "rate": 20.0, "gst_percent": 12.0,
                                       "amount": 20.0}]}}
        self.purchases = {70: {"id": 70, "purchase_no": "9/FY2026-27", "total_amount": 55.0,
                               "final_amount": 55.0, "total_gst": 2.6, "version": 1,
                               "client_uuid": "p-70",
                               "items": [{"medicine_id": 316, "name": NAME, "batch_no": BATCH,
                                          "qty": 1, "rate": 55.0, "unit": "1",
                                          "tablets_per_stripe": 1}]}}
        self.landed: list[int] = []
        self.ops: list[tuple[int, str, int]] = []
        self.absolute: list[int] = []
        self.pushed_docs: dict[str, list[dict]] = {"sales": [], "purchases": []}
        self.refuse_sales = False

        def inventory(**kw):
            if int(kw.get("offset") or 0) > 0:
                return {"rows": []}
            live = [dict(m) for m in self.meds.values() if not m.get("deleted")]
            return {"rows": live}

        def get_doc(collection, local_id):
            held = {"medicines": self.meds, "sales": self.sales,
                    "purchases": self.purchases}.get(collection, {}).get(int(local_id))
            return dict(held) if held else None

        def upsert_docs(collection, docs):
            for doc in docs:
                target = int(doc.get("id") or 0)
                cu = str(doc.get("client_uuid") or "")
                owner = next((mid for mid, held in self.meds.items()
                              if cu and held.get("client_uuid") == cu), None)
                if owner is not None:
                    target = owner  # the server lands the write on the row that owns the uuid
                self.landed.append(target)
                held = self.meds.setdefault(target, {"id": target, "stock_qty": 0.0})
                ops = doc.get("stock_ops") or []
                for op in ops:
                    self.ops.append((target, op["op_uuid"], int(op["qty_delta"])))
                    held["stock_qty"] = float(held.get("stock_qty") or 0) + int(op["qty_delta"])
                if not ops and float(doc.get("stock_qty") or 0) != float(held.get("stock_qty") or 0):
                    self.absolute.append(target)
                    held["stock_qty"] = float(doc.get("stock_qty") or 0)
                for key in ("deleted", "is_hidden"):
                    if key in doc:
                        held[key] = bool(doc[key])
            return {}

        def push_bundle(bundle):
            for collection in ("sales", "purchases"):
                for doc in bundle.get(collection) or []:
                    if collection == "sales" and self.refuse_sales:
                        raise RuntimeError("Cannot reach server")
                    self.pushed_docs[collection].append(dict(doc))
                    store = self.sales if collection == "sales" else self.purchases
                    store[int(doc["id"])] = dict(doc)
            if bundle.get("medicines"):
                upsert_docs("medicines", bundle["medicines"])
            return {}

        def listing(store):
            def lister(**kw):
                batch = str(kw.get("batch") or "").upper()
                rows = [{"id": doc["id"]} for doc in store.values()
                        if any(batch in str(it.get("batch_no") or "").upper()
                               for it in doc.get("items") or [])]
                return {"rows": rows}
            return lister

        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.store_query_client.list_inventory", side_effect=inventory),
            mock.patch("core.store_query_client.list_sales", side_effect=listing(self.sales)),
            mock.patch("core.store_query_client.list_purchases",
                       side_effect=listing(self.purchases)),
            mock.patch("core.server_crud.get_doc", side_effect=get_doc),
            mock.patch("core.server_crud.upsert_docs", side_effect=upsert_docs),
            mock.patch("core.server_crud.push_bundle", side_effect=push_bundle),
            mock.patch("core.server_crud._device_id", return_value="dev"),
            mock.patch("core.online_guard.ensure_can_mutate", return_value=None),
            mock.patch("core.online_catalog.invalidate", return_value=None),
            mock.patch("core.online_catalog.patch_docs", return_value=None),
        ):
            stack.enter_context(patch)

    def merge(self, keep_ids=(244,)):
        # 244 is the row the owner chose to keep.
        return purchase_service.merge_online_medicine_batch_duplicates(keep_ids=keep_ids)

    def nothing_written(self):
        self.assertEqual(self.landed, [], "a medicine row was written")
        self.assertEqual(self.ops, [])
        self.assertEqual(self.pushed_docs, {"sales": [], "purchases": []})
        self.assertEqual((self.meds[244]["stock_qty"], self.meds[316]["stock_qty"]), (10.0, 4.0))
        self.assertFalse(self.meds[316]["deleted"])


class MergingTwoLiveRows(_Store):
    def test_no_write_lands_on_a_deleted_row(self):
        self.merge()
        self.assertNotIn(570, self.landed, "a deleted medicine was written again")

    def test_stock_moves_by_record_not_by_an_absolute_figure(self):
        self.merge()
        self.assertEqual(self.absolute, [], "stock was written with no movement logged")
        self.assertEqual(sorted((mid, qty) for mid, _uuid, qty in self.ops), [(244, 4), (316, -4)])
        self.assertEqual((self.meds[244]["stock_qty"], self.meds[316]["stock_qty"]), (14.0, 0.0))
        self.assertTrue(self.meds[316]["deleted"])

    def test_lines_point_at_the_row_that_stays(self):
        self.merge()
        [sale] = self.pushed_docs["sales"]
        self.assertEqual([it["medicine_id"] for it in sale["items"]], [244, 1])
        self.assertEqual((sale["bill_no"], sale["total_amount"]), ("SCB44/FY2026-27", 60.0))
        self.assertEqual(sale["items"][0]["gst_percent"], 5.0)
        [purchase] = self.pushed_docs["purchases"]
        self.assertEqual([it["medicine_id"] for it in purchase["items"]], [244])
        self.assertEqual((purchase["total_amount"], purchase["total_gst"]), (55.0, 2.6))

    def test_a_second_pass_writes_nothing(self):
        self.merge()
        written = len(self.landed)
        self.merge()
        self.assertEqual(len(self.landed), written)

    def test_a_row_whose_lines_cannot_be_moved_is_left_alone(self):
        self.refuse_sales = True
        self.merge()
        self.assertFalse(self.meds[316]["deleted"])
        self.assertEqual(self.ops, [])
        self.assertEqual((self.meds[244]["stock_qty"], self.meds[316]["stock_qty"]), (10.0, 4.0))


class NothingMergesWithoutTheOwnersPick(_Store):
    """The merge changes store data: stock, deleted rows, and whole bills pushed again.

    It ran by itself -- from Inventory every 15 minutes and after every Online purchase save
    or edit -- keeping whichever row it liked best. Re-pointing a sale line pushes the whole
    stored sale, and the server then recomputes that customer's balance. The audit asked for
    the owner to pick the row to keep first. Now only a merge the owner asks for, naming the
    row that stays, writes anything.
    """

    def test_the_unattended_pass_writes_nothing(self):
        self.assertEqual(purchase_service.hide_online_zero_stock_duplicates(), 0)
        self.nothing_written()

    def test_a_merge_with_no_pick_writes_nothing(self):
        self.assertEqual(self.merge(keep_ids=()), 0)
        self.nothing_written()

    def test_a_pick_of_two_rows_in_one_group_writes_nothing(self):
        self.assertEqual(self.merge(keep_ids=(244, 316)), 0)
        self.nothing_written()

    def test_the_row_the_owner_picks_is_the_row_that_stays(self):
        self.assertEqual(self.merge(keep_ids=(316,)), 1)
        self.assertEqual(sorted((mid, qty) for mid, _uuid, qty in self.ops), [(244, -10), (316, 10)])
        self.assertEqual((self.meds[244]["stock_qty"], self.meds[316]["stock_qty"]), (0.0, 14.0))
        self.assertTrue(self.meds[244]["deleted"])
        self.assertFalse(self.meds[316]["deleted"])

    def test_inventory_and_purchase_saves_start_no_merge(self):
        with mock.patch.object(purchase_service, "merge_online_medicine_batch_duplicates") as merge:
            purchase_service.hide_online_zero_stock_duplicates()
        merge.assert_not_called()
        import ast
        import os

        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "core", "desktop_pages_service.py")
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        names = {getattr(node, "id", getattr(node, "attr", "")) for node in ast.walk(tree)}
        self.assertFalse(
            names & {"hide_online_zero_stock_duplicates", "merge_online_medicine_batch_duplicates",
                     "_kick_online_medicine_merge"},
            "Inventory still starts the medicine merge by itself",
        )


class MergingAClone(_Store):
    def test_identical_stock_is_kept_once_and_the_rest_is_logged(self):
        self.meds[316]["stock_qty"] = 10.0  # a copy made by another device
        self.merge()
        self.assertEqual(sorted((mid, qty) for mid, _uuid, qty in self.ops), [(316, -10)])
        self.assertEqual(self.meds[244]["stock_qty"], 10.0)
        self.assertEqual(self.absolute, [])


if __name__ == "__main__":
    unittest.main()
