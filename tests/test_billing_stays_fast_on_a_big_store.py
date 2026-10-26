"""Sales billing on a big store (Shrihari Medical: 64,032 batches, 8,539 customers, 13,115 bills;
planned for 5 lakh batches) stays fast, and gives exactly the bills it gave before.

What was slow or broken, and is pinned here:

* The Sales medicine list read the whole store: no index anywhere, and for every batch a
  sub-query over every purchase line (its first purchase). Opening the list took 25 s and
  typing "p" 10 s; the requests piled up and "pa" answered after 51 s.
* The list fetched 320 rows and THEN dropped the empty batches, so on a store where most
  batches are empty (3,214 of 64,032 had stock) "pan" listed 2 medicines of the dozens in
  stock, and a name could show part of its stock. Then it sorted A-Z, putting "ABD PAD"
  above every PA... medicine.
* Every bill line opened the store database twice and read the store registry three times
  just for settings; the Sales settings call read the registry 34 times.
* A save took the year's highest bill number (reading every bill) even when offline-first
  had a number block ready.
* The worker's full stock refresh updated every batch of the store in one write: 0.7 s on
  64,000 batches, 11 s on 5 lakh, every 30 minutes -- the till's Save waited behind it.
* F7 / F8 kept the counter waiting for the PDF and the printer (3-10 s) before the next bill.

No server, no browser, no printer.
"""
from __future__ import annotations

import os
import random
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from unittest import mock

from core import bill_output, db_setup, desktop_sales_service as sales, sync_prefs
from core.name_search_rank import order_by_sql
from core.offline_first import pull, schema
from core.perf_indexes import ensure_perf_indexes
from core.printer_manager import PrinterManager
from tests.test_offline_first_save_and_print_render_once import (
    _item, _join_pdf_threads, _make_store, _medicine, _till,
)

DAY = "2026-10-25"


def _plain_store() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db_setup.initialise(conn)
    conn.commit()
    return conn


def _add(conn, name, stock, *, expiry="2028-12-31", hidden=0, created="2026-01-01 00:00:00"):
    return conn.execute(
        "INSERT INTO medicines (name, stock_qty, mrp, rate, unit, type, batch_no, expiry_date, "
        "is_hidden, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (name, stock, 10.0, 7.0, "10", "Tablet", "B", expiry, hidden, created),
    ).lastrowid


def _reference_names(conn, q, *, limit=40, reserved=None, bill_date=DAY):
    """What the list must answer: every batch with stock left, not hidden, not expired, there
    on the bill date, matching; names best match first (order_by_sql), each with all its
    batches added up; the first ``limit`` names."""
    from core.batch_visibility import is_expired_as_of, medicine_existed_sql

    reserved = reserved or {}
    as_of = sales._parse_bill_date(bill_date)
    order, oparams = order_by_sql("name", q)
    where, params = ["COALESCE(is_hidden,0)=0", medicine_existed_sql()], [as_of.isoformat()]
    if q:
        where.append("name LIKE ?")
        params.append(f"%{q}%")
    rows = conn.execute(
        f"SELECT id, name, COALESCE(expiry_date,''), COALESCE(stock_qty,0) FROM medicines "
        f"WHERE {' AND '.join(where)} ORDER BY {order}, id", params + oparams).fetchall()
    out: dict[str, float] = {}
    counts: dict[str, int] = {}
    for mid, name, exp, stock in rows:
        name = (name or "").strip()
        if not name or (exp and is_expired_as_of(exp, as_of)):
            continue
        avail = max(0.0, float(stock) - float(reserved.get(str(mid), 0)))
        if avail <= 0:
            continue
        if name not in out and len(out) >= limit:
            continue
        out[name] = out.get(name, 0.0) + avail
        counts[name] = counts.get(name, 0) + 1
    return [(n, out[n], counts[n]) for n in out]


def _answer(conn, q, **kw):
    return [(n["name"], float(n["stock"]), n["batch_count"])
            for n in sales.list_medicine_names(conn, q, bill_date=DAY, **kw)["names"]]


class TheMedicineListOnABigStore(unittest.TestCase):
    def setUp(self):
        self.conn = _plain_store()
        ensure_perf_indexes(self.conn)

    def test_empty_batches_do_not_crowd_out_the_ones_in_stock(self):
        # 400 empty PAN batches sort before the ones on the shelf.
        for i in range(400):
            _add(self.conn, f"PAN {i:03d} EMPTY", 0)
        for name in ("PAN 40", "PANTOP D", "PANTOCID", "NORTIPAN"):
            _add(self.conn, name, 10)
            _add(self.conn, name, 5)
        got = _answer(self.conn, "pan")
        self.assertEqual([g[0] for g in got], ["PAN 40", "PANTOCID", "PANTOP D", "NORTIPAN"])
        self.assertTrue(all(g[1] == 15.0 and g[2] == 2 for g in got), got)

    def test_best_match_first_not_a_to_z(self):
        for name in ("ABD PAD", "PACIMOL 500", "BONNISPAZ", "PAN 40"):
            _add(self.conn, name, 3)
        self.assertEqual([g[0] for g in _answer(self.conn, "pa")],
                         ["PACIMOL 500", "PAN 40", "ABD PAD", "BONNISPAZ"])

    def test_a_name_with_many_batches_shows_all_its_stock(self):
        for i in range(500):
            _add(self.conn, "DOLO 650", 1)
        _add(self.conn, "DOLONEX", 2)
        self.assertEqual(_answer(self.conn, "dolo", limit=2),
                         [("DOLO 650", 500.0, 500), ("DOLONEX", 2.0, 1)])

    def test_same_names_and_stock_as_a_full_read_of_the_store(self):
        rnd = random.Random(11)
        words = ["PARA", "PAN", "DOLO", "AMOXY", "M-CAL", "B/COMPLEX", "VIT (M)", "CALPOL", "ZINC",
                 "OMEZ", "PA_X", "50%OFF", "mecovet", "Pantop"]
        for i in range(3000):
            name = f"{rnd.choice(words)} {rnd.choice(['', 'TAB', 'SYRUP', 'M', str(rnd.randint(1, 900))])}".strip()
            stock = rnd.choice([0, 0, 0, 1, 5, 20, -2])
            expiry = rnd.choice(["2028-12-31", "2026-01-31", "", "12/27"])
            _add(self.conn, name, stock, expiry=expiry, hidden=1 if rnd.random() < 0.05 else 0,
                 created=rnd.choice(["2026-01-01 00:00:00", "2026-12-01 00:00:00", ""]))
        some = [r[0] for r in self.conn.execute("SELECT id FROM medicines WHERE stock_qty > 0 LIMIT 30")]
        reserved = {str(mid): 1 for mid in some}
        for q in ["", "p", "pa", "PA", "pan", "m", "ca", "(m", "/c", "-c", "pa_", "50%", "zz", "a", "9"]:
            for limit in (5, 40):
                self.assertEqual(_answer(self.conn, q, limit=limit, reserved=reserved),
                                 _reference_names(self.conn, q, limit=limit, reserved=reserved),
                                 (q, limit))

    def test_a_big_store_answers_in_well_under_a_second(self):
        rnd = random.Random(3)
        rows = [(f"MED{rnd.randint(0, 20000):05d} {rnd.choice(['TAB', 'SYP', 'CAP'])}",
                 rnd.choice([0, 0, 0, 0, 7]), 10.0, 7.0, "10", "Tablet", "B", "2028-12-31", 0,
                 "2026-01-01") for _ in range(60000)]
        self.conn.executemany(
            "INSERT INTO medicines (name, stock_qty, mrp, rate, unit, type, batch_no, expiry_date, "
            "is_hidden, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        self.conn.execute("INSERT INTO purchases (id, purchase_date) VALUES (1, '2026-02-01')")
        self.conn.executemany("INSERT INTO purchase_items (purchase_id, medicine_id, qty) VALUES (1, ?, 1)",
                              [(i,) for i in range(1, 60000, 3)])
        for q in ("", "m", "med1", "tab", "zzz"):
            t = time.perf_counter()
            sales.list_medicine_names(self.conn, q, bill_date=DAY)
            self.assertLess(time.perf_counter() - t, 1.0, q)   # 25 s before the indexes


class TheIndexes(unittest.TestCase):
    def test_made_once_and_only_where_the_columns_are(self):
        conn = _plain_store()
        # db_setup.initialise makes them already (a new or copied store is quick from its first
        # open); the engine's own call then finds nothing left to make.
        self.assertEqual(ensure_perf_indexes(conn), [])
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertIn("idx_purchase_items_medicine", have)
        self.assertIn("idx_sales_items_sale", have)
        self.assertIn("idx_medicines_instock_name", have)
        bare = sqlite3.connect(":memory:")
        bare.execute("CREATE TABLE medicines (id INTEGER PRIMARY KEY, name TEXT)")
        made = ensure_perf_indexes(bare)
        # (and the pages' exact-name lookup, core/perf_indexes.LOOKUP_INDEXES)
        self.assertEqual(set(made), {"idx_medicines_name_nocase", "idx_medicines_name_upper",
                                     "ix_perf_medicines_name"})


def _old_set_stock(conn, server_stock):
    """set_stock_from_server as it was: one conditional UPDATE per batch of the store."""
    pending = pull.pending_stock_by_medicine(conn)
    n = 0
    for row in server_stock or []:
        try:
            mid, qty, hidden = int(row[0]), int(row[1]), int(row[2] or 0)
        except (TypeError, ValueError, IndexError):
            continue
        local = qty + pending.get(mid, 0)
        n += conn.execute(
            "UPDATE medicines SET stock_qty=?, is_hidden=? WHERE id=? AND "
            "(COALESCE(stock_qty,0) <> ? OR COALESCE(is_hidden,0) <> ?)",
            (local, hidden, mid, local, hidden)).rowcount or 0
    return n


class TheFullStockRefresh(unittest.TestCase):
    def _store(self, seed):
        conn = _plain_store()
        schema.ensure_schema(conn)
        rnd = random.Random(seed)
        for i in range(1, 1501):
            conn.execute("INSERT INTO medicines (id, name, stock_qty, is_hidden) VALUES (?,?,?,?)",
                         (i, f"M{i}", rnd.choice([0, 3, 7, None, 2.0, "4"]), rnd.choice([0, 1, None])))
        for i in range(1, 60):     # this PC's own unsent sales
            conn.execute("INSERT INTO of_stock_journal (medicine_id, delta, at) VALUES (?, ?, '')",
                         (rnd.randint(1, 1500), -rnd.randint(1, 3)))
        conn.commit()
        figures = [[rnd.randint(1, 1600), rnd.choice([0, 3, 7, 9]), rnd.choice([0, 1, None])]
                   for _ in range(2500)]
        figures += [["x", 1, 0], [5], [7, "8", "1"]]
        return conn, figures

    def test_same_stock_as_one_update_per_batch(self):
        for seed in (1, 2, 3):
            a, figures = self._store(seed)
            b, _ = self._store(seed)
            with schema.capture_paused(a), schema.capture_paused(b):
                n_new = pull.set_stock_from_server(a, figures)
                n_old = _old_set_stock(b, figures)
            self.assertEqual(n_new, n_old, seed)
            q = "SELECT id, stock_qty, typeof(stock_qty), is_hidden FROM medicines ORDER BY id"
            self.assertEqual(a.execute(q).fetchall(), b.execute(q).fetchall(), seed)

    def test_the_worker_writes_the_whole_store_a_chunk_at_a_time(self):
        from core.offline_first import worker

        path = _make_store()
        self.addCleanup(lambda: [os.path.exists(path + s) and os.remove(path + s) for s in ("", "-wal", "-shm")])
        conn = worker.open_worker_connection(path)
        self.addCleanup(conn.close)
        conn.execute("BEGIN")
        conn.executemany("INSERT INTO medicines (id, name, stock_qty) VALUES (?, ?, 0)",
                         [(i, f"M{i}") for i in range(1, 12001)])
        conn.execute("COMMIT")
        cycle = worker.SyncCycle(path)
        cycle.conn = conn
        batches = []
        real = worker.write_batch

        def counting(c, **kw):
            batches.append(1)
            return real(c, **kw)

        figures = [[i, 5, 0] for i in range(1, 12001)]
        with mock.patch.object(worker, "write_batch", counting), \
                mock.patch.object(worker.client, "changes", return_value={"changes": []}), \
                mock.patch.object(worker.client, "stock", return_value={"stock": figures}):
            cycle.pull()
        self.assertEqual(len(batches), 3)          # 12,000 batches, 5,000 per write
        self.assertEqual(conn.execute("SELECT SUM(stock_qty) FROM medicines").fetchone()[0], 60000)


class _PrintBase(unittest.TestCase):
    def setUp(self):
        self.path = _make_store()
        self.conn = _till(self.path)
        self.save_dir = tempfile.mkdtemp(prefix="perf_bills_")
        self.printed: list[str] = []
        self.dialogs: list[str] = []
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.addCleanup(self._cleanup)
        self.silent = True
        for p in (
            mock.patch.object(sync_prefs, "get_sync_mode", return_value=sync_prefs.MODE_OFFLINE_FIRST),
            mock.patch("core.store_manager.get_active_db_path", return_value=self.path),
            mock.patch("core.bill_save_prefs.resolve_sales_bill_save_dir", return_value=self.save_dir),
            mock.patch.object(sales, "_next_hint", return_value=""),
            mock.patch.object(sales, "_remember_doctor_online"),
            mock.patch.object(bill_output, "_is_dot_matrix_printer_mode", return_value=False),
            mock.patch.object(bill_output, "_try_pdf_via_browser", side_effect=self._browser),
            mock.patch.object(bill_output, "_open_system_print_dialog",
                              side_effect=lambda pdf, **k: self.dialogs.append(pdf)),
            mock.patch.object(PrinterManager, "load_settings",
                              classmethod(lambda cls: {"silent_print_enabled": self.silent})),
            mock.patch.object(PrinterManager, "get_printer_for_slot", classmethod(lambda cls, slot: "P")),
            mock.patch.object(PrinterManager, "print_pdf_silently",
                              classmethod(lambda cls, pdf, printer=None, copies=1: self.printed.append(pdf))),
        ):
            stack.enter_context(p)
        self.mid = _medicine(self.conn, "DOLO 650", 30.0)

    def _browser(self, html_path, pdf_path, **_kw):
        with open(pdf_path, "wb") as fh:
            fh.write(b"%PDF-1.4 " + b"x" * 700)
        return True

    def _cleanup(self):
        _join_pdf_threads()
        try:
            self.conn.close()
        except Exception:
            pass
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    def f7(self, **extra):
        body = {"items": [_item(self.mid, "DOLO 650", 2, 30.0)], "customer_name": "",
                "payment_mode": "Cash", "pay_full": True, "bill_date": DAY, "discount_rs": 0,
                "slot": 1, "mode": "slot", "save_first": True, **extra}
        return sales.print_sale(self.conn, body)


class SaveAndPrintDoesNotWaitForThePrinter(_PrintBase):
    def test_the_save_answers_first_and_the_second_call_prints(self):
        res = self.f7(defer_print=True)
        self.assertTrue(res.get("ok") and res.get("deferred"), res)
        self.assertTrue(res["bill_no"].startswith("SCB"), res)
        self.assertEqual(self.printed, [])                 # nothing printed yet
        _join_pdf_threads()
        pdfs = [f for _d, _s, fs in os.walk(self.save_dir) for f in fs if f.endswith(".pdf")]
        self.assertEqual(pdfs, [])                         # nor a PDF: the print makes it
        out = sales.print_sale(self.conn, {"sale_id": res["sale_id"], "bill_no": res["bill_no"],
                                           "slot": 1, "mode": "slot", "save_first": False,
                                           "just_saved": True})
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(len(self.printed), 1)
        self.assertEqual(out["bill_no"], res["bill_no"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 1)

    def test_a_dot_matrix_slip_printed_later_still_leaves_the_bill_pdf(self):
        renders = []
        with mock.patch.object(bill_output, "_is_dot_matrix_printer_mode", return_value=True), \
                mock.patch.object(bill_output, "_print_bill_dot_matrix_with_slot",
                                  side_effect=lambda *a, **k: ("", None)), \
                mock.patch.object(sales, "_try_save_sale_pdf", side_effect=lambda c, sid: renders.append(sid)):
            res = self.f7(defer_print=True)
            self.assertTrue(res.get("deferred") and res.get("dot_matrix"), res)
            sales.print_sale(self.conn, {"sale_id": res["sale_id"], "slot": 1, "mode": "slot",
                                         "save_first": False, "just_saved": True})
            _join_pdf_threads()
        self.assertEqual(renders, [res["sale_id"]])

    def test_a_print_dialog_is_still_shown_before_the_answer(self):
        self.silent = False
        res = self.f7(defer_print=True)
        self.assertTrue(res.get("ok"), res)
        self.assertFalse(res.get("deferred"), res)
        self.assertEqual(len(self.dialogs), 1)

    def test_without_the_flag_nothing_changes(self):
        res = self.f7()
        self.assertTrue(res.get("ok"), res)
        self.assertFalse(res.get("deferred"))
        self.assertEqual(len(self.printed), 1)


class AFailedSaveLeavesNothingBehind(_PrintBase):
    def test_half_a_bill_is_rolled_back_before_another_request_can_commit_it(self):
        from core import billing_service

        seen = {}

        def half_then_fail(*a, **k):
            self.conn.execute("INSERT INTO sales (bill_no, bill_date, total_amount) VALUES ('HALF', ?, 1)", (DAY,))
            # A print (or tick) arriving now must wait for the lock, and then find nothing to commit.
            t = threading.Thread(target=lambda: seen.setdefault(
                "after", sales.print_sale(self.conn, {"sale_id": 999, "slot": 1, "save_first": False})))
            t.start()
            seen["thread"] = t
            time.sleep(0.2)
            raise RuntimeError("disk full")

        with mock.patch.object(billing_service, "save_new_bill", side_effect=half_then_fail):
            res = sales.save_sale(self.conn, {"items": [_item(self.mid, "DOLO 650", 1, 30.0)],
                                              "customer_name": "RAM", "payment_mode": "Cash",
                                              "pay_full": True, "bill_date": DAY})
        seen["thread"].join(10)
        self.assertFalse(res.get("ok"))
        other = sqlite3.connect(self.path)
        self.assertEqual(other.execute("SELECT COUNT(*) FROM sales WHERE bill_no='HALF'").fetchone()[0], 0)
        other.close()


class OneReadPerRequest(unittest.TestCase):
    def test_a_bill_line_reads_the_settings_without_opening_the_store_again(self):
        path = _make_store()
        self.addCleanup(lambda: [os.path.exists(path + s) and os.remove(path + s) for s in ("", "-wal", "-shm")])
        conn = _till(path)
        self.addCleanup(conn.close)
        mid = _medicine(conn, "DOLO 650", 30.0)
        from core import desktop_api, store_manager

        opens = []
        real = sqlite3.connect
        with mock.patch.object(sync_prefs, "get_sync_mode", return_value=sync_prefs.MODE_OFFLINE_FIRST), \
                mock.patch.object(store_manager, "get_active_db_path", return_value=path), \
                mock.patch.dict(desktop_api._db, {"conn": conn, "path": path}):
            sales.build_line(conn, {"medicine_id": mid, "qty": 1, "bill_date": DAY})   # first use migrates
            with mock.patch("sqlite3.connect", side_effect=lambda *a, **k: opens.append(a) or real(*a, **k)):
                for _ in range(5):
                    r = sales.build_line(conn, {"medicine_id": mid, "qty": 1, "bill_date": DAY})
                    self.assertTrue(r.get("ok"), r)
        self.assertEqual(opens, [])

    def test_the_registry_is_read_once_per_request_and_again_after_a_save(self):
        from core import request_memo, store_manager

        reads = []
        real = store_manager._load_registry_from_disk
        with mock.patch.object(store_manager, "_load_registry_from_disk",
                               side_effect=lambda **k: reads.append(1) or real(**k)):
            with request_memo.one_read():
                a = store_manager.load_registry()
                a["mutated"] = True
                b = store_manager.load_registry()
                self.assertNotIn("mutated", b)            # each caller gets its own copy
                self.assertEqual(len(reads), 1)
                request_memo.forget()
                store_manager.load_registry()
                self.assertEqual(len(reads), 2)
            store_manager.load_registry()
            store_manager.load_registry()
            self.assertEqual(len(reads), 4)               # outside a request: as before


class TheBillNumber(unittest.TestCase):
    def test_a_block_number_does_not_read_every_bill_of_the_year(self):
        from core import billing_service
        from core.offline_first import numbers

        conn = _plain_store()
        schema.ensure_schema(conn)
        numbers.add_block(conn, "sales", 2026, 501, 700)
        with mock.patch.object(sync_prefs, "get_sync_mode", return_value=sync_prefs.MODE_OFFLINE_FIRST), \
                mock.patch("core.fy_serial.allocate_bill_number", side_effect=AssertionError("read all bills")):
            self.assertEqual(billing_service._allocate_bill_number(conn, "SCB", bill_date=DAY),
                             "SCB501/FY2026-27")
        numbers.drop_taken(conn, 2026, {"sales": {"ranges": [{"to": 700, "taken": 700}]}})
        with mock.patch.object(sync_prefs, "get_sync_mode", return_value=sync_prefs.MODE_OFFLINE_FIRST):
            # Blocks used up: the year's highest number + 1, as before.
            self.assertEqual(billing_service._allocate_bill_number(conn, "SCB", bill_date=DAY),
                             "SCB1/FY2026-27")


if __name__ == "__main__":
    unittest.main()
