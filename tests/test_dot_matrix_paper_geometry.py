"""The A6 slip is a fixed page: measured margins, and the table fills the rest.

The shop's slips are 10.5 cm tall and 14.5 cm wide, with the tractor holes
taking about 0.8 cm at each side. Every bill keeps the same top and bottom
margin and the medicine table takes whatever space is left, so each slip
carries the same frame and the paper always moves exactly one slip.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.dot_matrix_print as dmp  # noqa: E402
from core.bill_config import DEFAULT_BILL_PRINT_SETTINGS as DEFAULTS  # noqa: E402


def units(cm):
    return round(cm / 2.54 * 216)


class _Item:
    def __init__(self, n):
        self.name = f"MEDICINE NAME {n}"
        self.batch = "B123"
        self.expiry = "10/27"
        self.qty = 2
        self.mrp = 45.5
        self.rate = 45.5
        self.amount = 91.0
        self.gst_percent = 0


class _Ctx:
    store_name = "VAIBHAV MEDICAL"
    address = "P.KALE"
    email = ""
    phone = "9325485954"
    gstin = ""
    dl_no = "20-MH-EUL-640037"
    fssai = ""
    show_fssai_on_bill = False
    blessing_line = "SHREE GANESHAY NAMAH"
    bill_no = "SCB2961/FY2026-27"
    bill_date = "20/09/26"
    bill_date_landscape = "20/09/26"
    cust_name = "RAGHAV"
    cust_addr = "VAKANA"
    doctor_name = "DR WAGH SIR"
    doctor_reg = ""
    grand_total = 91.0
    discount = 0
    gst_amount = 0
    gst_enabled = False
    rounding = 0
    recovery_wish_line = "I WISH FOR YOUR SPEEDY RECOVERY."
    is_continued = False

    def __init__(self, n):
        self.items = [_Item(i) for i in range(n)]


def shop_settings(**over):
    s = {k: v for k, v in DEFAULTS.items()
         if k.startswith(("dot_matrix", "show_")) or k in ("items_per_bill_page",)}
    s["paper_size"] = "A6"
    s.update(over)
    return s


class TheSlipIsAFixedPage(unittest.TestCase):

    def bill(self, items, **over):
        ctx = _Ctx(items)
        merged = dmp._settings_for_ctx(shop_settings(**over), ctx)
        return ctx, merged, dmp._dm(merged), dmp.format_bill_text(ctx, merged).splitlines()

    def test_the_defaults_are_the_shop_s_paper(self):
        self.assertEqual(DEFAULTS["dot_matrix_slip_height_cm"], 10.5)
        self.assertEqual(DEFAULTS["dot_matrix_top_offset_cm"], 1.0)
        self.assertEqual(DEFAULTS["dot_matrix_bottom_margin_cm"], 1.0)
        self.assertEqual(DEFAULTS["dot_matrix_print_width_cm"], 12.9)
        self.assertEqual(DEFAULTS["dot_matrix_left_offset_cm"], 0.8)

    def test_a_bill_never_runs_past_the_slip(self):
        for items in (1, 3, 6, 10, 12):
            with self.subTest(items=items):
                _ctx, _merged, dm, lines = self.bill(items)
                used = dm.escp_top_forward_216 + len(lines) * dm.escp_line_spacing
                self.assertLessEqual(used, dm.escp_slip_216)

    def test_the_bottom_margin_is_kept(self):
        for items in (1, 6, 12):
            with self.subTest(items=items):
                _ctx, _merged, dm, lines = self.bill(items)
                left = dm.escp_slip_216 - dm.escp_top_forward_216 - len(lines) * dm.escp_line_spacing
                self.assertGreaterEqual(left, units(1.0) - dm.escp_line_spacing)

    def test_the_table_fills_the_space_whatever_the_bill_holds(self):
        # one item and three items both fill the same page, so the frame does
        # not jump about from slip to slip
        _c1, _m1, dm1, small = self.bill(1)
        _c3, _m3, dm3, medium = self.bill(3)
        self.assertEqual(len(small) * dm1.escp_line_spacing, len(medium) * dm3.escp_line_spacing)

    def test_every_bill_moves_exactly_one_slip(self):
        for items in (1, 6, 12):
            with self.subTest(items=items):
                _ctx, _merged, dm, lines = self.bill(items)
                end = dmp._a6_end_feed_units(dm, len(lines))
                net = (-dm.escp_top_reverse + dm.escp_top_forward_216
                       + len(lines) * dm.escp_line_spacing + end)
                self.assertEqual(net, dm.escp_slip_216)

    def test_a_different_paper_is_just_different_numbers(self):
        _ctx, _merged, dm, lines = self.bill(3, dot_matrix_slip_height_cm=12.0,
                                             dot_matrix_top_offset_cm=0.5,
                                             dot_matrix_bottom_margin_cm=0.5)
        self.assertEqual(dm.escp_slip_216, units(12.0))
        self.assertEqual(dm.escp_top_forward_216, units(0.5))
        used = dm.escp_top_forward_216 + len(lines) * dm.escp_line_spacing
        self.assertLessEqual(used, dm.escp_slip_216)


class TheSlipWidth(unittest.TestCase):

    def test_the_bill_fits_between_the_tractor_holes(self):
        ctx = _Ctx(3)
        merged = dmp._settings_for_ctx(shop_settings(), ctx)
        dm = dmp._dm(merged)
        lines = dmp.printed_text(dmp.format_bill_text(ctx, merged)).splitlines()
        self.assertEqual(dm.line_width, int(12.9 / 2.54 * 12))
        self.assertLessEqual(max(len(line) for line in lines), dm.line_width)
        printed_cm = (dm.line_width + dm.escp_left_cols) / 12 * 2.54
        self.assertLessEqual(printed_cm, 14.5)

    def test_the_frame_still_adds_up(self):
        dm = dmp._a6_base_layout(shop_settings())
        self.assertEqual(dm.hdr_left_w + dm.hdr_right_w, dm.line_width - 2)
        self.assertEqual(dm.foot_left_w + dm.foot_right_w, dm.line_width - 3)
        self.assertEqual(sum(dm.col_widths) + len(dm.col_widths) + 1, dm.line_width)

    def test_a_wider_paper_gives_a_wider_bill(self):
        dm = dmp._a6_base_layout(shop_settings(dot_matrix_print_width_cm=14.0))
        self.assertEqual(dm.line_width, int(14.0 / 2.54 * 12))
        self.assertEqual(sum(dm.col_widths) + len(dm.col_widths) + 1, dm.line_width)


if __name__ == "__main__":
    unittest.main()
