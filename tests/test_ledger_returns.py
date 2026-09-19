"""Returns must appear on the Settings -> Ledger statement.

Both ledger paths (local SQLite and the online server fetch) used to build the
statement from sales/purchases and payments only. A customer who bought 1000,
returned 400 and paid the remaining 600 was fully settled, yet the ledger
showed 400 still outstanding -- the refund never reduced the balance. The same
held for suppliers.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db_setup, sync_prefs  # noqa: E402
from core import desktop_settings_service as dss  # noqa: E402


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


class LocalLedgerReturnsTests(unittest.TestCase):
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

    def test_sales_return_settles_customer(self):
        conn = _store()
        cid = _ins(conn, "customers", name="C1", phone="9")
        sid = _ins(conn, "sales", customer_id=cid, bill_no="B1",
                   bill_date="2026-08-01", total_amount=1000,
                   amount_paid=0, due_amount=1000)
        _ins(conn, "sales_returns", sale_id=sid, customer_id=cid,
             return_date="2026-08-05", refund_amount=400, return_no="R1")
        _ins(conn, "customer_payments", customer_id=cid,
             payment_date="2026-08-10", amount=600, payment_mode="Cash")
        conn.commit()
        res = self._ledger(conn, "customer", "C1")
        self.assertAlmostEqual(res["summary"]["closing"], 0.0, places=2)
        self.assertIn("return", [r.get("tag") for r in res["rows"]])

    def test_purchase_return_settles_supplier(self):
        conn = _store()
        sup = _ins(conn, "suppliers", name="S1")
        pid = _ins(conn, "purchases", supplier_id=sup, bill_number="P1",
                   purchase_no="P1", purchase_date="2026-08-01",
                   total_amount=1000, final_amount=1000, amount_paid=0)
        _ins(conn, "purchase_returns", purchase_id=pid, supplier_id=sup,
             return_date="2026-08-05", refund_amount=400, return_no="PR1")
        _ins(conn, "supplier_payments", supplier_id=sup,
             payment_date="2026-08-10", amount=600, mode="Cash",
             payment_no="SP1")
        conn.commit()
        res = self._ledger(conn, "supplier", "S1")
        self.assertAlmostEqual(res["summary"]["closing"], 0.0, places=2)

    def test_deleted_return_is_ignored(self):
        conn = _store()
        cid = _ins(conn, "customers", name="C2", phone="9")
        sid = _ins(conn, "sales", customer_id=cid, bill_no="B1",
                   bill_date="2026-08-01", total_amount=1000,
                   amount_paid=0, due_amount=1000)
        _ins(conn, "sales_returns", sale_id=sid, customer_id=cid,
             return_date="2026-08-05", refund_amount=400,
             return_no="R1", deleted=1)
        conn.commit()
        res = self._ledger(conn, "customer", "C2")
        self.assertAlmostEqual(res["summary"]["closing"], 1000.0, places=2)

    def test_return_before_window_lands_in_opening(self):
        conn = _store()
        cid = _ins(conn, "customers", name="C3", phone="9")
        sid = _ins(conn, "sales", customer_id=cid, bill_no="B0",
                   bill_date="2026-01-10", total_amount=1000,
                   amount_paid=0, due_amount=1000)
        _ins(conn, "sales_returns", sale_id=sid, customer_id=cid,
             return_date="2026-01-20", refund_amount=400, return_no="R0")
        conn.commit()
        res = self._ledger(conn, "customer", "C3")
        self.assertAlmostEqual(res["summary"]["opening"], 600.0, places=2)

    def test_no_returns_unchanged(self):
        conn = _store()
        cid = _ins(conn, "customers", name="NOR", phone="9")
        _ins(conn, "sales", customer_id=cid, bill_no="B1",
             bill_date="2026-08-01", total_amount=500,
             amount_paid=0, due_amount=500)
        _ins(conn, "customer_payments", customer_id=cid,
             payment_date="2026-08-10", amount=200, payment_mode="Cash")
        conn.commit()
        res = self._ledger(conn, "customer", "NOR")
        self.assertAlmostEqual(res["summary"]["closing"], 300.0, places=2)


class LedgerExcludesDeadRowsTests(unittest.TestCase):
    """Deleted rows and autosave drafts must not reach the statement.

    A bill deleted on another device arrives here as deleted=1 (see
    soft_delete_local in core/server_entity_sync.py) rather than being removed,
    and an unfinished bill sits in `sales` with is_autosave=1. The local ledger
    counted both: deleted sales/purchases overstated the debt, a deleted
    payment understated it, and a half-typed bill showed up as a real one.
    The online path already filtered these; the local path did not.
    """

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

    def test_deleted_sale_excluded(self):
        conn = _store()
        cid = _ins(conn, "customers", name="C1", phone="9")
        _ins(conn, "sales", customer_id=cid, bill_no="KEEP",
             bill_date="2026-08-01", total_amount=500, due_amount=500)
        _ins(conn, "sales", customer_id=cid, bill_no="DEL",
             bill_date="2026-08-02", total_amount=700, due_amount=700, deleted=1)
        conn.commit()
        res = self._ledger(conn, "customer", "C1")
        self.assertAlmostEqual(res["summary"]["closing"], 500.0, places=2)

    def test_autosave_draft_excluded(self):
        conn = _store()
        cid = _ins(conn, "customers", name="C2", phone="9")
        _ins(conn, "sales", customer_id=cid, bill_no="REAL",
             bill_date="2026-08-01", total_amount=500, due_amount=500)
        _ins(conn, "sales", customer_id=cid, bill_no="DRAFT",
             bill_date="2026-08-02", total_amount=900, due_amount=900,
             is_autosave=1)
        conn.commit()
        res = self._ledger(conn, "customer", "C2")
        self.assertAlmostEqual(res["summary"]["closing"], 500.0, places=2)

    def test_deleted_payment_not_credited(self):
        conn = _store()
        cid = _ins(conn, "customers", name="C3", phone="9")
        _ins(conn, "sales", customer_id=cid, bill_no="B1",
             bill_date="2026-08-01", total_amount=1000, due_amount=1000)
        _ins(conn, "customer_payments", customer_id=cid,
             payment_date="2026-08-05", amount=300,
             payment_mode="Cash", deleted=1)
        conn.commit()
        res = self._ledger(conn, "customer", "C3")
        self.assertAlmostEqual(res["summary"]["closing"], 1000.0, places=2)

    def test_deleted_purchase_excluded(self):
        conn = _store()
        sup = _ins(conn, "suppliers", name="S1")
        _ins(conn, "purchases", supplier_id=sup, bill_number="P1",
             purchase_no="P1", purchase_date="2026-08-01", final_amount=400)
        _ins(conn, "purchases", supplier_id=sup, bill_number="PD",
             purchase_no="PD", purchase_date="2026-08-02",
             final_amount=600, deleted=1)
        conn.commit()
        res = self._ledger(conn, "supplier", "S1")
        self.assertAlmostEqual(res["summary"]["closing"], 400.0, places=2)

    def test_opening_balance_ignores_dead_rows(self):
        conn = _store()
        cid = _ins(conn, "customers", name="C4", phone="9")
        _ins(conn, "sales", customer_id=cid, bill_no="OLD",
             bill_date="2026-01-05", total_amount=800, due_amount=800)
        _ins(conn, "sales", customer_id=cid, bill_no="OLDDEL",
             bill_date="2026-01-06", total_amount=600,
             due_amount=600, deleted=1)
        _ins(conn, "customer_payments", customer_id=cid,
             payment_date="2026-01-07", amount=200,
             payment_mode="Cash", deleted=1)
        conn.commit()
        res = self._ledger(conn, "customer", "C4")
        self.assertAlmostEqual(res["summary"]["opening"], 800.0, places=2)

    def test_supplier_opening_ignores_deleted_payment(self):
        conn = _store()
        sup = _ins(conn, "suppliers", name="S2")
        _ins(conn, "purchases", supplier_id=sup, bill_number="OP",
             purchase_no="OP", purchase_date="2026-01-05", final_amount=900)
        _ins(conn, "supplier_payments", supplier_id=sup,
             payment_date="2026-01-06", amount=400, mode="Cash",
             payment_no="X", deleted=1)
        conn.commit()
        res = self._ledger(conn, "supplier", "S2")
        self.assertAlmostEqual(res["summary"]["opening"], 900.0, places=2)


class OnlineLedgerReturnsTests(unittest.TestCase):
    """The online path builds the same statement from server rows."""

    def setUp(self):
        import core.store_query_client as sq
        self._saved = {n: getattr(sq, n) for n in dir(sq) if n.startswith("list_")}
        rows = {
            "list_sales": [{"id": 1, "customer_id": 7, "customer_name": "C1",
                            "bill_no": "B1", "bill_date": "2026-08-01",
                            "total_amount": 1000, "discount": 0}],
            "list_customer_payments": [{"id": 1, "customer_id": 7,
                                        "payment_date": "2026-08-10",
                                        "amount": 600, "payment_mode": "Cash"}],
            "list_sales_returns": [{"id": 1, "customer_id": 7, "return_no": "R1",
                                    "return_date": "2026-08-05",
                                    "refund_amount": 400}],
            "list_purchases": [{"id": 1, "supplier_id": 3, "bill_number": "P1",
                                "purchase_date": "2026-08-01",
                                "final_amount": 1000}],
            "list_supplier_payments": [{"id": 1, "supplier_id": 3,
                                        "payment_date": "2026-08-10",
                                        "amount": 600, "mode": "Cash",
                                        "payment_no": "SP1"}],
            "list_purchase_returns": [{"id": 1, "supplier_id": 3,
                                       "return_no": "PR1",
                                       "return_date": "2026-08-05",
                                       "refund_amount": 400}],
            "list_customers": [{"id": 7, "name": "C1"}],
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

    def test_customer_return_credited_online(self):
        res = dss._get_ledger_online("customer", "C1", "2026-04-01", "2027-03-31")
        self.assertAlmostEqual(res["summary"]["closing"], 0.0, places=2)

    def test_supplier_return_credited_online(self):
        res = dss._get_ledger_online("supplier", "S1", "2026-04-01", "2027-03-31")
        self.assertAlmostEqual(res["summary"]["closing"], 0.0, places=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class DeletingACashReturnGivesTheMoneyBack(unittest.TestCase):
    """Deleting a sales return that paid cash used to inflate the customer's due.

    A return settled in cash writes a customer_payments row of -payout, tagged
    with the return number, to record the money that left the drawer. Deleting
    the return reversed the stock and the return row and left that negative
    payment sitting there -- so the balance came back higher than it started,
    and the shop went looking for money it had already handed over.
    """

    def setUp(self):
        import sqlite3

        from core import db_setup, desktop_returns_service as ret

        self.ret = ret
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db_setup.initialise(self.conn)
        ret._ensure_return_tables(self.conn)
        self._online = mock.patch("core.sync_prefs.is_online_mode", return_value=False)
        self._online.start()

    def tearDown(self):
        self._online.stop()
        self.conn.close()

    def test_the_refund_payment_is_removed_with_the_return(self):
        cur = self.conn.cursor()
        cur.execute("INSERT INTO customers (name) VALUES ('ZZ CASH CUSTOMER')")
        customer_id = cur.lastrowid
        cur.execute(
            "INSERT INTO sales_returns (return_no, customer_id, return_date, refund_amount) "
            "VALUES ('SR-CASH-1', ?, '2026-09-07', 400)",
            (customer_id,),
        )
        return_id = cur.lastrowid
        cur.execute(
            "INSERT INTO customer_payments "
            "(customer_id, payment_date, amount, payment_mode, reference_no, note) "
            "VALUES (?, '2026-09-07', -400, 'cash', 'SR-CASH-1', 'Refund SR-CASH-1')",
            (customer_id,),
        )
        self.conn.commit()

        res = self.ret.delete_sales_return(self.conn, {"id": return_id})
        self.assertTrue(res.get("ok"), res)

        left = self.conn.execute(
            "SELECT COUNT(*) FROM customer_payments "
            "WHERE reference_no='SR-CASH-1' AND COALESCE(deleted,0)=0"
        ).fetchone()[0]
        self.assertEqual(
            left, 0,
            "the cash refund row survived the delete — the customer's due is now "
            "higher than before the return was ever made",
        )

    def test_a_credit_return_leaves_ordinary_payments_alone(self):
        """Only the negative row tagged with THIS return may be touched."""
        cur = self.conn.cursor()
        cur.execute("INSERT INTO customers (name) VALUES ('ZZ CREDIT CUSTOMER')")
        customer_id = cur.lastrowid
        cur.execute(
            "INSERT INTO sales_returns (return_no, customer_id, return_date, refund_amount) "
            "VALUES ('SR-CREDIT-1', ?, '2026-09-07', 250)",
            (customer_id,),
        )
        return_id = cur.lastrowid
        cur.execute(
            "INSERT INTO customer_payments "
            "(customer_id, payment_date, amount, payment_mode, reference_no, note) "
            "VALUES (?, '2026-09-06', 900, 'cash', 'SCB-77', 'Bill payment')",
            (customer_id,),
        )
        self.conn.commit()

        self.assertTrue(self.ret.delete_sales_return(self.conn, {"id": return_id}).get("ok"))
        kept = self.conn.execute(
            "SELECT COUNT(*) FROM customer_payments "
            "WHERE COALESCE(deleted,0)=0 AND amount>0"
        ).fetchone()[0]
        self.assertEqual(kept, 1, "a real payment was deleted with the return")
