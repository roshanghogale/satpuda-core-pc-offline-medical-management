"""The slip the shop picked on 25-09-2026.

The counter looked at the printed samples and said which lines are not worth the
paper: SHREE GANESHAY NAMAH, the GST INVOICE title, the GST strip and figure, and
"I WISH FOR YOUR SPEEDY RECOVERY." The signature moves onto the same row as HAVE A
NICE DAY and the total, the | rules go, and every line saved goes to the medicine
table. That slip is what a dot matrix prints unless the settings ask for "classic".
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.dot_matrix_print as dmp  # noqa: E402
from core.bill_config import DEFAULT_BILL_PRINT_SETTINGS  # noqa: E402
from tests.test_dot_matrix_paper_geometry import _Ctx, shop_settings  # noqa: E402


def slip(items=3, **settings):
    ctx = _Ctx(items)
    ctx.discount = 5.0
    merged = dmp._settings_for_ctx(shop_settings(dot_matrix_slip_height_cm=10.16, **settings), ctx)
    return dmp.printed_text(dmp.format_bill_text(ctx, merged)).splitlines()


class TheNewSlip(unittest.TestCase):

    def test_it_is_the_default_for_a_dot_matrix(self):
        self.assertEqual(DEFAULT_BILL_PRINT_SETTINGS["dot_matrix_style"], "compact")
        self.assertTrue(dmp._is_compact({}))
        self.assertFalse(dmp._is_compact({"dot_matrix_style": "classic"}))

    def test_the_lines_the_shop_did_not_want(self):
        text = "\n".join(slip())
        for gone in ("SHREE GANESHAY NAMAH", "GST INVOICE", "TAX INVOICE",
                     "I WISH FOR YOUR SPEEDY RECOVERY", "GST "):
            self.assertNotIn(gone, text, gone)
        self.assertNotIn("|", text)         # no vertical rules in this style

    def test_the_signature_sits_between_the_nice_line_and_the_money(self):
        lines = slip()
        row = next(line for line in lines if "SIGN OF Q.P." in line)
        # The signature takes the middle of the row that carries the total.
        self.assertIn("Total", row)
        self.assertLess(row.index("SIGN OF Q.P."), row.rindex("Total"))
        self.assertTrue(any("HAVE A NICE DAY" in line for line in lines))
        # The money rows stand under each other.
        disc = next(line for line in lines if "DISC" in line)
        self.assertEqual(disc.rindex("DISC"), row.rindex("Total"))

    def test_every_line_is_the_full_width(self):
        widths = {len(line) for line in slip()}
        self.assertEqual(len(widths), 1, widths)

    def test_the_freed_lines_go_to_the_medicines(self):
        new = slip(items=1)
        old = slip(items=1, dot_matrix_style="classic")
        self.assertEqual(len(new), len(old))            # the slip is the same height
        blank_new = sum(1 for line in new if not line.strip())
        blank_old = sum(1 for line in old if not line.strip())
        self.assertGreater(blank_new, blank_old)        # ...with more room inside the table

    def test_classic_keeps_its_blessing_and_rules_but_not_the_wish(self):
        lines = slip(dot_matrix_style="classic")
        text = "\n".join(lines)
        self.assertIn("SHREE GANESHAY NAMAH", text)
        self.assertIn("|", text)
        # The shop asked for the wish line off on BOTH slips, and for one footer block.
        self.assertNotIn("I WISH FOR YOUR SPEEDY RECOVERY", text)
        self.assertIn("Total", next(l for l in lines if "SIGN OF Q.P." in l))


class NothingIsCutOff(unittest.TestCase):
    """A printed bill read "...24.53CGST, HAV": HAVE A NICE DAY had been sliced off.

    Anything too long for its zone now continues on the next line instead -- the GST
    working, a shop's second DL number, a long name -- on both slips.
    """

    def bill(self, style="compact", **over):
        ctx = _Ctx(3)
        ctx.gst_enabled = True
        ctx.gst_amount = 49.05
        ctx.dl_no = "20-MH-EUL-640037, 21-MH-EUL-640038"
        ctx.doctor_name = "DR SHIVSHANKAR RAMCHANDRA DESHMUKH"
        for k, v in over.items():
            setattr(ctx, k, v)
        merged = dmp._settings_for_ctx(
            shop_settings(dot_matrix_slip_height_cm=10.16, dot_matrix_style=style), ctx)
        return dmp.printed_text(dmp.format_bill_text(ctx, merged)).splitlines()

    def test_the_wish_is_not_sliced_off_the_gst_line(self):
        lines = self.bill("classic")
        self.assertTrue(any("HAVE A NICE DAY" in line for line in lines))
        gst = next(line for line in lines if "SGST" in line)
        self.assertNotIn("HAV", gst.replace("HAVE A NICE DAY", ""))

    def test_a_second_dl_number_goes_on_the_next_line(self):
        for style in ("compact", "classic"):
            text = chr(10).join(self.bill(style))
            self.assertIn("20-MH-EUL-640037", text, style)
            self.assertIn("21-MH-EUL-640038", text, style)

    def test_a_long_doctor_name_keeps_its_tail(self):
        text = chr(10).join(self.bill())
        self.assertIn("DESHMUKH", text)

    def test_the_expiry_keeps_its_year(self):
        for style in ("compact", "classic"):
            # The shelf says 10/27; "10/2" was what the slip printed.
            self.assertIn("10/27", chr(10).join(self.bill(style)), style)

    def test_the_batch_column_cannot_be_switched_off(self):
        keys = [c[0] for c in dmp._medicine_columns({"show_batch": False})]
        self.assertIn("batch", keys)
        for style in ("compact", "classic"):
            self.assertIn("Batch No", chr(10).join(self.bill(style)), style)


class WhatTheShopPicked(unittest.TestCase):
    """25-09-2026: bold total, the item count, and the due when money is owed."""

    def bill(self, *, paid=None, prev_due=0.0, items=3, **settings):
        ctx = _Ctx(items)
        ctx.previous_due = prev_due
        if paid is not None:
            ctx.amount_paid = paid
        merged = dmp._settings_for_ctx(
            shop_settings(dot_matrix_slip_height_cm=10.16, **settings), ctx)
        return dmp.format_bill_text(ctx, merged)

    def test_the_total_line_is_struck_twice(self):
        raw = self.bill()
        row = next(l for l in raw.splitlines() if "Total" in l)
        self.assertTrue(row.startswith("---BOLD---") and row.endswith("---NOBOLD---"))
        # ...and the printer codes are not characters on the paper.
        printed = next(l for l in dmp.printed_text(raw).splitlines() if "Total" in l)
        self.assertEqual(len(printed), len(dmp.printed_text(raw).splitlines()[0]))
        self.assertNotIn("---BOLD---", dmp.printed_text(raw))

    def test_only_the_total_is_bold(self):
        raw = self.bill(prev_due=250.0, paid=10.0)
        for line in raw.splitlines():
            if "Total Due" in line or "Bill Due" in line or "Prev Due" in line:
                self.assertNotIn("---BOLD---", line, line)

    def test_the_count_line(self):
        lines = dmp.printed_text(self.bill(items=3)).splitlines()
        self.assertTrue(any("3 aushadhe," in l and "nag" in l for l in lines), lines)
        off = dmp.printed_text(self.bill(items=3, dot_matrix_item_count=False))
        self.assertNotIn("aushadhe,", off)

    def test_the_due_only_when_money_is_owed(self):
        owed = dmp.printed_text(self.bill(prev_due=250.0, paid=10.0))
        self.assertIn("Prev Due", owed)
        self.assertIn("Total Due", owed)
        paid_up = dmp.printed_text(self.bill(paid=999999.0))
        self.assertNotIn("Due", paid_up)


if __name__ == "__main__":
    unittest.main()


class TheBoldTotalReachesThePaper(unittest.TestCase):
    """1 Oct 2026: with "Total printed bold" on, the slip printed "Total" and no amount.
    The A6 printer line was cut to 64 letters with the bold codes counted as letters,
    so the last 10 -- the figure -- fell off. The bytes the printer gets are checked here."""

    def raw(self, **settings):
        ctx = _Ctx(3)
        merged = dmp._settings_for_ctx(shop_settings(dot_matrix_slip_height_cm=10.16, **settings), ctx)
        text = dmp.format_bill_text(ctx, merged)
        total_row = next(l for l in dmp.printed_text(text).splitlines() if "Total" in l and "SIGN" in l)
        amount = total_row.split("Total")[-1].strip()
        return dmp.render_escp_document(text, paper="A6", layout=dmp._dm(merged)), amount

    def test_the_amount_is_printed_in_bold(self):
        payload, amount = self.raw(dot_matrix_bold_total=True)
        self.assertTrue(amount and amount.replace(".", "").isdigit(), amount)
        at = payload.index(b"\x1bE")
        row = payload[at:payload.index(b"\r\n", at)]
        self.assertIn(amount.encode(), row)
        self.assertIn(b"\x1bF", row)
        self.assertNotIn(b"BOLD---", payload)

    def test_the_amount_is_printed_without_bold_too(self):
        payload, amount = self.raw(dot_matrix_bold_total=False)
        self.assertIn(amount.encode(), payload)

    def test_every_printed_line_keeps_its_width(self):
        payload, _ = self.raw(dot_matrix_bold_total=True)
        body = payload.split(b"\r\n")[1:-1]
        widths = {len(row.replace(b"\x1bE", b"").replace(b"\x1bF", b"")) for row in body if row}
        self.assertLessEqual(len(widths), 1, widths)


class TheWholeBillInBold(unittest.TestCase):
    def test_off_by_default(self):
        self.assertFalse(DEFAULT_BILL_PRINT_SETTINGS["dot_matrix_bold_all"])

    def test_every_line_is_bold_and_the_paper_is_the_same(self):
        ctx = _Ctx(3)
        plain = dmp.format_bill_text(ctx, dmp._settings_for_ctx(shop_settings(dot_matrix_slip_height_cm=10.16), ctx))
        bold = dmp.format_bill_text(ctx, dmp._settings_for_ctx(
            shop_settings(dot_matrix_slip_height_cm=10.16, dot_matrix_bold_all=True), ctx))
        self.assertEqual(dmp.printed_text(bold), dmp.printed_text(plain))
        for line in bold.splitlines():
            if line.strip():
                self.assertTrue(line.startswith("---BOLD---") and line.endswith("---NOBOLD---"), line)
        merged = dmp._settings_for_ctx(shop_settings(dot_matrix_slip_height_cm=10.16, dot_matrix_bold_all=True), ctx)
        payload = dmp.render_escp_document(bold, paper="A6", layout=dmp._dm(merged))
        rows = [r for r in payload.split(b"\r\n")[1:-1] if r.strip(b" \x1bEF")]
        self.assertTrue(all(b"\x1bE" in r for r in rows))
        self.assertNotIn(b"BOLD---", payload)
