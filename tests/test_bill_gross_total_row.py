"""A "Gross Total" row on the bill: the total before the bill discount (owner, 17 Oct 2026).

The printed Total is what the customer pays, after the discount. The shop also wants the
bill's own total -- the sum of the Amount column -- on paper. It is a "Show on Printed
Bill" option, off by default so no shop's bill changes by itself. A fourth total row must
not push Total out of the rotated copies' short box, nor the caption out of the signature
box. Nothing here prints.
"""
from __future__ import annotations

import unittest

from tests._bill_fixtures import discounted_bill_context, isolated_bill_settings


def _settings(paper: str, **extra) -> dict:
    from core.bill_config import apply_print_bill_layout, load_bill_print_settings

    st = apply_print_bill_layout(dict(load_bill_print_settings()), print_slot_copies=1)
    st["paper_size"] = paper
    st.update(extra)
    return st


class TheRow(unittest.TestCase):
    def render(self, paper="A4", n=4, ctx=None, **extra) -> str:
        from bill_templates.classic import render_classic_bill_html_multi

        ctx = ctx or discounted_bill_context()
        with isolated_bill_settings():
            html = render_classic_bill_html_multi([ctx] * n, _settings(paper, **extra))
        return html.split("</style>", 1)[1]

    def test_off_by_default(self):
        from core.bill_config import DEFAULT_BILL_PRINT_SETTINGS

        self.assertFalse(DEFAULT_BILL_PRINT_SETTINGS["show_gross_total"])
        self.assertNotIn("Gross Total", self.render())

    def test_on_it_is_the_amount_column_before_the_discount(self):
        body = self.render(show_gross_total=True)
        # 25 + 170 + 63 = 258; DISC 8; Total 250
        self.assertIn('<td>Gross Total</td><td class="r">258.00</td>', body)
        cell = body[body.index("Gross Total"):]
        self.assertLess(cell.index("Gross Total"), cell.index("DISC"))
        self.assertLess(cell.index("DISC"), cell.index("250.00"))

    def test_not_printed_when_it_would_only_repeat_the_total(self):
        ctx = discounted_bill_context()
        ctx.discount = 0.0
        ctx.grand_total = ctx.sub_total
        self.assertNotIn("Gross Total", self.render(ctx=ctx, show_gross_total=True))

    def test_a_carry_forward_page_does_not_show_it(self):
        from bill_templates.classic import gross_total_to_show

        ctx = discounted_bill_context()
        ctx.is_continued = True
        self.assertEqual(gross_total_to_show(ctx, {"show_gross_total": True}), 0.0)

    def test_four_rows_use_the_dense_table(self):
        self.assertIn('class="sum-table sum-dense"', self.render(show_gross_total=True))
        self.assertNotIn("sum-dense", self.render())

    def test_offered_with_the_other_bill_fields(self):
        from core.bill_config import BILL_PRINT_FIELD_OPTIONS

        keys = [k for _g, items in BILL_PRINT_FIELD_OPTIONS for k, _l in items]
        self.assertIn("show_gross_total", keys)
        self.assertLess(keys.index("show_gross_total"), keys.index("show_discount"))


class TheRotatedBoxIsSizedForItsRows(unittest.TestCase):
    """The rotated copies' totals band was sized at a flat 3.1 mm a row while a 9 pt row
    is 4.2 mm: three rows already overflowed on A4 and a fourth cut Total off."""

    def band(self, paper: str, **extra) -> float:
        from bill_templates.classic import _bottom_row1_mm
        from core.bill_page_config import get_classic_layout

        with isolated_bill_settings():
            st = _settings(paper, **extra)
            return _bottom_row1_mm(discounted_bill_context(), st, get_classic_layout(paper))

    def test_three_rows_get_room_for_three_real_rows(self):
        # DISC + GST + Total at 9 / 9.8 pt is ~13 mm; the old flat estimate gave the
        # 12 mm minimum, which the rows overflowed.
        self.assertGreater(self.band("A4"), 12.5)

    def test_four_dense_rows_fit_where_four_rows_did_not(self):
        four = self.band("A4", show_gross_total=True)
        self.assertGreaterEqual(four, 12.0)
        self.assertLessEqual(four, 14.0)

    def test_bigger_type_asks_for_more(self):
        self.assertGreater(self.band("A4", font_size_pct=120), self.band("A4"))


class TheSignatureBoxKeepsItsCaption(unittest.TestCase):
    def test_caption_is_pinned_to_the_bottom_of_a_flex_box(self):
        from bill_templates.classic import render_classic_bill_html_multi

        with isolated_bill_settings():
            html = render_classic_bill_html_multi([discounted_bill_context()] * 2, _settings("A4"))
        css = html.split("</style>", 1)[0]
        blocks = css.split("  .signature-cell {")[1:]
        self.assertTrue(any("display: flex" in b[:160] and "min-height: 0" in b[:160] for b in blocks))
        self.assertIn(".signature-cell .sign-label", css)
        self.assertIn("margin-top: auto", css[css.index(".signature-cell .sign-label"):][:120])


class DotMatrix(unittest.TestCase):
    def test_the_slip_shows_it_above_disc(self):
        from core.dot_matrix_print import _settings_with_layout, _totals_lines

        ctx = discounted_bill_context()
        with isolated_bill_settings():
            on = _totals_lines(ctx, _settings_with_layout(_settings("A5", show_gross_total=True)))
            off = _totals_lines(ctx, _settings_with_layout(_settings("A5")))
        self.assertTrue(on[0].startswith("Gross Total") and on[0].endswith("258.00"), on)
        self.assertTrue(on[1].startswith("DISC"), on)
        self.assertFalse(any("Gross" in line for line in off))


if __name__ == "__main__":
    unittest.main()
