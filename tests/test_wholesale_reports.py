"""Wholesale reports (owner, 18 Oct 2026): sales register, party-wise / item-wise sales,
outstanding with ageing, price list, GSTR-1 B2B from the invoice's own GSTIN and GST
columns, and the receipt number on the payment report.

A store is made in memory with the app's own schema (core.db_setup) and a few wholesale
tax invoices whose figures are worked by hand: rates exclude GST, GST is added on top.
"""
import base64
import csv
import io
import os
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import gst_reports as gr  # noqa: E402
from core import wholesale_reports as wr  # noqa: E402

SHOP_GSTIN = "27AAPFU0939F1ZV"          # Maharashtra


def _valid(base14: str) -> str:
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    total = 0
    for i, ch in enumerate(base14):
        v = chars.index(ch) * (2 if i % 2 else 1)
        total += v // 36 + v % 36
    return base14 + chars[(36 - total % 36) % 36]


PARTY_A_GSTIN = _valid("27AAACA1111A1Z")   # same state: CGST + SGST
PARTY_B_GSTIN = _valid("29AAACB2222B1Z")   # another state: IGST


class _Store(unittest.TestCase):
    def setUp(self):
        from core.db_setup import initialise

        self.conn = sqlite3.connect(":memory:")
        initialise(self.conn)
        for p in (
            mock.patch.object(gr, "_online", return_value=False),
            mock.patch("core.customer_gst._online", return_value=False),
            mock.patch("core.sync_prefs.is_online_mode", return_value=False),
            mock.patch("core.desktop_export_service._is_online", return_value=False),
            mock.patch("core.pharmacy_profile_io.load_pharmacy_profile",
                       return_value={"name": "TEST WHOLESALE", "gstin": SHOP_GSTIN, "gst_enabled": 1}),
        ):
            p.start()
            self.addCleanup(p.stop)
        c = self.conn
        c.execute("INSERT INTO settings (name, value) VALUES ('business_mode', 'wholesale')")
        c.execute("INSERT INTO customers (id, name, phone, party_type, rate_tier, credit_limit, credit_days)"
                  " VALUES (1, 'Party A', '9000000001', 'medical', 'A', 1000, 30),"
                  " (2, 'Party B', '9000000002', 'medical', 'B', 0, 15),"
                  " (3, 'Walk-in C', '', 'retail', NULL, 0, 0)")
        meds = [(1, "Medicine One", "B1", "2027-06", "3004", 12, 40.0, 50, 48, 46, 100, 70),
                (2, "Medicine Two", "B2", "2027-01", "30049099", 5, 18.0, 25, 24, None, 0, 30),
                (3, "Medicine Three", "B3", "2026-12", "3004", 12, 9.0, 12, 11, 10, 40, 15)]
        for mid, name, batch, exp, hsn, gst, pr, ra, rb, rc, stock, mrp in meds:
            c.execute("INSERT INTO medicines (id, name, batch_no, expiry_date, hsn_code, gst_percent, purchase_rate,"
                      " rate_a, rate_b, rate_c, stock_qty, mrp, type) VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'Tablet')",
                      (mid, name, batch, exp, hsn, gst, pr, ra, rb, rc, stock, mrp))

        def invoice(sid, no, day, cust, gstin, pos, due, lines, paid=0.0):
            """lines: (medicine, qty, free, rate, gst%): taxable = qty x rate, GST on top."""
            taxable = sum(q * r for _, q, _, r, _ in lines)
            tax = sum(round(q * r * g / 100, 2) for _, q, _, r, g in lines)
            inter = pos != "27"
            cg = 0.0 if inter else round(tax / 2, 2)
            total = taxable + tax
            c.execute("INSERT INTO sales (id, bill_no, bill_date, customer_id, customer_name, total_amount,"
                      " amount_paid, invoice_mode, buyer_gstin, place_of_supply, taxable_total, cgst_total,"
                      " sgst_total, igst_total, due_date, discount, rounding)"
                      " VALUES (?,?,?,?,?,?,?, 'wholesale', ?,?,?,?,?,?,?,0,0)",
                      (sid, f"WS{no}/FY2026-27", day, cust, {1: "Party A", 2: "Party B"}[cust], total, paid,
                       gstin, pos, taxable, cg, 0.0 if inter else round(tax - cg, 2), tax if inter else 0.0, due))
            for mid, qty, free, rate, g in lines:
                t = round(qty * rate, 2)
                ga = round(t * g / 100, 2)
                c.execute("INSERT INTO sales_items (sale_id, medicine_id, qty, free_qty, rate, gst_percent, taxable,"
                          " gst_amt, amount, rate_tier, batch_no, hsn_code) VALUES (?,?,?,?,?,?,?,?,?, 'A', ?, ?)",
                          (sid, mid, qty, free, rate, g, t, ga, t + ga, f"B{mid}", ""))

        # WS1 Party A, same state: 10 x 50 @12% + 4 x 25 @5% = 600 + 65 GST = 665.00
        invoice(1, 1, "2026-07-01", 1, PARTY_A_GSTIN, "27", "2026-07-31", [(1, 10, 1, 50, 12), (2, 4, 0, 25, 5)])
        # WS2 Party B, other state: 20 x 48 @12% = 960 + 115.20 IGST = 1075.20, 200 paid at the counter
        invoice(2, 2, "2026-08-10", 2, PARTY_B_GSTIN, "29", "2026-08-25", [(1, 20, 2, 48, 12)], paid=200)
        # WS3 Party A, no due date kept: due from the bill date. 5 x 12 @12% = 60 + 7.20 = 67.20
        invoice(3, 3, "2026-09-20", 1, "", "27", "", [(3, 5, 0, 12, 12)])
        # WS4 Party A: 1 x 500 @12% = 560.00; takes Party A over its 1,000 limit
        invoice(4, 4, "2026-10-10", 1, PARTY_A_GSTIN, "27", "2026-11-09", [(1, 10, 0, 50, 12)])
        # a retail bill (MRP, tax inside): never in the wholesale reports
        c.execute("INSERT INTO sales (id, bill_no, bill_date, customer_id, customer_name, total_amount, amount_paid,"
                  " discount, rounding) VALUES (9, 'SCB9/FY2026-27', '2026-08-12', 3, 'Walk-in C', 112, 112, 0, 0)")
        c.execute("INSERT INTO sales_items (sale_id, medicine_id, qty, rate, gst_percent, amount)"
                  " VALUES (9, 1, 1, 112, 12, 112)")
        # Party A pays 300 against WS1 (receipt R-17 on paper)
        c.execute("INSERT INTO customer_payments (id, customer_id, payment_date, amount, payment_mode, allocations,"
                  " receipt_no) VALUES (1, 1, '2026-08-01', 300, 'cash', ?, 'R-17')",
                  ('[{"bill_id": 1, "bill_no": "WS1", "amount": 300}]',))
        c.commit()


class SalesRegister(_Store):
    def test_wholesale_invoices_with_their_heads_and_what_is_owed(self):
        rep = wr.sales_register(self.conn, "2026-07-01", "2026-09-30")
        sec = rep["sections"][0]
        self.assertEqual(wr.REGISTER_COLUMNS, sec["columns"])
        rows = {r[0]: dict(zip(sec["columns"], r)) for r in sec["rows"]}
        self.assertEqual({"WS1", "WS2", "WS3"}, set(rows))          # no retail bill, no October bill
        a = rows["WS1"]
        self.assertEqual((600.0, 32.5, 32.5, 0.0, 665.0), (a["Taxable Value"], a["CGST"], a["SGST"], a["IGST"],
                                                             a["Total"]))
        self.assertEqual(("31-07-2026", 300.0, 365.0), (a["Due Date"], a["Paid"], a["Balance"]))
        self.assertEqual(PARTY_A_GSTIN, a["GSTIN"])
        b = rows["WS2"]
        self.assertEqual((960.0, 0.0, 0.0, 115.2, 200.0, 875.2), (b["Taxable Value"], b["CGST"], b["SGST"],
                                                                   b["IGST"], b["Paid"], b["Balance"]))
        self.assertEqual("", rows["WS3"]["Due Date"])

    def test_totals_row(self):
        rep = wr.sales_register(self.conn, "2026-07-01", "2026-09-30")
        total = rep["sections"][0]["total_row"]
        self.assertEqual("TOTAL", total[0])
        self.assertEqual((1620.0, 1807.4, 500.0, 1307.4), (total[4], total[8], total[10], total[11]))
        self.assertEqual(1620.0, rep["totals"]["taxable"])

    def test_a_party_alone(self):
        rep = wr.sales_register(self.conn, "2026-07-01", "2026-12-31", party_id=2)
        self.assertEqual(["WS2"], [r[0] for r in rep["sections"][0]["rows"]])

    def test_a_wrong_range_is_said(self):
        with self.assertRaises(ValueError):
            wr.sales_register(self.conn, "2026-09-30", "2026-07-01")


class PartyAndItemSales(_Store):
    def test_party_wise(self):
        rep = wr.party_sales(self.conn, "2026-07-01", "2026-10-31")
        sec = rep["sections"][0]
        rows = {r[0]: dict(zip(sec["columns"], r)) for r in sec["rows"]}
        a = rows["Party A"]
        self.assertEqual((3, 29, 1, 1160.0, 132.2, 1292.2), (a["Bills"], a["Qty"], a["Free Qty"], a["Taxable Value"],
                                                            a["GST"], a["Amount"]))
        self.assertEqual((1, 20, 2, 960.0), (rows["Party B"]["Bills"], rows["Party B"]["Qty"],
                                             rows["Party B"]["Free Qty"], rows["Party B"]["Taxable Value"]))
        self.assertEqual(2367.4, sec["total_row"][7])

    def test_item_wise_by_party(self):
        rep = wr.item_sales(self.conn, "2026-07-01", "2026-10-31", party_id=1)
        sec = rep["sections"][0]
        rows = [dict(zip(sec["columns"], r)) for r in sec["rows"]]
        self.assertEqual(["Medicine One", "Medicine Three", "Medicine Two"], [r["Medicine"] for r in rows])
        one = rows[0]
        self.assertEqual(("Party A", 20, 1, 1000.0, 120.0, 1120.0, "3004"),
                         (one["Party"], one["Qty"], one["Free Qty"], one["Taxable Value"], one["GST"], one["Amount"],
                          one["HSN"]))


class Outstanding(_Store):
    def test_ageing_from_the_due_date(self):
        rep = wr.outstanding(self.conn, "2026-10-18")
        party = {r[0]: dict(zip(wr.AGEING_COLUMNS, r)) for r in rep["sections"][0]["rows"]}
        a = party["Party A"]
        # WS1 365.00 due 31-07 -> 79 days late; WS3 67.20 due on its date 20-09 -> 28; WS4 not due yet
        self.assertEqual((365.0, 67.2, 560.0), (a["Due 61-90"], a["Due 0-30"], a["Not Due"]))
        self.assertEqual((992.2, 79, 3), (a["Balance"], a["Days Late (oldest)"], a["Bills"]))
        self.assertEqual((1000.0, "No"), (a["Limit"], a["Limit Crossed"]))
        b = party["Party B"]
        self.assertEqual((875.2, 875.2, 54, "", ""), (b["Balance"], b["Due 31-60"], b["Days Late (oldest)"],
                                                      b["Limit"], b["Limit Crossed"]))
        self.assertNotIn("Walk-in C", party)                          # paid in full

    def test_credit_limit_crossed_and_old_bills_go_90_plus(self):
        self.conn.execute("UPDATE customers SET credit_limit=900 WHERE id=1")
        rep = wr.outstanding(self.conn, "2027-01-15")
        a = next(dict(zip(wr.AGEING_COLUMNS, r)) for r in rep["sections"][0]["rows"] if r[0] == "Party A")
        self.assertEqual("Yes", a["Limit Crossed"])
        self.assertEqual(365.0 + 67.2, a["Due 90+"])
        self.assertEqual(1, rep["totals"]["crossed"])

    def test_a_payment_without_a_choice_clears_the_oldest_bill(self):
        self.conn.execute("INSERT INTO customer_payments (customer_id, payment_date, amount, payment_mode)"
                          " VALUES (1, '2026-10-01', 365, 'cash')")
        rep = wr.outstanding(self.conn, "2026-10-18")
        bills = [dict(zip(wr.OPEN_BILL_COLUMNS, r)) for r in rep["sections"][1]["rows"] if r[0] == "Party A"]
        self.assertEqual(["WS3", "WS4"], [b["Bill No"] for b in bills])
        self.assertEqual(("20-09-2026", 28, "Due 0-30"), (bills[0]["Due Date"], bills[0]["Days Late"],
                                                          bills[0]["Bucket"]))


class PriceList(_Store):
    def test_every_rate_in_stock_only(self):
        rep = wr.price_list(self.conn)
        sec = rep["sections"][0]
        rows = [dict(zip(sec["columns"], r)) for r in sec["rows"]]
        self.assertEqual(["Medicine One", "Medicine Three"], [r["Medicine"] for r in rows])   # Two: no stock
        self.assertEqual((40.0, 50.0, 48.0, 46.0, "12%", 100, "B1", "3004"),
                         tuple(rows[0][k] for k in ("Purchase Rate", "Rate A", "Rate B", "Rate C", "GST %", "Stock",
                                                    "Batch", "HSN")))

    def test_one_tier_without_the_purchase_rate(self):
        rep = wr.price_list(self.conn, tier="c", in_stock_only=False)
        sec = rep["sections"][0]
        self.assertIn("Rate C", sec["columns"])
        self.assertNotIn("Purchase Rate", sec["columns"])
        self.assertNotIn("Rate A", sec["columns"])
        two = next(dict(zip(sec["columns"], r)) for r in sec["rows"] if r[0] == "Medicine Two")
        self.assertEqual("", two["Rate C"])
        self.assertEqual(1, rep["without_rate"])
        with self.assertRaises(ValueError):
            wr.price_list(self.conn, tier="D")


class Gstr1FromTheInvoice(_Store):
    def test_b2b_takes_the_invoice_gstin_and_its_own_taxable(self):
        rep = gr.build(self.conn, "2026-07-01", "2026-09-30")
        b2b = {(r["invoice_no"], r["rate"]): r for r in rep["gstr1"]["b2b"]}
        one = b2b[("WS1", 12)]
        self.assertEqual((PARTY_A_GSTIN, 500.0, 30.0, 30.0, 0.0), (one["gstin"], one["taxable"], one["cgst"],
                                                                   one["sgst"], one["igst"]))
        self.assertEqual((100.0, 2.5, 2.5), (b2b[("WS1", 5)]["taxable"], b2b[("WS1", 5)]["cgst"],
                                             b2b[("WS1", 5)]["sgst"]))
        two = b2b[("WS2", 12)]
        self.assertEqual((PARTY_B_GSTIN, "29", 960.0, 115.2, 0.0), (two["gstin"], two["pos"], two["taxable"],
                                                                    two["igst"], two["cgst"]))
        # WS3 kept no GSTIN and the party has none on file: B2C, its figures still its own
        b2cs = {r["rate"]: r for r in rep["gstr1"]["b2cs"]}
        self.assertEqual(60.0 + 100.0, b2cs[12]["taxable"])         # WS3 60 + the retail bill's 100 (112 incl.)

    def test_the_tally_still_adds_up(self):
        rep = gr.build(self.conn, "2026-07-01", "2026-09-30")
        gap = next(r for r in rep["tally"] if r["line"].startswith("PHARAK (0"))
        self.assertEqual(0.0, gap["amount"])

    def test_a_wholesale_line_without_its_taxable_falls_back_to_the_retail_rule(self):
        self.conn.execute("UPDATE sales_items SET taxable=NULL WHERE sale_id=3")
        p = gr.load_period(self.conn, "2026-09-01", "2026-09-30")
        bill = next(b for b in p.bills if b.no == "WS3")
        self.assertTrue(bill.wholesale)
        self.assertEqual(gr.printed_bill_gst([(ln.amount, ln.rate) for ln in bill.lines], 0).taxable,
                         gr.bill_tax(bill).taxable)


class PaymentReportReceipts(_Store):
    def _csv(self, kind="customer"):
        from core.desktop_export_service import export_payment_report

        res = export_payment_report(self.conn, {"kind": kind, "from": "2026-07-01", "to": "2026-12-31",
                                                "format": "csv"})
        self.assertTrue(res["ok"], res)
        return list(csv.reader(io.StringIO(base64.b64decode(res["content_base64"]).decode("utf-8-sig"))))

    def test_the_receipt_no_column(self):
        rows = self._csv()
        self.assertEqual(["Date", "Payment", "Receipt No", "Party", "Mode", "Amount"], rows[1][:6])
        self.assertEqual(["2026-08-01", "#1", "R-17", "Party A", "cash", "300.0"], rows[2][:6])
        self.assertEqual(["WS1", "2026-07-01", "300.0", "Chosen"], rows[3][6:])

    def test_no_receipt_numbers_no_column(self):
        self.conn.execute("UPDATE customer_payments SET receipt_no=NULL")
        rows = self._csv()
        self.assertNotIn("Receipt No", rows[1])

    def test_a_supplier_payment_receipt(self):
        from core.desktop_export_service import payment_report

        self.conn.execute("INSERT INTO suppliers (id, name) VALUES (1, 'Supplier A')")
        self.conn.execute("INSERT INTO supplier_payments (supplier_id, payment_no, payment_date, amount, mode,"
                          " receipt_no) VALUES (1, 'SP1', '2026-09-01', 50, 'cash', 'S-9')")
        rep = payment_report(self.conn, "supplier", "2026-09-01", "2026-09-30")
        self.assertEqual("S-9", rep["payments"][0]["receipt_no"])
        self.assertTrue(rep["has_receipts"])

    def test_the_pdf_still_renders(self):
        from core.desktop_export_service import (
            _payment_report_lines,
            _payment_report_pdf,
            payment_report,
            payment_report_columns,
        )

        rep = payment_report(self.conn, "customer", "2026-07-01", "2026-12-31")
        captured = {}

        def fake(html, path):
            captured["html"] = html
            return "", path

        with mock.patch("core.document_output.save_html_as_pdf", fake):
            _payment_report_pdf("x.pdf", "t", "s", _payment_report_lines(rep), payment_report_columns(rep))
        self.assertIn("<th>Receipt No</th>", captured["html"])
        self.assertIn("<td class=num>300.00</td>", captured["html"])


class ExportAndMode(_Store):
    def test_csv_keeps_the_total_line(self):
        res = wr.export(self.conn, {"report": "register", "from": "2026-07-01", "to": "2026-09-30", "format": "csv"})
        self.assertTrue(res["ok"], res)
        rows = list(csv.reader(io.StringIO(base64.b64decode(res["content_base64"]).decode("utf-8-sig"))))
        self.assertEqual(wr.REGISTER_COLUMNS, rows[0])
        self.assertEqual("TOTAL", rows[-1][0])
        self.assertTrue(res["filename"].startswith("wholesale_wholesale_sales_register_"))

    def test_outstanding_csv_has_both_tables(self):
        res = wr.export(self.conn, {"report": "outstanding", "as_of": "2026-10-18", "format": "csv"})
        text = base64.b64decode(res["content_base64"]).decode("utf-8-sig")
        self.assertIn("Outstanding by party", text)
        self.assertIn("Open bills", text)

    def test_nothing_to_export(self):
        res = wr.export(self.conn, {"report": "register", "from": "2025-01-01", "to": "2025-01-31", "format": "csv"})
        self.assertFalse(res["ok"])

    def test_a_retail_store_is_refused(self):
        self.assertEqual("", wr.refusal(self.conn))
        self.conn.execute("UPDATE settings SET value='retail' WHERE name='business_mode'")
        self.assertIn("wholesale", wr.refusal(self.conn))

    def test_unknown_report(self):
        with self.assertRaises(ValueError):
            wr.build(self.conn, "nope", {})

    def test_parties_for_the_filter(self):
        self.assertEqual(["Party A", "Party B"], [p["name"] for p in wr.parties(self.conn)])


if __name__ == "__main__":
    unittest.main()
