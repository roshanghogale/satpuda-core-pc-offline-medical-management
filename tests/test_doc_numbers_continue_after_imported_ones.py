"""A data import numbered a store's old supplier payments PAY1-1..PAY1-265 as if PC 1 had made
them (Shrihari, store 154). PC 1's counter started at 1, so its first supplier payment failed with
"UNIQUE constraint failed: supplier_payments.payment_no". The next number now follows the highest
one already in the store."""
import sqlite3
import unittest

from core.offline_first import numbers
from core.offline_first.schema import meta_set


class DocNumbersContinue(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:")
        self.c.execute("CREATE TABLE of_meta (k TEXT PRIMARY KEY, v TEXT)")
        self.c.execute("CREATE TABLE of_doc_counters (prefix TEXT PRIMARY KEY, next_no INTEGER NOT NULL)")
        self.c.execute("CREATE TABLE supplier_payments (id INTEGER PRIMARY KEY, payment_no TEXT UNIQUE)")
        self.c.execute("CREATE TABLE sales_returns (id INTEGER PRIMARY KEY, return_no TEXT UNIQUE)")
        meta_set(self.c, "device_no", 1)

    def test_a_payment_number_follows_the_imported_ones(self):
        for n in (1, 2, 265, 30):
            self.c.execute("INSERT INTO supplier_payments (payment_no) VALUES (?)", (f"PAY1-{n}",))
        self.c.execute("INSERT INTO supplier_payments (payment_no) VALUES ('PAY2-900')")
        self.assertEqual(numbers.doc_number(self.c, "PAY"), "PAY1-266")
        self.assertEqual(numbers.doc_number(self.c, "PAY"), "PAY1-267")

    def test_an_empty_store_starts_at_one_and_returns_are_separate(self):
        self.assertEqual(numbers.doc_number(self.c, "SR"), "SR1-1")
        self.c.execute("INSERT INTO sales_returns (return_no) VALUES ('SR1-1')")
        self.assertEqual(numbers.doc_number(self.c, "SR"), "SR1-2")
        self.assertEqual(numbers.doc_number(self.c, "PAY"), "PAY1-1")


if __name__ == "__main__":
    unittest.main()
