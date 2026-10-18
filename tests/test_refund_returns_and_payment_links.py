"""Refund a return from the Payment screen; a payment's View box; the Ledger names what paid
what (owner, 18 Oct 2026).

"Settings -> Payment: a payment kind 'Refund a return' -- pick the party, its returns with
credit still open, tick several, an amount per return, cash/online. Customer: we pay the
customer; supplier: the supplier pays us back." / "A payment given to several bills shows all
of them, and a View box." / "Ledger: on a payment row 'Cleared: S2-55 ₹500 · S2-61 ₹200', on a
bill row 'Paid by: PAY-12 ₹300, return SR1-3 ₹50'."

Scratch stores only; Offline. Nothing reaches a server.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import desktop_settings_service as settings  # noqa: E402
from core import desktop_returns_service as returns_svc  # noqa: E402
from core import payment_details, return_credit, return_refunds  # noqa: E402
from core.customer_service import recalculate_customer_due  # noqa: E402
from tests.test_payments_choose_bills import Base as _Books  # noqa: E402
from tests.test_returns_as_party_credit import CUSTOMER, SUPPLIER, _item, _Shop  # noqa: E402


def _history_row(out, pay_id):
    return next(h for h in out["history"] if int(h["id"]) == int(pay_id))


class ACustomerRefund(_Shop):
    def setUp(self):
        super().setUp()
        s1 = self.sell([_item(self.syrup, "COUGH SYRUP", 3, 10.0)])
        self.ret = self.give_back(s1["sale_id"], 2)  # 20 kept as credit
        self.rno = self.ret["return_no"]

    def refund(self, amount, mode="cash", rid=None, **extra):
        return return_refunds.save_refund(self.conn, {
            "kind": "customer", "party": CUSTOMER, "mode": mode, "date": "2026-10-18",
            "refunds": [{"return_id": rid or self.ret["return_id"], "amount": amount}], **extra})

    def test_the_screen_lists_the_open_returns(self):
        got = return_refunds.refund_returns(self.conn, "customer", party=CUSTOMER)
        self.assertTrue(got["supported"])
        self.assertEqual([(r["return_no"], r["left"]) for r in got["returns"]], [(self.rno, 20.0)])
        self.assertEqual(got["returns"][0]["items"][0]["name"], "COUGH SYRUP")

    def test_the_refund_is_the_row_a_cash_return_writes(self):
        self.assertEqual(self.customer_balance(), (0.0, 20.0))
        out = self.refund(15)
        pid = out["payment_ids"][0]
        row = self.conn.execute(
            "SELECT customer_id, amount, payment_mode, cash_amount, online_amount, reference_no, note "
            "FROM customer_payments WHERE id=?", (pid,)).fetchone()
        self.assertEqual(row, (self.customer_id(), -15.0, "cash", -15.0, 0.0, self.rno, f"Refund {self.rno}"))
        self.assertEqual(self.open_left(), {self.rno: 5.0})
        self.assertEqual(self.customer_balance(), (0.0, 5.0))
        h = _history_row(out, pid)
        self.assertTrue(h["refund"])
        self.assertEqual(h["label"], f"Refund {self.rno}")
        self.assertEqual(h["refund_of"]["return_id"], self.ret["return_id"])
        self.assertEqual(h["cleared"], [])

    def test_online_with_a_note(self):
        out = self.refund(20, mode="online", note="paid by phone")
        row = self.conn.execute(
            "SELECT amount, payment_mode, cash_amount, online_amount, note FROM customer_payments WHERE id=?",
            (out["payment_ids"][0],)).fetchone()
        self.assertEqual(row, (-20.0, "online", 0.0, -20.0, f"Refund {self.rno} · paid by phone"))
        self.assertEqual(self.open_left(), {})

    def test_more_than_the_open_credit_is_refused(self):
        with self.assertRaisesRegex(ValueError, "₹20.00 credit left"):
            self.refund(21)
        with self.assertRaisesRegex(ValueError, "Tick a return"):
            self.refund(0)
        with self.assertRaisesRegex(ValueError, "Cash or Online"):
            self.refund(5, mode="cheque")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM customer_payments").fetchone()[0], 0)

    def test_several_returns_at_once(self):
        s2 = self.sell([_item(self.tab, "PAIN TAB", 2, 20.0)])
        ret2 = self.give_back(s2["sale_id"], 1, mid=self.tab, rate=20.0)
        out = return_refunds.save_refund(self.conn, {
            "kind": "customer", "party": CUSTOMER, "mode": "cash",
            "refunds": [{"return_id": self.ret["return_id"], "amount": 20},
                        {"return_id": ret2["return_id"], "amount": 5}]})
        self.assertEqual(len(out["payment_ids"]), 2)
        refs = [r[0] for r in self.conn.execute(
            "SELECT reference_no FROM customer_payments ORDER BY id").fetchall()]
        self.assertEqual(refs, [self.rno, ret2["return_no"]])
        self.assertEqual(self.open_left(), {ret2["return_no"]: 15.0})
        self.assertEqual(self.customer_balance(), (0.0, 15.0))

    def test_an_edit_reads_the_credit_without_itself(self):
        pid = self.refund(15)["payment_ids"][0]
        listed = return_refunds.refund_returns(self.conn, "customer", party=CUSTOMER, payment_id=pid)
        self.assertEqual([r["left"] for r in listed["returns"]], [20.0])
        self.assertEqual(listed["editing"]["amount"], 15.0)
        self.refund(20, id=pid)
        self.assertEqual(self.conn.execute("SELECT amount FROM customer_payments WHERE id=?", (pid,)).fetchone()[0],
                         -20.0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM customer_payments").fetchone()[0], 1)
        with self.assertRaises(ValueError):
            self.refund(21, id=pid)

    def test_deleting_the_refund_gives_the_credit_back(self):
        pid = self.refund(15)["payment_ids"][0]
        settings.delete_payment(self.conn, {"kind": "customer", "id": pid})
        self.assertEqual(self.open_left(), {self.rno: 20.0})
        self.assertEqual(self.customer_balance(), (0.0, 20.0))

    def test_the_view_box_names_the_return(self):
        pid = self.refund(15)["payment_ids"][0]
        d = payment_details.payment_details(self.conn, "customer", pid)["payment"]
        self.assertTrue(d["refund"])
        self.assertEqual(d["amount"], -15.0)
        self.assertEqual([(r["return_no"], r["amount"]) for r in d["refunded"]], [(self.rno, 15.0)])

    def test_the_ledger_says_refund(self):
        self.refund(15)
        led = settings.get_ledger(self.conn, "customer", CUSTOMER, "2026-04-01", "2027-03-31")
        refund = next(r for r in led["rows"] if r.get("tag") == "refund")
        self.assertEqual(refund["particulars"], f"Refund {self.rno} (cash)")
        self.assertEqual(refund["credit"], -15.0)
        self.assertTrue(refund["links"].startswith(f"Refund of {self.rno}"))
        ret_row = next(r for r in led["rows"] if r.get("tag") == "return")
        self.assertIn("refunded ₹15.00", ret_row["links"])
        self.assertIn("₹5.00 left as credit", ret_row["links"])
        self.assertAlmostEqual(led["summary"]["closing"], -5.0, places=2)

    def test_online_stores_are_told_it_is_not_here(self):
        with mock.patch("core.sync_prefs.is_online_mode", lambda *a, **k: True):
            self.assertFalse(return_refunds.refund_returns(self.conn, "customer", party=CUSTOMER)["supported"])
            with self.assertRaises(ValueError):
                self.refund(5)


class ASupplierRefund(_Shop):
    def setUp(self):
        super().setUp()
        p1 = self.buy("INV-1", paid_in_full=True)
        self.ret = returns_svc.save_purchase_return(self.conn, {
            "purchase_id": p1["purchase_id"], "supplier_id": self.supplier_id(),
            "items": [{"medicine_id": self.tab, "qty": 1, "rate": 50.0, "type": "Syrup", "unit": "1"}]})
        self.assertTrue(self.ret.get("ok"), self.ret)
        self.rno = self.ret["return_no"]

    def supplier_balance(self):
        return self.conn.execute("SELECT total_due, total_credit FROM suppliers WHERE id=?",
                                 (self.supplier_id(),)).fetchone()

    def refund(self, amount, mode="online", **extra):
        return return_refunds.save_refund(self.conn, {
            "kind": "supplier", "party": SUPPLIER, "mode": mode, "date": "2026-10-18",
            "refunds": [{"return_id": self.ret["return_id"], "amount": amount}], **extra})

    def test_the_agency_pays_us_back(self):
        self.assertEqual(self.open_left("supplier"), {self.rno: 50.0})
        self.assertEqual(self.supplier_balance(), (0.0, 50.0))
        out = self.refund(30)
        pid = out["payment_ids"][0]
        row = self.conn.execute(
            "SELECT supplier_id, amount, mode, reference, payment_no, due_before, due_after "
            "FROM supplier_payments WHERE id=?", (pid,)).fetchone()
        self.assertEqual(row[:4], (self.supplier_id(), -30.0, "Online", self.rno))
        self.assertTrue(str(row[4]).startswith("PAY"), row[4])
        self.assertEqual(row[5:], (0.0, 0.0))  # still in credit with them
        # open_returns now takes supplier refunds off too (it only did customers)
        self.assertEqual(self.open_left("supplier"), {self.rno: 20.0})
        self.assertEqual(self.supplier_balance(), (0.0, 20.0))
        h = _history_row(out, pid)
        self.assertTrue(h["refund"])
        self.assertEqual(h["label"], f"Refund {self.rno}")

    def test_the_rest_of_the_credit_still_pays_the_next_purchase(self):
        self.refund(30)
        p2 = self.buy("INV-2")  # 100 on due; 20 of credit is left with the agency
        due = self.conn.execute("SELECT total_due FROM purchases WHERE id=?", (p2["purchase_id"],)).fetchone()[0]
        self.assertAlmostEqual(due, 80.0, places=2)
        self.assertEqual(self.supplier_balance(), (80.0, 0.0))

    def test_more_than_left_is_refused_and_delete_restores(self):
        with self.assertRaisesRegex(ValueError, "₹50.00 credit left"):
            self.refund(60)
        pid = self.refund(50)["payment_ids"][0]
        self.assertEqual(self.open_left("supplier"), {})
        listed = return_refunds.refund_returns(self.conn, "supplier", party=SUPPLIER, payment_id=pid)
        self.assertEqual([r["left"] for r in listed["returns"]], [50.0])
        settings.delete_payment(self.conn, {"kind": "supplier", "id": pid})
        self.assertEqual(self.open_left("supplier"), {self.rno: 50.0})
        self.assertEqual(self.supplier_balance(), (0.0, 50.0))

    def test_an_edit_keeps_its_number(self):
        pid = self.refund(30)["payment_ids"][0]
        no = self.conn.execute("SELECT payment_no FROM supplier_payments WHERE id=?", (pid,)).fetchone()[0]
        self.refund(40, mode="cash", id=pid)
        row = self.conn.execute("SELECT payment_no, amount, mode FROM supplier_payments WHERE id=?",
                                (pid,)).fetchone()
        self.assertEqual(row, (no, -40.0, "Cash"))
        self.assertEqual(self.open_left("supplier"), {self.rno: 10.0})


class AnOrdinaryPaymentShowsEveryBill(_Books):
    def test_one_payment_given_to_two_bills_shows_both_and_the_rest(self):
        out = self.pay_customer(200, [{"bill_id": self.s1, "amount": 90}, {"bill_id": self.s2, "amount": 60}])
        pid = out["payment_id"]
        row = _history_row(out, pid)
        self.assertEqual([(c["bill_id"], c["how"]) for c in row["cleared"]],
                         [(self.s1, "chosen"), (self.s2, "chosen")])
        self.assertEqual(row["credit_left"], 50.0)
        self.assertFalse(row["refund"])
        d = payment_details.payment_details(self.conn, "customer", pid)
        self.assertTrue(d["supported"])
        p = d["payment"]
        self.assertEqual((p["party"], p["amount"], p["mode"], p["date"]), ("TEST CUSTOMER A", 200.0, "cash", "2026-10-10"))
        self.assertEqual(
            [(c["bill_no"], c["date"], c["bill_total"], c["amount"], c["how"]) for c in p["cleared"]],
            [("SCB1", "2026-10-01", 100.0, 90.0, "chosen"), ("SCB2", "2026-10-05", 80.0, 60.0, "chosen")])
        self.assertEqual(p["credit_left"], 50.0)
        self.assertEqual(len(p["chosen"]), 2)

    def test_a_supplier_payment_view(self):
        out = settings.save_payment(self.conn, {
            "kind": "supplier", "party": "TEST SUPPLIER", "amount": 600, "mode": "Cash", "date": "2026-10-10",
            "allocations": [{"bill_id": self.p2, "amount": 300}]})
        p = payment_details.payment_details(self.conn, "supplier", out["payment_id"])["payment"]
        self.assertTrue(p["payment_no"].startswith("PAY"))
        self.assertEqual([(c["bill_id"], c["amount"], c["how"]) for c in p["cleared"]],
                         [(self.p2, 300.0, "chosen"), (self.p1, 300.0, "oldest")])
        self.assertEqual(p["credit_left"], 0.0)

    def test_a_missing_payment(self):
        with self.assertRaisesRegex(ValueError, "not found"):
            payment_details.payment_details(self.conn, "customer", 999)


class TheLedgerNamesWhatPaidWhat(_Books):
    def ledger(self, kind, party):
        return settings.get_ledger(self.conn, kind, party, "2026-04-01", "2027-03-31")

    def test_customer_rows(self):
        # a return settled in SCB2 (as a bill with Return rows does), then a payment for SCB2
        rid = self.conn.execute(
            "INSERT INTO sales_returns (return_no, customer_id, return_date, refund_amount, allocations) "
            "VALUES ('SR1-3', ?, '2026-10-06', 10, ?)",
            (self.cid, json.dumps([{"bill_id": self.s2, "bill_no": "SCB2", "amount": 10}]))).lastrowid
        self.conn.commit()
        recalculate_customer_due(self.conn, self.cid, sync=False)
        pid = self.pay_customer(80, [{"bill_id": self.s2, "amount": 50}])["payment_id"]
        rows = self.ledger("customer", "TEST CUSTOMER A")["rows"]
        pay = next(r for r in rows if r.get("ref_kind") == "payment")
        self.assertEqual(pay["links"], "Cleared: SCB2 ₹50.00 ✓ · SCB1 ₹30.00")
        self.assertEqual([(c["bill_id"], c["amount"]) for c in pay["cleared"]], [(self.s2, 50.0), (self.s1, 30.0)])
        sale2 = next(r for r in rows if r.get("ref_kind") == "bill" and r["ref_id"] == self.s2)
        self.assertEqual(sale2["links"],
                         f"Paid by: at the counter ₹20.00 · return SR1-3 ₹10.00 ✓ · Payment #{pid} ₹50.00 ✓")
        sale1 = next(r for r in rows if r.get("ref_kind") == "bill" and r["ref_id"] == self.s1)
        self.assertEqual(sale1["links"], f"Paid by: at the counter ₹10.00 · Payment #{pid} ₹30.00")
        ret = next(r for r in rows if r.get("ref_kind") == "return" and r["ref_id"] == rid)
        self.assertEqual(ret["links"], "Cleared: SCB2 ₹10.00 ✓")
        # the figures did not move
        self.assertAlmostEqual(self.ledger("customer", "TEST CUSTOMER A")["summary"]["closing"], 60.0, places=2)

    def test_supplier_rows(self):
        out = settings.save_payment(self.conn, {
            "kind": "supplier", "party": "TEST SUPPLIER", "amount": 350, "mode": "Cash", "date": "2026-10-10",
            "allocations": [{"bill_id": self.p2, "amount": 300}]})
        no = self.conn.execute("SELECT payment_no FROM supplier_payments WHERE id=?",
                               (out["payment_id"],)).fetchone()[0]
        rows = self.ledger("supplier", "TEST SUPPLIER")["rows"]
        pay = next(r for r in rows if r.get("ref_kind") == "payment")
        self.assertEqual(pay["links"], "Cleared: 2 ₹300.00 ✓ · 1 ₹50.00")
        p2 = next(r for r in rows if r.get("ref_kind") == "bill" and r["ref_id"] == self.p2)
        self.assertEqual(p2["links"], f"Paid by: {no} ₹300.00 ✓")
        p1 = next(r for r in rows if r.get("ref_kind") == "bill" and r["ref_id"] == self.p1)
        self.assertEqual(p1["paid_by"][0]["amount"], 50.0)

    def test_a_payment_with_nothing_to_clear_is_credit(self):
        conn = self.conn
        conn.execute("UPDATE sales SET amount_paid = total_amount WHERE customer_id=?", (self.cid,))
        conn.commit()
        recalculate_customer_due(conn, self.cid, sync=False)
        self.pay_customer(25)
        pay = next(r for r in self.ledger("customer", "TEST CUSTOMER A")["rows"] if r.get("ref_kind") == "payment")
        self.assertEqual(pay["links"], "Cleared: ₹25.00 left as credit")


class OpenReturnsCountSupplierRefunds(_Books):
    def test_a_negative_supplier_payment_with_the_return_no(self):
        rid = self.conn.execute(
            "INSERT INTO purchase_returns (return_no, supplier_id, return_date, refund_amount) "
            "VALUES ('PR1-1', ?, '2026-10-06', 1000)", (self.sid,)).lastrowid
        self.conn.commit()
        # 800 of it pays both purchases, 200 is open
        self.assertEqual([r["left"] for r in return_credit.open_returns(self.conn, "supplier", self.sid)], [200.0])
        self.conn.execute(
            "INSERT INTO supplier_payments (supplier_id, payment_date, amount, mode, reference) "
            "VALUES (?, '2026-10-07', -150, 'Cash', 'PR1-1')", (self.sid,))
        self.conn.commit()
        got = return_credit.open_returns(self.conn, "supplier", self.sid)
        self.assertEqual([(r["return_id"], r["left"], r["refunded"]) for r in got], [(rid, 50.0, 150.0)])


if __name__ == "__main__":
    unittest.main()
