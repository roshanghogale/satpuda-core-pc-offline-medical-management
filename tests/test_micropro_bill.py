"""Bill template "Bill of Supply (Micropro style)" (owner, 28 Oct 2026).

Settings > Pharmacy Profile > Bill Template had GST Invoice and Tax Invoice; the owner
asked for a third, Bill of Supply, printed like the plain slip of the shops' old Micropro
program (a shop's photo: BILL OF SUPPLY on top, Qty "14xTAB", Com, Batch, Exp,
Amount, Net Amount, E & O E, drug licence, Pharmacist, the composition line). The dot
matrix slip and the normal printed bill both follow it (core/micropro_bill.py).

Nothing here saves, prints or touches the network.
"""
import unittest

from core.bill_config import AVAILABLE_TEMPLATES, DEFAULT_BILL_PRINT_SETTINGS, BillContext, BillItem
from core import bill_heading, micropro_bill as mp
from core import dot_matrix_print as dmp

LINES = [
    ("MOXIKIND CV 625", "MANKIND", "B6AEZ191", "2027-10-01", 14, 257.80, "Tablet", "10"),
    ("PENTAB 40", "ALEMBIC", "EV26008012", "2028-04-01", 7, 86.19, "Tablet", "15"),
    ("PIO AF CREAM", "AGLOWMED", "CD-339", "2028-05-01", 1, 214.50, "Cream", "30 GM"),
    ("SETFLOW SYRUP", "MACLEODS", "ME-11574", "2028-06-01", 1, 262.50, "Syrup", ""),
    ("CIPLADINE SOLUTION", "CIPLA", "N0251053", "2027-11-01", 1, 45.92, "Solution", "100 ML"),
]


def _ctx(**kw):
    items = [BillItem(name=n, manufacturer=m, batch=b, expiry=e, qty=q, amount=a, mrp=a / q,
                      rate=a / q, gst_percent=5, type=t, unit=u) for n, m, b, e, q, a, t, u in LINES]
    total = round(sum(i.amount for i in items), 2)
    base = dict(
        store_name="SHREE MEDICAL & GENERAL STORES",
        address="STATION ROAD, MAIN MARKET, JALNA 431203",
        dl_no="MH-JLN-000001, MH-JLN-000002", bill_no="CA/1001", bill_date="22/10/26",
        bill_date_landscape="22-10-2026", cust_name="Ramesh Kumar Patil",
        cust_addr="CITY HOSPITAL JALNA", doctor_name="DR.A. SHARMA", items=items,
        sub_total=total, grand_total=total, amount_paid=total, gst_amount=30.0, gst_enabled=True,
        bill_heading="tax_invoice", bill_supply_reason="composition",
    )
    base.update(kw)
    return BillContext(**base)


def _settings(**kw):
    # the composition line ticked on (it is off by default since 29 Oct 2026)
    base = dict(template="bill_of_supply", show_composition_line=True)
    base.update(kw)
    return dict(DEFAULT_BILL_PRINT_SETTINGS, **base)


class TheTemplate(unittest.TestCase):
    def test_it_is_the_third_choice(self):
        self.assertEqual(list(AVAILABLE_TEMPLATES)[:3], ["classic", "legacy", "bill_of_supply"])
        self.assertEqual(AVAILABLE_TEMPLATES["bill_of_supply"], "Bill of Supply (Micropro style)")

    def test_it_is_a_bill_of_supply_whatever_the_store_heading(self):
        ctx = _ctx()
        self.assertTrue(bill_heading.is_bill_of_supply(ctx, _settings()))
        self.assertFalse(bill_heading.is_bill_of_supply(ctx, dict(DEFAULT_BILL_PRINT_SETTINGS)))
        self.assertTrue(bill_heading.supply_declaration(ctx, _settings()))

    def test_a_supplier_document_keeps_its_own_layout(self):
        ctx = _ctx(document_title="PURCHASE RETURN")
        self.assertFalse(mp.is_micropro(_settings(), ctx))
        self.assertFalse(bill_heading.is_bill_of_supply(ctx, _settings()))

    def test_qty_company_and_expiry_read_like_micropro(self):
        rows = mp.item_rows(_ctx())
        self.assertEqual([r["qty"] for r in rows], ["14xTAB", "7xTAB", "1x30 GM", "1xSYRUP", "1x100 ML"])
        self.assertEqual(rows[0]["com"], "MANKI")
        self.assertEqual(rows[0]["exp"], "10-27")
        self.assertEqual(mp.jurisdiction_city(_ctx().address), "JALNA")


class DotMatrixSlip(unittest.TestCase):
    def _text(self, **kw):
        return dmp.printed_text(dmp.format_bill_text(_ctx(), _settings(**kw)))

    def test_the_slip(self):
        text = self._text(paper_size="A6")
        lines = text.splitlines()
        self.assertEqual(lines[0].strip(), "BILL OF SUPPLY")
        self.assertEqual(lines[1].strip(), "SHREE MEDICAL & GENERAL STORES")
        for want in ("Date : 22-10-2026", "Bill No.: CA/1001", "Name : RAMESH KUMAR PATIL",
                     "Doct : DR.A. SHARMA", "Addr : CITY HOSPITAL", "14xTAB",
                     "MOXIKIND CV 625", "MANKI", "B6AEZ191", "10-27", "257.80",
                     "Net Amount :", "866.91", "E & O E, Subject to JALNA jurisdiction",
                     "For SHREE MEDICAL & GENERAL STORES",
                     "Drug Lic. No. MH-JLN-000001, MH-JLN-000002", "Pharmacist", "Get well soon.."):
            self.assertIn(want, text)
        self.assertIn("Composition taxable person", text)
        header = next(line for line in lines if line.startswith("Qty"))
        self.assertEqual(header.split(), ["Qty", "Description", "Com", "Batch", "Exp", "Amount"])
        self.assertNotIn("|", text)
        self.assertNotIn("GST INVOICE", text)
        self.assertNotIn("SGST", text)

    def test_every_line_fits_the_paper(self):
        for paper in ("A6", "A5", "A4"):
            settings = dmp._settings_for_ctx(_settings(paper_size=paper), _ctx())
            width = dmp._dm(settings).line_width
            for line in self._text(paper_size=paper).splitlines():
                self.assertLessEqual(len(line), width, (paper, line))

    def test_a_fixed_slip_gives_the_spare_lines_to_the_medicines(self):
        settings = dmp._settings_for_ctx(_settings(paper_size="A6", dot_matrix_slip_height_cm=12.7), _ctx())
        slip = dmp._a6_slip_line_count(dmp._dm(settings))
        lines = dmp.format_bill_text(_ctx(), settings).splitlines()
        self.assertEqual(len(lines), slip)
        # the footer at the bottom: the composition line (two lines on the narrow slip) is last
        self.assertIn("Composition taxable person", dmp.printed_text(" ".join(lines[-2:])))

    def test_the_other_templates_did_not_change(self):
        text = dmp.printed_text(dmp.format_bill_text(_ctx(), dict(DEFAULT_BILL_PRINT_SETTINGS)))
        self.assertNotIn("E & O E", text)
        self.assertNotIn("Description", text)


class PrintedBill(unittest.TestCase):
    """The normal printer keeps its own bill: only the heading says BILL OF SUPPLY
    (owner, 28 Oct 2026: "for the normal printer only bill of supply heading is enough")."""

    def test_the_normal_printer_bill_is_headed_bill_of_supply(self):
        from core.bill_config import render_bill_html

        for paper in ("A5", "A4"):
            html = render_bill_html(_ctx(), _settings(paper_size=paper))
            self.assertIn("BILL OF SUPPLY", html, paper)
            self.assertIn("Composition taxable person", html, paper)
            self.assertNotIn("GST INVOICE", html, paper)
            self.assertNotIn("SGST", html, paper)
            # the usual layout, not the Micropro slip
            self.assertIn('class="medicine-table', html, paper)
            self.assertNotIn("14xTAB", html, paper)
            self.assertNotIn("E &amp; O E", html, paper)

    def test_gst_invoice_is_untouched(self):
        from core.bill_config import render_bill_html

        html = render_bill_html(_ctx(), dict(DEFAULT_BILL_PRINT_SETTINGS))
        self.assertIn("GST INVOICE", html)
        self.assertNotIn("BILL OF SUPPLY", html)


if __name__ == "__main__":
    unittest.main()
