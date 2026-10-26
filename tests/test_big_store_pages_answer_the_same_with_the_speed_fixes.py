"""Big stores open their pages fast -- and every page answers exactly what it did before.

Owner, 26 Oct 2026: shops moved over from Medivision / Marg (Shrihari Medical Jalna: 64,032
medicine batches, 13,115 bills; Jagadamba ~65,000 medicines) waited on every page. The store
file had no index on any business table, the startup alerts asked the medicines table by name
thousands of times (20 s), Reorder by Supplier opened the sync-mode file once per candidate
(10-19 s), Sales History and Inventory read the theme file once per row, and Alert &
Monitoring built "where each batch came from" four times per screen.

The speed-ups must change no figure, row or order. These build a small shop through the
app's own copy path (tests/perf/make_big_store.py) and compare every page's answer with the
lookup indexes and without them, and the rewritten pieces with the code they replaced.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "perf"))

import make_big_store  # noqa: E402

_BUILT: dict = {}


def _shop() -> tuple[str, str]:
    """(app-data folder, store file) of one small shop, built once for this module."""
    if not _BUILT:
        tmp = tempfile.mkdtemp(prefix="bigstore_")
        src = make_big_store.Synthetic(make_big_store.PRESETS["tiny"], seed=7)
        db = make_big_store.build(os.path.join(tmp, "data"), src, store_name="Perf Tiny",
                                  progress=False, register=False)
        _BUILT.update(tmp=tmp, data=os.path.join(tmp, "data"), db=db)
    return _BUILT["data"], _BUILT["db"]


def tearDownModule():
    if _BUILT.get("tmp"):
        shutil.rmtree(_BUILT["tmp"], ignore_errors=True)


def _strip_perf_indexes(path: str) -> None:
    from core.perf_indexes import BILLING_INDEXES, LOOKUP_INDEXES

    ours = {n for n, *_ in BILLING_INDEXES} | {n for n, *_ in LOOKUP_INDEXES}
    c = sqlite3.connect(path)
    for (n,) in c.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall():
        if n not in ours:
            continue
        c.execute(f"DROP INDEX {n}")
    c.execute("DROP TABLE IF EXISTS sqlite_stat1")
    c.commit()
    c.close()


def _canon(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


class _ShopCase(unittest.TestCase):
    def setUp(self):
        data, db = _shop()
        self.tmp = tempfile.mkdtemp(prefix="bigstore_case_")
        self.data = data
        self.patches = [mock.patch("core.license_manager._appdata_dir", lambda: data)]
        for p in self.patches:
            p.start()
        self.with_db = os.path.join(self.tmp, "with.db")
        self.without_db = os.path.join(self.tmp, "without.db")
        src = sqlite3.connect(db)
        for target in (self.with_db, self.without_db):
            dst = sqlite3.connect(target)
            src.backup(dst)
            dst.close()
        src.close()
        _strip_perf_indexes(self.without_db)
        from core.perf_indexes import ensure_perf_indexes

        c = sqlite3.connect(self.with_db)
        ensure_perf_indexes(c)
        c.close()
        from core import read_memo

        read_memo.forget()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _open(self, path: str) -> sqlite3.Connection:
        c = sqlite3.connect(path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c


class EveryPageAnswersTheSame(_ShopCase):
    def _pages(self, conn) -> dict:
        from core import desktop_pages_service as pages
        from core import desktop_settings_service as st
        from core.desktop_startup_service import get_startup_alerts
        from core.doctor_margin_report import build_report
        from core import gst_screen
        from core.read_memo import forget

        today = date.today().isoformat()
        cust = conn.execute(
            "SELECT c.name FROM sales s JOIN customers c ON c.id=s.customer_id "
            "GROUP BY c.id ORDER BY COUNT(*) DESC, c.id LIMIT 1").fetchone()[0]
        sup = conn.execute(
            "SELECT s.name FROM purchases p JOIN suppliers s ON s.id=p.supplier_id "
            "GROUP BY s.id ORDER BY COUNT(*) DESC, s.id LIMIT 1").fetchone()[0]
        out = {}
        calls = {
            "sales_history": lambda: pages.list_sales_history(conn, sort="Recent (newest first)"),
            "purchase_history": lambda: pages.list_purchase_history(conn, sort="Recent (newest first)"),
            "inventory": lambda: pages.list_inventory(conn),
            "inventory_all": lambda: pages.list_inventory(conn, show="all", limit=10000),
            "inventory_expired": lambda: pages.list_inventory(conn, show="expired"),
            "alerts": lambda: st.get_alerts(conn),
            "startup_alerts": lambda: get_startup_alerts(conn, force=True),
            "reorder_bulk": lambda: st.reorder_action(conn, {"action": "bulk_tabs"}),
            "reorder_candidates": lambda: st.reorder_action(conn, {"action": "candidates"}),
            "payments_customer": lambda: st.get_payments(conn, "customer"),
            "payments_supplier": lambda: st.get_payments(conn, "supplier"),
            "ledger_customer": lambda: st.get_ledger(conn, "customer", cust, "", today),
            "ledger_supplier": lambda: st.get_ledger(conn, "supplier", sup, "", today),
            "contacts": lambda: st.get_contacts(conn),
            "gst_sale": lambda: gst_screen.build(conn, "sale", "2025-04-01", today, 1.0),
            "doctor_margin": lambda: build_report(conn, {"from": "2025-04-01", "to": today, "doctor": "",
                                                         "mode": "regular", "pct": 10, "types": [],
                                                         "style": "detailed"}),
        }
        for name, fn in calls.items():
            forget()
            out[name] = _canon(fn())
        return out

    def test_with_and_without_the_lookup_indexes(self):
        a, b = self._open(self.without_db), self._open(self.with_db)
        try:
            before, after = self._pages(a), self._pages(b)
        finally:
            a.close()
            b.close()
        for name in before:
            self.assertEqual(before[name], after[name], name)
        # the shop really has lists to compare
        self.assertGreater(len(json.loads(before["sales_history"])["rows"]), 100)
        self.assertGreater(len(json.loads(before["inventory"])["rows"]), 100)

    def test_a_second_read_from_memory_is_the_same_as_the_first(self):
        from core import desktop_settings_service as st

        c = self._open(self.with_db)
        try:
            first = _canon(st.get_alerts(c))
            second = _canon(st.get_alerts(c))
        finally:
            c.close()
        self.assertEqual(first, second)


class TheLookupIndexes(_ShopCase):
    def test_made_once_with_statistics_that_keep_every_other_plan(self):
        from core.perf_indexes import BILLING_INDEXES, LOOKUP_INDEXES, STAT, ensure_perf_indexes

        c = sqlite3.connect(self.without_db)
        try:
            made = ensure_perf_indexes(c)
            lookup = {n for n, _t, _c in LOOKUP_INDEXES}
            self.assertEqual(set(made), lookup | {n for n, *_ in BILLING_INDEXES})
            self.assertEqual(ensure_perf_indexes(c), [])
            stats = dict(c.execute("SELECT idx, stat FROM sqlite_stat1").fetchall())
            for n in made:
                # the pages' lookups are "unordered"; the billing indexes keep no statistics
                self.assertEqual(stats.get(n), STAT if n in lookup else None, n)

            def plan(sql, *p):
                return " ".join(r[-1] for r in c.execute("EXPLAIN QUERY PLAN " + sql, p).fetchall())

            # a lookup by name / by bill uses an index ...
            self.assertIn("ix_perf_medicines_name", plan("SELECT * FROM medicines WHERE name=?", "X"))
            self.assertIn("idx_sales_items_sale", plan("SELECT * FROM sales_items WHERE sale_id=?", 1))
            # ... the billing search still reads its name index in order (not stopped by the
            # "unordered" statistics of the lookups) ...
            self.assertIn("idx_medicines_instock_name", plan(
                "SELECT name FROM medicines WHERE stock_qty > 0 ORDER BY name COLLATE NOCASE LIMIT 50"))
            # ... and a whole list in name order is read and sorted as before
            self.assertNotIn("ix_perf", plan(
                "SELECT name, SUM(stock_qty) FROM medicines GROUP BY name"))
            from core import alert_monitoring_service as am

            self.assertTrue(all("idx_" not in plan(sql) and "ix_perf" not in plan(sql) for sql in (
                "SELECT id, name FROM medicines NOT INDEXED WHERE COALESCE(is_hidden,0)=0 "
                "ORDER BY name COLLATE NOCASE, batch_no",
                "SELECT DISTINCT TRIM(m.name) FROM sales_items i NOT INDEXED CROSS JOIN medicines m "
                "ON m.id=i.medicine_id WHERE TRIM(COALESCE(m.name,''))!='' LIMIT 800",
            )))
            self.assertTrue(callable(am.fetch_low_stock_alerts))
        finally:
            c.close()

    def test_an_older_store_without_some_tables_is_fine(self):
        from core.perf_indexes import ensure_perf_indexes

        path = os.path.join(self.tmp, "old.db")
        c = sqlite3.connect(path)
        try:
            c.execute("CREATE TABLE medicines (id INTEGER PRIMARY KEY, name TEXT)")
            c.execute("CREATE TABLE sales_items (id INTEGER PRIMARY KEY, qty REAL)")  # no sale_id
            self.assertEqual(set(ensure_perf_indexes(c)), {
                "idx_medicines_name_nocase", "idx_medicines_name_upper", "ix_perf_medicines_name"})
            self.assertEqual(ensure_perf_indexes(c), [])
        finally:
            c.close()

    def test_the_engine_makes_them_when_it_opens_a_store(self):
        from core import desktop_api

        with mock.patch("core.offline_first.worker.start_for", lambda path: None):
            conn = desktop_api._open_conn(self.without_db)
        try:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'")}
            self.assertIn("idx_sales_items_sale", names)
            self.assertIn("ix_perf_medicines_name", names)
        finally:
            conn.close()


class TheRewrittenPieces(_ShopCase):
    def test_inventory_view_counts_match_the_row_by_row_count(self):
        from core.inventory_views import VIEWS, _expired, count_local

        def old_count(conn, today):
            out = {v: 0 for v in VIEWS}
            for is_hidden, stock, expiry in conn.execute(
                    "SELECT COALESCE(is_hidden,0), COALESCE(stock_qty,0), COALESCE(expiry_date,'') "
                    "FROM medicines WHERE COALESCE(deleted,0)=0"):
                out["all"] += 1
                if is_hidden:
                    out["hidden"] += 1
                    continue
                out["active"] += 1
                try:
                    if float(stock or 0) <= 0:
                        out["out_of_stock"] += 1
                except (TypeError, ValueError):
                    pass
                if _expired(expiry, today):
                    out["expired"] += 1
            return out

        c = sqlite3.connect(self.with_db)
        try:
            # odd rows a store can hold: text stock, hidden text flag, month-only expiry
            ids = [r[0] for r in c.execute("SELECT id FROM medicines ORDER BY id LIMIT 6")]
            c.execute("UPDATE medicines SET stock_qty='0' WHERE id=?", (ids[0],))
            c.execute("UPDATE medicines SET stock_qty='abc' WHERE id=?", (ids[1],))
            c.execute("UPDATE medicines SET stock_qty=NULL WHERE id=?", (ids[2],))
            c.execute("UPDATE medicines SET is_hidden=1 WHERE id=?", (ids[3],))
            c.execute("UPDATE medicines SET expiry_date='09/25' WHERE id=?", (ids[4],))
            c.execute("UPDATE medicines SET stock_qty=-2.5 WHERE id=?", (ids[5],))
            c.commit()
            for today in (date.today(), date(2025, 6, 1), date(2028, 1, 1)):
                self.assertEqual(count_local(c, today), old_count(c, today), today)
        finally:
            c.close()

    def test_a_kept_read_is_dropped_when_the_store_changes(self):
        from core.read_memo import memo

        c = sqlite3.connect(self.with_db, check_same_thread=False)
        other = sqlite3.connect(self.with_db)
        try:
            read = lambda: c.execute("SELECT COUNT(*) FROM customers").fetchone()[0]  # noqa: E731
            n = memo(c, "t.count", read)
            self.assertEqual(memo(c, "t.count", lambda: -1), n)          # kept
            c.execute("INSERT INTO customers (name) VALUES ('memo test 1')")
            c.commit()
            self.assertEqual(memo(c, "t.count", read), n + 1)           # this connection wrote
            other.execute("INSERT INTO customers (name) VALUES ('memo test 2')")
            other.commit()
            self.assertEqual(memo(c, "t.count", read), n + 2)           # another one wrote
            c.execute("INSERT INTO customers (name) VALUES ('memo test 3')")
            # inside an open write: read fresh, never kept
            self.assertEqual(memo(c, "t.count", read), n + 3)
            c.rollback()
            self.assertEqual(memo(c, "t.count", read), n + 2)
        finally:
            c.close()
            other.close()

    def test_an_expiry_parsed_twice_is_the_same_date(self):
        from core.alert_thresholds import _parse_expiry_text, parse_expiry

        for raw in ("2026-09-14", "09/26", "12/2027", "14-09-2026", "garbage", " 2026-01-01 "):
            self.assertEqual(parse_expiry(raw), _parse_expiry_text(raw.strip()))
            self.assertEqual(parse_expiry(raw), _parse_expiry_text(raw.strip()))
        self.assertIsNone(parse_expiry(""))
        self.assertIsNone(parse_expiry(None))


if __name__ == "__main__":
    unittest.main()
