"""A purchase edit whose medicine document the store refuses says truthfully what moved.

Two devices edit one purchase from the same version, so their movements share op_uuids
("purchase:<uuid>:med:<mid>:edit:v4"). When they moved one medicine by different amounts the
store refuses that medicine's document (stockOperations: "... was already applied with qty_delta
-20; this push carries -10 under the same op_uuid. Nothing was changed."). Every other document
of the bundle committed under its own savepoint, so this edit's movements on the other medicines
stayed on the shelf, while the save showed the server's "Nothing was changed." for the whole edit
(staging dev2 BOTH_DIFF, store 4 purchase 2997).

Now the PC reads the store's copy and answers for the whole edit: when the edit is not on the
store, a movement that is certainly this edit's (the other change left that medicine's lines as
they were) is put back and any other is named for a stock check; when the edit is on the store
(its stamp was the newer one), the medicine whose movement was refused is named. The refused
medicine's own document moved nothing.

The fake store of test_a_purchase_edit_is_not_lost_to_a_payment stands in for server-live. Nothing
reaches a server.
"""
from __future__ import annotations

import copy
import unittest

from core import online_mutation_queue as omq
from core import server_crud
from tests.test_a_purchase_edit_is_not_lost_to_a_payment import (  # noqa: E402
    CU,
    _lines,
    _Race,
    _totals,
    purchase_service,
)


class _Refused(_Race):
    def edit_to(self, ointment, cream):
        items = _lines(ointment=ointment, cream=cream)
        calc = dict(_totals(items), expenditure=0.0, previous_due=0.0, previous_credit=0.0,
                    cash_paid=0.0, online_paid=0.0, amount_paid=0.0,
                    gst_calc_method="discount_after_gst", current_credit=0.0,
                    bill_cleared=0, account_cleared=0)
        calc.update(due=calc["final_amount"], total_due=calc["final_amount"])
        return purchase_service.update_purchase_online_now(
            2997, 297, "JP-2997", "2026-08-07", calc, items, client_uuid=CU,
        )

    def phone_edit(self, *, stamp=None, **qty):
        """The phone's edit of 2997 from the same version, with its own movements."""
        if stamp is None:
            self.another_device_edits(**qty)
            return
        row = self.store.purchases[2997]
        old = {int(i["medicine_id"]): float(i["qty"]) for i in row["items"]}
        items = _lines(ointment=qty.get("ointment", old[17512]), cream=qty.get("cream", old[17513]))
        doc = copy.deepcopy(row)
        money = _totals(items)
        doc.update(money, items=items, version=int(row["version"]) + 1, updated_at=stamp,
                   device_id="phone", due=money["final_amount"],
                   due_amount=money["final_amount"], total_due=money["final_amount"])
        self.store.upsert_purchase(doc)
        for item in items:
            mid = int(item["medicine_id"])
            delta = int(item["qty"] - old[mid])
            if delta:
                self.store.apply_op({"op_uuid": f"purchase:{CU}:med:{mid}:edit:v{doc['version']}",
                                     "op": "purchase_edit", "qty_delta": delta, "medicine_id": mid,
                                     "ref_collection": "purchases", "ref_id": 2997})


class TheEditIsNotOnTheStore(_Refused):
    def test_its_movement_on_the_other_medicine_is_put_back(self):
        # The phone took DAM 5 OINT to 58 (-2). This edit: DAM 5 OINT 59 (-1, refused) and
        # DAM-6 CREAM 21 (+1, applied -- the phone never moved the cream).
        self.store.before_next_bundle.append(lambda: self.phone_edit(ointment=58))
        with self.assertRaises(server_crud.PurchaseEditNotSaved) as caught:
            self.edit_to(59, 21)
        exc = caught.exception
        message = str(exc)
        self.assertNotIn("Nothing was changed", message)
        self.assertIn("83", message)
        self.assertIn("another device", message)
        self.assertIn("put back", message)
        self.assertFalse(getattr(exc, "saved", False))
        self.assertTrue(omq._is_permanent_refusal(exc), "the queue would replay the edit")
        self.assertEqual(self.lines(), [(17512, 58.0), (17513, 20.0)], "the phone's edit was lost")
        self.assertEqual(self.stock(17512), 598.0, "the phone's movement is not the one on the shelf")
        self.assertEqual(self.stock(17513), 200.0, "this edit's cream movement stayed on the shelf")

    def test_nothing_this_edit_moved_is_claimed_or_named_when_every_movement_was_refused(self):
        self.store.before_next_bundle.append(lambda: self.phone_edit(ointment=58, cream=22))
        with self.assertRaises(server_crud.PurchaseEditNotSaved) as caught:
            self.edit_to(59, 21)
        message = str(caught.exception)
        self.assertNotIn("put back", message)
        self.assertNotIn("Check the stock", message)
        self.assertIn("another device", message)
        self.assertEqual(self.stock(17512), 598.0)
        self.assertEqual(self.stock(17513), 202.0)

    def test_a_medicine_both_edits_moved_the_same_way_is_named_not_guessed(self):
        # Both took DAM 5 OINT to 59 under one op_uuid: the store kept one movement, and whose it
        # was cannot be told, so it is named. The cream (+2 phone, +1 here) was refused.
        self.store.before_next_bundle.append(lambda: self.phone_edit(ointment=59, cream=22))
        with self.assertRaises(server_crud.PurchaseEditNotSaved) as caught:
            self.edit_to(59, 21)
        message = str(caught.exception)
        self.assertIn("Check the stock of DAM 5 OINT", message)
        self.assertEqual(self.stock(17512), 599.0)
        self.assertEqual(self.stock(17513), 202.0)


class TheEditIsOnTheStore(_Refused):
    def test_the_medicine_whose_movement_was_refused_is_named_and_nothing_is_put_back(self):
        # The phone's edit carries the older stamp, so this edit is stored over it (the server's
        # last-write-wins rule) while its DAM 5 OINT movement was refused.
        self.store.before_next_bundle.append(
            lambda: self.phone_edit(ointment=58, stamp="2026-09-01T00:00:00.000Z"))
        with self.assertRaises(server_crud.PurchaseEditNotSaved) as caught:
            self.edit_to(59, 21)
        exc = caught.exception
        message = str(exc)
        self.assertTrue(getattr(exc, "saved", False), "a stored edit was reported as not saved")
        self.assertIn("was saved", message)
        self.assertIn("DAM 5 OINT", message)
        self.assertNotIn("DAM-6 CREAM", message)
        self.assertNotIn("Nothing was changed", message)
        self.assertEqual(self.lines(), [(17512, 59.0), (17513, 21.0)])
        self.assertEqual(self.stock(17513), 201.0, "a movement of a stored edit was taken back")
        self.assertEqual(self.stock(17512), 598.0)


class ThePurchaseScreensSave(unittest.TestCase):
    """desktop_purchase_service.save_purchase_bill, the Purchase screen's save.

    A stored edit with a refused movement is saved: the medicine to check is a save warning,
    which never blocks. An edit that was not saved is still a failed save.
    """

    def save(self, raised):
        import sqlite3
        from contextlib import ExitStack
        from unittest import mock

        from core import db_setup, desktop_purchase_service

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        body = {"editing_purchase_id": 2997, "supplier_name": "JAISHIV PHARMA",
                "bill_number": "JP-2997", "purchase_date": "2026-08-07",
                "edit_previous_due": 0, "edit_previous_credit": 0,
                "gst_calc_method": "discount_after_gst",
                "items": [{"medicine_id": 17512, "name": "DAM 5 OINT", "type": "Ointment",
                           "qty": 59, "rate": 50, "mrp": 70, "gst_pct": 5, "batch": "B7",
                           "expiry": "12/27", "unit": "1"}]}
        with ExitStack() as stack:
            for patch in (
                mock.patch("core.sync_prefs.is_online_mode", return_value=True),
                mock.patch("core.purchase_service.update_purchase", side_effect=raised),
                mock.patch("core.purchase_service.get_or_create_supplier", return_value=297),
                mock.patch("core.desktop_returns_service.edit_below_returned_error",
                           return_value=""),
                mock.patch("core.save_warnings.duplicate_supplier_bill_warnings", return_value=[]),
                mock.patch("core.save_warnings.purchase_line_warnings", return_value=[]),
                mock.patch("core.online_catalog.find_supplier_by_id", return_value=None),
            ):
                stack.enter_context(patch)
            return desktop_purchase_service.save_purchase_bill(conn, body)

    def test_a_stored_edit_with_a_refused_movement_saves_with_a_warning(self):
        out = self.save(server_crud.PurchaseEditNotSaved(
            "Purchase 83 was saved, but the server refused this edit's stock movement for "
            "DAM 5 OINT (DAM 5 OINT: stock op already applied). Check the stock of DAM 5 OINT.",
            2997, saved=True, purchase_no="83/FY2026-27"))
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out.get("purchase_no"), "83")
        self.assertTrue(any("DAM 5 OINT" in w for w in out.get("warnings") or []), out)

    def test_an_edit_that_was_not_saved_is_still_a_failed_save(self):
        out = self.save(server_crud.PurchaseEditNotSaved(
            "Purchase 83 was changed on another device while this edit was being saved, so this "
            "edit was not saved over that change.", 2997))
        self.assertFalse(out.get("ok"), out)
        self.assertIn("another device", out.get("error") or "")


if __name__ == "__main__":
    unittest.main()
