"""What a supplier bill photo must turn into on the Purchase page.

Built from a real PARAKH MEDICAL AGENCIES invoice: four lines, two of them
injections measured in ML, one syrup in ML, one liquid in LIT, and an overall
discount printed only in rupees.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class DosageFormFromNameTests(unittest.TestCase):
    """The form printed on the product name beats the pack's unit.

    Everything measured in ML or LIT used to come back as "Liquid" -- a 30ML
    injection, a 500ML syrup and a 5LIT liquid were indistinguishable, so every
    imported row said Liquid and the shop retyped all of them.
    """

    def setUp(self):
        from core.layout_config import get_med_types
        from core.medicine_type_detector import (
            match_type_to_available,
            resolve_medicine_type,
        )

        self.types = get_med_types()
        self._resolve = resolve_medicine_type
        self._match = match_type_to_available

    def _type(self, name, pack):
        got = self._resolve(
            conn=None, name=name, pack=pack, qty_unit="", pkg_unit=pack,
            bill_text=f"{name} {pack}", available_types=self.types,
            prefer_name_detection=True, save_learned=False, use_learned=False,
        )
        return self._match(got, self.types) or got

    def test_the_four_lines_on_the_real_bill(self):
        for name, pack, want in (
            ("MECOVET DXL INJ", "30ML", "Injection"),
            ("MECOVET XL LIQ", "5LIT", "Liquid"),
            ("MECOVET-XL SYP", "500ML", "Syrup"),
            ("ZAKSHOT INJ", "20ML", "Injection"),
        ):
            with self.subTest(name=name):
                self.assertEqual(self._type(name, pack), want)

    def test_solid_forms_still_work(self):
        self.assertEqual(self._type("AMOXY 500 TAB", "10"), "Tablet")
        self.assertEqual(self._type("BECOSULES CAP", "10"), "Capsule")
        self.assertEqual(self._type("CALCIUM BOLUS", "4"), "Bolus")

    def test_topical_names_are_not_stolen_by_the_new_rules(self):
        self.assertEqual(self._type("HIMAX CREAM", "50GM"), "Cream")
        self.assertEqual(self._type("DERMA GEL", "30GM"), "Gel")
        self.assertEqual(self._type("NEEM FACE WASH", "100ML"), "Lotion")
        self.assertEqual(self._type("PET SHAMPOO", "200ML"), "Shampoo")

    def test_the_more_specific_drop_wins(self):
        self.assertEqual(self._type("CIPLOX EYE DROPS", "10ML"), "Eye Drops")
        self.assertEqual(self._type("OTEK EAR DROPS", "15ML"), "Ear Drops")

    def test_a_vial_is_not_just_an_injection(self):
        self.assertEqual(self._type("MELONEX INJ VIAL", "30ML"), "Injection - Vial")

    def test_a_name_with_no_form_word_still_follows_the_pack(self):
        # Unchanged behaviour: nothing in the name to go on, so the pack decides.
        self.assertEqual(self._type("MECOVET DXL", "30ML"), "Liquid")


class OverallDiscountPercentTests(unittest.TestCase):
    """Bills print the discount in rupees; the page shows a percent box too."""

    def setUp(self):
        from core.desktop_purchase_service import _import_discount_pct

        self.pct = _import_discount_pct
        self.rows = [
            {"amount": 5857.40},
            {"amount": 6120.00},
            {"amount": 1755.60},
            {"amount": 634.32},
        ]

    class _Invoice:
        gross_amount = 14367.32

    def test_the_percent_matches_the_printed_bill(self):
        self.assertEqual(self.pct(self._Invoice(), self.rows, 718.37), 5.0)

    def test_it_falls_back_to_the_sum_of_the_lines(self):
        class NoGross:
            pass

        self.assertEqual(self.pct(NoGross(), self.rows, 718.37), 5.0)

    def test_no_discount_gives_no_percent(self):
        self.assertEqual(self.pct(self._Invoice(), self.rows, 0), 0.0)

    def test_a_bill_with_no_amounts_at_all_is_not_a_division_by_zero(self):
        class NoGross:
            pass

        self.assertEqual(self.pct(NoGross(), [], 100.0), 0.0)

class PrintedLineFiguresTests(unittest.TestCase):
    """Each row must read the way the supplier's bill prints it.

    taxable/item_amount carry the bill-level discount spread across the rows --
    right for the totals and for GST, wrong for the table. 5857.40 showed as
    5842.76 and not one line could be ticked off against the paper, even though
    every total agreed.
    """

    ROWS = [
        # name, qty, rate, gst%, what the PARAKH bill prints in AMOUNT
        ("MECOVET DXL INJ", 20, 292.87, 5.0, 5857.40),
        ("MECOVET XL LIQ", 2.5, 2448.00, 0.0, 6120.00),
        ("MECOVET-XL SYP", 5.5, 319.20, 0.0, 1755.60),
        ("ZAKSHOT INJ", 4.5, 140.96, 5.0, 634.32),
    ]

    def _figures(self, discount_pct=0.0):
        from core.desktop_purchase_service import _add_line_figures

        rows = [
            {
                "name": n, "qty": q, "rate": r, "gst_pct": g,
                "discount_pct": discount_pct,
            }
            for n, q, r, g, _ in self.ROWS
        ]
        _add_line_figures(rows)
        return rows

    def test_every_line_matches_the_printed_amount(self):
        for row, (_, _, _, _, want) in zip(self._figures(), self.ROWS):
            with self.subTest(name=row["name"]):
                self.assertAlmostEqual(row["line_amount"], want, places=2)

    def test_the_lines_add_up_to_the_bills_sub_total(self):
        total = sum(r["line_amount"] for r in self._figures())
        self.assertAlmostEqual(total, 14367.32, places=2)

    def test_gst_is_shown_on_the_lines_own_value(self):
        rows = self._figures()
        self.assertAlmostEqual(rows[0]["line_gst_amt"], 292.87, places=2)
        self.assertAlmostEqual(rows[1]["line_gst_amt"], 0.0, places=2)

    def test_a_line_discount_does_come_off_that_line(self):
        # A per-line Disc% is printed on the line, unlike the bill-level one.
        rows = self._figures(discount_pct=10.0)
        self.assertAlmostEqual(rows[0]["line_amount"], 5271.66, places=2)

    def test_a_row_with_no_numbers_is_skipped_quietly(self):
        from core.desktop_purchase_service import _add_line_figures

        rows = [{"name": "X"}, "not a dict", {"qty": "abc", "rate": None}]
        _add_line_figures(rows)
        self.assertEqual(rows[0]["line_amount"], 0.0)

if __name__ == "__main__":
    unittest.main(verbosity=2)
