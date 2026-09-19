"""The financial year in a bill number must be the year of the bill's own date.

A purchase dated 2026-08-31 was filed as "1/FY2020-21", so the shop's purchase
numbering appeared to restart at 1 in the middle of the year -- the next bill
should have been 103. The date is what the shop typed and can see on screen, so
it decides which year the number belongs to.
"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.fy_serial import (  # noqa: E402
    encode_purchase_no,
    fy_start_year_for_date,
    fy_start_year_in_code,
    fy_label,
)


class TestFyFromDate(unittest.TestCase):
    def test_april_starts_the_year(self):
        self.assertEqual(fy_start_year_for_date("2026-04-01"), 2026)

    def test_march_still_belongs_to_the_previous_year(self):
        self.assertEqual(fy_start_year_for_date("2026-03-31"), 2025)

    def test_the_bill_that_went_wrong(self):
        # 31 August 2026 is FY 2026-27, never FY 2020-21.
        self.assertEqual(fy_start_year_for_date("2026-08-31"), 2026)

    def test_a_date_object_reads_the_same_as_its_string(self):
        self.assertEqual(
            fy_start_year_for_date(date(2026, 8, 31)),
            fy_start_year_for_date("2026-08-31"),
        )

    def test_an_unreadable_date_does_not_invent_an_old_year(self):
        # Falls back to today, which is never 2020.
        self.assertGreaterEqual(fy_start_year_for_date("A001909"), 2024)


class TestNumberCarriesTheRightYear(unittest.TestCase):
    def test_the_corrected_number(self):
        fy = fy_start_year_for_date("2026-08-31")
        self.assertEqual(encode_purchase_no(103, fy), "103/FY2026-27")

    def test_the_wrong_number_the_shop_saw(self):
        # Kept as a witness: this is what a claimed FY of 2020 produced.
        self.assertEqual(encode_purchase_no(1, 2020), "1/FY2020-21")

    def test_label_matches_the_year(self):
        self.assertEqual(fy_label(2026), "2026-27")
        self.assertEqual(fy_label(2025), "2025-26")

    def test_a_number_and_its_bill_date_must_agree(self):
        # The rule the client now enforces before saving.
        for bill_date, serial in (("2026-08-31", 103), ("2026-03-31", 264)):
            fy = fy_start_year_for_date(bill_date)
            no = encode_purchase_no(serial, fy)
            self.assertIn(f"FY{fy}-", no, f"{bill_date} -> {no}")


class TestEditKeepsOrMovesTheNumber(unittest.TestCase):
    """An edit keeps the bill's number. Moving its date to another financial
    year is the one exception, because the year is part of the number.

    Offline already did this through resync_purchase_fy_number; the Online path
    had no such rule, so a bill first entered with a 2020 date kept 1/FY2020-21
    after its date was corrected to 2026 -- and the shop's purchase numbering
    looked as though it had restarted at 1 in the middle of the year.
    """

    def _needs_renumber(self, purchase_no: str, new_date: str) -> bool:
        held = fy_start_year_in_code(purchase_no)
        return held is not None and held != fy_start_year_for_date(new_date)

    def test_an_ordinary_edit_keeps_the_number(self):
        self.assertFalse(self._needs_renumber("103/FY2026-27", "2026-08-31"))
        self.assertFalse(self._needs_renumber("103/FY2026-27", "2026-12-01"))

    def test_a_date_moved_across_1_april_renumbers(self):
        # 31 March is the old year, 1 April the new one.
        self.assertTrue(self._needs_renumber("103/FY2026-27", "2026-03-31"))
        self.assertFalse(self._needs_renumber("264/FY2025-26", "2026-03-31"))

    def test_the_bill_that_went_wrong(self):
        self.assertTrue(self._needs_renumber("1/FY2020-21", "2026-08-31"))

    def test_a_number_without_a_year_is_left_alone(self):
        # Nothing to disagree with, so the edit must not touch it.
        self.assertIsNone(fy_start_year_in_code("103"))
        self.assertFalse(self._needs_renumber("103", "2026-08-31"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
