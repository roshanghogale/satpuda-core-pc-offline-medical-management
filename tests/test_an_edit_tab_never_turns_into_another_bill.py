"""A tab editing a saved purchase does not quietly become another supplier's bill.

Shivkrupa (6 Oct 2026) had purchase 106 -- TULJAI MEDICAL AGENCY, bill 2512, seven lines -- open
for edit, imported VINOD MEDICALS bill 2199 into that tab and saved. The save was an edit, so 106
became the VINOD bill and the TULJAI purchase, and the stock it had brought in, were gone.

An edit whose supplier or bill number is not the saved purchase's now writes nothing and asks:
a new purchase (the saved one stays as it is), or change the saved one. Editing the lines,
rates or date of the same bill is not asked about.

Everything runs on an in-memory store. Nothing reaches a server and no file is written.
"""
from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from core import db_setup, desktop_purchase_service as svc  # noqa: E402


def _offline():
    return (
        mock.patch("core.online_catalog.medicine_by_id", return_value=None),
        mock.patch("core.server_crud.get_doc", return_value=None),
    )


class TheTabEditingPurchase106(unittest.TestCase):
    def setUp(self):
        self.conn = conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        conn.commit()
        first = self.save(supplier="TULJAI MEDICAL AGENCY", bill="2512",
                          items=[self.line("PARA TAB"), self.line("AMOX CAP", batch="A1")])
        self.assertTrue(first.get("ok"), first)
        self.pid = int(first["purchase_id"])
        self.pno = first["purchase_no"]

    def line(self, name, **changes):
        item = {"name": name, "type": "Tablet", "batch": "B1", "expiry": "12/28", "qty": 2,
                "rate": 60.0, "mrp": 100.0, "unit": "10"}
        item.update(changes)
        return item

    def save(self, *, supplier, bill, items, **body):
        payload = {"supplier_name": supplier, "bill_number": bill,
                   "purchase_date": "2026-09-10", "items": items, "cash_paid": 0}
        payload.update(body)
        catalog, doc = _offline()
        with catalog, doc:
            return svc.save_purchase_bill(self.conn, payload)

    def saved(self):
        return self.conn.execute(
            "SELECT s.name, p.bill_number, (SELECT COUNT(*) FROM purchase_items i "
            "WHERE i.purchase_id=p.id) FROM purchases p JOIN suppliers s ON s.id=p.supplier_id "
            "WHERE p.id=?", (self.pid,),
        ).fetchone()

    def live(self):
        return self.conn.execute(
            "SELECT COUNT(*) FROM purchases WHERE COALESCE(deleted,0)=0").fetchone()[0]

    def vinod(self, **body):
        return self.save(supplier="VINOD MEDICALS", bill="2199",
                         items=[self.line("CIPCAL", batch="C1"), self.line("DOLO", batch="D1"),
                                self.line("MIKACIN", batch="M1")],
                         editing_purchase_id=self.pid, **body)

    def test_another_suppliers_bill_is_refused_and_writes_nothing(self):
        res = self.vinod()
        self.assertFalse(res.get("ok"), res)
        self.assertTrue(res.get("need_confirm"))
        self.assertEqual(res.get("code"), "edit_other_bill")
        self.assertEqual(res["editing"]["purchase_id"], self.pid)
        self.assertIn("TULJAI MEDICAL AGENCY", res["message"])
        self.assertIn("VINOD MEDICALS", res["message"])
        self.assertEqual(self.saved(), ("TULJAI MEDICAL AGENCY", "2512", 2))
        self.assertEqual(self.live(), 1)

    def test_saved_as_new_it_is_a_second_purchase(self):
        # What the screen sends after "Navin purchase save kar": no editing id.
        res = self.save(supplier="VINOD MEDICALS", bill="2199",
                        items=[self.line("CIPCAL", batch="C1")])
        self.assertTrue(res.get("ok"), res)
        self.assertNotEqual(int(res["purchase_id"]), self.pid)
        self.assertEqual(self.saved(), ("TULJAI MEDICAL AGENCY", "2512", 2))
        self.assertEqual(self.live(), 2)

    def test_the_shop_can_still_change_the_purchase_on_purpose(self):
        res = self.vinod(confirm_replace_purchase=True)
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.saved(), ("VINOD MEDICALS", "2199", 3))
        self.assertEqual(self.live(), 1)

    def test_only_the_bill_number_changed_is_asked_too(self):
        res = self.save(supplier="TULJAI MEDICAL AGENCY", bill="2600",
                        items=[self.line("PARA TAB")], editing_purchase_id=self.pid)
        self.assertEqual(res.get("code"), "edit_other_bill", res)
        self.assertEqual(self.saved(), ("TULJAI MEDICAL AGENCY", "2512", 2))

    def test_editing_the_same_bill_is_not_asked(self):
        catalog, doc = _offline()
        with catalog, doc:
            loaded = svc.load_purchase(self.conn, self.pid)
        items = loaded["form"]["items"] + [self.line("ZINC TAB", batch="Z1")]
        # Same bill, written a little differently: spacing and case are not another bill.
        res = self.save(supplier="tuljai  medical agency", bill=" 2512",
                        items=items, editing_purchase_id=self.pid)
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.saved()[2], 3)
        self.assertEqual(self.live(), 1)


if __name__ == "__main__":
    unittest.main()
