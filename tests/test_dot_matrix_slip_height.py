"""A6 dot matrix: with a slip height set, every bill moves the paper by exactly
one slip, whatever its length - so bill after bill starts at the same place.

The paper movement of a job is read straight out of the ESC/P bytes: ESC j n
(back n/216"), each CR LF at the ESC 3 line spacing, and ESC J n (forward).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.dot_matrix_print import (  # noqa: E402
    _a6_base_layout,
    _a6_end_feed_units,
    format_alignment_test,
    render_escp_document,
)


def paper_advance_216(payload: bytes) -> int:
    """Net paper movement of one job in 1/216 inch.

    A form feed takes the paper to the printer's next top-of-form, which is the
    page length set by ESC C - that is how the printer's own tear-off mode
    moves the paper, so it counts here too.
    """
    spacing, pos, i, page = 0, 0, 0, 0
    while i < len(payload):
        b = payload[i]
        if b == 0x0C:                  # FF: on to the next top-of-form
            if page > 0:
                rem = pos % page
                pos += (page - rem) if rem else page
            i += 1
            continue
        if b == 0x1B and i + 1 < len(payload):
            cmd = payload[i + 1]
            if cmd == 0x43:            # ESC C n  page length in lines
                page = payload[i + 2] * (spacing or 1); i += 3; continue
            if cmd == 0x33:            # ESC 3 n  line spacing
                spacing = payload[i + 2]; i += 3; continue
            if cmd == 0x6A:            # ESC j n  reverse feed
                pos -= payload[i + 2]; i += 3; continue
            if cmd == 0x4A:            # ESC J n  forward feed
                pos += payload[i + 2]; i += 3; continue
            if cmd in (0x43, 0x6C, 0x61, 0x55, 0x78, 0x33):
                i += 3; continue       # one-argument commands
            if cmd == 0x24:
                i += 4; continue       # ESC $ nL nH
            i += 2; continue           # ESC @, ESC M, ESC E, ESC F ...
        if b == 0x0A:
            pos += spacing
        i += 1
    return pos


def a6_job(lines: int, settings: dict) -> bytes:
    dm = _a6_base_layout(settings)
    text = "\n".join(f"line {n}" for n in range(lines))
    return render_escp_document(text, paper="A6", layout=dm)


class SlipHeight(unittest.TestCase):

    def test_every_bill_moves_exactly_one_slip(self):
        settings = {"dot_matrix_slip_height_cm": 12.0, "dot_matrix_top_offset_cm": 0.8}
        slip = round(12.0 / 2.54 * 216)
        for lines in (10, 18, 24, 27):
            with self.subTest(lines=lines):
                self.assertEqual(paper_advance_216(a6_job(lines, settings)), slip)

    def test_a_different_top_offset_still_moves_one_slip(self):
        # A printer counts a page in whole lines, so a slip that is not a whole
        # number of lines lands within one line rounding (here 0.12 mm) of it.
        for offset in (0.0, 0.5, 1.5):
            settings = {"dot_matrix_slip_height_cm": 10.2, "dot_matrix_top_offset_cm": offset}
            self.assertAlmostEqual(paper_advance_216(a6_job(20, settings)),
                                   round(10.2 / 2.54 * 216), delta=3)

    def test_a_bill_taller_than_the_slip_gets_no_backward_feed(self):
        settings = {"dot_matrix_slip_height_cm": 5.0, "dot_matrix_top_offset_cm": 0.0}
        payload = a6_job(40, settings)
        self.assertGreaterEqual(paper_advance_216(payload), round(5.0 / 2.54 * 216))

    def test_without_a_slip_height_the_tear_feed_is_unchanged(self):
        settings = {"dot_matrix_tear_feed_cm": 2.5, "dot_matrix_top_offset_cm": 0.8}
        dm = _a6_base_layout(settings)
        lines = 20
        expected = lines * dm.escp_line_spacing - dm.escp_top_reverse + dm.escp_tear_feed_216
        self.assertEqual(paper_advance_216(a6_job(lines, settings)), expected)


class TheTearOffCycle(unittest.TestCase):
    """The app ejects the slip and pulls back exactly what it ejected.

    The printer's own auto tear-off did not eject fully and pulled the paper
    back even when it was already in the right place, so the app runs the whole
    cycle: after a bill it feeds the slip past the tear edge, and before the
    next one it pulls back that same distance - never more - landing on the top
    of the next slip. The top margin is then fed FORWARD, down from the
    perforation, which is what the setting reads as.
    """

    SETTINGS = {
        "dot_matrix_slip_height_cm": 12.0,
        "dot_matrix_top_offset_cm": 0.8,
        "dot_matrix_tear_gap_cm": 2.5,
    }

    def test_it_is_the_default(self):
        self.assertEqual(_a6_base_layout(self.SETTINGS).escp_tear_mode, "software")

    def test_the_pull_back_equals_the_eject_and_nothing_more(self):
        dm = _a6_base_layout(self.SETTINGS)
        self.assertEqual(dm.escp_tear_gap_216, round(2.5 / 2.54 * 216))
        self.assertEqual(dm.escp_top_reverse, dm.escp_tear_gap_216)

    def test_the_top_margin_is_fed_forward_not_reversed(self):
        dm = _a6_base_layout(self.SETTINGS)
        self.assertEqual(dm.escp_top_forward_216, round(0.8 / 2.54 * 216))

    def test_pull_back_then_top_margin_before_the_first_line(self):
        payload = a6_job(20, self.SETTINGS)
        head = payload[: payload.index(b"\r\n")]
        dm = _a6_base_layout(self.SETTINGS)
        pulled = forward = 0
        i = 0
        while i < len(head) - 2:
            if head[i:i + 2] == b"\x1bj":
                pulled += head[i + 2]; i += 3; continue
            if head[i:i + 2] == b"\x1bJ":
                forward += head[i + 2]; i += 3; continue
            i += 1
        self.assertEqual(pulled, dm.escp_tear_gap_216)
        self.assertEqual(forward, dm.escp_top_forward_216)

    def test_a_pull_back_over_one_command_is_split(self):
        # ESC j carries at most 255/216 inch
        payload = a6_job(20, {**self.SETTINGS, "dot_matrix_tear_gap_cm": 4.0})
        self.assertGreaterEqual(payload.count(b"\x1bj"), 2)

    def test_every_bill_ends_by_ejecting_to_the_tear_edge(self):
        dm = _a6_base_layout(self.SETTINGS)
        for lines in (10, 24, 40):     # 40 lines is longer than the slip
            with self.subTest(lines=lines):
                self.assertGreaterEqual(_a6_end_feed_units(dm, lines), dm.escp_tear_gap_216)

    def test_the_net_movement_is_still_one_slip(self):
        for lines in (10, 18, 24):
            with self.subTest(lines=lines):
                self.assertEqual(paper_advance_216(a6_job(lines, self.SETTINGS)),
                                 round(12.0 / 2.54 * 216))

    def test_without_a_slip_height_nothing_is_pulled_back(self):
        dm = _a6_base_layout({"dot_matrix_tear_gap_cm": 2.5, "dot_matrix_top_offset_cm": 0.8})
        self.assertEqual(dm.escp_tear_gap_216, 0)
        self.assertEqual(dm.escp_top_reverse, round(0.8 / 2.54 * 216))


class ThePrinterOwnTearOff(unittest.TestCase):
    """Opt-in: leave the paper to a printer whose auto tear-off does work.

    The app sends a page length that IS the slip and a form feed, and the
    printer takes it to its own next top-of-form. On the shop's LX-310 this
    did not eject fully, which is why the app's own cycle is the default.
    """

    PRINTER = {"dot_matrix_slip_height_cm": 12.0, "dot_matrix_tear_mode": "printer"}

    def test_a_page_is_exactly_one_slip(self):
        from core.dot_matrix_print import _a6_page_plan
        for slip_cm in (10.2, 12.0, 12.7, 15.0):
            with self.subTest(slip_cm=slip_cm):
                slip = round(slip_cm / 2.54 * 216)
                spacing, lines = _a6_page_plan(slip, 30)
                self.assertLessEqual(abs(spacing * lines - slip), 3)   # within 0.4 mm
                self.assertTrue(20 <= spacing <= 32)

    def test_the_job_sets_that_page_length_and_ends_with_a_form_feed(self):
        settings = self.PRINTER
        dm = _a6_base_layout(settings)
        payload = a6_job(12, settings)
        i = payload.index(b"\x1bC")
        page_216 = payload[i + 2] * dm.escp_line_spacing
        self.assertLessEqual(abs(page_216 - dm.escp_slip_216), 3)   # within 0.4 mm of the slip
        self.assertTrue(payload.endswith(b"\x0c"))

    def test_the_software_does_not_also_pull_the_paper(self):
        dm = _a6_base_layout(self.PRINTER)
        self.assertEqual(dm.escp_tear_gap_216, 0)
        payload = a6_job(12, self.PRINTER)
        tail = payload[payload.rindex(b"\r\n") + 2:]           # after the last printed line
        self.assertNotIn(b"\x1bJ", tail)                       # the form feed does the moving
        self.assertTrue(tail.endswith(b"\x0c"))

    def test_it_is_opt_in(self):
        self.assertEqual(_a6_base_layout({"dot_matrix_slip_height_cm": 12.0}).escp_tear_mode,
                         "software")
        self.assertEqual(_a6_base_layout(self.PRINTER).escp_tear_mode, "printer")


class StartOfEveryBill(unittest.TestCase):

    def test_skip_over_perforation_is_cancelled_after_init(self):
        payload = a6_job(10, {})
        self.assertTrue(payload.startswith(b"\x1b@"))
        self.assertIn(b"\x1b@\x1bx\x00\x1bO", payload[:8])

    def test_left_offset_moves_every_line_with_esc_l(self):
        payload = a6_job(10, {"dot_matrix_left_offset_cm": 1.0})
        i = payload.index(b"\x1bl")
        self.assertEqual(payload[i + 2], round(1.0 / 2.54 * 12))
        self.assertLess(payload.index(b"\x1bM"), i)       # after the 12 CPI pitch
        self.assertNotIn(b"\x1b$", payload)               # ESC $ moved only one line

    def test_zero_left_offset_is_the_first_column(self):
        payload = a6_job(10, {})
        i = payload.index(b"\x1bl")
        self.assertEqual(payload[i + 2], 0)

    def test_the_old_horizontal_position_setting_carries_over(self):
        dm = _a6_base_layout({"dot_matrix_hpos_60ths": 30})   # half an inch
        self.assertEqual(dm.escp_left_cols, 6)


class WindowsDriverPlacement(unittest.TestCase):

    def test_a6_starts_top_left_not_centred(self):
        from core.printer_manager import gdi_placement_box
        # a class driver that thinks the paper is 10 inches wide, 120 x 72 dpi
        mx, my, w, h = gdi_placement_box(1200, 792, 120, 72, {
            "left_cm": 0.0, "top_cm": 0.0, "width_cm": 13.6, "height_cm": 12.0,
        })
        self.assertEqual((mx, my), (0, 0))
        self.assertEqual(w, int(13.6 / 2.54 * 120))
        self.assertEqual(h, int(12.0 / 2.54 * 72))

    def test_left_offset_moves_it_right(self):
        from core.printer_manager import gdi_placement_box
        mx, _my, _w, _h = gdi_placement_box(1200, 792, 120, 72, {"left_cm": 2.54})
        self.assertEqual(mx, 120)

    def test_only_a6_gets_the_slip_placement(self):
        from core.dot_matrix_print import gdi_placement_for
        a6 = gdi_placement_for({"dot_matrix_slip_height_cm": 12}, "A6")
        self.assertEqual(a6["slip_cm"], 12.0)
        self.assertEqual(a6["height_cm"], 12.0)
        self.assertIsNone(gdi_placement_for({}, "A5"))


class PrinterReadiness(unittest.TestCase):

    def test_a_stuck_job_is_described(self):
        from core.printer_manager import PrinterManager
        import types
        fake = types.SimpleNamespace(EnumJobs=lambda h, a, b, c: [
            {"pDocument": "Satpuda Bill 1", "Status": 0x10},          # printing - fine
            {"pDocument": "Satpuda Bill 2", "Status": 0x40},          # out of paper
        ])
        sys.modules["win32print"], old = fake, sys.modules.get("win32print")
        try:
            self.assertIn("out of paper", PrinterManager._stuck_job_note(object()))
        finally:
            if old is None:
                sys.modules.pop("win32print", None)
            else:
                sys.modules["win32print"] = old

    def test_a_spooling_job_is_not_stuck(self):
        from core.printer_manager import PrinterManager
        import types
        fake = types.SimpleNamespace(EnumJobs=lambda h, a, b, c: [
            {"pDocument": "Satpuda Bill 1", "Status": 0x8 | 0x10},    # spooling + printing
        ])
        sys.modules["win32print"], old = fake, sys.modules.get("win32print")
        try:
            self.assertEqual(PrinterManager._stuck_job_note(object()), "")
        finally:
            if old is None:
                sys.modules.pop("win32print", None)
            else:
                sys.modules["win32print"] = old


class AlignmentTest(unittest.TestCase):

    def test_it_fits_the_slip_width_and_marks_the_start(self):
        text, dm = format_alignment_test({})
        rows = text.splitlines()
        self.assertTrue(rows[0].startswith("===="))
        self.assertTrue(all(len(r) <= dm.line_width for r in rows))

    def test_it_moves_the_paper_like_a_bill(self):
        settings = {"dot_matrix_slip_height_cm": 12.0}
        text, dm = format_alignment_test(settings)
        payload = render_escp_document(text, paper="A6", layout=dm)
        self.assertEqual(paper_advance_216(payload), round(12.0 / 2.54 * 216))


if __name__ == "__main__":
    unittest.main()
