"""A sale edit is never lost to a customer receipt that lands while the edit is on its way.

Staging, 14 Sep (proof of round 10, d5race_fixed_OPP0_s4 and d5race_released_OPP0_s4): device B
read sale 36478 at version 4 and stamped its edit (NIMICA PLUS 2 -> 1) version 5. Before the edit
reached the store, device A saved a receipt; the server's own cascade cleared the bill and wrote
it at version 5 with updated_at NOW(). B's edit then arrived at the same version with an older
stamp and the server skipped the sale whole -- sales ['skipped'], medicines ['upserted'] -- while
the same bundle's stock movement was applied: total 251, NIMICA PLUS still 2, stock 0 -> 1.

The PC now reads the store's answer for the sale, as it does for a purchase. A skipped sale is
read back. When the store's copy differs from the copy the edit was worked out from only in what
the customer cascade writes (total_due, due_amount and the two cleared flags), the edit goes again
over that copy's version and the server's cascade sets the dues from the ledger. When another
device changed the bill first, the edit is not forced over it: it is refused with the reason,
the stock it certainly moved is put back, and the queue parks it instead of replaying it.

A fake store stands in for server-live: upsertHelper.shouldAcceptIncoming, upsertSale (a skipped
sale is answered 'skipped'; an accepted one runs cascadeCustomerAfterLedgerChange),
partyDueCascade.cascadeSalesFifo, upsertCustomerPayment (the receipt, then the cascade) and
stockOperations.applyStockOperation. Nothing reaches a server.
"""
from __future__ import annotations

import copy
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest import mock

from core import billing_service, online_mutation_queue, server_crud  # noqa: E402

CUSTOMER = 15097
CU = "cu-36478"


def r2(value) -> float:
    return round(float(value or 0), 2)


def _moment(text) -> datetime:
    return datetime.fromisoformat(str(text).replace("Z", "+00:00"))


def _line(mid, name, qty, rate):
    return {"medicine_id": mid, "name": name, "medicine_name": name, "type": "Bolus",
            "qty": float(qty), "rate": float(rate), "amount": r2(qty * rate),
            "item_discount": 0.0, "gst_percent": 5.0, "batch_no": "B1",
            "expiry_date": "2027-12-01", "cost_price": 60.0}


def _lines(nimica=2, meloxi=1):
    return [_line(16343, "NIMICA PLUS", nimica, 100), _line(16344, "MELOXI BOLUS", meloxi, 51)]


def _sale(sid, no, date_s, items, *, version, cu):
    total = r2(sum(i["amount"] for i in items))
    return {"id": sid, "local_id": sid, "client_uuid": cu, "bill_no": no, "fy_start_year": 2026,
            "fy_serial": int(no[3:].split("/")[0]), "customer_id": CUSTOMER,
            "customer_name": "AMAY MANDWALE", "customer_phone": "", "customer_address": "",
            "bill_date": date_s, "total_amount": total, "discount": 0.0, "discount_pct": 0.0,
            "rounding": 0.0, "amount_paid": 0.0, "cash_paid": 0.0, "online_paid": 0.0,
            "previous_due": 0.0, "previous_credit": 0.0, "due_amount": total,
            "credit_amount": 0.0, "total_due": total, "paid_due": 0.0, "bill_cleared": False,
            "account_cleared": False, "doctor_name": "", "is_autosave": False, "deleted": False,
            "version": version, "updated_at": "2026-09-10T09:00:00.000Z", "device_id": "pc-a",
            "items": copy.deepcopy(items)}


class FakeStore:
    def __init__(self):
        self.sales = {
            36478: _sale(36478, "SCB1402/FY2026-27", "2026-09-01", _lines(), version=4, cu=CU),
            37379: _sale(37379, "SCB1411/FY2026-27", "2026-09-05",
                         [_line(16350, "ALBOMAR", 1, 421)], version=2, cu="cu-37379"),
        }
        self.customers = {CUSTOMER: {"id": CUSTOMER, "local_id": CUSTOMER, "name": "AMAY MANDWALE",
                                     "total_due": 672.0, "total_credit": 0.0, "version": 9,
                                     "updated_at": "2026-09-10T09:00:00.000Z"}}
        self.medicines = {
            mid: {"id": mid, "local_id": mid, "name": name, "type": "Bolus", "unit": "1",
                  "stock_qty": 0.0, "rate": 60.0, "version": 3,
                  "updated_at": "2026-09-10T09:00:00.000Z"}
            for mid, name in ((16343, "NIMICA PLUS"), (16344, "MELOXI BOLUS"), (16350, "ALBOMAR"))
        }
        self.receipts: list[float] = []
        self.ledger: dict[str, dict] = {}
        self.bundles: list[dict] = []
        self.answers: list[dict] = []
        self.reads: list[tuple[str, int]] = []
        self.before_next_bundle: list = []
        self.before_every_sale_bundle = None
        self.drop_answer_of_next_sale_bundle = False
        self._last = datetime.now(timezone.utc)

    def now(self) -> str:
        moment = max(datetime.now(timezone.utc), self._last + timedelta(milliseconds=1))
        self._last = moment
        return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

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

    # partyDueCascade.cascadeCustomerAfterLedgerChange / cascadeSalesFifo
    def cascade(self):
        bills = sorted((s for s in self.sales.values()
                        if int(s.get("customer_id") or 0) == CUSTOMER and not s.get("deleted")
                        and not s.get("is_autosave")),
                       key=lambda s: (str(s["bill_date"])[:10], int(s["id"])))
        net = r2(sum(float(s["total_amount"]) - float(s["amount_paid"]) for s in bills)
                 - sum(self.receipts))
        cust = self.customers[CUSTOMER]
        cust.update(total_due=max(0.0, net), total_credit=max(0.0, -net))
        pool = r2(sum(self.receipts))
        for s in bills:
            pool = r2(pool + max(0.0, r2(float(s["amount_paid"]) - float(s["total_amount"]))))
        for s in bills:
            unpaid = max(0.0, r2(float(s["total_amount"]) - float(s["amount_paid"])))
            if unpaid <= 0.01:
                rem, cleared = 0.0, True
            elif pool + 0.01 >= unpaid:
                pool = r2(pool - unpaid)
                rem, cleared = 0.0, True
            else:
                rem = r2(unpaid - pool)
                pool = 0.0
                cleared = rem <= 0.01
            if (abs(float(s["total_due"]) - rem) > 0.009 or abs(float(s["due_amount"]) - rem) > 0.009
                    or bool(s["account_cleared"]) != cleared or bool(s["bill_cleared"]) != (rem <= 0.01)):
                s.update(total_due=rem, due_amount=rem, account_cleared=cleared,
                         bill_cleared=rem <= 0.01, version=int(s["version"]) + 1,
                         updated_at=self.now())

    def receive(self, amount):
        """upsertCustomerPayment: the receipt, then the cascade in the same transaction."""
        self.receipts.append(float(amount))
        self.cascade()

    def apply_op(self, op) -> str:
        key = str(op.get("op_uuid") or "")
        qty = int(op.get("qty_delta") or 0)
        held = self.ledger.get(key)
        if held is not None:
            return "failed" if qty and qty != int(held["qty_delta"]) else "skipped"
        if not key or not qty:
            return "skipped"
        self.ledger[key] = dict(op)
        med = self.medicines[int(op["medicine_id"])]
        med["stock_qty"] = float(med["stock_qty"]) + qty
        med["version"] = int(med["version"]) + 1
        return "applied"

    def upsert_medicine(self, doc) -> dict:
        ops = doc.get("stock_ops") or []
        statuses = [self.apply_op(op) for op in ops]
        if "failed" in statuses:
            return {"id": doc["id"], "status": "failed", "error": "stock op already applied"}
        return {"id": doc["id"], "status": "upserted"}

    def upsert_sale(self, doc) -> dict:
        sid = int(doc["id"])
        held = self.sales.get(sid)
        if not self.accepts(held, doc):
            return {"id": sid, "status": "skipped", "bill_no": (held or {}).get("bill_no")}
        row = copy.deepcopy(held) if held else {}
        for key, value in doc.items():
            if key.startswith("_") or value is None:
                continue
            row[key] = copy.deepcopy(value)
        self.sales[sid] = row
        self.cascade()
        return {"id": sid, "status": "upserted", "bill_no": row["bill_no"]}

    def push_bundle(self, token, bundle, **kwargs):
        for docs in bundle.values():
            for d in docs if isinstance(docs, list) else []:
                if isinstance(d, dict) and d.get("updated_at"):
                    self._last = max(self._last, _moment(d["updated_at"]))
        hooks, self.before_next_bundle = self.before_next_bundle, []
        for hook in hooks:
            hook()
        if "sales" in bundle and self.before_every_sale_bundle:
            self.before_every_sale_bundle()
        self.bundles.append(copy.deepcopy(bundle))
        out: dict = {}
        for col in ("stock_operations", "medicines", "sales", "customers"):
            docs = bundle.get(col) or []
            if not docs:
                continue
            results = []
            for d in docs:
                if col == "medicines":
                    results.append(self.upsert_medicine(d))
                elif col == "sales":
                    results.append(self.upsert_sale(d))
                elif col == "stock_operations":
                    results.append({"id": 0, "status": self.apply_op(d)})
                else:
                    results.append({"id": d.get("id"), "status": "skipped"})
            out[col] = {"results": results}
        if "customers" in bundle:
            self.cascade()  # pushBundle's partyRecompute at the end
        self.answers.append(copy.deepcopy(out))
        if "sales" in bundle and self.drop_answer_of_next_sale_bundle:
            self.drop_answer_of_next_sale_bundle = False
            raise ConnectionError("connection reset before the answer")
        return out

    def pull_doc(self, token, collection, local_id, **kwargs):
        self.reads.append((collection, int(local_id)))
        table = {"sales": self.sales, "medicines": self.medicines,
                 "customers": self.customers}.get(collection, {})
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
            mock.patch("core.online_catalog.find_customer_by_id",
                       side_effect=lambda cid: copy.deepcopy(self.store.customers.get(int(cid)))),
            mock.patch("core.online_catalog.patch_docs", return_value=None),
            mock.patch("core.online_catalog.invalidate", return_value=None),
            mock.patch("core.quick_sale_medicine.resolve_quick_sale_medicines", return_value=None),
            mock.patch("core.store_live_refresh.emit", return_value=None),
        ):
            stack.enter_context(patch)

    # -- the edit: NIMICA PLUS 2 -> 1, as B typed it -----------------------------------------
    def edit(self, nimica=1, meloxi=1):
        medicines = [
            {"id": 16343, "name": "NIMICA PLUS", "type": "Bolus", "qty": nimica, "rate": 100.0,
             "amount": r2(nimica * 100), "medicine_discount": 0.0, "batch": "B1",
             "expiry": "2027-12-01", "gst_percent": 5.0},
            {"id": 16344, "name": "MELOXI BOLUS", "type": "Bolus", "qty": meloxi, "rate": 51.0,
             "amount": r2(meloxi * 51), "medicine_discount": 0.0, "batch": "B1",
             "expiry": "2027-12-01", "gst_percent": 5.0},
        ]
        billing_service.update_existing_bill_online_now(
            36478, medicines, 0, 0, 0, 0, "AMAY MANDWALE", "", "", 0,
            bill_date="2026-09-01",
        )

    def another_device_edits(self, nimica=None, meloxi=None):
        """A phone's edit of 36478 lands first: the next version, stamped after B's."""
        row = self.store.sales[36478]
        old = {int(i["medicine_id"]): float(i["qty"]) for i in row["items"]}
        items = _lines(nimica=old[16343] if nimica is None else nimica,
                       meloxi=old[16344] if meloxi is None else meloxi)
        doc = copy.deepcopy(row)
        total = r2(sum(i["amount"] for i in items))
        doc.update(items=items, total_amount=total, version=int(row["version"]) + 1,
                   updated_at=self.store.now(), device_id="phone")
        self.store.upsert_sale(doc)
        for item in items:
            mid = int(item["medicine_id"])
            delta = int(old[mid] - item["qty"])
            if delta:
                self.store.apply_op({"op_uuid": f"sale:{CU}:med:{mid}:edit:v{doc['version']}",
                                     "op": "sale_edit", "qty_delta": delta, "medicine_id": mid,
                                     "ref_collection": "sales", "ref_id": 36478})

    def lines(self, sid=36478):
        return sorted((int(i["medicine_id"]), float(i["qty"])) for i in self.store.sales[sid]["items"])

    def due(self, sid):
        s = self.store.sales[sid]
        return float(s["due_amount"]), bool(s["bill_cleared"]), bool(s["account_cleared"])

    def stock(self, mid):
        return self.store.medicines[mid]["stock_qty"]

    def sale_pushes(self):
        return [b for b in self.store.bundles if "sales" in b]

    def moves(self, mid):
        return {k: int(op["qty_delta"]) for k, op in self.store.ledger.items()
                if int(op.get("medicine_id") or 0) == mid}


class AReceiptLandsWhileTheEditTravels(_Race):
    def receive_then_edit(self, amount):
        self.store.before_next_bundle.append(lambda: self.store.receive(amount))
        self.edit()

    def assert_the_edit_stands(self):
        first = self.store.answers[0]["sales"]["results"][0]
        self.assertEqual(first["status"], "skipped", "the race did not happen")
        self.assertEqual(self.lines(), [(16343, 1.0), (16344, 1.0)],
                         "the store kept the lines from before the edit")
        self.assertEqual(self.store.sales[36478]["total_amount"], 151.0,
                         "the bill's total no longer matches its lines")
        self.assertEqual(self.stock(16343), 1.0, "stock no longer matches the bill's lines")
        self.assertEqual(self.moves(16343), {f"sale:{CU}:med:16343:edit:v5": 1})

    def test_a_receipt_that_clears_the_bill(self):
        self.receive_then_edit(461.0)
        self.assert_the_edit_stands()
        self.assertEqual(self.due(36478), (0.0, True, True))
        # 461 received against 151: the 310 left goes to the next bill (421).
        self.assertEqual(self.due(37379), (111.0, False, False))
        self.assertEqual(self.store.customers[CUSTOMER]["total_due"], 111.0)

    def test_a_part_receipt(self):
        self.receive_then_edit(100.0)
        self.assert_the_edit_stands()
        self.assertEqual(self.due(36478), (51.0, False, False))
        self.assertEqual(self.due(37379), (421.0, False, False))
        self.assertEqual(self.store.customers[CUSTOMER]["total_due"], 472.0)

    def test_an_over_receipt(self):
        self.receive_then_edit(700.0)
        self.assert_the_edit_stands()
        self.assertEqual(self.due(36478), (0.0, True, True))
        self.assertEqual(self.due(37379), (0.0, True, True))
        self.assertEqual(self.store.customers[CUSTOMER]["total_credit"], 128.0)

    def test_the_next_edit_goes_on_from_the_version_that_landed(self):
        self.receive_then_edit(100.0)
        landed = int(self.store.sales[36478]["version"])
        pushes = len(self.sale_pushes())
        self.edit(nimica=3)
        self.assertEqual(len(self.sale_pushes()), pushes + 1, "the second edit was skipped too")
        self.assertGreater(int(self.store.sales[36478]["version"]), landed)
        self.assertEqual(self.lines(), [(16343, 3.0), (16344, 1.0)])
        self.assertEqual(self.stock(16343), -1.0)

    def test_an_unanswered_push_over_the_store_copy_is_read_back_not_replayed(self):
        self.store.before_next_bundle.append(lambda: self.store.receive(461.0))
        self.store.before_next_bundle.append(
            lambda: setattr(self.store, "drop_answer_of_next_sale_bundle", False))
        original = self.store.push_bundle

        calls = {"n": 0}

        def push(token, bundle, **kwargs):
            if "sales" in bundle:
                calls["n"] += 1
                if calls["n"] == 2:
                    self.store.drop_answer_of_next_sale_bundle = True
            return original(token, bundle, **kwargs)

        with mock.patch("core.server_api.push_bundle", side_effect=push):
            self.edit()
        self.assertEqual(self.lines(), [(16343, 1.0), (16344, 1.0)])
        self.assertEqual(self.stock(16343), 1.0, "the edit's stock moved twice")
        self.assertEqual(len(self.sale_pushes()), 2, "a landed push was sent again")


class AnEditWithNothingInTheWay(_Race):
    def test_it_is_one_push_and_one_read_as_before(self):
        self.edit()
        self.assertEqual(len(self.sale_pushes()), 1)
        self.assertEqual(self.store.reads.count(("sales", 36478)), 1)
        self.assertEqual(self.lines(), [(16343, 1.0), (16344, 1.0)])
        self.assertEqual(self.stock(16343), 1.0)
        self.assertEqual(self.due(36478), (151.0, False, False))


class AnotherDevicesChangeGotThereFirst(_Race):
    def test_its_edit_stands_this_edit_is_refused_and_its_stock_is_put_back(self):
        self.store.before_next_bundle.append(lambda: self.another_device_edits(meloxi=2))
        not_saved = getattr(server_crud, "SaleEditNotSaved", server_crud.BundleDocumentRejected)
        with self.assertRaises(not_saved) as caught:
            self.edit()
        self.assertIsInstance(caught.exception, server_crud.BundleDocumentRejected,
                              "a queued edit would be replayed instead of shown to the shop")
        self.assertTrue(online_mutation_queue._is_permanent_refusal(caught.exception))
        message = str(caught.exception)
        self.assertIn("SCB1402", message)
        self.assertIn("another device", message)
        self.assertEqual(self.lines(), [(16343, 2.0), (16344, 2.0)], "the phone's edit was overwritten")
        self.assertEqual(self.stock(16343), 0.0, "this edit's stock movement was left on the shelf")
        self.assertEqual(self.stock(16344), -1.0)

    def test_the_same_change_made_on_both_devices_is_simply_there(self):
        self.store.before_next_bundle.append(lambda: self.another_device_edits(nimica=1))
        self.edit()
        self.assertEqual(self.lines(), [(16343, 1.0), (16344, 1.0)])
        self.assertEqual(self.stock(16343), 1.0, "one movement, not two and not none")

    def test_a_medicine_both_edits_moved_is_named_not_guessed(self):
        self.store.before_next_bundle.append(lambda: self.another_device_edits(nimica=1, meloxi=2))
        not_saved = getattr(server_crud, "SaleEditNotSaved", server_crud.BundleDocumentRejected)
        with self.assertRaises(not_saved) as caught:
            self.edit()
        self.assertEqual(self.lines(), [(16343, 1.0), (16344, 2.0)])
        self.assertEqual(self.stock(16343), 1.0)
        self.assertEqual(self.stock(16344), -1.0)
        self.assertIn("NIMICA PLUS", str(caught.exception))


class AStoreThatKeepsChangingUnderTheEdit(_Race):
    def test_it_stops_puts_its_stock_back_and_says_to_save_again(self):
        self.store.before_every_sale_bundle = lambda: self.store.receive(1.0)
        not_saved = getattr(server_crud, "SaleEditNotSaved", server_crud.BundleDocumentRejected)
        with self.assertRaises(not_saved) as caught:
            self.edit()
        self.assertIn("again", str(caught.exception))
        self.assertLessEqual(len(self.sale_pushes()), 6, "it kept pushing")
        self.assertEqual(self.lines(), [(16343, 2.0), (16344, 1.0)])
        self.assertEqual(self.stock(16343), 0.0)


if __name__ == "__main__":
    unittest.main()
