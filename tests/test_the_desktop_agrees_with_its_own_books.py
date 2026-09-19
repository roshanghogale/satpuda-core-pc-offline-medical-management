"""Three places where the DESKTOP was the one that was wrong.

The rule for this round of work is that the desktop's arithmetic is the reference and
Android is corrected to match it. These three are the exceptions: each was checked against
the live Roshan store on the server and the desktop lost.

1. The Ledger statement debited a bill TWICE for its discount and never credited the money
   handed over at the counter. `sales.total_amount` is already net of `discount`
   (`calc_engine.calc_bill_summary:88-89`) and the desktop's own single source of truth,
   `customer_service.recalculate_customer_due:271`, subtracts `amount_paid`. On live Roshan
   customer 14311 (A K PAWAR) -- billed 1503.00, paid 1503.00, no payments, no returns --
   the server, `customers.total_due` and the phone all say 0.00 while this statement closed
   at 1503.00. Store-wide it overstated the debt by
   SUM(amount_paid) - SUM(discount) = 26,379.48 - 48.80 = Rs 26,330.68.
   The supplier statement had the same defect against `amount_paid_at_entry`.

2. Home -> "Collected" in ONLINE mode dropped every standalone receipt.
   `/summaries/home` returns `collected = SUM(sales.amount_paid)` and carries no payments
   field at all, so `m_pay = max(0, remote.month_collected - m_bill)` was subtracting a
   number from itself and was structurally 0. Online read 40,237.03 for FY 2026-27 where
   this same file's Offline branch reads 64,205.71 on the same books.

3. Alert & Monitoring -> "Customer Dues" chased the entry-time `sales.due_amount` snapshot
   instead of the cascaded `total_due` / `account_cleared`, so it listed bills the shop had
   already been paid for: 21 bills totalling Rs 8,859.56 against the store's real
   Rs 4,712.26.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db_setup, sync_prefs  # noqa: E402
from core import alert_monitoring_service as alerts  # noqa: E402
from core import desktop_settings_service as dss  # noqa: E402
from core import home_dashboard  # noqa: E402


def _store():
    conn = sqlite3.connect(tempfile.mktemp(suffix=".db"))
    conn.row_factory = sqlite3.Row
    db_setup.initialise(conn)
    return conn


def _ins(conn, table, **vals):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    use = {k: v for k, v in vals.items() if k in cols}
    return conn.execute(
        f"INSERT INTO {table} ({','.join(use)}) "
        f"VALUES ({','.join('?' * len(use))})",
        list(use.values()),
    ).lastrowid


class TheStatementMustCloseWhereTheAccountCloses(unittest.TestCase):
    """Local (SQLite) ledger."""

    def setUp(self):
        self._online = sync_prefs.is_online_mode
        sync_prefs.is_online_mode = lambda *a, **k: False

    def tearDown(self):
        sync_prefs.is_online_mode = self._online

    def _ledger(self, conn, kind, party):
        return dss.get_ledger(
            conn, kind=kind, party=party,
            date_from="2026-04-01", date_to="2027-03-31",
        )

    def test_a_bill_paid_at_the_counter_closes_at_zero(self):
        """The A K PAWAR shape, straight off the live store."""
        conn = _store()
        cid = _ins(conn, "customers", name="A K PAWAR", phone="9")
        _ins(conn, "sales", customer_id=cid, bill_no="SCB116",
             bill_date="2026-09-09", total_amount=1503.0,
             amount_paid=1503.0, due_amount=0, total_due=0,
             account_cleared=1, bill_cleared=1)
        conn.commit()
        res = self._ledger(conn, "customer", "A K PAWAR")
        self.assertAlmostEqual(
            res["summary"]["closing"], 0.0, places=2,
            msg="the statement still shows a fully-paid bill as outstanding",
        )
        sale_row = [r for r in res["rows"] if r.get("tag") == "sale"][0]
        self.assertAlmostEqual(sale_row["debit"], 1503.00, places=2)
        self.assertAlmostEqual(sale_row["credit"], 1503.00, places=2)

    def test_the_discount_is_not_deducted_twice(self):
        """SCB2 on live Roshan: subtotal 56.00, discount 5.60, total_amount 50.00."""
        conn = _store()
        cid = _ins(conn, "customers", name="DISC", phone="9")
        _ins(conn, "sales", customer_id=cid, bill_no="SCB2",
             bill_date="2026-08-01", total_amount=50.0, discount=5.60,
             amount_paid=0, due_amount=50.0, total_due=50.0)
        conn.commit()
        res = self._ledger(conn, "customer", "DISC")
        self.assertAlmostEqual(
            res["summary"]["closing"], 50.0, places=2,
            msg="total_amount is already net of the discount; 44.40 is a double deduction",
        )

    def test_the_opening_balance_is_built_the_same_way(self):
        conn = _store()
        cid = _ins(conn, "customers", name="OPEN", phone="9")
        _ins(conn, "sales", customer_id=cid, bill_no="OLD",
             bill_date="2026-01-10", total_amount=1000.0,
             amount_paid=400.0, due_amount=600.0, total_due=600.0)
        conn.commit()
        res = self._ledger(conn, "customer", "OPEN")
        self.assertAlmostEqual(res["summary"]["opening"], 600.0, places=2)

    def test_the_supplier_entry_payment_is_a_credit(self):
        conn = _store()
        sup = _ins(conn, "suppliers", name="S ENTRY")
        _ins(conn, "purchases", supplier_id=sup, bill_number="P1",
             purchase_no="P1", purchase_date="2026-08-01",
             total_amount=2210.0, final_amount=2210.0,
             amount_paid=2210.0, amount_paid_at_entry=2210.0)
        conn.commit()
        res = self._ledger(conn, "supplier", "S ENTRY")
        self.assertAlmostEqual(
            res["summary"]["closing"], 0.0, places=2,
            msg="a purchase paid in full at entry still showed as owed",
        )

    def test_the_supplier_opening_credits_entry_payments_too(self):
        conn = _store()
        sup = _ins(conn, "suppliers", name="S OPEN")
        _ins(conn, "purchases", supplier_id=sup, bill_number="P0",
             purchase_no="P0", purchase_date="2026-01-05",
             total_amount=900.0, final_amount=900.0,
             amount_paid=400.0, amount_paid_at_entry=400.0)
        conn.commit()
        res = self._ledger(conn, "supplier", "S OPEN")
        self.assertAlmostEqual(res["summary"]["opening"], 500.0, places=2)

    def test_the_statement_agrees_with_recalculate_customer_due(self):
        """The whole point: two ways of asking the same question, one answer."""
        from core import customer_service

        conn = _store()
        cid = _ins(conn, "customers", name="AGREE", phone="9")
        _ins(conn, "sales", customer_id=cid, bill_no="B1", bill_date="2026-05-01",
             total_amount=1000.0, amount_paid=250.0, due_amount=750.0, total_due=750.0)
        _ins(conn, "sales", customer_id=cid, bill_no="B2", bill_date="2026-06-01",
             total_amount=500.0, amount_paid=500.0, due_amount=0, total_due=0,
             account_cleared=1, bill_cleared=1)
        _ins(conn, "customer_payments", customer_id=cid,
             payment_date="2026-07-01", amount=200.0, payment_mode="Cash")
        _ins(conn, "sales_returns", customer_id=cid, return_no="R1",
             return_date="2026-07-05", refund_amount=50.0)
        conn.commit()
        due, credit = customer_service.recalculate_customer_due(conn, cid)
        res = self._ledger(conn, "customer", "AGREE")
        self.assertAlmostEqual(res["summary"]["closing"], due - credit, places=2)
        self.assertAlmostEqual(res["summary"]["closing"], 500.0, places=2)


class TheOnlineStatementIsTheSameStatement(unittest.TestCase):
    """The server-backed branch has to answer identically."""

    def setUp(self):
        import core.store_query_client as sq

        self._saved = {n: getattr(sq, n) for n in dir(sq) if n.startswith("list_")}
        rows = {
            "list_sales": [{
                "id": 1, "customer_id": 7, "customer_name": "A K PAWAR",
                "bill_no": "SCB116", "bill_date": "2026-09-09",
                "total_amount": 1503.0, "discount": 0, "amount_paid": 1503.0,
            }],
            "list_customer_payments": [],
            "list_sales_returns": [],
            "list_purchases": [{
                "id": 1, "supplier_id": 3, "bill_number": "P1",
                "purchase_date": "2026-08-01", "final_amount": 2210.0,
                "amount_paid_at_entry": 2210.0, "amount_paid": 2210.0,
            }],
            "list_supplier_payments": [],
            "list_purchase_returns": [],
            "list_customers": [{"id": 7, "name": "A K PAWAR"}],
            "list_suppliers": [{"id": 3, "name": "S1"}],
        }
        for name, data in rows.items():
            setattr(sq, name, (lambda d: (lambda **k: {"rows": d}))(data))
        self._overlays = {}
        for n in ("overlay_customer_payment_dicts", "overlay_supplier_payment_dicts",
                  "overlay_sale_dicts", "overlay_purchase_dicts"):
            if hasattr(dss, n):
                self._overlays[n] = getattr(dss, n)
                setattr(dss, n, lambda *a, **k: [])
        # The online statement reads queued bills and payments from the queue module
        # itself, and that read this machine's real queue file: one leftover queued bill
        # for customer 7 closed the statement at 10.00. An empty queue in a temp folder.
        from core import online_mutation_queue as omq

        self._queue_dir = tempfile.mkdtemp(prefix="statement_queue_")
        self._queue_path = omq._queue_path
        omq._queue_path = lambda: os.path.join(self._queue_dir, "store.json")

    def tearDown(self):
        import shutil

        import core.store_query_client as sq
        from core import online_mutation_queue as omq

        for n, fn in self._saved.items():
            setattr(sq, n, fn)
        for n, fn in self._overlays.items():
            setattr(dss, n, fn)
        omq._queue_path = self._queue_path
        shutil.rmtree(self._queue_dir, ignore_errors=True)

    def test_online_customer_closes_at_zero(self):
        res = dss._get_ledger_online("customer", "A K PAWAR", "2026-04-01", "2027-03-31")
        self.assertAlmostEqual(res["summary"]["closing"], 0.0, places=2)

    def test_online_supplier_closes_at_zero(self):
        res = dss._get_ledger_online("supplier", "S1", "2026-04-01", "2027-03-31")
        self.assertAlmostEqual(res["summary"]["closing"], 0.0, places=2)


class CollectedMeansMoneyThatCameIn(unittest.TestCase):
    """Home tiles 2 / 8 / 11, online."""

    def setUp(self):
        import core.store_query_client as sq

        self._sq = sq
        self._saved = {
            n: getattr(sq, n)
            for n in ("home_summary", "list_sales", "list_customer_payments")
            if hasattr(sq, n)
        }
        self._online = sync_prefs.is_online_mode
        sync_prefs.is_online_mode = lambda *a, **k: True

        # The live Roshan shape: FY bills total 44,431.81 with 26,379.48 paid at the
        # counter, and 37,826.23 taken later as standalone receipts.
        bills = [
            {"total_amount": 44431.81, "amount_paid": 26379.48,
             "total_due": 18052.33, "account_cleared": False,
             "bill_cleared": False, "bill_date": "2026-06-01"},
        ]
        pays = [{"amount": 37826.23, "payment_date": "2026-06-02"}]

        sq.home_summary = lambda *a, **k: {
            "today": "2026-09-09",
            "month_start": "2026-09-01",
            "fy_start": "2026-04-01",
            "fy_end": "2027-03-31",
            "fy_label": "2026-27",
            "today_sales": 0, "today_collected": 0, "today_bills": 0,
            "month_sales": 0, "month_collected": 0, "month_bills": 0,
            # the server's own figure: SUM(amount_paid), no payments term
            "year_sales": 44431.81, "year_collected": 26379.48, "year_bills": 101,
            "customer_due": 4712.26, "supplier_due": 0.0, "stock_value": 344854.86,
        }

        def _sales(**kw):
            if str(kw.get("from_date") or "")[:10] == "2026-04-01":
                return {"rows": bills}
            return {"rows": []}

        def _pays(**kw):
            if str(kw.get("from_date") or "")[:10] == "2026-04-01":
                return {"rows": pays}
            return {"rows": []}

        sq.list_sales = _sales
        sq.list_customer_payments = _pays

    def tearDown(self):
        for n, fn in self._saved.items():
            setattr(self._sq, n, fn)
        sync_prefs.is_online_mode = self._online

    def test_year_collected_counts_standalone_receipts(self):
        conn = _store()
        stats = home_dashboard.query_dashboard_stats(conn)
        # tile 11 is Year Collected — bill_paid + max(payments, shortfall)
        self.assertEqual(stats["values"][10], "₹64,206")
        # and NOT the server's raw SUM(amount_paid)
        self.assertNotEqual(stats["values"][10], "₹26,379")


class TheDueListChasesWhatIsStillOwed(unittest.TestCase):
    """Alert & Monitoring -> Customer Dues."""

    def setUp(self):
        self._online = sync_prefs.is_online_mode
        sync_prefs.is_online_mode = lambda *a, **k: False

    def tearDown(self):
        sync_prefs.is_online_mode = self._online

    def test_a_cleared_bill_is_not_chased(self):
        """SCB81 on live Roshan: due_amount 2680, total_due 0, account_cleared true."""
        conn = _store()
        cid = _ins(conn, "customers", name="SHYAM GAYKI", phone="9", total_due=0)
        _ins(conn, "sales", customer_id=cid, bill_no="SCB81",
             bill_date="2026-07-01", total_amount=2680.0, amount_paid=0.0,
             due_amount=2680.0, total_due=0.0, account_cleared=1, bill_cleared=1)
        conn.commit()
        rows = alerts.fetch_customer_due_bills(conn)
        self.assertEqual(
            rows, [],
            "the list demanded Rs 2,680 the store has already been paid",
        )

    def test_a_partly_paid_bill_is_chased_for_the_cascaded_remainder(self):
        """SCB8 SAURAV: snapshot 491.00, cascaded 418.00."""
        conn = _store()
        cid = _ins(conn, "customers", name="SAURAV", phone="9", total_due=418.0)
        _ins(conn, "sales", customer_id=cid, bill_no="SCB8",
             bill_date="2026-05-01", total_amount=491.0, amount_paid=0.0,
             due_amount=491.0, total_due=418.0, account_cleared=0, bill_cleared=0)
        conn.commit()
        rows = alerts.fetch_customer_due_bills(conn)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0][6], 418.0, places=2)

    def test_the_list_totals_the_party_dues(self):
        """The whole store: the list and SUM(customers.total_due) must agree."""
        conn = _store()
        live = [("A", 131.0, 131.0, 0), ("B", 1450.0, 1449.58, 0), ("C", 418.0, 418.0, 0)]
        cleared = [("D", 2680.0, 0.0, 1), ("E", 517.0, 0.0, 1)]
        n = 0
        for name, snap, cascaded, acct in live + cleared:
            n += 1
            cid = _ins(conn, "customers", name=name, phone="9", total_due=cascaded)
            _ins(conn, "sales", customer_id=cid, bill_no=f"B{n}",
                 bill_date="2026-05-01", total_amount=snap, amount_paid=0.0,
                 due_amount=snap, total_due=cascaded, account_cleared=acct,
                 bill_cleared=acct)
        conn.commit()
        rows = alerts.fetch_customer_due_bills(conn)
        listed = round(sum(float(r[6]) for r in rows), 2)
        party = round(
            conn.execute(
                "SELECT COALESCE(SUM(total_due),0) FROM customers WHERE total_due>0"
            ).fetchone()[0],
            2,
        )
        self.assertEqual(listed, party)
        self.assertEqual(listed, 1998.58)
        self.assertEqual(len(rows), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
