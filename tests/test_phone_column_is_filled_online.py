"""The Phone column on the two history screens, on a server-backed shop.

Offline both screens fill it from a JOIN. Online it was a hardcoded empty
string in the row builder -- so the column a shop uses to ring a customer about
an unpaid bill, or a supplier about a wrong delivery, was blank in the mode the
shops actually run in, on the same screen that showed it filled Offline.

The sale and purchase documents carry the party's name and id but no phone. The
cached party lists do, so this is a dict lookup rather than a round trip.
"""
import os
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db_setup, desktop_pages_service as pages  # noqa: E402

CUSTOMER = {"id": 7, "local_id": 7, "name": "ZZ CUSTOMER ONE", "phone": "9990001111"}
SUPPLIER = {"id": 4, "local_id": 4, "name": "ZZ SUPPLIER ONE", "phone": "9992223333"}

SALES = [{
    "id": 1, "local_id": 1, "bill_no": "S1", "bill_date": "2026-09-01",
    "customer_id": 7, "customer_name": "ZZ CUSTOMER ONE", "doctor_name": "",
    "total_amount": 100, "discount": 0, "amount_paid": 100, "due_amount": 0,
    "account_cleared": 1,
}]

PURCHASES = [{
    "id": 1, "local_id": 1, "purchase_no": "P1", "bill_number": "B-1",
    "purchase_date": "2026-09-01", "supplier_id": 4,
    "supplier_name": "ZZ SUPPLIER ONE", "final_amount": 100,
    "amount_paid": 100, "due": 0,
}]


class _Online(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db_setup.initialise(self.conn)
        self._patches = [
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.online_catalog.find_customer_by_id",
                       side_effect=lambda i: CUSTOMER if int(i or 0) == 7 else None),
            mock.patch("core.online_catalog.find_supplier_by_id",
                       side_effect=lambda i: SUPPLIER if int(i or 0) == 4 else None),
            mock.patch("core.store_query_client.list_sales",
                       return_value={"rows": SALES}),
            mock.patch("core.store_query_client.list_purchases",
                       return_value={"rows": PURCHASES}),
            # The summary tiles are a separate call. Left unmocked they reach
            # for a real server -- the suite's guard blocks that, but the test
            # should say what it exercises rather than lean on the block.
            mock.patch("core.store_query_client.sales_summary", return_value={}),
            mock.patch("core.store_query_client.purchases_summary", return_value={}),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.conn.close()

    def _cell(self, res, column):
        self.assertTrue(res["rows"], "no rows came back at all")
        return res["rows"][0][res["columns"].index(column)]


class TheSalesHistoryPhoneColumn(_Online):
    def test_it_is_not_blank(self):
        got = self._cell(pages.list_sales_history(self.conn), "Phone")
        self.assertEqual(got, "9990001111", "the Phone column was blank Online")


class ThePurchaseHistoryPhoneColumn(_Online):
    def test_it_is_not_blank(self):
        got = self._cell(pages.list_purchase_history(self.conn), "Phone")
        self.assertEqual(got, "9992223333", "the Phone column was blank Online")


class TheLookupNeverBreaksTheScreen(unittest.TestCase):
    def test_an_unknown_party_is_simply_blank(self):
        with mock.patch("core.online_catalog.find_customer_by_id", return_value=None), \
             mock.patch("core.online_catalog.find_customer_by_name", return_value=None):
            self.assertEqual(pages._party_phone("customers", 999, "NOBODY"), "")

    def test_a_failing_cache_is_blank_not_an_exception(self):
        with mock.patch("core.online_catalog.find_customer_by_id",
                        side_effect=OSError("no route to host")):
            self.assertEqual(pages._party_phone("customers", 7, "ZZ CUSTOMER ONE"), "")

    def test_the_name_is_used_when_the_id_misses(self):
        """A party created on another device may not be cached under that id yet."""
        with mock.patch("core.online_catalog.find_customer_by_id", return_value=None), \
             mock.patch("core.online_catalog.find_customer_by_name",
                        return_value=CUSTOMER):
            self.assertEqual(
                pages._party_phone("customers", 0, "ZZ CUSTOMER ONE"), "9990001111"
            )


if __name__ == "__main__":
    unittest.main()
