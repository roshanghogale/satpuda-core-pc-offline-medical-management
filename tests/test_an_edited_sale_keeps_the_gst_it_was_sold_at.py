"""Editing a sale keeps the GST % each line was sold at.

Every edit loader read the medicine's rate TODAY -- offline COALESCE(m.gst_percent, 0),
online `it.get("gst_percent") or <medicine's rate>` -- and saving the edit writes the
loaded lines back, so after a rate change editing an old bill rewrote its GST. That
undid the reprint fix (tests/test_a_reprint_keeps_the_gst_it_was_sold_at.py).

Online was worse. Neither the create push nor the edit push sent gst_percent, and the
server re-inserts a sale's lines with `it.gst_percent ?? null`, so a bill made or edited
on the PC never kept its rate at all. On the Tauri Sales page a qty edit rebuilt the
line through build_line, which priced it at today's rate once more.

Here the lines were sold at 5% and 0% (exempt) while the medicine master now says 12%;
a line that never stored a rate still falls back to the medicine. Everything runs on an
in-memory database or patched server documents.
"""
from __future__ import annotations

import importlib
import sqlite3
import unittest
from contextlib import ExitStack
from datetime import date
from unittest import mock

from core import billing_service, db_setup, desktop_sales_service, server_crud  # noqa: E402

# Sold at 5%, sold at 0%, and a line that never stored a rate.
SOLD = {1: 5.0, 2: 0.0, 3: 12.0}

MASTER = {
    mid: {"id": mid, "local_id": mid, "name": name, "type": "SYRUP", "unit": "1",
          "gst_percent": 12.0, "mrp": mrp, "rate": 60.0, "stock_qty": 50}
    for mid, name, mrp in ((1, "CETIRIZINE SYRUP", 105.0), (2, "BANDAGE ROLL", 50.0),
                           (3, "ORS SACHET", 105.0))
}


def _gst_by_medicine(lines, key="id") -> dict:
    return {int(line[key]): line["gst_percent"] for line in lines}


def _offline_shop() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    db_setup.initialise(conn)
    conn.execute("INSERT INTO customers (id, name) VALUES (1, 'RAM')")
    conn.executemany(
        "INSERT INTO medicines (id, name, type, unit, gst_percent, mrp, rate, stock_qty, "
        "batch_no, expiry_date, created_at) VALUES (?, ?, 'SYRUP', '1', 12, ?, 60, 50, "
        "'B1', '2028-12-31', '2026-01-01 00:00:00')",
        [(m["id"], m["name"], m["mrp"]) for m in MASTER.values()],
    )
    conn.execute(
        "INSERT INTO sales (id, bill_no, customer_id, bill_date, total_amount, discount, "
        "amount_paid, cash_paid) VALUES (1, 'SCB1', 1, '2026-09-13', 260, 0, 260, 260)"
    )
    conn.executemany(
        "INSERT INTO sales_items (sale_id, medicine_id, qty, rate, gst_percent, amount) "
        "VALUES (1, ?, 1, ?, ?, ?)",
        [(1, 105.0, 5, 105.0), (2, 50.0, 0, 50.0), (3, 105.0, None, 105.0)],
    )
    conn.commit()
    return conn


class AnOfflineEdit(unittest.TestCase):
    def setUp(self):
        self.conn = _offline_shop()

    def tearDown(self):
        self.conn.close()

    def test_the_billing_loader_reads_the_rate_sold(self):
        # Classic's main-screen edit (core.sales_form_io) and Tauri both load through this.
        lines = billing_service.load_sale_medicines(self.conn, 1)
        self.assertEqual(_gst_by_medicine(lines), SOLD)

    def test_the_tauri_edit_form_carries_it(self):
        data = desktop_sales_service.load_sale(self.conn, 1)
        self.assertTrue(data.get("ok"), data)
        self.assertEqual(_gst_by_medicine(data["form"]["items"]), SOLD)

    def test_saving_the_edit_keeps_it(self):
        lines = billing_service.load_sale_medicines(self.conn, 1)
        with mock.patch("core.online_guard.ensure_can_mutate", return_value=None):
            billing_service.update_existing_bill(
                self.conn, 1, lines, 0, 0, 260.0, 0, "RAM", "", "", 0,
                bill_date="2026-09-13",
            )
        stored = dict(self.conn.execute(
            "SELECT medicine_id, gst_percent FROM sales_items WHERE sale_id=1"
        ).fetchall())
        self.assertEqual(stored, SOLD)

    def test_the_classic_edit_windows_read_it(self):
        try:
            import tkinter  # noqa: F401
        except ImportError:
            self.skipTest("tkinter is not installed")
        for module in ("widgets.bill_edit", "ui.billing.bill_edit"):
            with self.subTest(module=module):
                page = object.__new__(importlib.import_module(module).BillEditPage)
                page.conn = self.conn
                page.cursor = self.conn.cursor()
                page.sale_id = 1
                page.selected_medicines = []
                page.previous_due = 0
                page._load_sale_data()
                self.assertEqual(_gst_by_medicine(page.selected_medicines), SOLD)

    def test_a_qty_edit_on_the_same_batch_keeps_it(self):
        def rebuilt(**extra):
            body = {"medicine_id": 1, "qty": 2, "bill_date": "2026-09-13", **extra}
            res = desktop_sales_service.build_line(self.conn, body)
            self.assertTrue(res.get("ok"), res)
            return res["line"]["gst_percent"]

        self.assertEqual(rebuilt(gst_percent=5), 5.0)
        self.assertEqual(rebuilt(gst_percent=0), 0.0)
        # A new line, or a line moved to another batch, is priced at today's rate.
        self.assertEqual(rebuilt(), 12.0)


class AnOfflineLineWithNoRateAnywhere(unittest.TestCase):
    """Offline the same line stays NULL: COALESCE(..., 0) made the edit store an exempt 0%."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        db_setup.initialise(self.conn)
        self.conn.execute("INSERT INTO customers (id, name) VALUES (1, 'RAM')")
        self.conn.execute(
            "INSERT INTO medicines (id, name, type, unit, gst_percent, mrp, rate, stock_qty, "
            "batch_no, expiry_date, created_at) VALUES (9, 'COTTON ROLL', 'OTHER', '1', NULL, "
            "30, 20, 10, 'B9', '2028-12-31', '2026-01-01 00:00:00')"
        )
        self.conn.execute(
            "INSERT INTO sales (id, bill_no, customer_id, bill_date, total_amount, discount, "
            "amount_paid, cash_paid) VALUES (1, 'SCB1', 1, '2026-09-13', 30, 0, 30, 30)"
        )
        self.conn.execute(
            "INSERT INTO sales_items (sale_id, medicine_id, qty, rate, gst_percent, amount) "
            "VALUES (1, 9, 1, 30.0, NULL, 30.0)"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_the_billing_loader_leaves_it_blank(self):
        self.assertIsNone(billing_service.load_sale_medicines(self.conn, 1)[0]["gst_percent"])

    def test_saving_the_edit_keeps_it_blank(self):
        lines = billing_service.load_sale_medicines(self.conn, 1)
        with mock.patch("core.online_guard.ensure_can_mutate", return_value=None):
            billing_service.update_existing_bill(
                self.conn, 1, lines, 0, 0, 30.0, 0, "RAM", "", "", 0,
                bill_date="2026-09-13",
            )
        stored = self.conn.execute(
            "SELECT gst_percent FROM sales_items WHERE sale_id=1"
        ).fetchone()
        self.assertIsNone(stored[0])

    def test_the_tauri_form_and_its_save_keep_it_blank(self):
        data = desktop_sales_service.load_sale(self.conn, 1)
        self.assertTrue(data.get("ok"), data)
        items = data["form"]["items"]
        self.assertIsNone(items[0]["gst_percent"])
        saved = desktop_sales_service._normalize_medicines_for_save(items)
        self.assertIsNone(saved[0]["gst_percent"])

    def test_the_classic_edit_windows_leave_it_blank(self):
        try:
            import tkinter  # noqa: F401
        except ImportError:
            self.skipTest("tkinter is not installed")
        for module in ("widgets.bill_edit", "ui.billing.bill_edit"):
            with self.subTest(module=module):
                page = object.__new__(importlib.import_module(module).BillEditPage)
                page.conn = self.conn
                page.cursor = self.conn.cursor()
                page.sale_id = 1
                page.selected_medicines = []
                page.previous_due = 0
                page._load_sale_data()
                self.assertIsNone(page.selected_medicines[0]["gst_percent"])


def _server_sale() -> dict:
    return {
        "id": 42, "local_id": 42, "client_uuid": "cu-42", "version": 3,
        "bill_no": "SCB42", "bill_date": "2026-09-13", "customer_id": 0,
        "total_amount": 260.0, "amount_paid": 260.0, "cash_paid": 260.0,
        "items": [
            {"medicine_id": 1, "medicine_name": "CETIRIZINE SYRUP", "qty": 1, "rate": 105.0,
             "amount": 105.0, "gst_percent": 5},
            {"medicine_id": 2, "medicine_name": "BANDAGE ROLL", "qty": 1, "rate": 50.0,
             "amount": 50.0, "gst_percent": 0},
            {"medicine_id": 3, "medicine_name": "ORS SACHET", "qty": 1, "rate": 105.0,
             "amount": 105.0, "gst_percent": None},
        ],
    }


class AnOnlineEdit(unittest.TestCase):
    """Online the engine's SQLite is empty; the sale is the server's document."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")  # no schema: nothing may read it
        self.pushed: list[dict] = []
        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.server_crud.get_doc", side_effect=lambda coll, i: (
                _server_sale() if coll == "sales" else dict(MASTER.get(int(i), {})))),
            mock.patch("core.server_crud.push_bundle",
                       side_effect=lambda bundle: self.pushed.append(bundle) or True),
            mock.patch("core.online_catalog.medicine_by_id",
                       side_effect=lambda i: dict(MASTER.get(int(i), {}))),
            mock.patch("core.online_catalog.find_customer_by_id",
                       return_value={"id": 7, "name": "RAM", "total_due": 0, "total_credit": 0}),
            mock.patch("core.online_catalog.patch_docs", return_value=None),
        ):
            stack.enter_context(patch)

    def tearDown(self):
        self.conn.close()

    def test_the_billing_loader_reads_the_rate_sold(self):
        lines = billing_service.load_sale_medicines(self.conn, 42)
        self.assertEqual(_gst_by_medicine(lines), SOLD)

    def test_the_tauri_edit_form_carries_it(self):
        with mock.patch.object(desktop_sales_service, "_returns_on_edit", return_value={}):
            data = desktop_sales_service.load_sale(self.conn, 42)
        self.assertTrue(data.get("ok"), data)
        self.assertEqual(_gst_by_medicine(data["form"]["items"]), SOLD)

    def test_saving_the_edit_sends_it_to_the_server(self):
        lines = billing_service.load_sale_medicines(self.conn, 42)
        billing_service.update_existing_bill_online_now(
            sale_id=42, medicines=lines, discount_pct=0, rounding=0, cash_paid=260.0,
            online_paid=0, customer_name="RAM", customer_phone="", doctor_name="",
            previous_due=0, bill_date=date(2026, 9, 13),
        )
        self.assertTrue(self.pushed, "the edit was never pushed")
        items = self.pushed[-1]["sales"][0]["items"]
        self.assertEqual(_gst_by_medicine(items, key="medicine_id"), SOLD)

    def test_a_new_online_bill_sends_the_rate_too(self):
        cart = [
            {"id": 1, "name": "CETIRIZINE SYRUP", "qty": 1, "rate": 105.0, "amount": 105.0,
             "gst_percent": 5.0},
            {"id": 2, "name": "BANDAGE ROLL", "qty": 1, "rate": 50.0, "amount": 50.0,
             "gst_percent": 0.0},
            {"id": 3, "name": "ORS SACHET", "qty": 1, "rate": 105.0, "amount": 105.0},
        ]
        with mock.patch.object(server_crud, "allocate_ids_map", return_value={"sales": 5001}), \
                mock.patch.object(server_crud, "allocate_id", return_value=5001), \
                mock.patch.object(server_crud, "_token", return_value="t"), \
                mock.patch("core.online_guard.ensure_can_mutate", return_value=None), \
                mock.patch("core.server_api.allocate_fy",
                           return_value={"fy_start_year": 2026, "fy_serial": 43,
                                         "bill_no": "SCB43/FY2026-27"}):
            server_crud.save_new_sale_online(
                customer_id=7, medicines=cart, discount_pct=0, rounding=0, cash_paid=260.0,
                online_paid=0, doctor_name="", doctor_phone="", previous_due=0,
                bill_date=date(2026, 9, 13),
            )
        self.assertTrue(self.pushed, "the sale was never pushed")
        items = self.pushed[-1]["sales"][0]["items"]
        self.assertEqual(_gst_by_medicine(items, key="medicine_id"), SOLD)


def _no_rate_sale() -> dict:
    return {
        "id": 43, "local_id": 43, "client_uuid": "cu-43", "version": 1,
        "bill_no": "SCB43", "bill_date": "2026-09-13", "customer_id": 0,
        "total_amount": 30.0, "amount_paid": 30.0, "cash_paid": 30.0,
        "items": [
            {"medicine_id": 9, "medicine_name": "COTTON ROLL", "qty": 1, "rate": 30.0,
             "amount": 30.0, "gst_percent": None},
        ],
    }


# A medicine whose master has no GST rate either.
COTTON = {"id": 9, "local_id": 9, "name": "COTTON ROLL", "type": "OTHER", "unit": "1",
          "mrp": 30.0, "rate": 20.0, "stock_qty": 10}


class AnOnlineLineWithNoRateAnywhere(unittest.TestCase):
    """A line with no stored rate, of a medicine with none, stays NULL through an edit.

    The loader turned it into 0.0 and the edit pushed that: an exempt 0% nobody sold it at.
    """

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")  # no schema: nothing may read it
        self.pushed: list[dict] = []
        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.server_crud.get_doc", side_effect=lambda coll, i: (
                _no_rate_sale() if coll == "sales" else dict(COTTON))),
            mock.patch("core.server_crud.push_bundle",
                       side_effect=lambda bundle: self.pushed.append(bundle) or True),
            mock.patch("core.online_catalog.medicine_by_id",
                       side_effect=lambda i: dict(COTTON)),
            mock.patch("core.online_catalog.find_customer_by_id", return_value={}),
            mock.patch("core.online_catalog.patch_docs", return_value=None),
        ):
            stack.enter_context(patch)

    def tearDown(self):
        self.conn.close()

    def test_the_billing_loader_leaves_it_blank(self):
        lines = billing_service.load_sale_medicines(self.conn, 43)
        self.assertIsNone(lines[0]["gst_percent"])

    def test_saving_the_edit_sends_it_blank(self):
        lines = billing_service.load_sale_medicines(self.conn, 43)
        billing_service.update_existing_bill_online_now(
            sale_id=43, medicines=lines, discount_pct=0, rounding=0, cash_paid=30.0,
            online_paid=0, customer_name="RAM", customer_phone="", doctor_name="",
            previous_due=0, bill_date=date(2026, 9, 13),
        )
        self.assertTrue(self.pushed, "the edit was never pushed")
        line = self.pushed[-1]["sales"][0]["items"][0]
        self.assertIn("gst_percent", line)
        self.assertIsNone(line["gst_percent"])


class TheLineRateRule(unittest.TestCase):
    def test_zero_is_a_rate_and_blank_is_not(self):
        master = {"gst_percent": 12}
        self.assertEqual(server_crud._line_gst_percent({"gst_percent": 0}, master), 0.0)
        self.assertEqual(server_crud._line_gst_percent({"gst_percent": "5"}, master), 5.0)
        self.assertEqual(server_crud._line_gst_percent({"gst_percent": ""}, master), 12.0)
        self.assertEqual(server_crud._line_gst_percent({}, master), 12.0)
        self.assertIsNone(server_crud._line_gst_percent({}, {}))


if __name__ == "__main__":
    unittest.main()
