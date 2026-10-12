"""A payment clears the bills it pays, on this PC, at once (owner, 12 Oct 2026).

On the beta a customer / supplier payment taken Offline or offline-first moved the party's total
but left each bill's due_amount / bill_cleared as billed: Sales History kept "Partial Payment",
Purchase History kept "Due" for ever (it read the entry figures only). The PC now writes the
same per-bill figures as the server's cascade (oldest bill first), and a due can be paid from
the history row (core/history_pay.py).
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db_setup, desktop_pages_service as pages, history_pay  # noqa: E402
from core.customer_service import recalculate_customer_due  # noqa: E402
from core.purchase_service import recalculate_supplier_due  # noqa: E402


def _store() -> sqlite3.Connection:
    conn = sqlite3.connect(tempfile.mktemp(suffix=".db"))
    db_setup.initialise(conn)
    return conn


class Base(unittest.TestCase):
    def setUp(self):
        self._p = mock.patch("core.sync_prefs.is_online_mode", lambda *a, **k: False)
        self._p.start()
        self.addCleanup(self._p.stop)
        self.conn = _store()
        c = self.conn
        self.cid = c.execute("INSERT INTO customers (name, phone) VALUES ('RAMA', '9000000001')").lastrowid
        self.sid = c.execute("INSERT INTO suppliers (name) VALUES ('ACME PHARMA')").lastrowid
        # two part-paid bills: 100 (paid 10) and 80 (paid 20) -> due 90 + 60
        self.s1 = c.execute(
            "INSERT INTO sales (bill_no, bill_date, customer_id, total_amount, amount_paid, due_amount, total_due) "
            "VALUES ('SCB1/FY2026-27', '2026-10-01', ?, 100, 10, 90, 90)", (self.cid,)).lastrowid
        self.s2 = c.execute(
            "INSERT INTO sales (bill_no, bill_date, customer_id, total_amount, amount_paid, due_amount, total_due) "
            "VALUES ('SCB2/FY2026-27', '2026-10-05', ?, 80, 20, 60, 60)", (self.cid,)).lastrowid
        # a purchase on credit: 500 due
        self.p1 = c.execute(
            "INSERT INTO purchases (purchase_no, purchase_date, supplier_id, total_amount, final_amount, "
            "amount_paid, due_amount, due) VALUES ('7/FY2026-27', '2026-10-02', ?, 500, 500, 0, 500, 500)",
            (self.sid,)).lastrowid
        c.commit()

    def sale(self, sid):
        return self.conn.execute(
            "SELECT due_amount, total_due, bill_cleared, account_cleared FROM sales WHERE id=?", (sid,)).fetchone()

    def purchase(self, pid):
        return self.conn.execute(
            "SELECT due_amount, due, total_due, bill_cleared FROM purchases WHERE id=?", (pid,)).fetchone()


class ACustomerPaymentClearsTheBills(Base):
    def test_oldest_bill_first_with_the_fields_history_reads(self):
        self.conn.execute(
            "INSERT INTO customer_payments (customer_id, payment_date, amount, payment_mode) "
            "VALUES (?, '2026-10-10', 100, 'cash')", (self.cid,))
        recalculate_customer_due(self.conn, self.cid, sync=False)
        self.assertEqual(self.sale(self.s1), (0.0, 0.0, 1, 1))     # oldest, fully paid
        self.assertEqual(self.sale(self.s2), (50.0, 50.0, 0, 0))   # 10 of its 60 paid

    def test_history_shows_the_new_status(self):
        self.conn.execute(
            "INSERT INTO customer_payments (customer_id, payment_date, amount, payment_mode) "
            "VALUES (?, '2026-10-10', 150, 'cash')", (self.cid,))
        recalculate_customer_due(self.conn, self.cid, sync=False)
        res = pages.list_sales_history(self.conn, from_date="2026-04-01", to_date="2027-03-31")
        status = {r[0]: r[-1] for r in res["rows"]}
        self.assertTrue(all("Cleared" in str(s) or "Paid" in str(s) for s in status.values()), status)


class ASupplierPaymentClearsThePurchase(Base):
    def test_purchase_history_follows_supplier_payments(self):
        self.conn.execute(
            "INSERT INTO supplier_payments (supplier_id, payment_date, amount, mode) "
            "VALUES (?, '2026-10-10', 200, 'Cash')", (self.sid,))
        recalculate_supplier_due(self.conn, self.sid)
        self.assertEqual(self.purchase(self.p1), (300.0, 300.0, 300.0, 0))
        res = pages.list_purchase_history(self.conn, from_date="2026-04-01", to_date="2027-03-31")
        cols = res["columns"]
        row = res["rows"][0]
        self.assertIn("Partial", str(row[cols.index("Status")]))
        self.conn.execute(
            "INSERT INTO supplier_payments (supplier_id, payment_date, amount, mode) "
            "VALUES (?, '2026-10-11', 300, 'Cash')", (self.sid,))
        recalculate_supplier_due(self.conn, self.sid)
        res = pages.list_purchase_history(self.conn, from_date="2026-04-01", to_date="2027-03-31")
        self.assertIn("Cleared", str(res["rows"][0][cols.index("Status")]))


class PayFromHistory(Base):
    def test_the_amount_that_clears_a_newer_bill_includes_the_older_dues(self):
        info = history_pay.bill_due_info(self.conn, "sale", self.s2)
        self.assertEqual((info["bill_due"], info["older_due"], info["to_clear"]), (60.0, 90.0, 150.0))
        self.assertTrue(info["oldest_first_note"])
        self.assertEqual(history_pay.bill_due_info(self.conn, "sale", self.s1)["older_due"], 0.0)

    def test_a_payment_from_the_row_is_a_ledger_entry_and_clears_the_bills(self):
        res = history_pay.pay_bill(self.conn, {"kind": "sale", "id": self.s2, "amount": 150, "mode": "Online"})
        self.assertEqual(res["after"]["bill_due"], 0.0)
        pay = self.conn.execute(
            "SELECT amount, online_amount, cash_amount, reference_no FROM customer_payments").fetchall()
        self.assertEqual(pay, [(150.0, 150.0, 0.0, "SCB2/FY2026-27")])
        self.assertEqual(self.sale(self.s1)[2:], (1, 1))
        self.assertEqual(self.sale(self.s2)[2:], (1, 1))
        due = self.conn.execute("SELECT total_due FROM customers WHERE id=?", (self.cid,)).fetchone()[0]
        self.assertEqual(due, 0.0)

    def test_paying_a_supplier_from_purchase_history(self):
        res = history_pay.pay_bill(self.conn, {"kind": "purchase", "id": self.p1, "amount": 500})
        self.assertIn("paid to ACME PHARMA", res["message"])
        self.assertEqual(self.purchase(self.p1)[3], 1)
        n = self.conn.execute("SELECT COUNT(*) FROM supplier_payments WHERE reference=?",
                              ("7/FY2026-27",)).fetchone()[0]
        self.assertEqual(n, 1)

    def test_nothing_to_pay_and_bad_amounts_are_refused(self):
        with self.assertRaises(ValueError):
            history_pay.pay_bill(self.conn, {"kind": "sale", "id": self.s1, "amount": 0})
        with self.assertRaises(ValueError):
            history_pay.bill_due_info(self.conn, "sale", 999999)


class OldDataIsRepairedOnce(Base):
    def test_the_one_time_repair_applies_payments_already_taken(self):
        # a payment taken by the old build: the party total moved, the bills did not
        self.conn.execute(
            "INSERT INTO customer_payments (customer_id, payment_date, amount, payment_mode) "
            "VALUES (?, '2026-10-10', 90, 'cash')", (self.cid,))
        self.conn.execute("DELETE FROM settings WHERE name='bill_dues_follow_payments_v1'")
        self.conn.commit()
        db_setup._run_bill_dues_follow_payments(self.conn)
        self.assertEqual(self.sale(self.s1)[2], 1)
        flag = self.conn.execute(
            "SELECT value FROM settings WHERE name='bill_dues_follow_payments_v1'").fetchone()[0]
        self.assertEqual(flag, "1")


if __name__ == "__main__":
    unittest.main()
