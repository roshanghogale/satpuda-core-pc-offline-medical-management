"""Cash & Expenses (owner, 25 Oct 2026): the register's expected cash is the opening cash plus
what the books say came in, less what went out and what was typed as spent; a day closes with
the counted cash; and what the shop typed reaches the other PC through the offline-first outbox
and comes back from the (fake) server, while the bills' cash is never stored twice.

Everything runs on temp store files; no server is contacted (tests/fake_sync_server.FakeV2Server
stands in for routes/syncV2.js).
"""
from __future__ import annotations

import base64
import os
import sqlite3
import tempfile
import unittest

from core import cash_register as cr
from core import db_setup
from core.offline_first import ids, outbox, schema, worker
from core.offline_first.schema import meta_set
from tests.fake_sync_server import FakeV2Server

D = "2026-10-24"
D2 = "2026-10-25"


def make_store(device: int, install: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".db", prefix=f"cash_{device}_")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    db_setup.initialise(conn)
    conn.commit()
    schema.ensure_schema(conn)
    base = device * 1_000_000_000
    meta_set(conn, "device_no", device)
    meta_set(conn, "id_base", base)
    meta_set(conn, "id_max", base + 999_999_999)
    meta_set(conn, "install_id", install)
    conn.commit()
    conn.close()
    return path


def till(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30, factory=ids.OfConnection)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def remove(path: str) -> None:
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


class Maths(unittest.TestCase):
    def test_expected_cash_is_opening_plus_cash_in_less_cash_out_and_expenses(self):
        self.assertEqual(cr.expected_cash(1500), 1500.0)
        self.assertEqual(cr.expected_cash(1500, cash_sales=300), 1800.0)
        self.assertEqual(cr.expected_cash(1500, cash_sales=300, cash_sales_returns=50), 1750.0)
        self.assertEqual(cr.expected_cash(1500, cash_sales=300, expenses=20.5), 1779.5)
        self.assertEqual(cr.expected_cash(1500, cash_purchases=1000, cash_supplier_payments=250), 250.0)
        self.assertEqual(cr.expected_cash(100, cash_receipts=100, cash_purchase_returns=40, cash_added=500), 740.0)
        self.assertEqual(
            cr.expected_cash(1500, cash_sales=300, cash_receipts=100, cash_added=500, cash_purchase_returns=40,
                             cash_sales_returns=50, cash_purchases=1000, cash_supplier_payments=250, expenses=20),
            1120.0)
        # Strings and None are money too (JSON bodies), and the answer is rounded to paise.
        self.assertEqual(cr.expected_cash("10.10", cash_sales=None, expenses="0.05"), 10.05)
        self.assertEqual(cr.expected_cash(0.1, cash_sales=0.2), 0.3)


class Register(unittest.TestCase):
    def setUp(self):
        self.path = make_store(3, "pc-a")
        self.conn = till(self.path)
        self.addCleanup(self._cleanup)
        c = self.conn
        self.cust = c.execute("INSERT INTO customers (name) VALUES ('RAM')").lastrowid
        self.sup = c.execute("INSERT INTO suppliers (name) VALUES ('TULJAI')").lastrowid
        c.commit()

    def _cleanup(self):
        try:
            self.conn.close()
        except Exception:
            pass
        remove(self.path)

    def sale(self, day, cash, online, total=None, **extra):
        total = total if total is not None else cash + online
        cols = {"bill_no": f"SCB{int(cash)}{int(online)}{len(extra)}", "bill_date": day, "customer_id": self.cust,
                "total_amount": total, "amount_paid": cash + online, "cash_paid": cash, "online_paid": online,
                **extra}
        sid = self.conn.execute(
            f"INSERT INTO sales ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tuple(cols.values())
        ).lastrowid
        self.conn.commit()
        return sid

    def test_the_day_adds_up_from_the_books_and_the_typed_lines(self):
        c = self.conn
        cr.set_opening(c, D, 1500)
        self.sale(D, 300, 200)                                   # 300 cash on a bill
        self.sale(D, 80, 0, is_autosave=1)                       # an autosave is not a bill
        gone = self.sale(D, 70, 0, bill_no="SCB-GONE")
        c.execute("UPDATE sales SET deleted=1 WHERE id=?", (gone,))
        self.sale(D2, 999, 0)                                    # another day
        # Dues received: 100 cash, 60 online (not cash), and an old-style row with the mode only
        c.execute("INSERT INTO customer_payments (customer_id, payment_date, amount, payment_mode, cash_amount, "
                  "online_amount, note) VALUES (?,?,?,?,?,?,?)", (self.cust, D, 100, "cash", 100, 0, "Dues"))
        c.execute("INSERT INTO customer_payments (customer_id, payment_date, amount, payment_mode, cash_amount, "
                  "online_amount) VALUES (?,?,?,?,?,?)", (self.cust, D, 60, "online", 0, 60))
        c.execute("INSERT INTO customer_payments (customer_id, payment_date, amount, payment_mode) "
                  "VALUES (?,?,?,?)", (self.cust, D, 25, "cash"))
        # A sales return refunded in cash: the refund row (negative receipt) is the money handed back
        c.execute("INSERT INTO sales_returns (return_no, customer_id, return_date, refund_amount, settle_mode) "
                  "VALUES ('SR1',?,?,50,'cash')", (self.cust, D))
        c.execute("INSERT INTO customer_payments (customer_id, payment_date, amount, payment_mode, cash_amount, "
                  "online_amount, reference_no, note) VALUES (?,?,?,?,?,?,?,?)",
                  (self.cust, D, -50, "cash", -50, 0, "SR1", "Refund SR1"))
        # A walk-in cash return has no receipt row: the refund itself
        c.execute("INSERT INTO sales_returns (return_no, customer_id, return_date, refund_amount, settle_mode) "
                  "VALUES ('SR2',NULL,?,30,'cash')", (D,))
        # A return kept as credit moves no cash
        c.execute("INSERT INTO sales_returns (return_no, customer_id, return_date, refund_amount, settle_mode) "
                  "VALUES ('SR3',?,?,500,'ledger')", (self.cust, D))
        # Purchases: a cash / online split, and an old row paid at entry without a split (cash)
        c.execute("INSERT INTO purchases (purchase_no, supplier_id, purchase_date, bill_number, total_amount, "
                  "final_amount, cash_paid_at_entry, online_paid_at_entry, amount_paid_at_entry) "
                  "VALUES ('P1',?,?,'T-77',3000,3000,1000,500,1500)", (self.sup, D))
        c.execute("INSERT INTO purchases (purchase_no, supplier_id, purchase_date, total_amount, final_amount, "
                  "amount_paid_at_entry) VALUES ('P2',?,?,200,200,200)", (self.sup, D))
        c.execute("INSERT INTO purchases (purchase_no, supplier_id, purchase_date, total_amount, final_amount, "
                  "amount_paid_at_entry, is_autosave) VALUES ('P3',?,?,200,200,200,1)", (self.sup, D))
        # Supplier payments: cash out, an online one (not cash), and a purchase-return refund (cash in)
        c.execute("INSERT INTO supplier_payments (payment_no, supplier_id, payment_date, amount, mode) "
                  "VALUES ('PAY1',?,?,250,'Cash')", (self.sup, D))
        c.execute("INSERT INTO supplier_payments (payment_no, supplier_id, payment_date, amount, mode) "
                  "VALUES ('PAY2',?,?,900,'Online')", (self.sup, D))
        c.execute("INSERT INTO supplier_payments (payment_no, supplier_id, payment_date, amount, mode, reference) "
                  "VALUES ('PAY3',?,?,-40,'Cash','PR1')", (self.sup, D))
        c.commit()
        cr.save_expense(c, {"date": D, "description": "Tea", "category": "Tea & snacks", "amount": 20, "at": "10:00"})
        cr.save_expense(c, {"date": D, "description": "Dolo from Shree Medical", "category": "Medicines bought",
                            "amount": "250.00"})
        cr.save_expense(c, {"date": D, "kind": "in", "description": "Owner added cash", "amount": 500})
        cr.save_expense(c, {"date": D2, "description": "Tea", "category": "Tea & snacks", "amount": 15})

        rep = cr.day_report(c, D)
        by = {s["key"]: s for s in rep["sections"]}
        self.assertEqual(by["sales"]["total"], 300.0)
        self.assertEqual([l["ref"] for l in by["sales"]["lines"]], ["SCB3002000"])
        self.assertEqual(by["receipts"]["total"], 125.0)
        self.assertEqual(by["sales_returns"]["total"], 80.0)
        self.assertEqual(sorted(l["ref"] for l in by["sales_returns"]["lines"]), ["SR1", "SR2"])
        self.assertEqual(by["purchases"]["total"], 1200.0)
        self.assertEqual(by["purchases"]["lines"][0]["ref"], "T-77")
        self.assertEqual(by["supplier_payments"]["total"], 250.0)
        self.assertEqual(by["purchase_returns"]["total"], 40.0)
        self.assertEqual(rep["totals"]["expenses"], 270.0)
        self.assertEqual(rep["totals"]["cash_added"], 500.0)
        self.assertEqual(rep["totals"]["cash_in"], 300 + 125 + 40 + 500)
        self.assertEqual(rep["totals"]["cash_out"], 80 + 1200 + 250)
        self.assertEqual(rep["totals"]["expected"],
                         cr.expected_cash(1500, cash_sales=300, cash_receipts=125, cash_purchase_returns=40,
                                          cash_added=500, cash_sales_returns=80, cash_purchases=1200,
                                          cash_supplier_payments=250, expenses=270))
        self.assertEqual(rep["totals"]["expected"], 665.0)
        self.assertTrue(rep["opening"]["set"])
        self.assertIsNone(rep["counted"])
        self.assertIsNone(rep["difference"])

        # Close: the count is kept, the difference is counted - expected; open again clears it.
        rep = cr.close_day(c, D, 640, "50 given as change") and cr.day_report(c, D)
        self.assertEqual(rep["counted"], 640.0)
        self.assertEqual(rep["difference"], -25.0)
        self.assertTrue(rep["closed_at"])
        self.assertEqual(rep["note"], "50 given as change")
        cr.reopen_day(c, D)
        rep = cr.day_report(c, D)
        self.assertIsNone(rep["counted"])
        self.assertFalse(rep["closed_at"])

        # A day closed without an opening typed still asks for one (shown as 0).
        cr.close_day(c, D2, 1000)
        rep2 = cr.day_report(c, D2)
        self.assertFalse(rep2["opening"]["set"])
        self.assertEqual(rep2["opening"]["value"], 0.0)
        self.assertEqual(rep2["totals"]["expected"], 999 - 15)
        cr.set_opening(c, D2, 10)
        self.assertTrue(cr.day_report(c, D2)["opening"]["set"])
        self.assertEqual(c.execute("SELECT COUNT(*) FROM cash_days WHERE day=?", (D2,)).fetchone()[0], 1)

        # The month: one line per day, expenses by category, the differences.
        rng = cr.range_report(c, "2026-10-01", "2026-10-31")
        self.assertEqual([d["date"] for d in rng["days"]], [D, D2])
        self.assertEqual(rng["days"][1]["difference"], 1000 - (10 + 999 - 15))
        self.assertEqual(rng["totals"]["expenses"], 285.0)
        self.assertEqual(rng["categories"][0], {"category": "Medicines bought", "amount": 250.0})
        self.assertEqual({x["category"]: x["amount"] for x in rng["categories"]}["Tea & snacks"], 35.0)
        self.assertEqual(len(rng["expenses"]), 3)

        # Excel for the day and the month.
        out = cr.export(c, {"date": D, "format": "xlsx"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["filename"], "Cash_Register_24-10-2026.xlsx")
        self.assertTrue(base64.b64decode(out["content_base64"]).startswith(b"PK"))
        out = cr.export(c, {"from": "2026-10-01", "to": "2026-10-31", "format": "xlsx"})
        self.assertEqual(out["filename"], "Cash_Register_01-10-2026_31-10-2026.xlsx")
        sections = cr._day_sections(cr.day_report(c, D))
        self.assertEqual(sections[1]["rows"][0][1], 1500.0)
        self.assertEqual(sections[-1]["rows"][4], ["= Expected cash in drawer", 665.0])

    def test_typed_lines_are_edited_deleted_and_refused_when_empty(self):
        c = self.conn
        with self.assertRaises(ValueError):
            cr.save_expense(c, {"date": D, "description": "Tea", "amount": 0})
        with self.assertRaises(ValueError):
            cr.save_expense(c, {"date": D, "description": "", "amount": 10})
        with self.assertRaises(ValueError):
            cr.set_opening(c, D, -1)
        with self.assertRaises(ValueError):
            cr.day_report(c, "24/10/2026")
        row = cr.save_expense(c, {"date": D, "description": "Tea", "amount": 20, "at": "9am"})
        self.assertRegex(row["at"], r"^\d{2}:\d{2}$")          # a bad time becomes now
        self.assertGreater(row["id"], 3_000_000_000)           # this PC's own id range
        edited = cr.save_expense(c, {"id": row["id"], "date": D, "description": "Tea x2", "amount": 40, "at": "10:30"})
        self.assertEqual((edited["description"], edited["amount"], edited["at"], edited["version"]), ("Tea x2", 40.0, "10:30", 2))
        self.assertEqual(cr.day_report(c, D)["totals"]["expenses"], 40.0)
        # A month nobody counted has no total difference (not "tallies", not 0).
        self.assertIsNone(cr.range_report(c, D, D2)["totals"]["difference"])
        self.assertEqual(cr._range_sections(cr.range_report(c, D, D2))[1]["rows"][-1][7], "")
        self.assertTrue(cr.delete_expense(c, row["id"]))
        self.assertEqual(cr.day_report(c, D)["expenses"], [])
        # Every write was noted for the server; the delete is a delete.
        noted = {(col, op) for col, op in c.execute("SELECT collection, op FROM of_dirty")}
        self.assertIn(("cash_expenses", "delete"), noted)

    def test_the_routes_answer_in_plain_json(self):
        c = self.conn
        code, out = cr.handle_post("/api/cash/opening", {"date": D, "opening_cash": "1500"}, c)
        self.assertEqual((code, out["opening"]["value"], out["opening"]["set"]), (200, 1500.0, True))
        code, out = cr.handle_post("/api/cash/expense", {"date": D, "description": "Tea", "amount": 20}, c)
        self.assertEqual((code, out["totals"]["expected"]), (200, 1480.0))
        code, out = cr.handle_post("/api/cash/expense/delete", {"id": out["saved_id"]}, c)
        self.assertEqual((code, out["totals"]["expected"]), (200, 1500.0))
        code, out = cr.handle_post("/api/cash/close", {"date": D, "counted_cash": 1490}, c)
        self.assertEqual((code, out["difference"]), (200, -10.0))
        code, out = cr.handle_get("/api/cash/day", {"date": [D]}, c)
        self.assertEqual((code, out["counted"]), (200, 1490.0))
        code, out = cr.handle_get("/api/cash/range", {"from": [D], "to": [D2]}, c)
        self.assertEqual((code, len(out["days"])), (200, 1))
        code, out = cr.handle_post("/api/cash/expense", {"date": D, "description": "Tea"}, c)
        self.assertEqual(code, 400)
        self.assertIn("amount", out["error"])
        self.assertIsNone(cr.handle_get("/api/other", {}, c))


class SyncRoundTrip(unittest.TestCase):
    """PC A and PC B of one shop, one fake server."""

    def setUp(self):
        self.server = FakeV2Server()
        self.a_path = make_store(3, "pc-a")
        self.b_path = make_store(4, "pc-b")
        self.a, self.b = till(self.a_path), till(self.b_path)
        self.ca, self.cb = worker.SyncCycle(self.a_path), worker.SyncCycle(self.b_path)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for cyc in (self.ca, self.cb):
            cyc.close()
        for conn in (self.a, self.b):
            try:
                conn.close()
            except Exception:
                pass
        remove(self.a_path)
        remove(self.b_path)

    def sync(self, *pcs):
        for name in pcs:
            cyc, install = {"a": (self.ca, "pc-a"), "b": (self.cb, "pc-b")}[name]
            with self.server.bind(install):
                cyc.run_once()

    def test_what_one_pc_types_reaches_the_other_and_a_delete_takes_it_back(self):
        cr.set_opening(self.a, D, 1500)
        tea = cr.save_expense(self.a, {"date": D, "description": "Tea", "category": "Tea & snacks", "amount": 20,
                                       "at": "10:00"})
        # A's own change is in the outbox as the two collections, with this PC as the writer.
        w = worker.open_worker_connection(self.a_path)
        self.addCleanup(w.close)
        with worker.write_batch(w):
            outbox.build_events(w, "pc-a")
        evs = outbox.pending_events(w)
        self.assertEqual([e["collection"] for e in evs], ["cash_days", "cash_expenses"])
        self.assertEqual(evs[0]["doc"]["day"], D)
        self.assertEqual(evs[0]["doc"]["opening_cash"], 1500.0)
        self.assertEqual(evs[1]["doc"]["description"], "Tea")
        self.assertEqual(evs[1]["doc"]["device_id"], "pc-a")
        w.close()

        self.sync("a", "b")
        self.assertEqual(self.server.docs[("cash_expenses", tea["id"])]["amount"], 20.0)
        self.assertEqual(self.server.docs[("cash_expenses", tea["id"])]["version"], 1)
        rep_b = cr.day_report(self.b, D)
        self.assertEqual((rep_b["opening"]["value"], rep_b["opening"]["set"]), (1500.0, True))
        self.assertEqual([(e["id"], e["description"], e["amount"]) for e in rep_b["expenses"]],
                         [(tea["id"], "Tea", 20.0)])
        # Nothing pulled was noted as B's own work.
        self.assertEqual(self.b.execute("SELECT COUNT(*) FROM of_dirty").fetchone()[0], 0)
        self.assertEqual(self.b.execute("SELECT COUNT(*) FROM of_events WHERE status='pending'").fetchone()[0], 0)

        # B edits the line and adds one, A deletes nothing yet: both PCs end up the same.
        cr.save_expense(self.b, {"id": tea["id"], "date": D, "description": "Tea x2", "amount": 40, "at": "10:00"})
        snacks = cr.save_expense(self.b, {"date": D, "description": "Snacks", "amount": 60, "at": "11:00"})
        self.assertGreater(snacks["id"], 4_000_000_000)           # B's own range
        self.sync("b", "a")
        rep_a = cr.day_report(self.a, D)
        self.assertEqual([(e["description"], e["amount"]) for e in rep_a["expenses"]],
                         [("Tea x2", 40.0), ("Snacks", 60.0)])
        self.assertEqual(self.server.docs[("cash_expenses", tea["id"])]["version"], 2)

        # A deletes the snacks line; B loses it.
        cr.delete_expense(self.a, snacks["id"])
        self.sync("a", "b")
        self.assertEqual([e["description"] for e in cr.day_report(self.b, D)["expenses"]], ["Tea x2"])
        self.assertTrue(self.server.docs[("cash_expenses", snacks["id"])]["deleted"])

        # B closes the day; A sees the count and the same difference. One row per day on both.
        cr.close_day(self.b, D, 1450, "ok")
        self.sync("b", "a")
        rep_a = cr.day_report(self.a, D)
        self.assertEqual((rep_a["counted"], rep_a["difference"], rep_a["note"]), (1450.0, -10.0, "ok"))
        self.assertTrue(rep_a["closed_at"])
        for conn in (self.a, self.b):
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM cash_days").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM of_dirty").fetchone()[0], 0)
        # The bills' cash never travelled as register rows: only the typed records are on the server.
        self.assertEqual(sorted({c for c, _ in self.server.docs}), ["cash_days", "cash_expenses"])

    def test_an_unsent_local_change_is_not_overwritten_by_the_pull(self):
        cr.set_opening(self.a, D, 1500)
        self.sync("a", "b")
        # Both PCs change the opening before syncing: B's own change goes up first and wins
        # on B; the server then holds B's figure (the later write) and A follows.
        cr.set_opening(self.a, D, 1600)
        cr.set_opening(self.b, D, 1700)
        self.sync("a")
        with self.server.bind("pc-b"):
            # B pulls A's change while its own is still unsent: kept.
            self.cb.pull()
        self.assertEqual(cr.day_report(self.b, D)["opening"]["value"], 1700.0)
        self.sync("b", "a")
        self.assertEqual(cr.day_report(self.a, D)["opening"]["value"], 1700.0)
        self.assertEqual(cr.day_report(self.b, D)["opening"]["value"], 1700.0)


if __name__ == "__main__":
    unittest.main()
