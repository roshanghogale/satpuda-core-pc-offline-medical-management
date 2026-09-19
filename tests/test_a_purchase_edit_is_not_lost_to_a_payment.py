"""A purchase edit is never lost to a supplier payment that lands while it is on its way.

The race, on staging in the fix round (runs n_s4_pc_OPP0 and n_s4_android_OPP0): device B reads
purchase 2997 at version 3 and stamps its edit version 4 at 05:51:03.38. Before the edit reaches
the store, device A saves a supplier payment; the server's own cascade clears the bill and writes
it at version 4 with updated_at NOW() (05:51:03.75). B's edit then arrives at the same version
with an older stamp and the server skips the purchase whole -- while the same bundle's stock
movements are applied. The lines stayed as they were, the stock moved by the edit, and the save
answered ok with no warning.

The PC now reads the store's answer. A purchase the store skipped is read back. When the store's
copy differs from the copy the edit was worked out from only in what the supplier cascade writes
(due, due_amount, total_due and the two cleared flags), the edit goes again over that copy's
version, and the server's cascade sets the dues from the ledger. When another device's edit, or
a delete, got there first, this edit is not forced over it: the save says so, and the stock this
edit moved is taken back wherever it certainly moved.

A fake store stands in for server-live: its version/updated_at rule (upsertHelper
shouldAcceptIncoming), its purchase FIFO cascade (partyDueCascade cascadePurchasesFifo, run on a
payment and after an accepted purchase) and its once-per-op_uuid stock ledger
(stockOperations.applyStockOperation). Nothing reaches a server.
"""
from __future__ import annotations

import copy
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest import mock

from core import purchase_service, server_crud  # noqa: E402

SUPPLIER = 297
CU = "cu-2997"
STOCK = {17512: 600.0, 17513: 200.0, 17514: 50.0}


def r2(value) -> float:
    return round(float(value or 0), 2)


def _moment(text) -> datetime:
    return datetime.fromisoformat(str(text).replace("Z", "+00:00"))


def _item(mid, name, kind, qty, rate, gst=5.0):
    taxable = r2(qty * rate)
    gst_amt = r2(taxable * gst / 100)
    return {"medicine_id": mid, "name": name, "type": kind, "qty": float(qty), "free_qty": 0.0,
            "rate": float(rate), "mrp": r2(rate * 1.4), "gst_pct": gst, "discount_pct": 0.0,
            "taxable": taxable, "gst_amt": gst_amt, "item_amount": r2(taxable + gst_amt),
            "batch_no": "B7", "expiry_date": "2027-12-01", "unit": "1", "tablets_per_stripe": 1,
            "hsn_code": "3004"}


def _lines(ointment=60, cream=20):
    return [_item(17512, "DAM 5 OINT", "Ointment", ointment, 50),
            _item(17513, "DAM-6 CREAM", "Cream", cream, 47)]


def _totals(items, rounding=0.0) -> dict:
    subtotal = r2(sum(i["taxable"] for i in items))
    gst = r2(sum(i["gst_amt"] for i in items))
    final = r2(subtotal + gst + rounding)
    return {"subtotal": subtotal, "total_gst": gst, "cgst": r2(gst / 2), "sgst": r2(gst / 2),
            "total_amount": final, "overall_discount": 0.0, "rounding": rounding,
            "need_to_pay": final, "final_amount": final}


def _bill(pid, no, date_s, items, *, rounding=0.0, version=3, stamp="2026-08-16T16:41:16.480Z"):
    money = _totals(items, rounding)
    final = money["final_amount"]
    row = {"id": pid, "local_id": pid, "client_uuid": f"cu-{pid}", "purchase_no": no,
           "fy_start_year": 2026, "fy_serial": int(no.split("/")[0]), "supplier_id": SUPPLIER,
           "supplier_name": "JAISHIV PHARMA", "purchase_date": date_s, "bill_number": f"JP-{pid}",
           "amount_paid": 0.0, "amount_paid_at_entry": 0.0, "cash_paid_at_entry": 0.0,
           "online_paid_at_entry": 0.0, "previous_due": 0.0, "previous_credit": 0.0,
           "due": final, "current_credit": 0.0, "total_due": final, "due_amount": final,
           "credit_amount": 0.0, "paid_due": 0.0, "bill_cleared": False,
           "account_cleared": False, "gst_calc_method": "discount_after_gst",
           "expenditure": 0.0, "is_autosave": False, "deleted": False, "version": version,
           "updated_at": stamp, "device_id": "pc-a", "items": copy.deepcopy(items)}
    row.update(money)
    return row


class FakeStore:
    """The parts of server-live a purchase edit and a supplier payment go through."""

    def __init__(self):
        self.purchases = {
            2997: _bill(2997, "83/FY2026-27", "2026-08-07", _lines()),
            3004: _bill(3004, "90/FY2026-27", "2026-08-15",
                        [_item(17514, "ALONIM GEL", "Gel", 1, 150)], rounding=0.5, version=2,
                        stamp="2026-08-20T11:55:39.041Z"),
        }
        self.medicines = {
            mid: {"id": mid, "local_id": mid, "name": name, "type": kind, "unit": "1",
                  "stock_qty": STOCK[mid], "version": 4, "updated_at": "2026-08-20T11:55:39.041Z"}
            for mid, name, kind in ((17512, "DAM 5 OINT", "Ointment"),
                                    (17513, "DAM-6 CREAM", "Cream"),
                                    (17514, "ALONIM GEL", "Gel"))
        }
        self.payments: list[float] = []
        self.ledger: dict[str, dict] = {}
        self.bundles: list[dict] = []
        self.answers: list[dict] = []
        self.reads: list[tuple[str, int]] = []
        self.before_next_bundle: list = []
        self.before_every_purchase_bundle = None
        self._last = datetime.now(timezone.utc)

    # -- clock: NOW() on the server is never before a stamp it has already seen -------------
    def now(self) -> str:
        moment = max(datetime.now(timezone.utc), self._last + timedelta(milliseconds=1))
        self._last = moment
        return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    # -- upsertHelper.shouldAcceptIncoming ---------------------------------------------------
    @staticmethod
    def accepts(held, doc) -> bool:
        if not held:
            return True
        if doc.get("deleted") and not held.get("deleted"):
            return True
        held_v, doc_v = int(held.get("version") or 1), int(doc.get("version") or 1)
        if doc_v != held_v:
            return doc_v > held_v
        return _moment(doc.get("updated_at")) > _moment(held.get("updated_at"))

    # -- partyDueCascade.cascadePurchasesFifo -----------------------------------------------
    def cascade(self):
        bills = sorted(
            (p for p in self.purchases.values()
             if int(p.get("supplier_id") or 0) == SUPPLIER and not p.get("deleted")
             and not p.get("is_autosave")),
            key=lambda p: (str(p.get("purchase_date"))[:10], int(p["id"])),
        )
        pool = r2(sum(self.payments))
        for p in bills:
            total = float(p.get("final_amount") if p.get("final_amount") is not None
                          else p.get("total_amount") or 0)
            entry = (float(p.get("cash_paid_at_entry") or 0) + float(p.get("online_paid_at_entry") or 0)
                     or float(p.get("amount_paid_at_entry") or 0) or float(p.get("amount_paid") or 0))
            unpaid = max(0.0, r2(total - entry))
            if unpaid <= 0.01:
                rem, cleared = 0.0, True
            elif pool + 0.01 >= unpaid:
                pool = r2(pool - unpaid)
                rem, cleared = 0.0, True
            else:
                rem = r2(unpaid - pool)
                pool = 0.0
                cleared = rem <= 0.01
            if (any(abs(float(p.get(k) or 0) - rem) > 0.009 for k in ("due", "due_amount", "total_due"))
                    or bool(p.get("bill_cleared")) != cleared
                    or bool(p.get("account_cleared")) != cleared):
                p.update(due=rem, due_amount=rem, total_due=rem, bill_cleared=cleared,
                         account_cleared=cleared, version=int(p.get("version") or 0) + 1,
                         updated_at=self.now())

    def pay(self, amount):
        """upsertSupplierPayment: the payment, then the cascade in the same transaction."""
        self.payments.append(float(amount))
        self.cascade()

    # -- stockOperations.applyStockOperation -------------------------------------------------
    def apply_op(self, op) -> str:
        key = str(op.get("op_uuid") or "")
        qty = int(op.get("qty_delta") or 0)
        held = self.ledger.get(key)
        if held is not None:
            return "failed" if qty and qty != int(held["qty_delta"]) else "skipped"
        if not key or not qty:
            return "skipped"
        self.ledger[key] = dict(op)
        med = self.medicines.get(int(op.get("medicine_id") or 0))
        if med is not None:
            med["stock_qty"] = float(med["stock_qty"]) + qty
            med["version"] = int(med["version"]) + 1
        return "applied"

    # -- syncService.upsertMedicine / upsertPurchase ----------------------------------------
    def upsert_medicine(self, doc) -> dict:
        mid = int(doc["id"])
        held = self.medicines.get(mid)
        accepted = self.accepts(held, doc)
        ops = doc.get("stock_ops") or []
        if accepted and held is not None:
            for key in ("unit", "rate", "mrp", "batch_no", "expiry_date", "type", "version",
                        "updated_at"):
                if doc.get(key) not in (None, ""):
                    held[key] = doc[key]
            if not ops and doc.get("stock_qty") is not None:
                held["stock_qty"] = float(doc["stock_qty"])
        statuses = [self.apply_op(op) for op in ops]  # applied whether the document was or not
        if "failed" in statuses:
            return {"id": mid, "status": "failed", "error": "stock op already applied"}
        return {"id": mid, "status": "upserted" if accepted or "applied" in statuses else "skipped"}

    def upsert_purchase(self, doc) -> dict:
        pid = int(doc["id"])
        held = self.purchases.get(pid)
        if not self.accepts(held, doc):
            return {"id": pid, "status": "skipped", "purchase_no": (held or {}).get("purchase_no")}
        row = copy.deepcopy(held) if held else {}
        for key, value in doc.items():
            # A field the push leaves out keeps what the row holds (F1).
            if key.startswith("_") or value is None:
                continue
            row[key] = copy.deepcopy(value)
        row["purchase_no"] = (held or {}).get("purchase_no") or doc.get("purchase_no")
        self.purchases[pid] = row
        self.cascade()
        return {"id": pid, "status": "upserted", "purchase_no": row["purchase_no"]}

    # -- the two endpoints the PC calls ------------------------------------------------------
    def push_bundle(self, token, bundle, **kwargs):
        for docs in bundle.values():
            for d in docs if isinstance(docs, list) else []:
                if isinstance(d, dict) and d.get("updated_at"):
                    self._last = max(self._last, _moment(d["updated_at"]))
        hooks, self.before_next_bundle = self.before_next_bundle, []
        for hook in hooks:
            hook()
        if "purchases" in bundle and self.before_every_purchase_bundle:
            self.before_every_purchase_bundle()
        self.bundles.append(copy.deepcopy(bundle))
        out: dict = {}
        for col in ("stock_operations", "medicines", "purchases", "suppliers"):
            docs = bundle.get(col) or []
            if not docs:
                continue
            results = []
            for d in docs:
                if col == "medicines":
                    results.append(self.upsert_medicine(d))
                elif col == "purchases":
                    results.append(self.upsert_purchase(d))
                elif col == "stock_operations":
                    results.append({"id": 0, "status": self.apply_op(d)})
                else:
                    results.append({"id": d.get("id"), "status": "skipped"})
            out[col] = {"results": results,
                        "upserted": sum(r["status"] in ("upserted", "applied") for r in results),
                        "skipped": sum(r["status"] == "skipped" for r in results),
                        "failed": sum(r["status"] == "failed" for r in results)}
        self.answers.append(copy.deepcopy(out))
        return out

    def pull_doc(self, token, collection, local_id, **kwargs):
        self.reads.append((collection, int(local_id)))
        table = {"purchases": self.purchases, "medicines": self.medicines}.get(collection)
        if table is None:
            return {"id": int(local_id), "name": "JAISHIV PHARMA", "version": 9,
                    "updated_at": "2026-09-01T00:00:00.000Z"}
        row = table.get(int(local_id))
        return copy.deepcopy(row) if row else None


class _Race(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.server_api.push_bundle", side_effect=self.store.push_bundle),
            mock.patch("core.server_api.pull_doc", side_effect=self.store.pull_doc),
            mock.patch("core.server_crud._token", return_value="t"),
            mock.patch("core.server_crud._device_id", return_value="pc-b"),
            mock.patch("core.online_guard.ensure_can_mutate", return_value=None),
            mock.patch("core.online_catalog.medicine_by_id", return_value=None),
            mock.patch("core.online_catalog.find_supplier_by_id",
                       return_value={"id": SUPPLIER, "local_id": SUPPLIER,
                                     "name": "JAISHIV PHARMA", "version": 9}),
            mock.patch("core.online_catalog.patch_docs", return_value=None),
            mock.patch("core.online_catalog.invalidate", return_value=None),
            mock.patch("core.sync_status.note_collection_change", return_value=None),
            mock.patch("core.sync_status.note_last_sync", return_value=None),
            mock.patch("core.sync_v3.data_change_bus.emit", return_value=None),
            mock.patch("core.store_live_refresh.emit", return_value=None),
            mock.patch.object(purchase_service, "_ensure_purchase_line_medicine_ids",
                              return_value=None),
            mock.patch.object(purchase_service, "hide_online_zero_stock_duplicates",
                              return_value=0),
        ):
            stack.enter_context(patch)

    # -- the edit: DAM 5 OINT 60 -> 59, as B typed it ----------------------------------------
    def edit(self, ointment=59):
        items = _lines(ointment=ointment)
        calc = dict(_totals(items), expenditure=0.0, previous_due=0.0, previous_credit=0.0,
                    cash_paid=0.0, online_paid=0.0, amount_paid=0.0,
                    gst_calc_method="discount_after_gst", current_credit=0.0,
                    bill_cleared=0, account_cleared=0)
        calc.update(due=calc["final_amount"], total_due=calc["final_amount"])
        return purchase_service.update_purchase_online_now(
            2997, SUPPLIER, "JP-2997", "2026-08-07", calc, items, client_uuid=CU,
        )

    def another_device_edits(self, **qty):
        """A phone's edit of 2997 lands first: the next version, stamped after B's."""
        row = self.store.purchases[2997]
        old = {int(i["medicine_id"]): float(i["qty"]) for i in row["items"]}
        items = _lines(ointment=qty.get("ointment", old[17512]), cream=qty.get("cream", old[17513]))
        doc = copy.deepcopy(row)
        doc.update(_totals(items), items=items, version=int(row["version"]) + 1,
                   updated_at=self.store.now(), device_id="phone")
        self.store.upsert_purchase(doc)
        for item in items:
            mid = int(item["medicine_id"])
            delta = int(item["qty"] - old[mid])
            if delta:
                self.store.apply_op({"op_uuid": f"purchase:{CU}:med:{mid}:edit:v{doc['version']}",
                                     "op": "purchase_edit", "qty_delta": delta, "medicine_id": mid,
                                     "ref_collection": "purchases", "ref_id": 2997})

    # -- reading the store -------------------------------------------------------------------
    def lines(self, pid=2997):
        return sorted((int(i["medicine_id"]), float(i["qty"])) for i in self.store.purchases[pid]["items"])

    def due(self, pid):
        p = self.store.purchases[pid]
        return float(p["due_amount"]), bool(p["bill_cleared"]), bool(p["account_cleared"])

    def stock(self, mid):
        return self.store.medicines[mid]["stock_qty"]

    def purchase_pushes(self):
        return [b for b in self.store.bundles if "purchases" in b]

    def moves(self, mid):
        return {k: int(op["qty_delta"]) for k, op in self.store.ledger.items()
                if int(op.get("medicine_id") or 0) == mid}


class APaymentLandsWhileTheEditTravels(_Race):
    def pay_then_edit(self, amount):
        self.store.before_next_bundle.append(lambda: self.store.pay(amount))
        return self.edit()

    def assert_the_edit_stands(self):
        self.assertEqual(self.lines(), [(17512, 59.0), (17513, 20.0)],
                         "the store kept the lines from before the edit")
        bill = self.store.purchases[2997]
        self.assertEqual((bill["subtotal"], bill["total_gst"], bill["final_amount"]),
                         (3890.0, 194.5, 4084.5))
        self.assertEqual(self.stock(17512), 599.0, "stock no longer matches the bill's lines")
        self.assertEqual(self.stock(17513), 200.0)
        self.assertEqual(self.moves(17512), {f"purchase:{CU}:med:17512:edit:v4": -1})

    def test_a_payment_that_clears_the_bill(self):
        number = self.pay_then_edit(4137.0)
        first = self.store.answers[0]["purchases"]["results"][0]
        self.assertEqual(first["status"], "skipped", "the race did not happen")
        self.assert_the_edit_stands()
        self.assertEqual(self.due(2997), (0.0, True, True))
        # 4137 paid against 4084.50: the 52.50 left goes to the next bill.
        self.assertEqual(self.due(3004), (105.5, False, False))
        self.assertEqual(number, "83/FY2026-27")

    def test_a_part_payment(self):
        self.pay_then_edit(1000.0)
        self.assert_the_edit_stands()
        self.assertEqual(self.due(2997), (3084.5, False, False))
        self.assertEqual(self.due(3004), (158.0, False, False))

    def test_an_over_payment(self):
        self.pay_then_edit(5000.0)
        self.assert_the_edit_stands()
        self.assertEqual(self.due(2997), (0.0, True, True))
        self.assertEqual(self.due(3004), (0.0, True, True))

    def test_the_next_edit_goes_on_from_the_version_that_landed(self):
        self.pay_then_edit(1000.0)
        landed = int(self.store.purchases[2997]["version"])
        pushes = len(self.purchase_pushes())
        self.edit(ointment=58)
        self.assertEqual(len(self.purchase_pushes()), pushes + 1, "the second edit was skipped too")
        self.assertGreater(int(self.store.purchases[2997]["version"]), landed)
        self.assertEqual(self.lines(), [(17512, 58.0), (17513, 20.0)])
        self.assertEqual(self.stock(17512), 598.0)
        # 58 x 50 + 5 % = 3045, the cream 987: 4032 less the 1000 paid.
        self.assertEqual(self.due(2997), (3032.0, False, False))


class AnEditWithNothingInTheWay(_Race):
    def test_it_is_one_push_and_one_read_as_before(self):
        self.edit()
        self.assertEqual(len(self.purchase_pushes()), 1)
        self.assertEqual(self.store.reads.count(("purchases", 2997)), 1)
        self.assertEqual(self.lines(), [(17512, 59.0), (17513, 20.0)])
        self.assertEqual(self.stock(17512), 599.0)
        self.assertEqual(self.due(2997), (4084.5, False, False))


class AnotherDevicesChangeGotThereFirst(_Race):
    def test_its_edit_stands_this_save_says_so_and_this_stock_is_taken_back(self):
        self.store.before_next_bundle.append(lambda: self.another_device_edits(cream=18))
        with self.assertRaises(server_crud.PurchaseEditNotSaved) as caught:
            self.edit()
        self.assertIsInstance(caught.exception, server_crud.BundleDocumentRejected,
                              "a queued edit would be replayed instead of shown to the shop")
        message = str(caught.exception)
        self.assertIn("83", message)
        self.assertIn("another device", message)
        self.assertEqual(self.lines(), [(17512, 60.0), (17513, 18.0)], "the phone's edit was overwritten")
        self.assertEqual(self.stock(17512), 600.0, "this edit's stock movement was left on the shelf")
        self.assertEqual(self.stock(17513), 198.0)

    def test_the_same_change_made_on_both_devices_is_simply_there(self):
        self.store.before_next_bundle.append(lambda: self.another_device_edits(ointment=59))
        self.edit()
        self.assertEqual(self.lines(), [(17512, 59.0), (17513, 20.0)])
        self.assertEqual(self.stock(17512), 599.0, "one movement, not two and not none")

    def test_a_medicine_both_edits_moved_is_never_taken_back_on_a_guess(self):
        # The phone moved DAM 5 OINT by the same -1 under the same op_uuid, so the store applied
        # the phone's movement and skipped B's: taking "B's" back would leave 600 against 59.
        self.store.before_next_bundle.append(lambda: self.another_device_edits(ointment=59, cream=21))
        with self.assertRaises(server_crud.PurchaseEditNotSaved) as caught:
            self.edit()
        self.assertEqual(self.lines(), [(17512, 59.0), (17513, 21.0)])
        self.assertEqual(self.stock(17512), 599.0)
        self.assertEqual(self.stock(17513), 201.0)
        self.assertIn("DAM 5 OINT", str(caught.exception))

    def test_a_bill_deleted_on_another_device_is_not_brought_back(self):
        def delete():
            row = self.store.purchases[2997]
            for item in row["items"]:
                mid = int(item["medicine_id"])
                self.store.apply_op({"op_uuid": f"purchase:{CU}:med:{mid}:delete:v1",
                                     "op": "purchase_delete", "qty_delta": -int(item["qty"]),
                                     "medicine_id": mid, "ref_collection": "purchases",
                                     "ref_id": 2997})
            row.update(deleted=True, version=int(row["version"]) + 1, updated_at=self.store.now())
            self.store.cascade()

        self.store.before_next_bundle.append(delete)
        with self.assertRaises(server_crud.PurchaseEditNotSaved) as caught:
            self.edit()
        self.assertIn("deleted", str(caught.exception))
        self.assertTrue(self.store.purchases[2997]["deleted"])
        self.assertEqual(self.stock(17512), 540.0)
        self.assertEqual(self.stock(17513), 180.0)


class AStoreThatKeepsChangingUnderTheEdit(_Race):
    def test_it_stops_takes_its_stock_back_and_says_to_save_again(self):
        self.store.before_every_purchase_bundle = lambda: self.store.pay(10.0)
        with self.assertRaises(server_crud.PurchaseEditNotSaved) as caught:
            self.edit()
        self.assertIn("again", str(caught.exception))
        self.assertLessEqual(len(self.purchase_pushes()), 6, "it kept pushing")
        self.assertEqual(self.lines(), [(17512, 60.0), (17513, 20.0)])
        self.assertEqual(self.stock(17512), 600.0)


if __name__ == "__main__":
    unittest.main()
