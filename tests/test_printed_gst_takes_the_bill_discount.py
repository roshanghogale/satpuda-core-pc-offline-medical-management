"""The GST printed on a bill is the tax in what the customer actually paid.

core/bill_context.py backed the tax out of each line BEFORE the bill discount --
round(amt * pct / (100 + pct), 2) -- and printed a taxable value of the grand total
AFTER it minus that tax. A discounted bill therefore printed more GST than it charged
and a taxable value short by the same amount. core.bill_gst spreads the discount over
the lines first and rounds half-up in Decimal. Android's BillGst.kt must agree; its
BillGstTest uses these same figures.
"""
from __future__ import annotations

import unittest
from decimal import Decimal

from core.bill_config import BillContext
from core.bill_gst import gst_strip_figures, printed_bill_gst, split_tax
from tests._bill_fixtures import discounted_bill_context


def _paise(values) -> list[str]:
    return [str(v) for v in values]


class TheWorkedExample(unittest.TestCase):
    """94.50, 118.00 and 41.40 at 5% and 59.00 at 18%, no discount."""

    def setUp(self):
        self.gst = printed_bill_gst([(94.50, 5), (118.00, 5), (41.40, 5), (59.00, 18)])

    def test_each_line(self):
        lines = self.gst.lines
        self.assertEqual(_paise(l.taxable for l in lines), ["90.00", "112.38", "39.43", "50.00"])
        self.assertEqual(_paise(l.tax for l in lines), ["4.50", "5.62", "1.97", "9.00"])
        self.assertEqual(_paise(l.cgst for l in lines), ["2.25", "2.81", "0.99", "4.50"])
        self.assertEqual(_paise(l.sgst for l in lines), ["2.25", "2.81", "0.98", "4.50"])

    def test_the_totals(self):
        g = self.gst
        self.assertEqual(
            _paise([g.net, g.taxable, g.tax, g.cgst, g.sgst]),
            ["312.90", "291.81", "21.09", "10.55", "10.54"],
        )

    def test_half_a_paisa_goes_up_not_to_even(self):
        # The float trap: round() works on the binary value of 0.985, which is just under.
        self.assertEqual(round(0.985, 2), 0.98)
        self.assertEqual(split_tax(Decimal("1.97")), (Decimal("0.99"), Decimal("0.98")))


class TheBillDiscountComesOffBeforeTheTax(unittest.TestCase):
    """258.00 of 12% lines less an 8.00 bill discount: the customer pays 250.00."""

    LINES = [(25.00, 12), (170.00, 12), (63.00, 12)]

    def test_the_discount_is_shared_in_proportion(self):
        g = printed_bill_gst(self.LINES, 8.00)
        self.assertEqual(_paise(l.discount for l in g.lines), ["0.78", "5.27", "1.95"])
        self.assertEqual(_paise(l.net for l in g.lines), ["24.22", "164.73", "61.05"])
        self.assertEqual(
            _paise([g.net, g.taxable, g.tax, g.cgst, g.sgst]),
            ["250.00", "223.22", "26.78", "13.40", "13.38"],
        )

    def test_the_old_sum_charged_tax_on_the_discount(self):
        old_gst = round(sum(round(a * p / (100 + p), 2) for a, p in self.LINES), 2)
        self.assertEqual(old_gst, 27.64)
        self.assertEqual(float(printed_bill_gst(self.LINES, 8.00).tax), 26.78)

    def test_leftover_paise_go_to_the_biggest_line(self):
        g = printed_bill_gst([(10, 5), (10, 5), (40, 0)], 1)
        self.assertEqual(_paise(l.discount for l in g.lines), ["0.17", "0.17", "0.66"])
        self.assertEqual(sum(l.discount for l in g.lines), Decimal("1.00"))

    def test_gst_off_backs_nothing_out(self):
        g = printed_bill_gst([(100, 12)], 10, gst_enabled=False)
        self.assertEqual(_paise([g.tax, g.taxable]), ["0.00", "90.00"])

    def test_a_discount_bigger_than_the_bill_stops_at_zero(self):
        g = printed_bill_gst([(50, 5)], 80)
        self.assertEqual(_paise([g.net, g.tax]), ["0.00", "0.00"])


class TheBillPrintsThoseFigures(unittest.TestCase):
    def test_the_bill_context_carries_them(self):
        ctx = discounted_bill_context()
        self.assertEqual(
            (ctx.gst_amount, ctx.taxable_amount, ctx.cgst_amount, ctx.sgst_amount),
            (26.78, 223.22, 13.40, 13.38),
        )

    def test_gst_off_prints_the_discounted_taxable_value_and_no_tax(self):
        ctx = discounted_bill_context(gst_enabled=False)
        self.assertEqual((ctx.gst_amount, ctx.taxable_amount), (0.0, 250.0))

    def test_the_pdf_bill_strip(self):
        from bill_templates.classic import _gst_line

        self.assertEqual(
            _gst_line(discounted_bill_context(), {"template": "classic"}),
            "GST 223.22*6+6%=13.38SGST+13.40CGST, HAVE A NICE DAY",
        )

    def test_the_dot_matrix_strip_and_gst_row(self):
        from core.dot_matrix_print import _footer_strip_line, _settings_with_layout, _totals_lines

        ctx = discounted_bill_context()
        settings = _settings_with_layout({"template": "classic", "paper_size": "A5", "dot_matrix_style": "classic"})
        self.assertEqual(
            _footer_strip_line(ctx, settings),
            "GST 223.22*6+6%=13.38SGST+13.40CGST, HAVE A NICE DAY",
        )
        gst_rows = [r for r in _totals_lines(ctx, settings) if r.startswith("GST")]
        self.assertEqual(len(gst_rows), 1)
        self.assertTrue(gst_rows[0].endswith("26.78"), gst_rows)

    def test_a_context_with_only_a_gst_amount_is_split_the_same_way(self):
        # Supplier documents and older callers fill gst_amount alone.
        ctx = BillContext(gst_amount=1.97, grand_total=41.40)
        self.assertEqual(gst_strip_figures(ctx), (39.43, 0.99, 0.98))


if __name__ == "__main__":
    unittest.main()
