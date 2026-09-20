"""An expiry that reached the shop must not be thrown away over a slash.

Stock loaded from a phone came in with no expiry at all. The loader form has a
plain text box labelled MM/YY and nothing puts the slash in for you, so people
type 0428 -- and every converter in the product tested for "/" and returned ""
for anything without one. No error was raised: the medicine imported, and its
expiry column was simply empty.

These are the shapes real rows arrive in.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.expiry_text import expiry_display, expiry_to_db  # noqa: E402

APRIL_2028 = "2028-04-01"

# What a person or a phone sends -> what the shelf should record.
READS = [
    # The form the fields ask for.
    ("04/28", APRIL_2028),
    ("4/28", APRIL_2028),
    ("04/2028", APRIL_2028),
    # No separator, because nothing typed one for them.
    ("0428", APRIL_2028),
    ("042028", APRIL_2028),
    # The separators people reach for instead.
    ("04-28", APRIL_2028),
    ("04.28", APRIL_2028),
    ("04 28", APRIL_2028),
    ("4-2028", APRIL_2028),
    # Already a date, from the server or an export.
    ("2028-04-01", APRIL_2028),
    ("2028-04-17", APRIL_2028),
    ("2028-04-01T00:00:00", APRIL_2028),
    ("2028-04", APRIL_2028),
    ("202804", APRIL_2028),
    # Whitespace around any of it.
    ("  04/28  ", APRIL_2028),
]

# Nothing here names a month and a year, so nothing may be invented.
UNREADABLE = ["", "   ", "-", "/", "abc", "13/28", "00/28", "28", "1", "0", "99/99"]


class AnExpiry(unittest.TestCase):

    def test_is_read_however_it_was_typed(self):
        for raw, expected in READS:
            with self.subTest(raw=raw):
                self.assertEqual(expiry_to_db(raw), expected)

    def test_that_names_no_month_and_year_is_left_empty(self):
        for raw in UNREADABLE:
            with self.subTest(raw=raw):
                self.assertEqual(expiry_to_db(raw), "")

    def test_is_never_invented_from_nothing(self):
        for raw in (None, "", 0):
            with self.subTest(raw=raw):
                self.assertEqual(expiry_to_db(raw), "")

    def test_comes_back_to_the_screen_as_mm_yy(self):
        for raw, _ in READS:
            with self.subTest(raw=raw):
                self.assertEqual(expiry_display(raw), "04/28")
        self.assertEqual(expiry_display(""), "")
        self.assertEqual(expiry_display("nonsense"), "")

    def test_survives_a_round_trip_through_the_database(self):
        for raw, expected in READS:
            with self.subTest(raw=raw):
                self.assertEqual(expiry_to_db(expiry_display(raw)), expected)

    def test_a_two_digit_year_is_this_century(self):
        self.assertEqual(expiry_to_db("01/00"), "2000-01-01")
        self.assertEqual(expiry_to_db("12/99"), "2099-12-01")

    def test_the_month_is_kept_out_of_the_year_slot(self):
        # 28/04 cannot mean month 28, so it is April 2028, not an empty cell.
        self.assertEqual(expiry_to_db("28/04"), APRIL_2028)

    def test_the_old_converters_agree_with_this_one(self):
        """Every copy in the product now reads the same text the same way."""
        from core.mobile_import_apply import _parse_expiry_to_db
        from core.purchase_service import expiry_to_db as purchase_expiry
        from core.desktop_inventory_service import _expiry_to_db as inventory_expiry

        for raw, expected in READS:
            with self.subTest(raw=raw):
                self.assertEqual(purchase_expiry(raw), expected)
                self.assertEqual(_parse_expiry_to_db(raw), expected)
                self.assertEqual(inventory_expiry(raw), expected)


if __name__ == "__main__":
    unittest.main()
