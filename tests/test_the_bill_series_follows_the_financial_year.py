"""Bill and purchase numbers restart each financial year and follow the shop's delete rule.

The rule the owner confirmed on 2026-09-13: the next number is the highest LIVE number in
that financial year plus one. Deleting the newest bill hands its number to the next bill;
deleting an older bill leaves its number unused. Numbers restart at 1 on 1 April, and the
bill's own date decides the year. Purchases follow the same rule.

A date moved across 1 April takes the new year's next number. The offline edit already
did (test_gate_a_fixes.FinancialYearSerialTests); the Online sale edit left it to the
server, which re-filed the bill while this PC kept listing and printing the old number.

The year is read off the NUMBER, not off the old date, so a bill whose number carries
another year than its date is re-numbered by any edit, even one that leaves the date alone.
Bills edited across 1 April before the fix are exactly that -- an old-year number on a
new-year date and fy_start_year -- and their serial keeps pushing the new year's next number
up until they are re-filed. Offline (resync_sale_fy_number), Online and the phone
(FySerial.movesToAnotherFy) apply it the same way.
Everything here runs on an in-memory database or a patched server.
"""
from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from core import billing_service, db_setup  # noqa: E402
from core.fy_serial import (  # noqa: E402
    allocate_purchase_number,
    allocate_sales_bill_no,
    encode_purchase_no,
    encode_sales_bill_no,
    fy_start_year_for_date,
    resync_sale_fy_number,
)


def _shop() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    db_setup.initialise(conn)
    conn.execute("INSERT INTO customers (id, name) VALUES (1, 'RAM')")
    conn.execute("INSERT INTO suppliers (id, name) VALUES (1, 'SHREE PHARMA')")
    return conn


def _add_sales(conn, *bills, deleted=0, autosave=0):
    for serial, bill_date in bills:
        fy = fy_start_year_for_date(bill_date)
        conn.execute(
            "INSERT INTO sales (bill_no, customer_id, bill_date, total_amount, fy_start_year, "
            "fy_serial, deleted, is_autosave) VALUES (?, 1, ?, 10, ?, ?, ?, ?)",
            (encode_sales_bill_no(serial, fy), bill_date, fy, serial, deleted, autosave),
        )


def _add_purchases(conn, *purchases):
    for serial, purchase_date in purchases:
        fy = fy_start_year_for_date(purchase_date)
        conn.execute(
            "INSERT INTO purchases (purchase_no, supplier_id, purchase_date, fy_start_year, "
            "fy_serial) VALUES (?, 1, ?, ?, ?)",
            (encode_purchase_no(serial, fy), purchase_date, fy, serial),
        )


class SalesBillNumbers(unittest.TestCase):
    def setUp(self):
        self.conn = _shop()

    def tearDown(self):
        self.conn.close()

    def test_31_march_continues_the_year_and_1_april_starts_again_at_1(self):
        _add_sales(self.conn, (1, "2026-03-10"), (2, "2026-03-31"))
        self.assertEqual(allocate_sales_bill_no(self.conn, "2026-03-31"), "SCB3/FY2025-26")
        self.assertEqual(allocate_sales_bill_no(self.conn, "2026-04-01"), "SCB1/FY2026-27")

    def test_the_new_year_counts_on_from_its_own_bills_only(self):
        _add_sales(self.conn, (150, "2026-03-31"), (1, "2026-04-01"), (2, "2026-04-02"))
        self.assertEqual(allocate_sales_bill_no(self.conn, "2026-04-03"), "SCB3/FY2026-27")

    def test_deleting_the_newest_bill_gives_its_number_to_the_next_bill(self):
        _add_sales(self.conn, (1, "2026-04-01"), (2, "2026-04-02"), (3, "2026-04-03"))
        self.conn.execute("DELETE FROM sales WHERE bill_no='SCB3/FY2026-27'")
        self.assertEqual(allocate_sales_bill_no(self.conn, "2026-04-03"), "SCB3/FY2026-27")

    def test_deleting_an_older_bill_leaves_its_number_unused(self):
        _add_sales(self.conn, (1, "2026-04-01"), (2, "2026-04-02"), (3, "2026-04-03"))
        self.conn.execute("DELETE FROM sales WHERE bill_no='SCB2/FY2026-27'")
        self.assertEqual(allocate_sales_bill_no(self.conn, "2026-04-03"), "SCB4/FY2026-27")

    def test_a_deleted_row_or_an_autosave_draft_does_not_hold_a_number(self):
        _add_sales(self.conn, (1, "2026-04-01"), (2, "2026-04-02"))
        _add_sales(self.conn, (3, "2026-04-03"), deleted=1)
        _add_sales(self.conn, (4, "2026-04-03"), autosave=1)
        self.assertEqual(allocate_sales_bill_no(self.conn, "2026-04-03"), "SCB3/FY2026-27")


class PurchaseNumbers(unittest.TestCase):
    def setUp(self):
        self.conn = _shop()

    def tearDown(self):
        self.conn.close()

    def test_31_march_continues_the_year_and_1_april_starts_again_at_1(self):
        _add_purchases(self.conn, (1, "2026-03-10"), (2, "2026-03-31"))
        self.assertEqual(allocate_purchase_number(self.conn, "2026-03-31"), "3/FY2025-26")
        self.assertEqual(allocate_purchase_number(self.conn, "2026-04-01"), "1/FY2026-27")

    def test_deleting_the_newest_purchase_gives_its_number_to_the_next(self):
        _add_purchases(self.conn, (1, "2026-04-01"), (2, "2026-04-02"))
        self.conn.execute("DELETE FROM purchases WHERE purchase_no='2/FY2026-27'")
        self.assertEqual(allocate_purchase_number(self.conn, "2026-04-02"), "2/FY2026-27")

    def test_deleting_an_older_purchase_leaves_its_number_unused(self):
        _add_purchases(self.conn, (1, "2026-04-01"), (2, "2026-04-02"))
        self.conn.execute("DELETE FROM purchases WHERE purchase_no='1/FY2026-27'")
        self.assertEqual(allocate_purchase_number(self.conn, "2026-04-02"), "3/FY2026-27")


class AnOnlineSaleEditMovedAcross1April(unittest.TestCase):
    """Online the server allocates; the edit must carry the new year's number itself."""

    EXISTING = {
        "id": 42, "local_id": 42, "client_uuid": "cu-42", "version": 3, "customer_id": 0,
        "bill_no": "SCB150/FY2025-26", "bill_date": "2026-03-31",
        "fy_start_year": 2025, "fy_serial": 150, "items": [],
    }

    def edit(self, bill_date, existing=None, allocated=None, pushed=None):
        doc = dict(existing or self.EXISTING)
        pushed = [] if pushed is None else pushed
        allocate = mock.Mock(return_value=(
            {"fy_start_year": 2026, "fy_serial": 4, "bill_no": "SCB4/FY2026-27"}
            if allocated is None else allocated
        ))
        with mock.patch("core.server_crud.push_bundle",
                        side_effect=lambda b: pushed.append(b) or True), \
                mock.patch("core.server_crud.get_doc", side_effect=lambda coll, i: (
                    dict(doc) if coll == "sales" else {"id": int(i), "stock_qty": 10})), \
                mock.patch("core.online_catalog.medicine_by_id",
                           side_effect=lambda i: {"id": int(i), "name": "MED", "stock_qty": 10}), \
                mock.patch("core.online_catalog.find_customer_by_id", return_value={}), \
                mock.patch("core.online_catalog.patch_docs", return_value=None), \
                mock.patch("core.server_api.store_token_for_active", return_value="t"), \
                mock.patch("core.server_api.allocate_fy", allocate):
            billing_service.update_existing_bill_online_now(
                sale_id=42,
                medicines=[{"id": 1, "name": "MED", "qty": 1, "rate": 10.0, "amount": 10.0}],
                discount_pct=0, rounding=0, cash_paid=10.0, online_paid=0,
                customer_name="RAM", customer_phone="", doctor_name="", previous_due=0,
                bill_date=bill_date,
            )
        self.assertTrue(pushed, "the edit was never pushed")
        return pushed[-1]["sales"][0], allocate

    def test_moving_the_date_into_the_new_year_takes_that_years_next_number(self):
        sale, allocate = self.edit("2026-04-02")
        allocate.assert_called_once_with("t", "sales", "2026-04-02")
        self.assertEqual(
            (sale["bill_no"], sale["fy_start_year"], sale["fy_serial"]),
            ("SCB4/FY2026-27", 2026, 4),
        )

    def test_a_date_change_inside_the_year_keeps_the_number(self):
        sale, allocate = self.edit("2026-03-30")
        allocate.assert_not_called()
        self.assertEqual(
            (sale["bill_no"], sale["fy_start_year"], sale["fy_serial"]),
            ("SCB150/FY2025-26", 2025, 150),
        )

    def test_an_old_year_number_on_a_new_year_date_moves_even_if_the_date_did_not(self):
        # Deliberate: the rule compares the number's year with the bill's date, not the
        # old date with the new one. This bill was moved to 5 April before the fix and
        # kept SCB150/FY2025-26; an edit that does not touch the date re-files it.
        stale = dict(self.EXISTING, bill_date="2026-04-05", fy_start_year=2026)
        sale, allocate = self.edit(None, existing=stale)
        allocate.assert_called_once_with("t", "sales", "2026-04-05")
        self.assertEqual(
            (sale["bill_no"], sale["bill_date"], sale["fy_start_year"], sale["fy_serial"]),
            ("SCB4/FY2026-27", "2026-04-05", 2026, 4),
        )

    def test_a_server_answer_without_a_number_refuses_the_edit(self):
        # It used to push the edit under the old number, still filed in 2025-26.
        pushed = []
        with self.assertRaises(RuntimeError) as caught:
            self.edit("2026-04-02", allocated={}, pushed=pushed)
        self.assertIn("Nothing was saved", str(caught.exception))
        self.assertEqual(pushed, [], "the edit went out under the old number")


class AnOnlinePurchaseEditMovedAcross1April(unittest.TestCase):
    EXISTING = {
        "id": 7, "local_id": 7, "client_uuid": "cu-7", "version": 2, "supplier_id": 1,
        "purchase_no": "5/FY2025-26", "purchase_date": "2026-03-31",
        "fy_start_year": 2025, "fy_serial": 5, "items": [],
    }

    def test_an_online_edit_keeps_the_number_the_server_keeps(self):
        # The server's purchase UPDATE never writes purchase_no, so a purchase moved into
        # 2026-27 keeps 5/FY2025-26 in the store. Sending 8/FY2026-27 made this PC list and
        # print a number no other device has. Until the server stores it, the edit keeps
        # the stored number and asks for none (Offline still re-files it).
        from core import purchase_service

        saved = mock.Mock()
        allocate = mock.Mock(return_value={"fy_start_year": 2026, "fy_serial": 8})
        with mock.patch("core.server_crud.get_doc", side_effect=lambda coll, i: (
                    dict(self.EXISTING) if coll == "purchases" else {})), \
                mock.patch("core.server_crud.save_new_purchase_online", saved), \
                mock.patch("core.online_catalog.patch_docs", return_value=None), \
                mock.patch("core.online_catalog.find_supplier_by_id",
                           return_value={"id": 1, "name": "SHREE PHARMA"}), \
                mock.patch("core.server_api.store_token_for_active", return_value="t"), \
                mock.patch("core.server_api.allocate_fy", allocate):
            purchase_service.update_purchase_online_now(7, 1, "", "2026-04-02", {}, [])
        allocate.assert_not_called()
        doc = saved.call_args.args[0]
        self.assertEqual(
            (doc["purchase_no"], doc["fy_start_year"], doc["fy_serial"], doc["purchase_date"]),
            ("5/FY2025-26", 2025, 5, "2026-04-02"),
        )


class AnOfflineEditUsesTheSameRule(unittest.TestCase):
    def test_an_old_year_number_on_a_new_year_date_is_re_filed(self):
        conn = _shop()
        self.addCleanup(conn.close)
        _add_sales(conn, (1, "2026-04-01"), (2, "2026-04-02"))
        conn.execute(
            "INSERT INTO sales (id, bill_no, customer_id, bill_date, total_amount, "
            "fy_start_year, fy_serial) VALUES (42, 'SCB150/FY2025-26', 1, '2026-04-05', 10, "
            "2026, 150)"
        )
        fresh = resync_sale_fy_number(
            conn.cursor(), conn, 42, "SCB150/FY2025-26", "2026-04-05"
        )
        self.assertEqual(fresh, "SCB3/FY2026-27")


if __name__ == "__main__":
    unittest.main()
