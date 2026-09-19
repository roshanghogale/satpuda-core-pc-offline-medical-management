"""A purchase line stocks by ITS OWN pack; the batch row keeps its pack.

Stock rule (owner): purchases (qty + free) x tablets-per-strip of THAT LINE's pack for
Tablet/Bolus/Capsule. A strip medicine bought loose -- Tabs/Strip 1 on the Purchase screen --
was stored with the medicine's own pack instead: the screen sends unit / quantity_value "1" and
tablets_per_stripe 1 with medicine_id null, the save found the medicine, filled the line's
missing ``pack`` from the catalogue ("10"), and the strip-size rule let 10 beat the typed 1. Five
loose tablets went on the shelf as fifty (store 4 MOLICOLD 17485 78 -> 128 on a Rs 26 bill;
store 127 ORAFAST 70 -> 150).

Now a line that carries its own Tabs/Strip is never given the catalogue's; each line is counted
with its own figure on save, edit and delete (also when an edit falls back from the ledger to
the lines); and the medicine row keeps the pack it has.

The server client is patched; nothing reaches a server.
"""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import ExitStack
from unittest import mock

from core import db_setup, desktop_purchase_service, online_mutation_queue, purchase_service
from core.purchase_service import expiry_to_db

MID = 17485


def _medicine(stock=78.0):
    return {"id": MID, "local_id": MID, "name": "MOLICOLD TAB", "type": "Tablet", "unit": "10",
            "batch_no": "TN118C", "expiry_date": expiry_to_db("10/27"), "stock_qty": stock,
            "version": 3, "is_hidden": False}


def _screen_line(qty, pack, *, free=0, rate=5.0):
    """A line exactly as PurchasePage.tabPayload sends a NEW line."""
    return {"medicine_id": None, "name": "MOLICOLD TAB", "type": "Tablet", "batch": "TN118C",
            "expiry": "10/27", "qty": qty, "free_qty": free, "rate": rate, "mrp": 6,
            "gst_pct": 5, "discount_pct": 0, "hsn_code": "", "manufacturer": "", "schedule": "",
            "content_drug": "", "unit": str(pack), "tablets_per_stripe": pack,
            "quantity_value": str(pack), "item_amount": 0, "amount": 0, "taxable": 0,
            "gst_amt": 0}


def _stored_line(qty, pack, *, free=0):
    """A purchase_items row as the server hands it back (purchaseItemPack stores unit/tps)."""
    return {"medicine_id": MID, "name": "MOLICOLD TAB", "type": "Tablet", "qty": qty,
            "free_qty": free, "rate": 5, "batch_no": "TN118C", "expiry_date": "2027-10-31",
            "unit": str(pack), "tablets_per_stripe": pack}


class _Online(unittest.TestCase):
    EXISTING: dict = {}

    def setUp(self):
        self.sent: list[dict] = []
        self.med = _medicine()
        self.ledger_down = True

        def get_doc(collection, local_id):
            if collection == "purchases":
                return dict(self.EXISTING) if self.EXISTING else None
            if collection == "medicines":
                return dict(self.med) if int(local_id) == MID else None
            return {"id": int(local_id), "name": "STG SUP", "version": 1}

        def pull(token, collection, **kw):
            if self.ledger_down:
                raise RuntimeError("Cannot reach server")
            return [dict(o) for o in getattr(self, "ledger", [])], {}

        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.online_guard.ensure_can_mutate", return_value=None),
            mock.patch("core.server_crud.get_doc", side_effect=get_doc),
            mock.patch("core.online_catalog.medicine_by_id",
                       side_effect=lambda mid: get_doc("medicines", mid)),
            mock.patch("core.online_catalog.medicines_for_name_match",
                       side_effect=lambda name: [dict(self.med)]),
            mock.patch("core.online_catalog.find_supplier_by_id",
                       return_value={"id": 301, "name": "STG SUP"}),
            mock.patch("core.online_catalog.patch_docs", return_value=None),
            mock.patch("core.online_catalog.patch_supplier_cache", return_value=None),
            mock.patch("core.online_catalog._token", return_value="t"),
            mock.patch("core.server_api.pull_collection", side_effect=pull),
            mock.patch("core.server_api.store_token_for_active", return_value="t"),
            mock.patch("core.server_api.allocate_fy",
                       return_value={"fy_start_year": 2026, "fy_serial": 112,
                                     "purchase_no": "112/FY2026-27"}),
            mock.patch("core.server_crud.allocate_id", return_value=3027),
            mock.patch("core.server_crud._device_id", return_value="dev"),
            mock.patch("core.server_crud.upsert_medicine_online", return_value=MID),
            mock.patch("core.server_crud.save_new_purchase_online",
                       side_effect=lambda doc: self.sent.append(doc) or int(doc.get("id") or 0)),
            mock.patch.object(purchase_service, "hide_online_zero_stock_duplicates",
                              return_value=0),
            mock.patch.object(purchase_service, "get_or_create_supplier", return_value=301),
            mock.patch.object(purchase_service, "get_supplier_due", return_value=(0.0, 0.0)),
            mock.patch("core.desktop_returns_service.edit_below_returned_error",
                       return_value=None),
        ):
            stack.enter_context(patch)

    def save(self, lines, **extra):
        body = {"supplier_name": "STG SUP", "purchase_date": "2026-09-12", "items": lines,
                "cash_paid": 0, "online_paid": 0}
        body.update(extra)
        # The Online engine's own connection: an initialised, empty :memory: database.
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        res = desktop_purchase_service.save_purchase_bill(conn, body)
        self.assertTrue(res.get("ok"), res)
        [doc] = self.sent
        [med] = [m for m in doc["_medicines"] if int(m["id"]) == MID]
        moved = sum(int(op.get("qty_delta") or 0) for op in med.get("stock_ops") or [])
        return doc, med, moved


class ANewPurchaseFromThePurchaseScreen(_Online):
    def test_five_loose_tablets_add_five(self):
        doc, med, moved = self.save([_screen_line(5, 1)])
        self.assertEqual(moved, 5, "5 loose tablets were stocked as strips of the catalogue pack")
        self.assertEqual(med["stock_qty"], 83.0)
        self.assertEqual((doc["items"][0]["unit"], doc["items"][0]["tablets_per_stripe"]), ("1", 1))
        self.assertEqual(med["unit"], "10", "the batch row lost its pack to a loose line")

    def test_a_strip_line_still_adds_strips_times_its_pack(self):
        doc, med, moved = self.save([_screen_line(3, 10, rate=49.98)])
        self.assertEqual(moved, 30)
        self.assertEqual((doc["items"][0]["unit"], doc["items"][0]["tablets_per_stripe"]), ("10", 10))
        self.assertEqual(med["unit"], "10")

    def test_a_strip_line_and_a_loose_line_on_one_medicine_each_count_their_own_pack(self):
        doc, med, moved = self.save([_screen_line(2, 10, free=1), _screen_line(5, 1)])
        self.assertEqual(moved, 35)
        self.assertEqual([it["unit"] for it in doc["items"]], ["10", "1"])
        self.assertEqual(med["unit"], "10")


class AnEditOfAPurchaseWithALooseLine(_Online):
    EXISTING = {"id": 3027, "local_id": 3027, "client_uuid": "p-3027",
                "purchase_no": "112/FY2026-27", "purchase_date": "2026-09-12", "version": 2,
                "supplier_id": 301, "items": [_stored_line(5, 1)]}

    def test_the_edit_takes_back_the_five_it_added(self):
        _doc, med, moved = self.save([_screen_line(3, 10, rate=49.98)],
                                     editing_purchase_id=3027, edit_previous_due=0)
        self.assertEqual(moved, 25, "the loose line was taken back as 5 strips of 10")
        self.assertEqual(med["unit"], "10")


class AnEditOfATwoLineBillWhenTheLedgerCannotBeRead(_Online):
    EXISTING = {"id": 3026, "local_id": 3026, "client_uuid": "p-3026",
                "purchase_no": "111/FY2026-27", "purchase_date": "2026-09-12", "version": 5,
                "supplier_id": 301, "items": [_stored_line(2, 10, free=1), _stored_line(5, 1)]}

    def test_the_lines_fallback_counts_each_line_with_its_own_pack(self):
        _doc, _med, moved = self.save([_screen_line(3, 10, free=1, rate=49.98)],
                                      editing_purchase_id=3026, edit_previous_due=0)
        self.assertEqual(moved, 5, "old side read the loose line as 50")

    def test_with_the_ledger_it_takes_back_what_the_save_logged(self):
        self.ledger_down = False
        self.ledger = [{"id": 1, "op_uuid": "purchase:p-3026:med:17485:v1", "medicine_id": MID,
                        "op": "purchase", "qty_delta": 35, "ref_collection": "purchases",
                        "ref_id": 3026, "created_at": "2026-09-12T06:00:00.000Z"}]
        _doc, _med, moved = self.save([_screen_line(3, 10, free=1, rate=49.98)],
                                      editing_purchase_id=3026, edit_previous_due=0)
        self.assertEqual(moved, 5)


class AQueuedDeleteOfAPurchaseWithALooseLine(_Online):
    EXISTING = {"id": 3027, "local_id": 3027, "client_uuid": "p-3027",
                "purchase_no": "112/FY2026-27", "purchase_date": "2026-09-12", "version": 2,
                "supplier_id": 301, "items": [_stored_line(2, 10, free=1), _stored_line(5, 1)]}

    def test_the_delete_takes_back_thirty_five(self):
        written: list[dict] = []
        with mock.patch("core.server_crud.delete_purchase_online", return_value=None), \
                mock.patch("core.server_crud.upsert_docs",
                           side_effect=lambda col, docs: written.extend(docs)), \
                mock.patch.object(online_mutation_queue, "_recompute_supplier_balance_online",
                                  return_value=None):
            online_mutation_queue._flush_purchase_delete({"purchase_id": 3027}, 3027)
        moved = sum(int(op["qty_delta"]) for m in written for op in m.get("stock_ops") or [])
        self.assertEqual(moved, -35, "the loose line was taken back as 5 strips of 10")


class OfflineALooseLine(unittest.TestCase):
    def setUp(self):
        self.conn = conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        conn.execute("INSERT INTO suppliers (id, name) VALUES (1, 'SHREE PHARMA')")
        conn.execute(
            "INSERT INTO medicines (id, name, type, unit, stock_qty, batch_no, expiry_date, "
            "created_at) VALUES (20, 'MOLICOLD TAB', 'Tablet', '10', 1, 'TN118C', '2027-10-31', "
            "'2026-01-01 09:00:00')")
        conn.commit()
        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.online_catalog.medicine_by_id",
                       return_value={"id": 20, "name": "MOLICOLD TAB", "type": "Tablet",
                                     "unit": "10"}),
            mock.patch("core.server_crud.get_doc", return_value=None),
        ):
            stack.enter_context(patch)

    @staticmethod
    def calc():
        keys = ("subtotal", "total_gst", "cgst", "sgst", "total_amount", "overall_discount",
                "rounding", "need_to_pay", "final_amount", "previous_due", "previous_credit",
                "due", "current_credit", "total_due", "bill_cleared", "account_cleared")
        return {k: 0 for k in keys}

    @staticmethod
    def line(qty, pack):
        return {"medicine_id": 20, "name": "MOLICOLD TAB", "type": "Tablet", "batch": "TN118C",
                "expiry": "10/27", "qty": qty, "free_qty": 0, "rate": 5, "mrp": 6,
                "unit": str(pack), "tablets_per_stripe": pack, "quantity_value": str(pack)}

    def med(self):
        return self.conn.execute("SELECT stock_qty, unit FROM medicines WHERE id=20").fetchone()

    def test_save_edit_and_delete(self):
        purchase_service.save_purchase(self.conn, 1, "2026-09-12", "INV-1", self.calc(),
                                       [self.line(5, 1)])
        self.assertEqual(self.med(), (6, "10"))
        pid = self.conn.execute("SELECT id FROM purchases ORDER BY id DESC LIMIT 1").fetchone()[0]
        # Nothing has been sold: the delete may go ahead, and takes back the 5 it added.
        res = desktop_purchase_service.delete_saved_purchase(self.conn, {"purchase_id": pid})
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.med()[0], 1)

    def test_an_edit_takes_back_the_loose_line_and_adds_the_strips(self):
        purchase_service.save_purchase(self.conn, 1, "2026-09-12", "INV-1", self.calc(),
                                       [self.line(5, 1)])
        pid = self.conn.execute("SELECT id FROM purchases ORDER BY id DESC LIMIT 1").fetchone()[0]
        purchase_service.update_purchase(self.conn, pid, 1, "INV-1", "2026-09-12", self.calc(),
                                         [self.line(3, 10)])
        self.assertEqual(self.med(), (31, "10"))


if __name__ == "__main__":
    unittest.main()
