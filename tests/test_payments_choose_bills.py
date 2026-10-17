"""A payment can name the bills it clears (owner, 17 Oct 2026).

"When collecting from a customer or paying a supplier, choose which bills the payment clears,
keep track of it, and a payment report showing which bills each payment cleared."

core/bill_clearing.py is the PC copy of the server's rule (server-live
src/services/billClearing.js); tests/fixtures/bill_clearing_vectors.json is shared with the
server and the phone. The dues stored on this PC, the payment documents sent to the server and
the ones pulled back all carry the choice; with no choice the figures are the old oldest-first
ones to the paisa.
"""
import csv
import io
import base64
import json
import os
import random
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import bill_clearing, db_setup, history_pay  # noqa: E402
from core import desktop_settings_service as settings  # noqa: E402
from core.customer_service import recalculate_customer_due  # noqa: E402
from core.due_fifo import cascade_purchases_fifo, cascade_sales_fifo  # noqa: E402
from core.purchase_service import compute_paid_via_payments_by_bill, recalculate_supplier_due  # noqa: E402

VECTORS = Path(__file__).parent / "fixtures" / "bill_clearing_vectors.json"


def _store() -> sqlite3.Connection:
    conn = sqlite3.connect(tempfile.mktemp(suffix=".db"))
    db_setup.initialise(conn)
    return conn


class TheSharedVectors(unittest.TestCase):
    def test_every_vector(self):
        cases = json.loads(VECTORS.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(cases), 10)
        for c in cases:
            with self.subTest(c["name"]):
                got = bill_clearing.clear_bills(c["bills"], c["credits"], overpay_joins=c["overpay_joins"])
                self.assertEqual({b["id"]: b for b in got["bills"]},
                                 {b["id"]: b for b in c["expect"]["bills"]})
                self.assertEqual(got["applied"], c["expect"]["applied"])

    def test_allocations_as_stored(self):
        self.assertEqual(
            bill_clearing.clean_allocations('[{"bill_id":"5","amount":"10.005"},{"bill_id":0,"amount":3}]'),
            [{"bill_id": 5, "bill_no": None, "amount": 10.01}])
        self.assertEqual(bill_clearing.clean_allocations("not json"), [])
        self.assertEqual(bill_clearing.clean_allocations(None), [])

    def test_rounding_is_javascripts(self):
        # Math.round: a half goes up, also below zero; Python's round() would give 0.12 / -0.12
        self.assertEqual(bill_clearing.r2(0.125), 0.13)
        self.assertEqual(bill_clearing.r2(-0.125), -0.12)
        self.assertEqual(bill_clearing.r2(1.005), 1.0)  # 100.49999999999999 in binary


class Base(unittest.TestCase):
    def setUp(self):
        self._p = mock.patch("core.sync_prefs.is_online_mode", lambda *a, **k: False)
        self._p.start()
        self.addCleanup(self._p.stop)
        self.conn = _store()
        c = self.conn
        self.cid = c.execute("INSERT INTO customers (name) VALUES ('TEST CUSTOMER A')").lastrowid
        self.other = c.execute("INSERT INTO customers (name) VALUES ('TEST CUSTOMER B')").lastrowid
        self.sid = c.execute("INSERT INTO suppliers (name) VALUES ('TEST SUPPLIER')").lastrowid
        # due 90 (oldest) and 60
        self.s1 = self.sale("SCB1/FY2026-27", "2026-10-01", self.cid, 100, 10)
        self.s2 = self.sale("SCB2/FY2026-27", "2026-10-05", self.cid, 80, 20)
        self.s_other = self.sale("SCB3/FY2026-27", "2026-10-06", self.other, 50, 0)
        self.p1 = self.purchase("1/FY2026-27", "2026-10-02", 500)
        self.p2 = self.purchase("2/FY2026-27", "2026-10-04", 300)
        c.commit()
        recalculate_customer_due(c, self.cid, sync=False)
        recalculate_supplier_due(c, self.sid)

    def sale(self, no, d, cid, total, paid):
        return self.conn.execute(
            "INSERT INTO sales (bill_no, bill_date, customer_id, total_amount, amount_paid, due_amount, total_due) "
            "VALUES (?,?,?,?,?,?,?)", (no, d, cid, total, paid, total - paid, total - paid)).lastrowid

    def purchase(self, no, d, total):
        return self.conn.execute(
            "INSERT INTO purchases (purchase_no, purchase_date, supplier_id, total_amount, final_amount, "
            "amount_paid, due_amount, due) VALUES (?,?,?,?,?,0,?,?)", (no, d, self.sid, total, total, total, total)
        ).lastrowid

    def due(self, table, bid):
        return self.conn.execute(f"SELECT total_due, bill_cleared FROM {table} WHERE id=?", (bid,)).fetchone()

    def pay_customer(self, amount, allocations=None, **extra):
        body = {"kind": "customer", "party": "TEST CUSTOMER A", "cash": amount, "online": 0,
                "date": "2026-10-10", **extra}
        if allocations is not None:
            body["allocations"] = allocations
        return settings.save_payment(self.conn, body)


class DuesFollowTheChosenBills(Base):
    def test_a_receipt_given_the_newer_bill_clears_it_not_the_oldest(self):
        self.conn.execute(
            "INSERT INTO customer_payments (customer_id, payment_date, amount, payment_mode, allocations) "
            "VALUES (?, '2026-10-10', 60, 'cash', ?)",
            (self.cid, json.dumps([{"bill_id": self.s2, "bill_no": "SCB2", "amount": 60}])))
        recalculate_customer_due(self.conn, self.cid, sync=False)
        self.assertEqual(self.due("sales", self.s1), (90.0, 0))
        self.assertEqual(self.due("sales", self.s2), (0.0, 1))
        total = self.conn.execute("SELECT total_due FROM customers WHERE id=?", (self.cid,)).fetchone()[0]
        self.assertEqual(total, 90.0)

    def test_a_supplier_payment_given_the_newer_purchase(self):
        self.conn.execute(
            "INSERT INTO supplier_payments (supplier_id, payment_date, amount, mode, allocations) "
            "VALUES (?, '2026-10-10', 400, 'Cash', ?)",
            (self.sid, json.dumps([{"bill_id": self.p2, "amount": 300}])))
        recalculate_supplier_due(self.conn, self.sid)
        # 300 on the newer purchase, the other 100 oldest first
        self.assertEqual(self.due("purchases", self.p2), (0.0, 1))
        self.assertEqual(self.due("purchases", self.p1), (400.0, 0))
        via = compute_paid_via_payments_by_bill(self.conn, [self.sid])
        self.assertEqual((via[self.p1], via[self.p2]), (100.0, 300.0))

    def test_with_no_choice_the_figures_are_the_old_ones(self):
        rnd = random.Random(1710)
        for trial in range(40):
            conn = _store()
            cid = conn.execute("INSERT INTO customers (name) VALUES ('T')").lastrowid
            sid = conn.execute("INSERT INTO suppliers (name) VALUES ('S')").lastrowid
            bills, purch = [], []
            for i in range(rnd.randint(1, 8)):
                d = f"2026-0{rnd.randint(1, 9)}-1{rnd.randint(0, 9)}"
                total = round(rnd.uniform(10, 900), 2)
                paid = round(rnd.choice([0, 0, total / 2, total, total + rnd.uniform(1, 50)]), 2)
                bid = conn.execute(
                    "INSERT INTO sales (bill_no, bill_date, customer_id, total_amount, amount_paid) "
                    "VALUES (?,?,?,?,?)", (f"B{trial}-{i}", d, cid, total, paid)).lastrowid
                bills.append((d, bid, total, paid))
                pid = conn.execute(
                    "INSERT INTO purchases (purchase_no, purchase_date, supplier_id, total_amount, final_amount, "
                    "amount_paid_at_entry) VALUES (?,?,?,?,?,?)",
                    (f"P{trial}-{i}", d, sid, total, total, min(paid, total))).lastrowid
                purch.append((d, pid, total, min(paid, total)))
            pays = rets = 0.0
            for _ in range(rnd.randint(0, 4)):
                amt = round(rnd.uniform(-50, 600), 2)
                conn.execute("INSERT INTO customer_payments (customer_id, payment_date, amount) VALUES (?, '2026-09-30', ?)",
                             (cid, amt))
                conn.execute("INSERT INTO supplier_payments (supplier_id, payment_date, amount) VALUES (?, '2026-09-30', ?)",
                             (sid, amt))
                pays += amt
            for _ in range(rnd.randint(0, 2)):
                amt = round(rnd.uniform(1, 200), 2)
                conn.execute("INSERT INTO sales_returns (customer_id, return_date, refund_amount) VALUES (?, '2026-09-20', ?)",
                             (cid, amt))
                conn.execute("INSERT INTO purchase_returns (supplier_id, return_date, refund_amount) VALUES (?, '2026-09-20', ?)",
                             (sid, amt))
                rets += amt
            conn.commit()
            recalculate_customer_due(conn, cid, sync=False)
            recalculate_supplier_due(conn, sid)
            old_s = cascade_sales_fifo([(b, t, p) for _d, b, t, p in sorted(bills)], pays, rets)
            old_p = cascade_purchases_fifo([(b, t, p) for _d, b, t, p in sorted(purch)], pays, rets)
            for bid, rem, cleared in old_s:
                got = conn.execute("SELECT total_due, account_cleared FROM sales WHERE id=?", (bid,)).fetchone()
                self.assertAlmostEqual(got[0], rem, places=2, msg=f"trial {trial} sale {bid}")
                self.assertEqual(got[1], cleared)
            for bid, rem, cleared in old_p:
                got = conn.execute("SELECT total_due, account_cleared FROM purchases WHERE id=?", (bid,)).fetchone()
                self.assertAlmostEqual(got[0], rem, places=2, msg=f"trial {trial} purchase {bid}")
                self.assertEqual(got[1], cleared)
            conn.close()


class SavingAChoice(Base):
    def test_the_choice_is_stored_and_clears_that_bill(self):
        out = self.pay_customer(100, [{"bill_id": self.s2, "bill_no": "SCB2/FY2026-27", "amount": 60}])
        stored = self.conn.execute("SELECT allocations FROM customer_payments WHERE id=?",
                                   (out["payment_id"],)).fetchone()[0]
        self.assertEqual(json.loads(stored), [{"bill_id": self.s2, "bill_no": "SCB2/FY2026-27", "amount": 60.0}])
        self.assertEqual(self.due("sales", self.s2), (0.0, 1))
        self.assertEqual(self.due("sales", self.s1), (50.0, 0))   # the other 40, oldest first
        row = next(h for h in out["history"] if h["id"] == out["payment_id"])
        self.assertEqual([(c["bill_id"], c["amount"], c["how"]) for c in row["cleared"]],
                         [(self.s2, 60.0, "chosen"), (self.s1, 40.0, "oldest")])
        self.assertEqual(row["allocations"][0]["bill_id"], self.s2)
        self.assertEqual(row["credit_left"], 0.0)

    def test_bad_choices_are_refused_and_nothing_is_saved(self):
        with self.assertRaisesRegex(ValueError, "not an open bill"):
            self.pay_customer(50, [{"bill_id": self.s_other, "amount": 50}])
        with self.assertRaisesRegex(ValueError, "owes ₹60.00"):
            self.pay_customer(100, [{"bill_id": self.s2, "amount": 70}])
        with self.assertRaisesRegex(ValueError, "The bills you ticked need ₹150.00; the amount is ₹100.00"):
            self.pay_customer(100, [{"bill_id": self.s1, "amount": 90}, {"bill_id": self.s2, "amount": 60}])
        with self.assertRaisesRegex(ValueError, "chosen twice"):
            self.pay_customer(100, [{"bill_id": self.s1, "amount": 10}, {"bill_id": self.s1, "amount": 10}])
        with self.assertRaises(ValueError):
            settings.save_payment(self.conn, {
                "kind": "supplier", "party": "TEST SUPPLIER", "amount": 100, "mode": "Cash",
                "allocations": [{"bill_id": 999, "amount": 50}]})  # no such purchase
        n = self.conn.execute("SELECT COUNT(*) FROM customer_payments").fetchone()[0]
        self.assertEqual(n, 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM supplier_payments").fetchone()[0], 0)

    def test_an_edit_keeps_or_replaces_the_choice(self):
        out = self.pay_customer(60, [{"bill_id": self.s2, "amount": 60}])
        pid = out["payment_id"]
        # the edited payment's own bill still owes its 60 without it, so this is allowed
        bills = settings.payment_open_bills(self.conn, "customer", "TEST CUSTOMER A", payment_id=pid)
        self.assertEqual({b["bill_id"]: b["owes"] for b in bills["bills"]}, {self.s1: 90.0, self.s2: 60.0})
        self.assertEqual(bills["allocations"][0]["bill_id"], self.s2)
        self.pay_customer(60, id=pid)  # no key (voice, an older screen): kept
        self.assertEqual(self.due("sales", self.s2), (0.0, 1))
        self.pay_customer(60, [], id=pid)  # "Oldest bills first"
        stored = self.conn.execute("SELECT allocations FROM customer_payments WHERE id=?", (pid,)).fetchone()[0]
        self.assertIsNone(stored)
        self.assertEqual(self.due("sales", self.s1), (30.0, 0))
        self.assertEqual(self.due("sales", self.s2), (60.0, 0))

    def test_a_supplier_payment_with_a_choice(self):
        out = settings.save_payment(self.conn, {
            "kind": "supplier", "party": "TEST SUPPLIER", "amount": 300, "mode": "Cash", "date": "2026-10-10",
            "allocations": [{"bill_id": self.p2, "bill_no": "2/FY2026-27", "amount": 300}]})
        self.assertEqual(self.due("purchases", self.p2), (0.0, 1))
        self.assertEqual(self.due("purchases", self.p1), (500.0, 0))
        row = next(h for h in out["history"] if h["id"] == out["payment_id"])
        self.assertEqual(row["cleared"], [{"bill_id": self.p2, "bill_no": "2/FY2026-27", "date": "2026-10-04",
                                           "amount": 300.0, "how": "chosen"}])

    def test_credit_left_and_the_report(self):
        self.pay_customer(200, [{"bill_id": self.s2, "amount": 60}])  # 60 chosen, 90 oldest, 50 credit
        with mock.patch("core.desktop_export_service._is_online", lambda: False):
            from core.desktop_export_service import export_payment_report

            res = export_payment_report(self.conn, {"kind": "customer", "from": "2026-10-01",
                                                    "to": "2026-10-31", "format": "csv"})
        self.assertTrue(res["ok"], res)
        text = base64.b64decode(res["content_base64"]).decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[1][:5], ["Date", "Payment", "Party", "Mode", "Amount"])
        body = [r for r in rows[2:]]
        self.assertEqual(body[0][:5], ["2026-10-10", f"#{1}", "TEST CUSTOMER A", "cash", "200.0"])
        self.assertEqual(body[1][5:], ["SCB2", "2026-10-05", "60.0", "Chosen"])
        self.assertEqual(body[2][5:], ["SCB1", "2026-10-01", "90.0", "Oldest first"])
        self.assertEqual(body[3][5:8], ["Left as credit", "", "50.0"])
        self.assertEqual(body[4][:5], ["TOTAL", "1 payments", "", "", "200.0"])
        with mock.patch("core.desktop_export_service._is_online", lambda: False):
            from core.desktop_export_service import export_payment_report

            empty = export_payment_report(self.conn, {"kind": "supplier", "format": "csv"})
        self.assertFalse(empty["ok"])


class TheChoiceTravelsWithThePayment(Base):
    def test_build_and_pull_round_trip(self):
        from core import server_entity_sync, server_sync
        from core.offline_first import outbox

        out = self.pay_customer(60, [{"bill_id": self.s2, "bill_no": "SCB2/FY2026-27", "amount": 60}])
        pid = out["payment_id"]
        doc = server_sync._build_docs(self.conn, "customer_payments", ids=[pid])[0]
        self.assertEqual(doc["allocations"], [{"bill_id": self.s2, "bill_no": "SCB2/FY2026-27", "amount": 60.0}])
        self.assertEqual(outbox.build_doc(self.conn, "customer_payments", pid)["allocations"], doc["allocations"])
        plain = self.pay_customer(10)["payment_id"]
        self.assertEqual(server_sync._build_docs(self.conn, "customer_payments", ids=[plain])[0]["allocations"], [])
        sp = settings.save_payment(self.conn, {
            "kind": "supplier", "party": "TEST SUPPLIER", "amount": 100, "mode": "Cash",
            "allocations": [{"bill_id": self.p2, "amount": 100}]})["payment_id"]
        sdoc = server_sync._build_docs(self.conn, "supplier_payments", ids=[sp])[0]
        self.assertEqual(sdoc["allocations"], [{"bill_id": self.p2, "bill_no": "2/FY2026-27", "amount": 100.0}])

        other = _store()
        with server_entity_sync.offline_first_apply():
            self.assertEqual(server_entity_sync.sync_down_doc(other, "customer_payments", str(pid), dict(doc)),
                             "applied")
            server_entity_sync.sync_down_doc(other, "supplier_payments", str(sp), dict(sdoc))
        got = other.execute("SELECT allocations FROM customer_payments WHERE id=?", (pid,)).fetchone()[0]
        self.assertEqual(json.loads(got), doc["allocations"])
        got = other.execute("SELECT allocations FROM supplier_payments WHERE id=?", (sp,)).fetchone()[0]
        self.assertEqual(json.loads(got), sdoc["allocations"])

        # an older server's document (no key) keeps what this PC holds; null clears it
        newer = dict(doc, version=int(doc.get("version") or 1) + 1, amount=61)
        newer.pop("allocations")
        with server_entity_sync.offline_first_apply():
            server_entity_sync.sync_down_doc(other, "customer_payments", str(pid), newer)
        row = other.execute("SELECT amount, allocations FROM customer_payments WHERE id=?", (pid,)).fetchone()
        self.assertEqual(row[0], 61.0)
        self.assertEqual(json.loads(row[1]), doc["allocations"])
        cleared = dict(doc, version=int(doc.get("version") or 1) + 2, allocations=None)
        with server_entity_sync.offline_first_apply():
            server_entity_sync.sync_down_doc(other, "customer_payments", str(pid), cleared)
        self.assertIsNone(other.execute("SELECT allocations FROM customer_payments WHERE id=?", (pid,)).fetchone()[0])


class PayFromHistoryPaysThisBill(Base):
    def test_the_payment_is_given_this_bill(self):
        info = history_pay.bill_due_info(self.conn, "sale", self.s2)
        self.assertEqual(info["to_clear"], 60.0)
        res = history_pay.pay_bill(self.conn, {"kind": "sale", "id": self.s2, "amount": 60})
        self.assertEqual(res["after"]["bill_due"], 0.0)
        self.assertEqual(self.due("sales", self.s1), (90.0, 0))  # the older bill is left as it was
        stored = self.conn.execute("SELECT allocations FROM customer_payments").fetchone()[0]
        self.assertEqual(json.loads(stored), [{"bill_id": self.s2, "bill_no": "SCB2/FY2026-27", "amount": 60.0}])

    def test_more_than_the_bill_goes_to_the_oldest(self):
        history_pay.pay_bill(self.conn, {"kind": "sale", "id": self.s2, "amount": 100})
        self.assertEqual(self.due("sales", self.s2), (0.0, 1))
        self.assertEqual(self.due("sales", self.s1), (50.0, 0))
        stored = json.loads(self.conn.execute("SELECT allocations FROM customer_payments").fetchone()[0])
        self.assertEqual(stored[0]["amount"], 60.0)

    def test_a_purchase_paid_from_its_row(self):
        history_pay.pay_bill(self.conn, {"kind": "purchase", "id": self.p2, "amount": 300})
        self.assertEqual(self.due("purchases", self.p2), (0.0, 1))
        self.assertEqual(self.due("purchases", self.p1), (500.0, 0))


if __name__ == "__main__":
    unittest.main()
