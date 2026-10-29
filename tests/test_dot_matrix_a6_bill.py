"""Bill template "Dot matrix A6" (owner, 29 Oct 2026): the owner's A6 sample on the dot
matrix slip -- Date / Bill No. beside the shop, Name (mobile) and Doctor beside the
patient's address, Total Items with Total Amount and Discount, then Amount, CGST, SGST |
Net Amount between double lines, E & O E, drug licence, Pharmacist, Get well soon...
The normal printer keeps its own bill (core/micropro_bill.A6_TEMPLATE_ID).

Made-up shop, patient and numbers only. Nothing here saves, prints or touches the network.
"""
import re
import unittest

from core.bill_config import AVAILABLE_TEMPLATES, DEFAULT_BILL_PRINT_SETTINGS, BillContext, BillItem
from core import bill_heading, micropro_bill as mp
from core import dot_matrix_print as dmp

LINES = [
    ("PARACETAMOL 500", "CIPLA LTD", "PCT2401", "2027-12-01", 10, 350.00, "Tablet", "10"),
    ("AMOXYCILLIN 500", "ALKEM LABORATORIES", "AMX3402", "2027-06-01", 5, 392.50, "Tablet", "10"),
    ("PANTOPRAZOLE 40", "INTAS PHARMACEUTICALS", "PAN7810", "2026-11-01", 7, 882.00, "Tablet", "10"),
    ("MUPIROCIN OINTMENT", "GSK", "MUP2501", "2027-08-01", 1, 85.00, "Tube", ""),
    ("LEVOCETIRIZINE SYRUP 60 ML", "ZYDUS", "LEV3605", "2027-10-01", 1, 92.50, "Syrup", ""),
    ("NORMAL SALINE SOLUTION", "FRESENIUS KABI", "NSS2403", "2027-07-01", 1, 45.00, "Solution", "100 ML"),
]


def _ctx(**kw):
    items = [BillItem(name=n, manufacturer=m, batch=b, expiry=e, qty=q, amount=a, mrp=a / q,
                      rate=a / q, gst_percent=12, type=t, unit=u) for n, m, b, e, q, a, t, u in LINES]
    total = round(sum(i.amount for i in items), 2)
    disc = round(total * 0.05, 2)
    net = round(total - disc, 2)
    tax = round(net - net / 1.12, 2)
    base = dict(
        store_name="DEMO MEDICAL & GENERAL STORES",
        address="MAIN ROAD, DEMO NAGAR, TAL. DEMO, DIST. DEMO - 444444",
        dl_no="MH-DEMO-123456, MH-DEMO-654321", bill_no="CA/000123", bill_date="24/10/26",
        bill_date_landscape="24-10-2026", cust_name="Demo Customer", cust_phone="9000000000",
        cust_addr="AT. DEMO NAGAR, TAL. DEMO, DIST. DEMO - 444444", doctor_name="DR. DEMO",
        items=items, sub_total=total, discount=disc, grand_total=net, amount_paid=net,
        taxable_amount=round(net - tax, 2), gst_amount=tax, cgst_amount=round(tax / 2, 2),
        sgst_amount=round(tax - round(tax / 2, 2), 2), gst_enabled=True,
        bill_heading="tax_invoice", bill_supply_reason="composition",
    )
    base.update(kw)
    return BillContext(**base)


def _settings(**kw):
    # the shop's usual 12.9 cm print width: 76 characters at 15 an inch, the sample's look
    base = dict(template="dot_matrix_a6", paper_size="A6", dot_matrix_print_width_cm=12.9)
    base.update(kw)
    return dict(DEFAULT_BILL_PRINT_SETTINGS, **base)


def _text(ctx=None, **kw):
    return dmp.format_bill_text(ctx or _ctx(), _settings(**kw))


def _plain(text: str) -> str:
    """The slip without the printer's bold marks."""
    return re.sub(r"---(NO)?BOLD---", "", text)


class TheTemplate(unittest.TestCase):
    def test_it_is_the_fourth_choice(self):
        self.assertEqual(list(AVAILABLE_TEMPLATES)[-1], "dot_matrix_a6")
        self.assertTrue(mp.is_a6_style(_settings()))
        self.assertFalse(mp.is_a6_style(dict(DEFAULT_BILL_PRINT_SETTINGS, template="bill_of_supply")))

    def test_it_keeps_the_store_heading(self):
        ctx = _ctx()
        self.assertFalse(bill_heading.is_bill_of_supply(ctx, _settings()))
        self.assertIn("TAX INVOICE", _text(ctx))

    def test_the_normal_printer_keeps_its_own_bill(self):
        from core.bill_config import render_bill_html

        html = render_bill_html(_ctx(), _settings())
        self.assertNotIn("Total Items :", html)

    def test_a_supplier_document_keeps_its_own_layout(self):
        self.assertFalse(mp.is_a6_style(_settings(), _ctx(document_title="PURCHASE RETURN")))


class ThePrinter(unittest.TestCase):
    def test_fifteen_characters_an_inch(self):
        ctx = _ctx()
        merged = dmp._settings_for_ctx(_settings(), ctx)
        dm = dmp._dm(merged)
        self.assertEqual(dm.escp_cpi, 15)
        self.assertEqual(dm.line_width, 76)
        job = dmp.render_escp_document(_text(ctx), paper="A6", layout=dm)
        self.assertIn(b"\x1bg", job)
        self.assertNotIn(b"\x1bM", job)
        # the left offset is counted in the same 15-an-inch columns
        dm2 = dmp._dm(dmp._settings_for_ctx(_settings(dot_matrix_left_offset_cm=2.54), ctx))
        self.assertEqual(dm2.escp_left_cols, 15)

    def test_other_bills_stay_at_twelve(self):
        merged = dmp._settings_for_ctx(dict(DEFAULT_BILL_PRINT_SETTINGS, paper_size="A6"), _ctx())
        self.assertEqual(dmp._dm(merged).escp_cpi, 12)


class TheSlip(unittest.TestCase):
    def test_header_like_the_sample(self):
        text = _plain(_text())
        self.assertRegex(text, r"DEMO MEDICAL & GENERAL STORES +Date  : 24-10-2026")
        self.assertRegex(text, r"MAIN ROAD, DEMO NAGAR.* +Bill No\.: CA/000123")
        self.assertRegex(text, r"Name   : DEMO CUSTOMER \(9000000000\) +Addr : AT\. DEMO NAGAR")
        self.assertNotIn("Cust. No", text)
        self.assertRegex(text, r"Doctor : DR\. DEMO")

    def test_a_narrow_slip_keeps_the_number_beside_a_short_name(self):
        text = _plain(_text(dot_matrix_print_width_cm=10.0))
        self.assertRegex(text, r"Name   : DEMO CUSTOMER \(9000000000\) +Addr : AT\. DEMO NAGAR")
        self.assertTrue(all(len(line) <= 60 for line in text.splitlines()))

    def test_a_long_name_keeps_its_whole_name(self):
        text = _plain(_text(_ctx(cust_name="Demo Longname Customer Patilkar")))
        self.assertIn("Name   : DEMO LONGNAME CUSTOMER PATILKAR", text)
        self.assertRegex(text, r"\n {9}\(9000000000\)")     # the number goes under it

    def test_the_address_is_the_patients_not_the_doctors(self):
        text = _plain(_text(_ctx(cust_addr="PATIENT HOUSE, DEMO VILLAGE")))
        self.assertRegex(text, r"Addr : PATIENT HOUSE,")
        self.assertIn("VILLAGE", text)

    def test_rows_and_money(self):
        text = _plain(_text())
        self.assertRegex(text, r"\n10 +PARACETAMOL 500 +CIPLA +PCT2401 +12-27 +350\.00")
        # Qty is the number only (owner): no TAB / TUBE / SYRUP
        for word in ("xTAB", "xTUBE", "xSYRUP", "x100 ML"):
            self.assertNotIn(word, text)
        self.assertRegex(text, r"FRESENIUS +NSS2403")
        self.assertRegex(text, r"Total Items : 6 \(25\) +Total Amount +: +1847\.00")
        self.assertRegex(text, r"Discount \(5\.00 %\) +: +92\.35")
        self.assertRegex(text, r"Amount : [\d.]+ +CGST \(6%\) : [\d.]+ +SGST \(6%\) : [\d.]+ +\| Net Amount : 1754\.65")
        self.assertRegex(text, r"E & O E\. Subject to DEMO jurisdiction +For DEMO MEDICAL")
        self.assertRegex(text, r"Drug Lic\. No\.: MH-DEMO-123456, MH-DEMO-654321 +Pharmacist")
        self.assertIn("Get well soon...", text)

    def test_a_bill_of_supply_prints_no_tax(self):
        text = _plain(_text(_ctx(bill_heading="bill_of_supply")))
        self.assertIn("BILL OF SUPPLY", text)
        self.assertNotIn("CGST", text)
        self.assertRegex(text, r"Net Amount : 1754\.65")

    def test_every_line_fits_the_slip(self):
        for cm in (10.0, 12.9, 14.0):
            dm = dmp._dm(dmp._settings_for_ctx(_settings(dot_matrix_print_width_cm=cm), _ctx()))
            text = _plain(_text(dot_matrix_print_width_cm=cm))
            self.assertTrue(all(len(line) <= dm.line_width for line in text.splitlines()), cm)


if __name__ == "__main__":
    unittest.main()
