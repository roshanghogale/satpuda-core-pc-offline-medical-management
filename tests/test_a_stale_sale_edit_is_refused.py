"""A bill opened for edit and changed meanwhile is not overwritten by the stale form (audit, 25 Oct 2026).

Offline-first pulls another PC's edit of the bill into this PC's copy while an edit tab is open
here; a walk-in merges into the day's counter bill; another tab saves the same bill. The stale
form then replaced that change without a word (the stock followed the form, so only the other
change was lost, and nobody was told). The bill opened for edit now carries a fingerprint
(edit_stamp); saving the edit of a bill whose lines or money changed since is refused.

A payment or another bill of the party moves only the bill's dues: that does not block the edit.
"""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import ExitStack
from unittest import mock

from core import db_setup

DAY = "2026-10-25"


class StaleEdit(unittest.TestCase):
    def setUp(self):
        from core import desktop_sales_service as svc

        self.svc = svc
        self.c = sqlite3.connect(":memory:", check_same_thread=False)
        db_setup.initialise(self.c)
        self.c.commit()
        stack = ExitStack()
        self.addCleanup(stack.close)
        for p in (mock.patch("core.sync_prefs.is_online_mode", return_value=False),
                  mock.patch("core.sync_prefs.is_offline_first", return_value=False),
                  mock.patch.object(svc, "_has_pharmacy_profile", return_value=True),
                  mock.patch.object(svc, "_try_save_sale_pdf", return_value=None),
                  mock.patch("core.bill_save_prefs.resolve_sales_bill_save_dir", return_value=""),
                  mock.patch("core.autosave_prefs.load_autosave_enabled", return_value=False)):
            stack.enter_context(p)
        self.m = self.c.execute(
            "INSERT INTO medicines (name, type, stock_qty, unit, mrp, rate, batch_no, expiry_date, gst_percent) "
            "VALUES ('DOLO','Syrup',100,'1',10,10,'B1','2028-12-01',12)").lastrowid
        self.c.commit()

    def item(self, qty):
        return {"id": self.m, "name": "DOLO", "qty": qty, "rate": 10.0, "amount": qty * 10.0,
                "medicine_discount": 0, "unit": "1", "type": "Syrup", "batch": "B1", "gst_percent": 12}

    def save(self, items, **extra):
        body = {"items": items, "customer_name": "RAM", "payment_mode": "Due", "pay_full": False,
                "bill_date": DAY, "discount_rs": 0, "auto_rounding": False, "rounding": 0}
        body.update(extra)
        return self.svc.save_sale(self.c, body)

    def lines(self, sid):
        return self.c.execute("SELECT qty FROM sales_items WHERE sale_id=?", (sid,)).fetchall()

    def test_an_edit_of_a_bill_changed_meanwhile_is_refused(self):
        sid = self.save([self.item(5)])["sale_id"]
        opened = self.svc.load_sale(self.c, sid)
        self.assertTrue(opened["edit_stamp"])
        # Meanwhile: the bill is changed elsewhere (another tab / another PC's edit pulled in).
        other = self.save([self.item(2)], editing_sale_id=sid)
        self.assertTrue(other["ok"], other)
        stale = self.save([self.item(7)], editing_sale_id=sid, edit_stamp=opened["edit_stamp"])
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["code"], "bill_changed")
        self.assertEqual(self.lines(sid), [(2,)])
        self.assertEqual(float(self.c.execute("SELECT stock_qty FROM medicines WHERE id=?", (self.m,)).fetchone()[0]), 98.0)
        fresh = self.svc.load_sale(self.c, sid)["edit_stamp"]
        ok = self.save([self.item(7)], editing_sale_id=sid, edit_stamp=fresh)
        self.assertTrue(ok["ok"], ok)
        self.assertEqual(self.lines(sid), [(7,)])

    def test_a_payment_meanwhile_does_not_block_the_edit(self):
        from core.desktop_settings_service import save_payment

        sid = self.save([self.item(5)])["sale_id"]
        stamp = self.svc.load_sale(self.c, sid)["edit_stamp"]
        save_payment(self.c, {"kind": "customer", "party": "RAM", "cash": 20, "online": 0, "date": DAY})
        self.assertEqual(self.svc.load_sale(self.c, sid)["edit_stamp"], stamp)
        out = self.save([self.item(4)], editing_sale_id=sid, edit_stamp=stamp)
        self.assertTrue(out["ok"], out)

    def test_an_edit_without_a_stamp_saves_as_before(self):
        sid = self.save([self.item(5)])["sale_id"]
        out = self.save([self.item(3)], editing_sale_id=sid)
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.lines(sid), [(3,)])


if __name__ == "__main__":
    unittest.main()
