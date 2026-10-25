"""Offline-first returns and payments, with real numbers (owner, 25 Oct 2026).

"In offline-first, after a return are stock and amounts right, and after a payment do bills clear?"

Runs only against a REHEARSAL server (never live), on the two-PC harness of
tests/test_offline_first_audit_server.py:
    OF_TEST_BASE=http://127.0.0.1:3999 OF_TEST_KEY=SC-V2TEST01 \
        python -m pytest -q tests/test_offline_first_returns_payments_numbers_server.py

Every step is done the way the screens do it (the return screen's request is built from the
loaded bill like salesReturnLogic.salesReturnBody; payments through save_payment / pay_bill),
then both PCs sync and Audit.check() holds both PCs and the server to the same rows. On top:
  * the exact stock, refund, cash payout, party due / credit and bill Paid / Partly / Due;
  * the customer's (supplier's) due is the sum of its open bills;
  * the cash register and the GST tables read the same on both PCs;
  * the same steps on a PLAIN OFFLINE store (no offline-first) give the very same numbers.
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import test_offline_first_audit_server as audit  # noqa: E402

DAY = audit.DAY


class PlainPC:
    """A plain Offline store: no offline-first bookkeeping, no server."""

    name = "plain"

    def __init__(self):
        from core import db_setup

        fd, self.path = tempfile.mkstemp(suffix=".db", prefix="ofn_plain_")
        os.close(fd)
        c = sqlite3.connect(self.path, check_same_thread=False)
        db_setup.initialise(c)
        c.commit()
        from core.db_setup import ensure_recent_columns

        ensure_recent_columns(c)
        c.execute("INSERT INTO pharmacy_profile (name) SELECT 'AUDIT SHOP' WHERE NOT EXISTS (SELECT 1 FROM pharmacy_profile)")
        c.commit()
        self.till = c

    @contextlib.contextmanager
    def acting(self):
        with mock.patch("core.sync_prefs.get_sync_mode", lambda *a, **k: "offline"), \
                mock.patch("core.offline_first.runtime.is_active", lambda *a, **k: False):
            yield

    def one(self, sql, *a):
        r = self.till.execute(sql, a).fetchone()
        return r[0] if r else None

    def q(self, sql, *a):
        return [tuple(r) for r in self.till.execute(sql, a).fetchall()]

    def close(self):
        self.till.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + s)
            except OSError:
                pass


def _r(v):
    return round(float(v or 0), 2)


@unittest.skipUnless(audit.BASE, "set OF_TEST_BASE to a rehearsal server")
class Numbers(audit.Audit):
    # -- helpers ---------------------------------------------------------------------------
    def tablet(self, pc, name, mrp_strip, stock, tps=10):
        mid = pc.till.execute(
            "INSERT INTO medicines (name, stock_qty, mrp, rate, unit, type, batch_no, expiry_date, gst_percent, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (name, stock, mrp_strip, mrp_strip * 0.7, str(tps), "Tablet", "T1", "2028-12-01", 12,
             "2026-01-01 00:00:00")).lastrowid
        pc.till.commit()
        return mid

    def syrup(self, pc, name, rate, stock):
        return self.medicine(pc, name, rate, stock)

    def return_body(self, pc, sale_id, wanted: dict, settle: str):
        """The Returns screen's request: lines from the loaded bill, discount as the bill's %."""
        from core import desktop_returns_service as returns_svc

        with pc.acting():
            bill = returns_svc.load_sales_bill_for_return(pc.till, sale_id)
        self.assertTrue(bill.get("ok"), bill)
        gross = float(bill["bill_total"]) + float(bill["discount"] or 0)
        pct = round(float(bill["discount"] or 0) / gross * 100, 2) if bill["discount"] else 0
        items = []
        for it in bill["items"]:
            q = wanted.get(it["medicine_id"])
            if not q:
                continue
            items.append({"medicine_id": it["medicine_id"], "name": it["name"], "qty": q, "rate": it["rate"],
                          "orig_qty": it["orig_qty"], "orig_amount": it["amount"], "type": it["type"],
                          "is_tablet": it["is_tablet"]})
        return {"sale_id": sale_id, "customer_id": bill["customer_id"], "customer_name": bill["customer"],
                "bill_no": bill["bill_no"], "items": items, "discount": pct, "reason": "", "settle_mode": settle}

    def sales_return(self, pc, sale_id, wanted, settle):
        from core import desktop_returns_service as returns_svc

        body = self.return_body(pc, sale_id, wanted, settle)
        with pc.acting():
            r = returns_svc.save_sales_return(pc.till, body)
        self.assertTrue(r.get("ok"), r)
        return r

    def purchase_return(self, pc, purchase_id, wanted: dict):
        from core import desktop_returns_service as returns_svc

        with pc.acting():
            p = returns_svc.load_purchase_for_return(pc.till, purchase_id)
        self.assertTrue(p.get("ok"), p)
        items = [{"medicine_id": it["medicine_id"], "name": it["name"], "qty": wanted[it["medicine_id"]],
                  "rate": it["rate"], "type": it["type"], "is_tablet": it["is_tablet"],
                  "tablets_per_stripe": it["tablets_per_stripe"]}
                 for it in p["items"] if wanted.get(it["medicine_id"])]
        with pc.acting():
            r = returns_svc.save_purchase_return(pc.till, {
                "purchase_id": purchase_id, "supplier_id": p["supplier_id"], "settle_mode": "ledger",
                "items": items, "discount": 0})
        self.assertTrue(r.get("ok"), r)
        return r

    def bills(self, pc, kind="customer"):
        """[(total, paid so far, due, status)] oldest first: status Paid / Partly / Due."""
        if kind == "customer":
            rows = pc.q("SELECT s.total_amount, s.amount_paid, s.due_amount, s.bill_cleared FROM sales s "
                        "JOIN customers c ON c.id=s.customer_id WHERE c.name=? AND COALESCE(s.deleted,0)=0 "
                        "ORDER BY s.id", self.cust)
        else:
            rows = pc.q("SELECT COALESCE(p.final_amount,p.total_amount), p.amount_paid, p.due_amount, p.bill_cleared "
                        "FROM purchases p JOIN suppliers s ON s.id=p.supplier_id WHERE s.name=? "
                        "AND COALESCE(p.deleted,0)=0 ORDER BY p.id", self.supp)
        out = []
        for total, _counter_paid, due, cleared in rows:
            # amount_paid is what was paid AT THE COUNTER; a later payment moves the due only.
            total, due = _r(total), _r(due)
            status = "Paid" if (cleared and due <= 0.005) else ("Partly" if due < total - 0.005 else "Due")
            out.append((total, round(total - due, 2), due, status))
        return out

    def due_is_open_bills(self, pc, kind="customer"):
        due, _credit = self.party(pc, kind)
        open_sum = round(sum(b[2] for b in self.bills(pc, kind)), 2)
        self.assertAlmostEqual(due, open_sum, 2, f"{pc.name}: {kind} due {due} vs open bills {open_sum}")

    def cash(self, pc):
        from core import cash_register

        with pc.acting():
            rep = cash_register.day_report(pc.till, DAY)
        return {s["key"]: s["total"] for s in rep["sections"]} | {"expected": rep["totals"]["expected"]}

    def gst(self, pc):
        from core import gst_reports

        with pc.acting():
            rep = gst_reports.build(pc.till, DAY, DAY)
        tot = gst_reports.table_totals(rep)
        return {k: v for k, v in tot.items() if k in ("b2cs", "cdnur", "hsn_b2c")} | {"counts": rep["counts"]}

    def plain(self):
        p = PlainPC()
        self.addCleanup(p.close)
        return p

    # -- 1. sales return: strips and loose tablets, bill discount, GST, credit then cash ------
    def _sales_return_flow(self, pc_sell, pc_ret1, pc_ret2, tab, syr, check):
        s = self.sell(pc_sell, [audit._item(tab, "T", 25, 5.0, type="Tablet", unit="10"),
                                audit._item(syr, "S", 4, 10.0)],
                      payment_mode="Cash", pay_full=False, cash_paid=50, discount_rs=16.5)
        check(pc_sell, "sale")
        out = {"bill": self.bills(pc_sell), "party_after_sale": self.party(pc_sell)}
        # 1 strip + 2 loose tablets and one syrup, kept as the customer's credit (ledger)
        r1 = self.sales_return(pc_ret1, s["sale_id"], {tab: 12, syr: 1}, "ledger")
        out["r1"] = (_r(r1["refund_amount"]), _r(r1.get("refund_payout")))
        out["party_r1"] = self.party(pc_ret1)
        out["stock_r1"] = (self.stock(pc_ret1, tab), self.stock(pc_ret1, syr))
        check(pc_ret1, "sales return 1 (credit)")
        # the rest of the bill, handed back in cash
        r2 = self.sales_return(pc_ret2, s["sale_id"], {tab: 13, syr: 3}, "cash")
        out["r2"] = (_r(r2["refund_amount"]), _r(r2.get("refund_payout")))
        out["party_r2"] = self.party(pc_ret2)
        out["stock_r2"] = (self.stock(pc_ret2, tab), self.stock(pc_ret2, syr))
        check(pc_ret2, "sales return 2 (cash)")
        out["cash"] = self.cash(pc_ret2)
        out["gst"] = self.gst(pc_ret2)
        return out

    def test_sales_return_strips_loose_discount_gst_matches_plain_offline(self):
        tab = self.tablet(self.a, f"NUM TAB {self.run_id}", 50.0, 200)
        self.settle()
        got = self._sales_return_flow(self.a, self.b, self.a, tab, self.m1,
                                      lambda pc, label: self.check(doer=pc, label=label))
        # Rs 125 + 40 = 165, less Rs 16.50 = 148.50, Rs 50 paid in cash.
        self.assertEqual(got["bill"], [(148.5, 50.0, 98.5, "Partly")])
        # 12 tablets x 5 + 1 x 10 = 70, less the bill's 10% = 63, off the due.
        self.assertEqual(got["r1"], (63.0, 0.0))
        self.assertEqual(got["party_r1"], (35.5, 0.0))
        self.assertEqual(got["stock_r1"], (187, 97))
        # the rest: (13 x 5 + 3 x 10) x 0.9 = 85.50: 35.50 clears the due, 50 back in cash.
        self.assertEqual(got["r2"], (85.5, 50.0))
        self.assertEqual(got["party_r2"], (0.0, 0.0))
        self.assertEqual(got["stock_r2"], (200, 100))
        self.assertEqual(got["cash"]["sales"], 50.0)
        self.assertEqual(got["cash"]["sales_returns"], 50.0)
        self.assertEqual(got["cash"]["expected"], 0.0)
        # the other PC: same stock, due, cash register and GST tables
        for pc in (self.a, self.b):
            self.assertEqual((self.stock(pc, tab), self.stock(pc, self.m1)), (200, 100), pc.name)
            self.assertEqual(self.party(pc), (0.0, 0.0), pc.name)
        self.assertEqual(self.cash(self.a), self.cash(self.b))
        self.assertEqual(self.gst(self.a), self.gst(self.b))
        # everything given back: the B2C sales net to nothing
        b2cs = got["gst"]["b2cs"]
        self.assertAlmostEqual(b2cs["taxable"] + b2cs["cgst"] + b2cs["sgst"], 0.0, 2, got["gst"])
        # a plain Offline store doing the same gives the same numbers
        p = self.plain()
        ptab = self.tablet(p, f"NUM TAB {self.run_id}", 50.0, 200)
        psyr = self.syrup(p, f"AUD SYRUP {self.run_id}", 10.0, 100)
        plain = self._sales_return_flow(p, p, p, ptab, psyr, lambda pc, label: None)
        for k in ("bill", "party_after_sale", "r1", "party_r1", "stock_r1", "r2", "party_r2", "stock_r2", "cash"):
            self.assertEqual(got[k], plain[k], k)
        self.assertEqual(got["gst"]["b2cs"], plain["gst"]["b2cs"])
        self.assertEqual(got["gst"]["hsn_b2c"], plain["gst"]["hsn_b2c"])

    def test_sales_return_of_a_cash_bill_in_cash_and_a_due_bill_to_due(self):
        """A paid bill's return in cash pays it all back; a due bill's return only cuts the due."""
        s1 = self.sell(self.a, [audit._item(self.m1, "S", 3, 10.0)])
        s2 = self.sell(self.a, [audit._item(self.m2, "T", 5, 20.0)], payment_mode="Due", pay_full=False)
        self.settle()
        r1 = self.sales_return(self.b, s1["sale_id"], {self.m1: 2}, "cash")
        # the customer owes 100 on the other bill: the 20 is taken off that due, nothing handed back
        self.assertEqual((_r(r1["refund_amount"]), _r(r1.get("refund_payout"))), (20.0, 0.0))
        self.assertEqual(self.party(self.b), (80.0, 0.0))
        self.check(doer=self.b, label="cash-settled return while a due is open")
        r2 = self.sales_return(self.a, s2["sale_id"], {self.m2: 5}, "cash")
        self.assertEqual((_r(r2["refund_amount"]), _r(r2.get("refund_payout"))), (100.0, 20.0))
        self.assertEqual(self.party(self.a), (0.0, 0.0))
        self.check(label="whole due bill returned in cash")
        for pc in (self.a, self.b):
            self.assertEqual((self.stock(pc, self.m1), self.stock(pc, self.m2)), (99, 100), pc.name)
            self.assertEqual(self.party(pc), (0.0, 0.0), pc.name)
            self.due_is_open_bills(pc)
        self.assertEqual(self.cash(self.a), self.cash(self.b))
        self.assertEqual(self.cash(self.a)["sales_returns"], 20.0)

    # -- 2. purchase return: strips, free qty -----------------------------------------------
    def _purchase_return_flow(self, pc_buy, pc_ret, tab, check):
        p = self.buy(pc_buy, f"PN{self.run_id}",
                     [self.pline_any(pc_buy, tab, 10, rate=40.0, free=2)])
        check(pc_buy, "purchase")
        out = {"stock_buy": self.stock(pc_buy, tab), "supp_buy": self.party(pc_buy, "supplier"),
               "bills_buy": self.bills(pc_buy, "supplier")}
        r = self.purchase_return(pc_ret, p["purchase_id"], {tab: 3})
        out["refund"] = _r(r["refund_amount"])
        out["stock_ret"] = self.stock(pc_ret, tab)
        out["supp_ret"] = self.party(pc_ret, "supplier")
        check(pc_ret, "purchase return")
        return out

    def pline_any(self, pc, mid, qty, rate, free=0):
        mrp = pc.one("SELECT mrp FROM medicines WHERE id=?", mid)
        name = pc.one("SELECT name FROM medicines WHERE id=?", mid)
        d = {"medicine_id": mid, "name": name, "type": "Tablet", "batch": "T1", "expiry": "12/28",
             "qty": qty, "rate": rate, "mrp": mrp, "unit": "10", "gst_pct": 0}
        if free:
            d["free_qty"] = free
        return d

    def test_purchase_return_strips_with_free_qty_matches_plain_offline(self):
        tab = self.tablet(self.a, f"NUM PTAB {self.run_id}", 50.0, 200)
        self.settle()
        got = self._purchase_return_flow(self.a, self.b, tab, lambda pc, label: self.check(doer=pc, label=label))
        # 10 strips + 2 free of 10 tablets: 120 tablets in; Rs 400 owed.
        self.assertEqual(got["stock_buy"], 320)
        self.assertEqual(got["supp_buy"], (400.0, 0.0))
        # 3 strips back: 30 tablets out, Rs 120 off the supplier's due.
        self.assertEqual(got["refund"], 120.0)
        self.assertEqual(got["stock_ret"], 290)
        self.assertEqual(got["supp_ret"], (280.0, 0.0))
        for pc in (self.a, self.b):
            self.assertEqual(self.stock(pc, tab), 290, pc.name)
            self.assertEqual(self.party(pc, "supplier"), (280.0, 0.0), pc.name)
        p = self.plain()
        ptab = self.tablet(p, f"NUM PTAB {self.run_id}", 50.0, 200)
        plain = self._purchase_return_flow(p, p, ptab, lambda pc, label: None)
        self.assertEqual(got, plain)

    # -- 3. customer payments ---------------------------------------------------------------
    def _customer_payment_flow(self, pcs, check):
        a, b = pcs
        from core import desktop_settings_service as svc
        from core.history_pay import pay_bill

        steps = {}
        s1 = self.sell(a, [audit._item(self.m1, "S", 10, 10.0)], payment_mode="Due", pay_full=False)
        s2 = self.sell(a, [audit._item(self.m2, "T", 3, 20.0)], payment_mode="Due", pay_full=False)
        s3 = self.sell(a, [audit._item(self.m1, "S", 4, 10.0)], payment_mode="Due", pay_full=False)
        check(a, "three due bills")
        ids = [s1["sale_id"], s2["sale_id"], s3["sale_id"]]

        def snap(pc, label):
            steps[label] = (self.party(pc), self.bills(pc))
            check(pc, label)

        p1 = self.pay(a, "customer", 130)                                   # oldest first
        snap(a, "part payment oldest first")
        self.pay(b, "customer", 30, allocations=[{"bill_id": ids[2], "amount": 30}])
        snap(b, "payment for a chosen bill")
        with a.acting():
            out = pay_bill(a.till, {"kind": "sale", "id": ids[1], "amount": 30})
        self.assertTrue(out.get("ok", True), out)
        snap(a, "pay from Sales History")
        p4 = self.pay(b, "customer", 50)                                    # more than is owed
        snap(b, "overpayment")
        self.pay(a, "customer", 100, id=p1["payment_id"])                   # edit 130 -> 100
        snap(a, "payment edited")
        with b.acting():
            svc.delete_payment(b.till, {"kind": "customer", "id": p4["payment_id"]})
        snap(b, "payment deleted")
        return steps

    def test_customer_payments_clear_bills_on_both_pcs_and_match_plain_offline(self):
        got = self._customer_payment_flow((self.a, self.b), lambda pc, label: self.check(doer=pc, label=label))
        self.assertEqual(got["part payment oldest first"],
                         ((70.0, 0.0), [(100.0, 100.0, 0.0, "Paid"), (60.0, 30.0, 30.0, "Partly"),
                                        (40.0, 0.0, 40.0, "Due")]))
        self.assertEqual(got["payment for a chosen bill"],
                         ((40.0, 0.0), [(100.0, 100.0, 0.0, "Paid"), (60.0, 30.0, 30.0, "Partly"),
                                        (40.0, 30.0, 10.0, "Partly")]))
        self.assertEqual(got["pay from Sales History"],
                         ((10.0, 0.0), [(100.0, 100.0, 0.0, "Paid"), (60.0, 60.0, 0.0, "Paid"),
                                        (40.0, 30.0, 10.0, "Partly")]))
        self.assertEqual(got["overpayment"],
                         ((0.0, 40.0), [(100.0, 100.0, 0.0, "Paid"), (60.0, 60.0, 0.0, "Paid"),
                                        (40.0, 40.0, 0.0, "Paid")]))
        # 100 + 30 + 30 + 50 = 210 against 200 of bills
        self.assertEqual(got["payment edited"][0], (0.0, 10.0))
        self.assertTrue(all(b[3] == "Paid" for b in got["payment edited"][1]), got["payment edited"])
        # 160 against 200: 40 open, the newest bills first to reopen
        self.assertEqual(got["payment deleted"][0], (40.0, 0.0))
        self.assertEqual(round(sum(b[2] for b in got["payment deleted"][1]), 2), 40.0)
        for pc in (self.a, self.b):
            self.due_is_open_bills(pc)
        self.assertEqual(self.bills(self.a), self.bills(self.b))
        self.assertEqual(self.cash(self.a), self.cash(self.b))
        p = self.plain()
        # the plain store's own medicines under the same ids is not needed: fresh ones, same rates
        self.m1, self.m2 = (self.syrup(p, f"AUD SYRUP {self.run_id}", 10.0, 100),
                            self.syrup(p, f"AUD TAB {self.run_id}", 20.0, 100))
        plain = self._customer_payment_flow((p, p), lambda pc, label: None)
        self.assertEqual(got, plain)

    # -- 4. supplier payments ---------------------------------------------------------------
    def _supplier_payment_flow(self, pcs, m1, m2, check):
        a, b = pcs
        from core import desktop_settings_service as svc
        from core.history_pay import pay_bill

        steps = {}
        p1 = self.buy(a, f"S1{self.run_id}", [self.pline_s(a, m1, 20, 5.0)])     # 100
        p2 = self.buy(a, f"S2{self.run_id}", [self.pline_s(a, m2, 6, 10.0)])     # 60
        p3 = self.buy(a, f"S3{self.run_id}", [self.pline_s(a, m1, 8, 5.0)])      # 40
        check(a, "three purchases on credit")
        ids = [p1["purchase_id"], p2["purchase_id"], p3["purchase_id"]]

        def snap(pc, label):
            steps[label] = (self.party(pc, "supplier"), self.bills(pc, "supplier"))
            check(pc, label)

        q1 = self.pay(a, "supplier", 130)
        snap(a, "part payment oldest first")
        self.pay(b, "supplier", 30, allocations=[{"bill_id": ids[2], "amount": 30}])
        snap(b, "payment for a chosen purchase")
        with a.acting():
            out = pay_bill(a.till, {"kind": "purchase", "id": ids[1], "amount": 30})
        self.assertTrue(out.get("ok", True), out)
        snap(a, "pay from Purchase History")
        q4 = self.pay(b, "supplier", 50)
        snap(b, "overpayment")
        self.pay(a, "supplier", 100, id=q1["payment_id"])
        snap(a, "payment edited")
        with b.acting():
            svc.delete_payment(b.till, {"kind": "supplier", "id": q4["payment_id"]})
        snap(b, "payment deleted")
        return steps

    def pline_s(self, pc, mid, qty, rate):
        mrp = pc.one("SELECT mrp FROM medicines WHERE id=?", mid)
        name = pc.one("SELECT name FROM medicines WHERE id=?", mid)
        return {"medicine_id": mid, "name": name, "type": "Syrup", "batch": "B1", "expiry": "12/28",
                "qty": qty, "rate": rate, "mrp": mrp, "unit": "1", "gst_pct": 0}

    def test_supplier_payments_clear_purchases_on_both_pcs_and_match_plain_offline(self):
        got = self._supplier_payment_flow((self.a, self.b), self.m1, self.m2,
                                          lambda pc, label: self.check(doer=pc, label=label))
        self.assertEqual(got["part payment oldest first"],
                         ((70.0, 0.0), [(100.0, 100.0, 0.0, "Paid"), (60.0, 30.0, 30.0, "Partly"),
                                        (40.0, 0.0, 40.0, "Due")]))
        self.assertEqual(got["payment for a chosen purchase"],
                         ((40.0, 0.0), [(100.0, 100.0, 0.0, "Paid"), (60.0, 30.0, 30.0, "Partly"),
                                        (40.0, 30.0, 10.0, "Partly")]))
        self.assertEqual(got["pay from Purchase History"],
                         ((10.0, 0.0), [(100.0, 100.0, 0.0, "Paid"), (60.0, 60.0, 0.0, "Paid"),
                                        (40.0, 30.0, 10.0, "Partly")]))
        self.assertEqual(got["overpayment"][0], (0.0, 40.0))
        self.assertTrue(all(b[3] == "Paid" for b in got["overpayment"][1]), got["overpayment"])
        self.assertEqual(got["payment edited"][0], (0.0, 10.0))
        self.assertEqual(got["payment deleted"][0], (40.0, 0.0))
        for pc in (self.a, self.b):
            self.due_is_open_bills(pc, "supplier")
        self.assertEqual(self.bills(self.a, "supplier"), self.bills(self.b, "supplier"))
        self.assertEqual(self.cash(self.a), self.cash(self.b))
        p = self.plain()
        pm1 = self.syrup(p, f"AUD SYRUP {self.run_id}", 10.0, 100)
        pm2 = self.syrup(p, f"AUD TAB {self.run_id}", 20.0, 100)
        plain = self._supplier_payment_flow((p, p), pm1, pm2, lambda pc, label: None)
        self.assertEqual(got, plain)


# Only this file's tests: the audit's own run from their file.
for _name in dir(audit.Audit):
    if _name.startswith("test_") and _name not in Numbers.__dict__:
        setattr(Numbers, _name, None)


if __name__ == "__main__":
    unittest.main()
