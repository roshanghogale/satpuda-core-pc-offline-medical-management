"""Two bill footer styles, standard and dot matrix (owner, 18 Oct 2026).

"New" (signature_position "center", the default): the signature box between the message and
the totals, no "For SHOP". "Old" ("right"): under the totals with "For SHOP". A print button
can choose its own; blank inherits Bill Template.
"""
from __future__ import annotations

import unittest

from tests._bill_fixtures import discounted_bill_context, isolated_bill_settings
from tests.test_dot_matrix_paper_geometry import shop_settings


def _standard(**over) -> str:
    from bill_templates.classic import render_classic_bill_html_multi
    from core.bill_config import apply_print_bill_layout, load_bill_print_settings

    ctx = discounted_bill_context()
    with isolated_bill_settings():
        st = apply_print_bill_layout(dict(load_bill_print_settings()), print_slot_copies=1)
        st["paper_size"] = "A5"
        st.update(over)
        html = render_classic_bill_html_multi([ctx], st)
    return html.split("</style>", 1)[1]


def _slip(**over) -> str:
    import core.dot_matrix_print as dmp

    ctx = discounted_bill_context()
    merged = dmp._settings_for_ctx(shop_settings(paper_size="A5", **over), ctx)
    return dmp.printed_text(dmp.format_bill_text(ctx, merged))


class StandardBill(unittest.TestCase):
    def test_new_is_the_default(self):
        body = _standard()
        self.assertIn("bottom-zone-sign-center", body)
        self.assertNotIn("For SATPUDA MEDICAL", body)
        self.assertIn("SIGN OF Q.P.", body)

    def test_old_keeps_for_shop_on_the_right(self):
        body = _standard(signature_position="right")
        self.assertNotIn("bottom-zone-sign-center", body)
        self.assertIn("For SATPUDA MEDICAL", body)


class DotMatrixSlip(unittest.TestCase):
    def test_new_has_the_signature_beside_the_total(self):
        text = _slip()
        self.assertNotIn("For SATPUDA MEDICAL", text)
        row = [ln for ln in text.splitlines() if "SIGN OF Q.P." in ln]
        self.assertTrue(row, text)

    def test_old_has_for_shop_under_the_totals(self):
        text = _slip(signature_position="right")
        lines = text.splitlines()
        self.assertTrue(any("For SATPUDA MEDICAL" in ln for ln in lines), text)
        total_at = max(i for i, ln in enumerate(lines) if "Total" in ln)
        sign_at = max(i for i, ln in enumerate(lines) if "SIGN OF Q.P." in ln)
        self.assertGreater(sign_at, total_at)

    def test_every_line_fits_the_paper(self):
        import core.dot_matrix_print as dmp

        for pos in ("center", "right"):
            ctx = discounted_bill_context()
            merged = dmp._settings_for_ctx(shop_settings(paper_size="A5", signature_position=pos), ctx)
            width = dmp._dm(merged).line_width
            for ln in _slip(signature_position=pos).splitlines():
                self.assertLessEqual(len(ln), width, (pos, ln))


class APrintButtonChoosesItsOwn(unittest.TestCase):
    def test_slot_overrides_and_blank_inherits(self):
        from core.bill_config import get_print_slot_settings

        base = {"signature_position": "center",
                "print_slot_1": {"signature_position": "right"},
                "print_slot_2": {"signature_position": ""}}
        self.assertEqual(get_print_slot_settings(base, 1)["signature_position"], "right")
        self.assertEqual(get_print_slot_settings(base, 2)["signature_position"], "center")


if __name__ == "__main__":
    unittest.main()
