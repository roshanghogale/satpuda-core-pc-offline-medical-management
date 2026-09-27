"""A document titled Tax Invoice states its taxable value and its tax.

The "legacy" template prints TAX INVOICE, and _show_gst_details returned False for it
in bill_templates/classic.py, core/dot_matrix_print.py and Android's BillRenderer -- so
the one bill headed Tax Invoice was the one bill with no GST on it. It now follows the
"GST amount row" setting like the GST Invoice, and nothing else about it changes: its
page is the GST Invoice page under a different title.
"""
from __future__ import annotations

import unittest

from core.bill_config import DEFAULT_BILL_PRINT_SETTINGS
from tests._bill_fixtures import discounted_bill_context, isolated_bill_settings


def _settings(template: str, **extra) -> dict:
    # The tax figures belong to the old slip; the shop's new one prints no GST.
    return {**DEFAULT_BILL_PRINT_SETTINGS, "template": template, "dot_matrix_style": "classic", **extra}


class ATaxInvoiceShowsItsTax(unittest.TestCase):
    def test_both_templates_follow_the_gst_setting(self):
        from bill_templates.classic import _show_gst_details as pdf_rule
        from core.dot_matrix_print import _show_gst_details as dot_matrix_rule

        for rule in (pdf_rule, dot_matrix_rule):
            old = {"dot_matrix_style": "classic"}      # the new slip never prints GST
            self.assertTrue(rule({**old, "template": "legacy"}))
            self.assertFalse(rule({**old, "template": "legacy", "show_gst": False}))
            self.assertTrue(rule({**old, "template": "classic"}))
            self.assertFalse(rule({**old, "template": "classic", "show_gst": False}))
        self.assertFalse(dot_matrix_rule({"template": "legacy"}))

    def test_the_pdf_bill_is_the_gst_invoice_under_another_title(self):
        from core.bill_config import render_bill_html

        ctx = discounted_bill_context()
        with isolated_bill_settings():
            tax_invoice = render_bill_html(ctx, _settings("legacy"))
            gst_invoice = render_bill_html(ctx, _settings("classic"))
        self.assertIn('<div class="inv-title">TAX INVOICE</div>', tax_invoice)
        self.assertIn('<tr><td>GST</td><td class="r">26.78</td></tr>', tax_invoice)
        self.assertIn("GST 223.22*6+6%=13.38SGST+13.40CGST", tax_invoice)
        self.assertEqual(tax_invoice.replace("TAX INVOICE", "GST INVOICE"), gst_invoice)

    def test_the_dot_matrix_slip_too(self):
        from core.dot_matrix_print import format_bill_text

        ctx = discounted_bill_context()
        with isolated_bill_settings():
            tax_invoice = format_bill_text(ctx, _settings("legacy", paper_size="A5"))
            gst_invoice = format_bill_text(ctx, _settings("classic", paper_size="A5"))
        self.assertIn("TAX INVOICE", tax_invoice)
        self.assertIn("GST 223.22*6+6%=13.38SGST+13.40CGST", tax_invoice)
        self.assertRegex(tax_invoice, r"GST\s+26\.78")
        self.assertEqual(tax_invoice.replace("TAX INVOICE", "GST INVOICE"), gst_invoice)


if __name__ == "__main__":
    unittest.main()
