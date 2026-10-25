"""Offline-first: Print Sales 1/2 right after a save prints the bill, not a blank page.

F7 / F8 save the bill first and then print it. The save started a background thread that
wrote Bill_<no>.pdf into the day's folder, and the print wrote the SAME file from the same
HTML at the same moment: whichever finished first deleted the HTML the other's browser was
about to load (a blank page printed) or replaced the PDF the other was about to hand to the
printer ("PDF not found"). On an Online PC the two were a server round-trip apart and seldom
met; on offline-first both read the local copy within a millisecond and met every time
(owner, 25 Oct 2026). Sales History printed fine: nothing else was writing the file.

Now a save that is about to be printed leaves the PDF to the print, and two renders of one
bill file never overlap. Everything runs on a temp offline-first store; no browser, no
printer, no server.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from unittest import mock

from core import bill_output, db_setup, desktop_sales_service, sync_prefs
from core.offline_first import ids, schema
from core.offline_first.schema import meta_set
from core.printer_manager import PdfNotFoundError, PrinterManager

DEVICE = 3
BASE = DEVICE * 1_000_000_000
DAY = "2026-10-25"


def _make_store() -> str:
    fd, path = tempfile.mkstemp(suffix=".db", prefix="of_print_")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    db_setup.initialise(conn)
    conn.commit()
    schema.ensure_schema(conn)
    meta_set(conn, "device_no", DEVICE)
    meta_set(conn, "id_base", BASE)
    meta_set(conn, "id_max", BASE + 999_999_999)
    meta_set(conn, "install_id", "pc-test-install")
    conn.execute("INSERT INTO pharmacy_profile (name) VALUES ('PRINT TEST SHOP')")
    conn.commit()
    conn.close()
    return path


def _till(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30, factory=ids.OfConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _medicine(conn, name, rate, stock=100):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(medicines)")}
    vals = {"name": name, "stock_qty": stock, "mrp": rate, "rate": rate, "unit": "10",
            "type": "Tablet", "batch_no": "B1", "expiry_date": "2028-12-01",
            "created_at": "2026-01-01 00:00:00"}
    use = {k: v for k, v in vals.items() if k in cols}
    mid = conn.execute(
        f"INSERT INTO medicines ({','.join(use)}) VALUES ({','.join('?' * len(use))})",
        list(use.values()),
    ).lastrowid
    conn.commit()
    return mid


def _item(mid, name, qty, rate):
    return {"id": mid, "name": name, "qty": qty, "rate": rate, "amount": round(qty * rate, 2),
            "medicine_discount": 0, "unit": "10", "type": "Tablet", "batch": "B1"}


def _join_pdf_threads(timeout=10.0):
    end = time.time() + timeout
    for t in list(threading.enumerate()):
        if t.name.startswith("sale-pdf-") and t is not threading.current_thread():
            t.join(max(0.0, end - time.time()))


class OfflineFirstSaveThenPrint(unittest.TestCase):
    # A browser takes about this long to make the PDF. The save's background render is the
    # quicker one, so without the lock it finished first and deleted the HTML the other
    # render's browser was still reading.
    RENDER_SECONDS = {"sale-pdf": 0.1, "other": 0.3}

    def setUp(self):
        self.path = _make_store()
        self.conn = _till(self.path)
        self.save_dir = tempfile.mkdtemp(prefix="of_bills_")
        self.renders: list[dict] = []        # every browser call: did the HTML still exist?
        self.printed: list[dict] = []        # every PDF handed to the printer
        self.dialogs: list[str] = []         # the dialog fallback (silent print gave up)
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.addCleanup(self._cleanup)
        for p in (
            mock.patch.object(sync_prefs, "get_sync_mode", return_value=sync_prefs.MODE_OFFLINE_FIRST),
            mock.patch("core.store_manager.get_active_db_path", return_value=self.path),
            mock.patch("core.bill_save_prefs.resolve_sales_bill_save_dir", return_value=self.save_dir),
            mock.patch.object(desktop_sales_service, "_next_hint", return_value=""),
            mock.patch.object(desktop_sales_service, "_remember_doctor_online"),
            mock.patch.object(bill_output, "_is_dot_matrix_printer_mode", return_value=False),
            mock.patch.object(bill_output, "_try_pdf_via_browser", side_effect=self._browser),
            mock.patch.object(bill_output, "_open_system_print_dialog", side_effect=self._dialog),
            mock.patch.object(PrinterManager, "load_settings", classmethod(lambda cls: {"silent_print_enabled": True})),
            mock.patch.object(PrinterManager, "get_printer_for_slot", classmethod(lambda cls, slot: "Test Printer")),
            mock.patch.object(PrinterManager, "print_pdf_silently", classmethod(self._printer)),
        ):
            stack.enter_context(p)
        self.mid = _medicine(self.conn, "DOLO 650", 30.0)

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

    # -- the browser, the printer, the dialog ---------------------------------------------
    def _browser(self, html_path, pdf_path, **_kw):
        name = threading.current_thread().name
        had_html = os.path.isfile(html_path)          # the browser opens the page...
        time.sleep(self.RENDER_SECONDS["sale-pdf" if name.startswith("sale-pdf-") else "other"])
        had_html = had_html and os.path.isfile(html_path)   # ...and is still reading it here
        self.renders.append({"html": html_path, "pdf": pdf_path, "had_html": had_html,
                             "thread": threading.current_thread().name})
        if not had_html:
            return False                     # a real browser prints its "file not found" page
        with open(pdf_path, "wb") as fh:
            fh.write(b"%PDF-1.4 " + b"x" * 700)
        return True

    def _printer(self, cls, pdf_path, printer_name=None, copies=1):
        exists = bool(pdf_path) and os.path.isfile(pdf_path)
        self.printed.append({"pdf": pdf_path, "exists": exists,
                             "size": os.path.getsize(pdf_path) if exists else 0})
        if not exists:
            raise PdfNotFoundError(f"PDF not found: {pdf_path}")

    def _dialog(self, pdf_path, **_kw):
        self.dialogs.append(pdf_path)

    # -- what the counter does ----------------------------------------------------------------
    def f7(self, slot=1):
        body = {"items": [_item(self.mid, "DOLO 650", 2, 30.0)], "customer_name": "",
                "payment_mode": "Cash", "pay_full": True, "bill_date": DAY, "discount_rs": 0,
                "slot": slot, "mode": "slot", "save_first": True}
        return desktop_sales_service.print_sale(self.conn, body)

    def test_print_sales_1_right_after_the_save_prints_the_bill(self):
        res = self.f7(slot=1)
        _join_pdf_threads()
        self.assertTrue(res.get("ok"), res)
        self.assertTrue(res.get("bill_no", "").startswith("SCB"), res)
        self.assertGreater(int(res.get("sale_id") or 0), BASE, res)
        # Every browser call found its HTML (no blank page), the printer got a PDF that was
        # there, and the silent print did not fall back to the dialog.
        self.assertTrue(all(r["had_html"] for r in self.renders), self.renders)
        self.assertEqual(len(self.printed), 1, self.printed)
        self.assertTrue(self.printed[0]["exists"] and self.printed[0]["size"] > 500, self.printed)
        self.assertEqual(self.dialogs, [])
        # The bill was rendered ONCE: the save left the PDF to the print that followed.
        self.assertEqual(len(self.renders), 1, self.renders)
        self.assertEqual(os.path.dirname(self.printed[0]["pdf"]),
                         os.path.join(self.save_dir, "Bills", DAY))
        self.assertTrue(os.path.isfile(res["pdf_path"]), res)

    def test_print_sales_2_right_after_the_save_prints_the_bill(self):
        res = self.f7(slot=2)
        _join_pdf_threads()
        self.assertTrue(res.get("ok"), res)
        self.assertTrue(all(r["had_html"] for r in self.renders), self.renders)
        self.assertEqual(len(self.printed), 1, self.printed)
        self.assertTrue(self.printed[0]["exists"], self.printed)
        self.assertEqual(self.dialogs, [])
        self.assertEqual(len(self.renders), 1, self.renders)

    def test_a_dot_matrix_slip_still_leaves_the_bill_pdf_after_the_print(self):
        order: list[str] = []

        def slip(conn, sale_id, slot, **_kw):
            order.append("slip")
            return "", None                      # RAW ESC/P: no file

        with mock.patch.object(bill_output, "_is_dot_matrix_printer_mode", return_value=True),              mock.patch.object(bill_output, "_print_bill_dot_matrix_with_slot", side_effect=slip):
            res = self.f7(slot=1)
            _join_pdf_threads()
        self.assertTrue(res.get("ok"), res)
        self.assertTrue(res.get("dot_matrix"), res)
        self.assertEqual(order, ["slip"])
        self.assertEqual(self.printed, [])
        # The PDF record was made once, after the slip had gone to the printer.
        self.assertEqual(len(self.renders), 1, self.renders)
        self.assertTrue(self.renders[0]["had_html"])
        self.assertTrue(os.path.isfile(self.renders[0]["pdf"]))

    def test_a_plain_save_still_writes_the_bill_pdf(self):
        body = {"items": [_item(self.mid, "DOLO 650", 1, 30.0)], "customer_name": "RAM",
                "payment_mode": "Cash", "pay_full": True, "bill_date": DAY, "discount_rs": 0}
        res = desktop_sales_service.save_sale(self.conn, body)
        _join_pdf_threads()
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(len(self.renders), 1, self.renders)
        self.assertTrue(self.renders[0]["had_html"])
        self.assertTrue(os.path.isfile(self.renders[0]["pdf"]))
        self.assertEqual(res["pdf_dir"], os.path.join(self.save_dir, "Bills", DAY))

    def test_two_renders_of_one_bill_file_never_overlap(self):
        """Reprint-last a moment after a save: whoever asks second waits for the first."""
        body = {"items": [_item(self.mid, "DOLO 650", 1, 30.0)], "customer_name": "RAM",
                "payment_mode": "Cash", "pay_full": True, "bill_date": DAY, "discount_rs": 0}
        saved = desktop_sales_service.save_sale(self.conn, body)
        self.assertTrue(saved.get("ok"), saved)
        # F9 while the save's PDF thread is still rendering.
        res = desktop_sales_service.print_sale(
            self.conn, {"sale_id": saved["sale_id"], "bill_no": saved["bill_no"], "slot": 1,
                        "mode": "silent", "save_first": False})
        _join_pdf_threads()
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(len(self.renders), 2, self.renders)
        self.assertTrue(all(r["had_html"] for r in self.renders), self.renders)
        self.assertEqual(len(self.printed), 1, self.printed)
        self.assertTrue(self.printed[0]["exists"], self.printed)
        self.assertTrue(os.path.isfile(self.printed[0]["pdf"]))


if __name__ == "__main__":
    unittest.main()
