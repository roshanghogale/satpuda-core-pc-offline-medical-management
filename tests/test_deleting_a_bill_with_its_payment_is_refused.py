"""A sales bill a payment was taken against is not deleted until that payment is (audit, 25 Oct 2026).

A payment taken on a bill (Sales History "pay", or bills ticked in Payments) names the bill in its
allocations. Deleting the bill left the receipt behind as the customer's credit: the party's dues
moved with no bill to show why. As with a sales return, the payment goes first.

A payment that only cleared the bill by the oldest-first rule (no bill named) does not hold it:
deleting the bill leaves that money as credit, as before.
"""
from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from core import db_setup


class BillWithPayment(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:")
        db_setup.initialise(self.c)
        self.c.commit()
        for p in (mock.patch("core.sync_prefs.is_online_mode", return_value=False),
                  mock.patch("core.sync_prefs.is_offline_first", return_value=False)):
            p.start()
            self.addCleanup(p.stop)
        self.cid = self.c.execute("INSERT INTO customers (name, phone) VALUES ('RAM','9000000001')").lastrowid
        self.mid = self.c.execute(
            "INSERT INTO medicines (name, type, stock_qty, unit, mrp, rate, batch_no) "
            "VALUES ('DOLO','Tablet',100,'1',25,20,'B1')").lastrowid
        self.c.commit()

    def bill(self, qty=5):
        from core.billing_service import save_new_bill

        line = {"id": self.mid, "qty": qty, "rate": 20.0, "amount": qty * 20.0, "medicine_discount": 0,
                "gst_percent": 12, "name": "DOLO", "batch": "B1", "expiry": "", "schedule": "", "unit": "1", "mrp": 25}
        return int(save_new_bill(self.c, self.cid, [line], 0, 0, 0, 0, "", "", 0, bill_date="2026-10-25",
                                 customer_name="RAM")[1])

    def delete(self, sid):
        from core import desktop_sales_service as svc

        return svc.delete_saved_sale(self.c, {"sale_id": sid})

    def party(self):
        r = self.c.execute("SELECT total_due, total_credit FROM customers WHERE id=?", (self.cid,)).fetchone()
        return (round(float(r[0] or 0), 2), round(float(r[1] or 0), 2))

    def test_a_payment_taken_on_the_bill_holds_it(self):
        from core.history_pay import pay_bill

        sid = self.bill()
        pay = pay_bill(self.c, {"kind": "sale", "id": sid, "amount": 100, "mode": "Cash", "date": "2026-10-25"})
        self.assertEqual(self.party(), (0.0, 0.0))
        out = self.delete(sid)
        self.assertFalse(out["ok"])
        self.assertEqual(out["code"], "bill_has_payments")
        self.assertIn("100.00", out["error"])
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM sales WHERE id=?", (sid,)).fetchone()[0], 1)
        self.assertEqual(float(self.c.execute("SELECT stock_qty FROM medicines WHERE id=?", (self.mid,)).fetchone()[0]), 95.0)
        self.assertEqual(self.party(), (0.0, 0.0))

        from core.desktop_settings_service import delete_payment

        delete_payment(self.c, {"kind": "customer", "id": pay["payment_id"]})
        out = self.delete(sid)
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.party(), (0.0, 0.0))

    def test_an_oldest_first_payment_does_not_hold_it(self):
        from core.desktop_settings_service import save_payment

        sid = self.bill()
        save_payment(self.c, {"kind": "customer", "party": "RAM", "cash": 40, "online": 0, "date": "2026-10-25"})
        out = self.delete(sid)
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.party(), (0.0, 40.0))

    def test_a_payment_naming_another_bill_does_not_hold_it(self):
        from core.history_pay import pay_bill

        s1, s2 = self.bill(1), self.bill(2)
        pay_bill(self.c, {"kind": "sale", "id": s2, "amount": 40, "mode": "Cash", "date": "2026-10-25"})
        out = self.delete(s1)
        self.assertTrue(out["ok"], out)


class EditingADeletedPayment(unittest.TestCase):
    """A receipt deleted meanwhile (another tab, or another PC's delete pulled in as deleted=1)
    is not edited back by the stale form."""

    setUp = BillWithPayment.setUp

    def test_customer_and_supplier(self):
        from core.desktop_settings_service import save_payment

        save_payment(self.c, {"kind": "customer", "party": "RAM", "cash": 40, "online": 0, "date": "2026-10-25"})
        pid = int(self.c.execute("SELECT id FROM customer_payments").fetchone()[0])
        self.c.execute("UPDATE customer_payments SET deleted=1 WHERE id=?", (pid,))
        sid = self.c.execute("INSERT INTO suppliers (name) VALUES ('AGENCY')").lastrowid
        spid = self.c.execute("INSERT INTO supplier_payments (supplier_id, payment_date, amount, mode, deleted) "
                              "VALUES (?, '2026-10-25', 70, 'Cash', 1)", (sid,)).lastrowid
        self.c.commit()
        with self.assertRaises(ValueError):
            save_payment(self.c, {"kind": "customer", "id": pid, "party": "RAM", "cash": 60, "online": 0,
                                  "date": "2026-10-25"})
        with self.assertRaises(ValueError):
            save_payment(self.c, {"kind": "supplier", "id": spid, "party": "AGENCY", "amount": 90, "mode": "Cash",
                                  "date": "2026-10-25"})
        self.assertEqual(float(self.c.execute("SELECT amount FROM customer_payments WHERE id=?", (pid,)).fetchone()[0]), 40.0)


if __name__ == "__main__":
    unittest.main()
