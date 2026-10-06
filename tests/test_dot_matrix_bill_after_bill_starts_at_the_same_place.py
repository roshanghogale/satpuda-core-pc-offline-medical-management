"""Bill after bill starts at the same place on its slip.

A shop printing on 6 x 4 inch continuous paper (4 inch = 10.16 cm, perforation to perforation)
had 10.5 cm saved as the slip height with the printer's own tear-off: the page sent to the
printer was 33 lines x 27/216 inch = 10.48 cm, so every bill moved the paper 3 mm more than one
slip and the top gap grew 0.7 cm -> 1.5 cm by the 4th bill, printed one by one.

The paper is made in inches, so a slip height within 2 mm of a half inch is that half inch
exactly, and a bill's whole paper movement -- parsed back out of the ESC/P it sends -- must be
exactly one slip, in both tear-off modes.
"""
from __future__ import annotations

import unittest

import core.dot_matrix_print as dmp
from core.bill_config import DEFAULT_BILL_PRINT_SETTINGS as DEFAULTS

from tests.test_dot_matrix_paper_geometry import _Ctx


def the_shop(**over):
    """Settings from the shop's screen (Paper & Copies), 6 Oct 2026."""
    s = {k: v for k, v in DEFAULTS.items() if k.startswith(("dot_matrix", "show_"))}
    s.update({
        "paper_size": "A6",
        "dot_matrix_line_spacing": 30,
        "dot_matrix_top_offset_cm": 0,
        "dot_matrix_slip_height_cm": 10.16,
        "dot_matrix_bottom_margin_cm": 1,
        "dot_matrix_print_width_cm": 13,
        "dot_matrix_tear_mode": "printer",
        "dot_matrix_tear_gap_cm": 4,
        "dot_matrix_left_offset_cm": 0,
        "dot_matrix_tear_feed_cm": 2.5,
        "items_per_bill_page": 10,
    })
    s.update(over)
    return s


def paper_moved(payload: bytes) -> int:
    """Net paper movement of one job, in 1/216 inch, as a 9-pin ESC/P printer does it."""
    pos = 0
    spacing = 36
    tof = 0
    page = 0
    i = 0
    while i < len(payload):
        b = payload[i]
        if b == 0x1B:
            cmd = payload[i + 1]
            if cmd == 0x33:                       # ESC 3 n
                spacing = payload[i + 2]
                i += 3
                continue
            if cmd == 0x4A:                       # ESC J n
                pos += payload[i + 2]
                i += 3
                continue
            if cmd == 0x6A:                       # ESC j n
                pos -= payload[i + 2]
                i += 3
                continue
            if cmd == 0x43:                       # ESC C n: page length; here is top-of-form
                page = payload[i + 2] * spacing
                tof = pos
                i += 3
                continue
            i += 3 if cmd in (0x78, 0x61, 0x55, 0x6C, 0x21) else 2
            continue
        if b == 0x0A:
            pos += spacing
        elif b == 0x0C:                           # FF: on to the next top-of-form
            into = (pos - tof) % page
            pos += (page - into) if into else 0
        i += 1
    return pos


def one_bill(settings, items=10) -> bytes:
    ctx = _Ctx(items)
    merged = dmp._settings_for_ctx(settings, ctx)
    text = dmp.format_bill_text(ctx, merged)
    return text, dmp.render_escp_document(text, paper="A6", layout=dmp._dm(merged))


class TheSlipHeightIsTheRealPaper(unittest.TestCase):
    def test_a_ruler_reading_near_an_inch_size_is_that_size(self):
        for cm in (10.16, 10.2, 10.1, 10.0):
            self.assertEqual(dmp.slip_units_from_cm(cm), 864, cm)   # 4 inch
        self.assertEqual(dmp.slip_units_from_cm(9.0), 756)          # 3.5 inch
        self.assertEqual(dmp.slip_units_from_cm(12.0), dmp._units(12.0))  # no inch size near

    def test_the_page_given_to_the_printer_is_exactly_the_slip(self):
        dm = dmp._a6_base_layout(the_shop())
        self.assertEqual(dm.escp_slip_216, 864)
        self.assertEqual(dm.escp_line_spacing * dm.escp_page_lines, 864)
        self.assertLessEqual(dm.escp_line_spacing, 30)   # never fewer lines than asked for

    def test_the_old_10_5_setting_did_move_more_than_the_paper(self):
        # What the shop saw: 891/216 inch a bill on 864/216 inch paper.
        _, payload = one_bill(the_shop(dot_matrix_slip_height_cm=10.5))
        self.assertEqual(paper_moved(payload) - 864, 27)


class EveryBillMovesExactlyOneSlip(unittest.TestCase):
    def test_printer_tear_off(self):
        for items in (1, 4, 10):
            text, payload = one_bill(the_shop(), items)
            self.assertEqual(paper_moved(payload), 864, items)
            self.assertLess(len(text.splitlines()), 32, "a full page would eject a blank slip")

    def test_satpuda_tear_off(self):
        for items in (1, 4, 10):
            _, payload = one_bill(the_shop(dot_matrix_tear_mode="software"), items)
            self.assertEqual(paper_moved(payload), 864, items)

    def test_four_bills_in_a_row_start_at_the_same_place(self):
        starts = []
        pos = 0
        for items in (3, 10, 1, 7):
            starts.append(pos % 864)
            pos += paper_moved(one_bill(the_shop(), items)[1])
        self.assertEqual(starts, [0, 0, 0, 0])


if __name__ == "__main__":
    unittest.main()
