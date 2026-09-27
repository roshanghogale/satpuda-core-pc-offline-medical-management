"""Save warnings from the store audit: they warn, the save goes ahead, ordinary saves are silent.

The 2026-09-13 audit found saves that were almost certainly typing mistakes: Rs 210 cash plus
Rs 210 online on a Rs 210 bill (SCB1061/FY2026-27); one supplier bill entered twice a second
apart (store 127, purchases 35 and 36); lines rated above MRP (Rs 96.84 on MRP Rs 34.01),
with MRP 0, expired on the purchase date (04/23 on a 2026-08-31 bill), without expiry or with
WITHOUT BATCH; Syrup lines on Tablet medicines; payments dated a year ahead. The PC now says
so at save. Nothing here refuses a save.

The one check that grew teeth is the duplicate supplier bill: a NEW purchase for a bill
number the supplier already has is refused before it is written, and the shop answers. The
warning text is what an edit or a deliberate "save anyway" still gets, and that is what is
pinned here -- tests/test_a_supplier_bill_is_not_saved_twice.py holds the refusal itself.

Offline cases use an in-memory store; Online cases patch the store query client. Nothing
reaches a server.
"""
from __future__ import annotations

import os
import sqlite3
import unittest
from datetime import date, timedelta
from unittest import mock

from core import db_setup, desktop_purchase_service, desktop_sales_service  # noqa: E402
from core import desktop_settings_service, save_warnings  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODAY = date.today()


def _ordinary_line(**changes):
    line = {"name": "PARA TAB", "type": "Tablet", "batch": "B1", "expiry": "12/28", "qty": 2,
            "rate": 60.0, "mrp": 100.0, "medicine_id": 20, "unit": "10"}
    line.update(changes)
    return line


class TheChecksThemselves(unittest.TestCase):
    def test_an_ordinary_sale_is_silent(self):
        self.assertEqual(save_warnings.sale_warnings(total=210, cash_paid=210, online_paid=0,
                                                     previous_due=0), [])
        self.assertEqual(save_warnings.sale_warnings(total=100, cash_paid=100, online_paid=50,
                                                     previous_due=50), [])

    def test_the_same_money_as_cash_and_online_is_named(self):
        [warning] = save_warnings.sale_warnings(total=210, cash_paid=210, online_paid=210,
                                                previous_due=0)
        self.assertIn("₹420.00", warning)
        self.assertIn("₹210.00 more", warning)

    def test_a_payment_dated_after_today(self):
        self.assertEqual(save_warnings.payment_warnings(TODAY.isoformat()), [])
        self.assertEqual(save_warnings.payment_warnings("not a date"), [])
        [warning] = save_warnings.payment_warnings("2026-11-20", today=date(2026, 7, 20))
        self.assertIn("2026-11-20", warning)

    def test_an_ordinary_purchase_line_is_silent(self):
        self.assertEqual(
            save_warnings.purchase_line_warnings([_ordinary_line()], "2026-08-31",
                                                 medicine_type=lambda it: "TABLET"),
            [],
        )

    def test_each_mistake_from_the_audit_is_named(self):
        lines = [
            _ordinary_line(name="EXPIRED CAP", expiry="04/23"),
            _ordinary_line(name="DEAR TAB", rate=96.84, mrp=34.01),
            _ordinary_line(name="NO MRP GEL", mrp=0),
            _ordinary_line(name="PLACEHOLDER TAB", batch="WITHOUT BATCH", expiry="WITHOUT EXP"),
            _ordinary_line(name="SYRUP LINE", type="Syrup"),
            _ordinary_line(name="BLANK EXPIRY", expiry=""),
        ]
        warnings = save_warnings.purchase_line_warnings(lines, "2026-08-31",
                                                        medicine_type=lambda it: "Tablet")
        text = "\n".join(warnings)
        for expected in ("Already expired on the purchase date: EXPIRED CAP (04/23)",
                         "Rate is above MRP on: DEAR TAB (rate ₹96.84, MRP ₹34.01)",
                         "MRP is 0 on: NO MRP GEL",
                         "No batch number (WITHOUT BATCH) on: PLACEHOLDER TAB",
                         "No expiry date on: PLACEHOLDER TAB, BLANK EXPIRY",
                         "SYRUP LINE (Syrup on this bill, Tablet in stock)"):
            self.assertIn(expected, text)


class _Shop(unittest.TestCase):
    def setUp(self):
        self.conn = conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        conn.execute("INSERT INTO suppliers (id, name) VALUES (1, 'SHREE PHARMA')")
        conn.execute("INSERT INTO suppliers (id, name) VALUES (2, 'OTHER AGENCY')")
        conn.execute("INSERT INTO customers (id, name) VALUES (7, 'RAM')")
        conn.execute(
            "INSERT INTO medicines (id, name, type, unit, stock_qty, batch_no, expiry_date, mrp, "
            "rate, created_at) VALUES (1, 'MED', 'Syrup', '1', 50, 'M1', '2028-01-01', 250, 150, "
            "'2026-01-01 09:00:00')"
        )
        conn.commit()


class DuplicateSupplierBills(_Shop):
    def setUp(self):
        super().setUp()
        for pid, no, sid, bill, deleted, autosave in (
            (1, "5/FY2026-27", 1, "INV-9", 0, 0),
            (2, "6/FY2026-27", 1, "INV-9", 1, 0),
            (3, "APU1", 1, "INV-9", 0, 1),
        ):
            self.conn.execute(
                "INSERT INTO purchases (id, purchase_no, supplier_id, bill_number, purchase_date, "
                "deleted, is_autosave) VALUES (?, ?, ?, ?, '2026-09-01', ?, ?)",
                (pid, no, sid, bill, deleted, autosave),
            )
        self.conn.commit()

    def test_the_same_suppliers_bill_is_named_once(self):
        [warning] = save_warnings.duplicate_supplier_bill_warnings(
            self.conn, supplier_id=1, bill_number=" inv-9 ")
        self.assertIn("5/FY2026-27", warning)
        self.assertNotIn("6/FY2026-27", warning)

    def test_another_supplier_or_the_purchase_itself_is_silent(self):
        self.assertEqual(save_warnings.duplicate_supplier_bill_warnings(
            self.conn, supplier_id=2, bill_number="INV-9"), [])
        self.assertEqual(save_warnings.duplicate_supplier_bill_warnings(
            self.conn, supplier_id=1, bill_number="INV-9", exclude_purchase_id=1), [])
        self.assertEqual(save_warnings.duplicate_supplier_bill_warnings(
            self.conn, supplier_id=1, bill_number=""), [])

    def test_online_it_asks_the_store_and_a_failed_read_is_silent(self):
        rows = {"rows": [{"id": 35, "purchase_no": "PUR20260617", "supplier_id": 11,
                          "supplier_name": "VET AGENCY", "bill_number": "A-102"}]}
        with mock.patch("core.sync_prefs.is_online_mode", return_value=True), \
                mock.patch("core.store_query_client.list_purchases", return_value=rows):
            [warning] = save_warnings.duplicate_supplier_bill_warnings(
                None, supplier_name="vet agency", bill_number="A-102")
        self.assertIn("PUR20260617", warning)
        with mock.patch("core.sync_prefs.is_online_mode", return_value=True), \
                mock.patch("core.store_query_client.list_purchases",
                           side_effect=RuntimeError("Cannot reach server")):
            self.assertEqual(save_warnings.duplicate_supplier_bill_warnings(
                None, supplier_name="VET AGENCY", bill_number="A-102"), [])


class TheTauriSavesStillSave(_Shop):
    def sale(self, cash, online):
        # save_sale writes the bill's PDF on a daemon thread that reads this in-memory
        # connection. The test closes the connection as it ends, and a PDF thread still reading
        # it crashed the whole suite (a segfault inside sqlite, about one run in three). No
        # thread is started here; the warning is all these tests look at.
        with mock.patch.object(desktop_sales_service, "_has_pharmacy_profile", return_value=True), \
                mock.patch("threading.Thread"):
            return desktop_sales_service.save_sale(self.conn, {
                "items": [{"id": 1, "name": "MED", "batch": "M1", "expiry": "2028-01-01",
                           "qty": 1, "rate": 210.0, "amount": 210.0, "mrp": 250,
                           "medicine_discount": 0, "gst_percent": 0, "schedule": ""}],
                "customer_name": "RAM", "bill_date": TODAY.isoformat(), "payment_mode": "Cash",
                "cash_paid": cash, "online_paid": online, "rounding": 0, "auto_rounding": False,
            })

    def test_a_sale_paid_twice_is_saved_and_warned(self):
        res = self.sale(210.0, 210.0)
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 1)
        [warning] = res.get("warnings") or [None]
        self.assertIn("more than this bill", warning or "")

    def test_an_ordinary_sale_has_no_warning(self):
        res = self.sale(210.0, 0.0)
        self.assertTrue(res.get("ok"), res)
        self.assertFalse(res.get("warnings"))

    def purchase(self, *, allow_duplicate=False, **line):
        item = {"name": "PARA TAB", "type": "Tablet", "batch": "PB1", "expiry": "12/28",
                "qty": 2, "rate": 60.0, "mrp": 100.0, "unit": "10"}
        item.update(line)
        with mock.patch("core.online_catalog.medicine_by_id", return_value=None), \
                mock.patch("core.server_crud.get_doc", return_value=None):
            return desktop_purchase_service.save_purchase_bill(self.conn, {
                "supplier_name": "SHREE PHARMA", "bill_number": "INV-9",
                "purchase_date": "2026-08-31", "items": [item], "cash_paid": 0,
                "allow_duplicate": allow_duplicate,
            })

    def test_a_purchase_with_mistyped_lines_is_saved_and_warned(self):
        res = self.purchase(rate=96.84, mrp=34.01, expiry="04/23")
        self.assertTrue(res.get("ok"), res)
        text = "\n".join(res.get("warnings") or [])
        self.assertIn("Rate is above MRP", text)
        self.assertIn("Already expired on the purchase date", text)
        # The same bill again is refused outright now; only a deliberate "save anyway"
        # writes it, and that still gets the warning naming the purchase it repeats.
        refused = self.purchase()
        self.assertEqual(refused.get("code"), "duplicate_bill", refused)
        again = self.purchase(allow_duplicate=True)
        self.assertTrue(again.get("ok"), again)
        self.assertIn("already saved as purchase", "\n".join(again.get("warnings") or []))
        live = self.conn.execute(
            "SELECT COUNT(*) FROM purchases WHERE COALESCE(deleted,0)=0").fetchone()[0]
        self.assertEqual(live, 2)

    def test_an_ordinary_purchase_has_no_warning(self):
        res = self.purchase()
        self.assertTrue(res.get("ok"), res)
        self.assertFalse(res.get("warnings"))

    def test_a_payment_dated_ahead_is_saved_and_warned(self):
        ahead = (TODAY + timedelta(days=365)).isoformat()
        out = desktop_settings_service.save_payment(self.conn, {
            "kind": "customer", "party": "RAM", "cash": 20, "date": ahead})
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM customer_payments").fetchone()[0], 1)
        self.assertIn(ahead, "\n".join(out.get("warnings") or []))
        today = desktop_settings_service.save_payment(self.conn, {
            "kind": "customer", "party": "RAM", "cash": 20, "date": TODAY.isoformat()})
        self.assertFalse(today.get("warnings"))


class ClassicShowsTheSameChecks(unittest.TestCase):
    def source(self, *parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
            return fh.read()

    def test_every_classic_save_asks(self):
        self.assertIn("sale_warnings(", self.source("ui", "billing", "billing.py"))
        purchase = self.source("ui", "purchase", "purchase.py")
        self.assertIn("purchase_line_warnings(", purchase)
        self.assertIn("duplicate_supplier_bill_warnings(", purchase)
        # And the classic page refuses a duplicate before it writes, like the Tauri save.
        self.assertIn("find_saved_supplier_bill(", purchase)
        self.assertIn("payment_warnings(",
                      self.source("ui", "settings", "settings_tabs", "payment_tab.py"))
        self.assertIn("payment_warnings(",
                      self.source("ui", "settings", "settings_tabs", "customer_payment_tab.py"))


if __name__ == "__main__":
    unittest.main()
