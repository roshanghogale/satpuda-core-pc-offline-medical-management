"""Accounts (owner, 28 Oct 2026): the trial balance ties, the balance sheet balances, the cash is the
cash register's own, and deleted / autosave rows count nowhere.

A temp store is made with the app's own schema (core.db_setup + the offline-first bookkeeping, as
tests/test_the_cash_register_adds_up_and_syncs.py does) and filled with a small month whose right
answer is worked by hand: when the books are complete, the balancing figure is exactly the shop's
opening cash + opening stock. No server, no live config: Online mode is patched off.
"""
from __future__ import annotations

import base64
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import accounts_report as ar  # noqa: E402
from core import cash_register as cr  # noqa: E402
from core import db_setup  # noqa: E402
from core.offline_first import ids, schema  # noqa: E402
from core.offline_first.schema import meta_set  # noqa: E402

D1, D2, D3 = "2026-10-01", "2026-10-02", "2026-10-03"
TODAY = "2026-10-28"
SHOP_GSTIN = "27AAPFU0939F1ZV"          # Maharashtra (tests/test_gst_reports.py)


def make_store() -> str:
    fd, path = tempfile.mkstemp(suffix=".db", prefix="accounts_")
    os.close(fd)
    conn = sqlite3.connect(path)
    db_setup.initialise(conn)
    conn.commit()
    schema.ensure_schema(conn)
    meta_set(conn, "device_no", 3)
    meta_set(conn, "id_base", 3_000_000_000)
    meta_set(conn, "id_max", 3_999_999_999)
    meta_set(conn, "install_id", "pc-accounts")
    conn.commit()
    conn.close()
    return path


def remove(path: str) -> None:
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


class _Store(unittest.TestCase):
    gst = False

    def setUp(self):
        self.path = make_store()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30, factory=ids.OfConnection)
        self.addCleanup(self._cleanup)
        p = mock.patch("core.sync_prefs.is_online_mode", return_value=False)
        p.start()
        self.addCleanup(p.stop)
        c = self.conn
        c.execute("INSERT INTO pharmacy_profile (name, gstin, gst_enabled) VALUES (?,?,?)",
                  ("TEST MEDICAL", SHOP_GSTIN if self.gst else "", 1 if self.gst else 0))
        self.ram = c.execute("INSERT INTO customers (name) VALUES ('RAM')").lastrowid
        self.shyam = c.execute("INSERT INTO customers (name) VALUES ('SHYAM')").lastrowid
        self.sup = c.execute("INSERT INTO suppliers (name, gstin) VALUES ('TULJAI', ?)",
                             (SHOP_GSTIN if self.gst else "",)).lastrowid
        # One medicine counted per unit (a syrup), purchase rate 10 a unit, GST 12% when the shop prints GST.
        self.med = c.execute("INSERT INTO medicines (name, type, unit, stock_qty, rate, mrp, gst_percent, hsn_code) "
                             "VALUES ('CIPCAL SYRUP','Syrup','1',0,10,15,12,'3004')").lastrowid
        c.commit()

    def _cleanup(self):
        try:
            self.conn.close()
        except Exception:
            pass
        remove(self.path)

    def ins(self, table: str, **cols) -> int:
        rid = self.conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                tuple(cols.values())).lastrowid
        self.conn.commit()
        return int(rid)

    def sale(self, no, day, cust, qty, total, cash=0.0, online=0.0, **extra) -> int:
        sid = self.ins("sales", bill_no=no, bill_date=day, customer_id=cust, total_amount=total,
                       amount_paid=cash + online, cash_paid=cash, online_paid=online, **extra)
        self.ins("sales_items", sale_id=sid, medicine_id=self.med, qty=qty, rate=15, amount=total,
                 gst_percent=12 if self.gst else None, cost_price=10)
        return sid

    def month(self) -> None:
        """The hand-worked month. Opening: 500 cash in the drawer, 100 units in stock (1,000)."""
        cr.set_opening(self.conn, D1, 500)
        # Bought 50 units for 500, 200 paid in cash at entry
        self.ins("purchases", purchase_no="P1", supplier_id=self.sup, purchase_date=D1, bill_number="T-1",
                 subtotal=500, total_gst=0, total_amount=500, final_amount=500,
                 cash_paid_at_entry=200, online_paid_at_entry=0, amount_paid_at_entry=200)
        # Sold 30 to RAM (450: 300 cash + 150 online) and 10 to SHYAM on credit (150)
        s1 = self.sale("S1", D2, self.ram, 30, 450, cash=300, online=150)
        self.sale("S2", D2, self.shyam, 10, 150)
        # SHYAM pays 50 online; RAM returns 2 units, refunded 30 in cash
        self.ins("customer_payments", customer_id=self.shyam, payment_date=D3, amount=50, payment_mode="online",
                 cash_amount=0, online_amount=50)
        ret = self.ins("sales_returns", return_no="SR1", sale_id=s1, customer_id=self.ram, return_date=D3,
                       refund_amount=30, settle_mode="cash")
        self.ins("sales_return_items", return_id=ret, medicine_id=self.med, qty=2, rate=15, amount=30)
        self.ins("customer_payments", customer_id=self.ram, payment_date=D3, amount=-30, payment_mode="cash",
                 cash_amount=-30, online_amount=0, reference_no="SR1", note="Refund SR1")
        # 5 units expired, written off; 100 cash paid to the supplier
        self.ins("stock_disposals", disposal_no="SD1", medicine_id=self.med, quantity=5, disposal_type="writeoff",
                 disposal_date=D3)
        self.ins("supplier_payments", payment_no="PAY1", supplier_id=self.sup, payment_date=D3, amount=100, mode="Cash")
        # Typed in Cash & Expenses: tea 20, the owner took 100 home, the owner put 1,000 in
        cr.save_expense(self.conn, {"date": D2, "kind": "out", "category": "Tea & snacks", "description": "Tea",
                                    "amount": 20})
        cr.save_expense(self.conn, {"date": D2, "kind": "out", "category": "Owner took cash", "description": "Home",
                                    "amount": 100})
        cr.save_expense(self.conn, {"date": D1, "kind": "in", "category": "", "description": "Owner added",
                                    "amount": 1000})
        # Today's stock: 100 + 50 - 30 - 10 + 2 - 5 = 107 units
        self.conn.execute("UPDATE medicines SET stock_qty=107 WHERE id=?", (self.med,))
        self.conn.commit()

    def noise(self) -> None:
        """Rows that must count nowhere: deleted or autosave bills, purchases, payments, returns, lines."""
        c = self.conn
        gone = self.sale("S-GONE", D2, self.shyam, 3, 45, cash=45)
        c.execute("UPDATE sales SET deleted=1 WHERE id=?", (gone,))
        self.sale("S-DRAFT", D2, self.shyam, 4, 60, cash=60, is_autosave=1)
        p = self.ins("purchases", purchase_no="P-GONE", supplier_id=self.sup, purchase_date=D2, total_amount=900,
                     final_amount=900, cash_paid_at_entry=900, amount_paid_at_entry=900)
        c.execute("UPDATE purchases SET deleted=1 WHERE id=?", (p,))
        self.ins("purchases", purchase_no="P-DRAFT", supplier_id=self.sup, purchase_date=D2, total_amount=700,
                 final_amount=700, is_autosave=1)
        pay = self.ins("customer_payments", customer_id=self.shyam, payment_date=D3, amount=70, payment_mode="cash",
                       cash_amount=70, online_amount=0)
        c.execute("UPDATE customer_payments SET deleted=1 WHERE id=?", (pay,))
        sp = self.ins("supplier_payments", payment_no="PAY-GONE", supplier_id=self.sup, payment_date=D3, amount=40,
                      mode="Cash")
        c.execute("UPDATE supplier_payments SET deleted=1 WHERE id=?", (sp,))
        r = self.ins("sales_returns", return_no="SR-GONE", customer_id=self.ram, return_date=D3, refund_amount=15,
                     settle_mode="ledger")
        c.execute("UPDATE sales_returns SET deleted=1 WHERE id=?", (r,))
        pr = self.ins("purchase_returns", return_no="PR-GONE", supplier_id=self.sup, return_date=D3, refund_amount=80)
        c.execute("UPDATE purchase_returns SET deleted=1 WHERE id=?", (pr,))
        d = self.ins("stock_disposals", disposal_no="SD-GONE", medicine_id=self.med, quantity=9,
                     disposal_type="writeoff", disposal_date=D3)
        c.execute("UPDATE stock_disposals SET deleted=1 WHERE id=?", (d,))
        e = self.ins("cash_expenses", day=D2, kind="out", category="Transport", description="Auto", amount=60,
                     deleted=1)
        self.assertTrue(e)
        c.commit()

    def report(self, start=D1, end=TODAY) -> dict:
        return ar.build(self.conn, start, end, today=TODAY)

    def assert_ties(self, rep: dict) -> None:
        tb, bs = rep["trial_balance"], rep["balance_sheet"]
        self.assertEqual(tb["total_dr"], tb["total_cr"])
        self.assertEqual(tb["difference"], 0.0)
        self.assertEqual(bs["total_liabilities"], bs["total_assets"])
        self.assertEqual(bs["difference"], 0.0)
        # The trial balance's rows add up to its totals
        self.assertAlmostEqual(sum(a["dr"] or 0 for a in tb["rows"]), tb["total_dr"], places=2)
        self.assertAlmostEqual(sum(a["cr"] or 0 for a in tb["rows"]), tb["total_cr"], places=2)


class HandWorkedMonth(_Store):
    def test_the_figures_are_the_hand_worked_ones_and_everything_ties(self):
        self.month()
        rep = self.report()
        f = rep["figures"]
        self.assertFalse(rep["gst_split"])
        self.assertEqual(f["sales"], 600.0)
        self.assertEqual(f["sales_returns"], 30.0)
        self.assertEqual(f["purchases"], 500.0)
        self.assertEqual(f["cost_of_goods_sold"], 380.0)          # (30 + 10 - 2) x 10
        self.assertEqual(f["written_off"], 50.0)
        self.assertEqual(f["closing_stock"], 1070.0)
        self.assertEqual(f["opening_stock"], 1000.0)               # worked back = the real opening stock
        self.assertEqual(f["expenses"], 20.0)                      # tea only: "Owner took cash" is drawings
        self.assertEqual(f["drawings"], 100.0)
        self.assertEqual(f["capital_introduced"], 1000.0)
        self.assertEqual(f["net_profit"], 120.0)                   # 570 - 380 - 20 - 50
        self.assertEqual(f["debtors"], 100.0)                      # SHYAM 150 - 50
        self.assertEqual(f["creditors"], 200.0)                    # 500 - 200 - 100
        self.assertEqual(f["cash"], 1350.0)
        self.assertEqual(f["bank"], 200.0)                         # 150 on S1 + 50 from SHYAM
        # Complete books from day one: the balancing figure is the opening cash + the opening stock.
        self.assertEqual(f["balancing"], 1500.0)
        self.assertEqual(f["capital_end"], 2520.0)
        self.assert_ties(rep)
        pl = rep["profit_loss"]
        self.assertEqual(pl["gross_profit"], 190.0)
        self.assertEqual(pl["net_profit"], 120.0)
        tb = {a["key"]: a for a in rep["trial_balance"]["rows"]}
        self.assertEqual(tb["capital"]["cr"], 1500.0)
        self.assertEqual(tb["drawings"]["dr"], 100.0)
        self.assertEqual(tb["expense:Tea & snacks"]["dr"], 20.0)
        self.assertNotIn("expense:Owner took cash", tb)
        self.assertEqual(rep["trial_balance"]["memo"][0]["amount"], 1070.0)

    def test_the_dues_are_the_apps_own(self):
        """As on today the debtors / creditors are what recalculate_customer_due / _supplier_due say."""
        self.month()
        from core.customer_service import get_customer_due
        from core.purchase_service import recalculate_supplier_due

        rep = self.report()
        dues = [get_customer_due(self.conn, cid) for cid in (self.ram, self.shyam)]
        self.assertEqual(rep["figures"]["debtors"], round(sum(d for d, _ in dues), 2))
        self.assertEqual(rep["figures"]["customer_advances"], round(sum(c for _, c in dues), 2))
        due, credit = recalculate_supplier_due(self.conn, self.sup, commit=False)
        self.assertEqual(rep["figures"]["creditors"], due)
        self.assertEqual(rep["figures"]["supplier_advances"], credit)

    def test_cash_and_bank_split_is_the_cash_registers(self):
        """The period's cash in / out is the cash register's days added up; the drawer anchor is
        the opening typed on day one."""
        self.month()
        moved = ar.money_moved(self.conn, D1, D3)
        days = [cr.day_report(self.conn, d) for d in (D1, D2, D3)]
        self.assertEqual(moved["cash_in"], round(sum(d["totals"]["cash_in"] for d in days), 2))
        self.assertEqual(moved["cash_out"],
                         round(sum(d["totals"]["cash_out"] + d["totals"]["expenses"] for d in days), 2))
        for key in ("sales", "receipts", "sales_returns", "purchases", "supplier_payments", "purchase_returns"):
            want = round(sum(next(s["total"] for s in d["sections"] if s["key"] == key) for d in days), 2)
            self.assertEqual(moved["cash"][key], want, key)
        # The drawer: day one's opening + what moved since = the register's expected cash on day three
        # when the openings follow the closes.
        cash = ar.cash_in_hand(self.conn, D3)
        self.assertEqual(cash["basis"], "opening")
        self.assertEqual(cash["amount"], round(500 + moved["cash_in"] - moved["cash_out"], 2))
        self.assertEqual(moved["bank_in"], 200.0)
        self.assertEqual(moved["bank_out"], 0.0)

    def test_a_counted_close_is_the_cash_anchor(self):
        self.month()
        cr.close_day(self.conn, D2, 900)            # the shop counted 900 at the close of day two
        cash = ar.cash_in_hand(self.conn, TODAY)
        self.assertEqual(cash["basis"], "counted")
        self.assertEqual(cash["day"], D2)
        after = ar.money_moved(self.conn, D3, TODAY)["cash_net"]       # -30 refund, -100 supplier
        self.assertEqual(after, -130.0)
        self.assertEqual(cash["amount"], 770.0)
        rep = self.report()
        self.assert_ties(rep)
        # The books said 1,350 would be there; the count leaves 770 -- the gap is untracked money
        # (cash taken without a line typed) and lands in the balancing figure, never in the profit.
        self.assertEqual(rep["figures"]["net_profit"], 120.0)
        self.assertEqual(rep["figures"]["balancing"], round(1500.0 + (770.0 - 1350.0), 2))

    def test_deleted_and_autosave_rows_count_nowhere(self):
        self.month()
        before = self.report()
        self.noise()
        after = self.report()
        self.assertEqual(before["figures"], after["figures"])
        self.assertEqual(before["trial_balance"]["rows"], after["trial_balance"]["rows"])
        self.assertEqual(before["money_moved"], after["money_moved"])

    def test_a_to_date_before_today_works_the_stock_back(self):
        """To = day two: the return, the write-off and nothing else happened after it, so the stock
        on day two was 107 - 2 (returned later) + 5 (written off later) = 110 units = 1,100."""
        self.month()
        rep = self.report(D1, D2)
        f = rep["figures"]
        self.assertTrue(rep["stock_worked_back"])
        self.assertEqual(f["closing_stock"], 1100.0)
        self.assertEqual(f["opening_stock"], 1000.0)              # the same opening, whatever the To date
        self.assertEqual(f["cost_of_goods_sold"], 400.0)
        self.assertEqual(f["debtors"], 150.0)                      # SHYAM had not paid yet
        self.assert_ties(rep)

    def test_an_empty_store_ties_at_zero(self):
        rep = self.report()
        self.assert_ties(rep)
        self.assertEqual(rep["trial_balance"]["total_dr"], 0.0)


class WithGst(_Store):
    gst = True

    def test_gst_is_split_out_exactly_as_the_gst_report_says(self):
        from core import gst_reports as gr

        self.month()
        # GST on the supplier bill: 500 incl. 53.57 GST (12%)
        self.conn.execute("UPDATE purchases SET subtotal=446.43, total_gst=53.57, cgst=26.79, sgst=26.78 "
                          "WHERE purchase_no='P1'")
        self.conn.commit()
        with mock.patch.object(gr, "_online", return_value=False):
            rep = self.report()
            g3 = gr.gstr3b(gr.load_period(self.conn, D1, TODAY))
        self.assertTrue(rep["gst_split"])
        pay = g3["Tax payable before ITC"]
        self.assertEqual(rep["figures"]["output_gst"], round(pay["igst"] + pay["cgst"] + pay["sgst"], 2))
        net = g3["4(C) Net ITC available"]
        self.assertEqual(rep["figures"]["input_gst"], round(net["igst"] + net["cgst"] + net["sgst"], 2))
        self.assertEqual(rep["figures"]["input_gst"], 53.57)
        self.assertGreater(rep["figures"]["output_gst"], 0)
        t = rep["trading"]
        # Sales + output GST + the credit notes' GST = the bill totals; returns + their GST = the refunds
        self.assertAlmostEqual(t["sales"] + t["output_gst"] + t["notes_gst"], t["sales_gross"], places=2)
        self.assertAlmostEqual(t["returns"] + t["notes_gst"], t["returns_gross"], places=2)
        self.assertEqual(rep["figures"]["purchases"], round(500 - 53.57, 2))
        self.assert_ties(rep)
        # GST payable (output - input) is on the Balance Sheet
        labels = [x["label"] for x in rep["balance_sheet"]["liabilities"] + rep["balance_sheet"]["assets"]]
        self.assertTrue(any(lbl.startswith("GST") for lbl in labels))


class Routes(_Store):
    def test_routes_answer_the_report_and_the_files(self):
        self.month()
        code, out = ar.handle_get("/api/accounts/trial-balance", {"from": [D1], "to": [TODAY]}, self.conn)
        self.assertEqual(code, 200, out)
        self.assertEqual(out["view"], "trial_balance")
        self.assertEqual(out["trial_balance"]["total_dr"], out["trial_balance"]["total_cr"])
        code, out = ar.handle_get("/api/accounts/balance-sheet", {}, self.conn)     # default: this FY
        self.assertEqual(code, 200, out)
        self.assertEqual(out["from"][5:], "04-01")
        import importlib.util

        # Excel needs openpyxl (the app's build ships it; a bare Python may not have it).
        for fmt in (("xlsx", "csv") if importlib.util.find_spec("openpyxl") else ("csv",)):
            code, out = ar.handle_post("/api/accounts/export", {"from": D1, "to": TODAY, "format": fmt}, self.conn)
            self.assertEqual(code, 200, out)
            self.assertTrue(out["filename"].endswith(f".{fmt}"))
            self.assertTrue(base64.b64decode(out["content_base64"]))
        code, out = ar.handle_post("/api/accounts/export", {"from": D1, "to": TODAY, "format": "csv",
                                                            "part": "balance_sheet"}, self.conn)
        text = base64.b64decode(out["content_base64"]).decode("utf-8-sig")
        self.assertIn("Capital & opening balances (balancing figure)", text)
        self.assertNotIn("Trial Balance", text)
        self.assertIsNone(ar.handle_get("/api/cash/day", {}, self.conn))

    def test_online_mode_says_so_instead_of_a_page_of_zeroes(self):
        with mock.patch("core.sync_prefs.is_online_mode", return_value=True):
            code, out = ar.handle_get("/api/accounts/report", {}, self.conn)
            self.assertEqual(code, 409)
            self.assertTrue(out["online_mode"])
            self.assertIn("Online mode", out["error"])
            code, out = ar.handle_post("/api/accounts/export", {"format": "xlsx"}, self.conn)
            self.assertEqual(code, 409)

    def test_the_default_range_is_this_financial_year(self):
        from datetime import date

        self.assertEqual(ar.fy_default(date(2026, 10, 28)), ("2026-04-01", "2026-10-28", "2026-27"))
        self.assertEqual(ar.fy_default(date(2027, 2, 3)), ("2026-04-01", "2027-02-03", "2026-27"))
        self.assertEqual(ar.clean_range("", "2026-03-15"), ("2025-04-01", "2026-03-15"))
        self.assertEqual(ar.clean_range("2026-10-05", "2026-10-01"), ("2026-10-01", "2026-10-05"))
        self.assertEqual(ar._inr(1234567.5), "12,34,567.50")
        self.assertEqual(ar._inr(-999.0), "-999.00")


if __name__ == "__main__":
    unittest.main()
