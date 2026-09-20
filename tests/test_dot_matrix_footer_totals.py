"""The money on a dot matrix bill answers to its own settings.

A shop turned the GST summary strip off and lost Total, LESS and Due with it -
the whole block hung off that one switch - and turning "Total row" off changed
nothing, because an empty block handed a total back anyway. A printed bill with
no amount on it is the worst kind of wrong, so each switch stands alone.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.dot_matrix_print as dmp  # noqa: E402
from tests.test_dot_matrix_paper_geometry import _Ctx, shop_settings  # noqa: E402


def bill_lines(ctx, **settings):
    merged = dmp._settings_for_ctx(shop_settings(dot_matrix_slip_height_cm=10.16, **settings), ctx)
    return dmp.format_bill_text(ctx, merged).splitlines()


def money_row(lines, word):
    return any(word in line for line in lines)


class TheTotalsBlock(unittest.TestCase):

    def setUp(self):
        self.ctx = _Ctx(1)
        self.ctx.discount = 5.0
        self.ctx.previous_due = 20.0

    def test_by_default_the_bill_shows_its_total(self):
        lines = bill_lines(self.ctx)
        self.assertTrue(money_row(lines, "Total"))
        self.assertTrue(money_row(lines, "HAVE A NICE DAY"))

    def test_hiding_the_gst_strip_keeps_the_money(self):
        lines = bill_lines(self.ctx, show_gst_strip=False)
        self.assertTrue(money_row(lines, "Total"), "the total went with the GST strip")
        self.assertTrue(money_row(lines, "DISC"))
        self.assertFalse(money_row(lines, "HAVE A NICE DAY"))

    def test_hiding_the_total_hides_the_total(self):
        lines = bill_lines(self.ctx, show_total=False)
        self.assertFalse(money_row(lines, "Total"))
        self.assertTrue(money_row(lines, "HAVE A NICE DAY"))

    def test_a_bill_with_no_total_and_no_strip_still_prints_its_discount(self):
        lines = bill_lines(self.ctx, show_total=False, show_gst_strip=False)
        self.assertTrue(money_row(lines, "DISC"))

    def test_the_due_lines_come_with_their_own_setting(self):
        lines = bill_lines(self.ctx, show_customer_due=True)
        for word in ("Prev Due", "Bill Due", "Total Due"):
            self.assertTrue(money_row(lines, word), word)

    def test_the_money_sits_above_the_recovery_wish(self):
        lines = bill_lines(self.ctx)
        total_at = max(i for i, l in enumerate(lines) if "Total" in l)
        wish_at = max(i for i, l in enumerate(lines) if "SPEEDY RECOVERY" in l)
        self.assertLess(total_at, wish_at)


if __name__ == "__main__":
    unittest.main()
