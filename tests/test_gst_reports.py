"""GST reports add up to the printed bills, paisa for paisa (3 Oct 2026).

A temporary store is made with the app's own schema (core.db_setup), filled with bills,
a return and supplier bills, and every report is checked against figures worked by hand
from the printed bill's rule (core.bill_gst).
"""
import os
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import gst_reports as gr  # noqa: E402
from core.customer_gst import save_customer_gst  # noqa: E402

SHOP_GSTIN = "27AAPFU0939F1ZV"          # Maharashtra
B2B_GSTIN = "27AAGCB7383J1Z1"           # filled in setUp with a valid check digit
OTHER_STATE_SUPPLIER = "29AAGCB7383J1Z4"


def _valid(base14: str) -> str:
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    total = 0
    for i, ch in enumerate(base14):
        v = chars.index(ch) * (2 if i % 2 else 1)
        total += v // 36 + v % 36
    return base14 + chars[(36 - total % 36) % 36]


class _Store(unittest.TestCase):
    def setUp(self):
        from core.db_setup import initialise

        self.conn = sqlite3.connect(":memory:")
        initialise(self.conn)
        for p in (
            mock.patch.object(gr, "_online", return_value=False),
            mock.patch("core.customer_gst._online", return_value=False),
            mock.patch("core.pharmacy_profile_io.load_pharmacy_profile",
                       return_value={"name": "TEST MEDICAL", "gstin": SHOP_GSTIN, "gst_enabled": 1}),
        ):
            p.start()
            self.addCleanup(p.stop)
        c = self.conn
        self.b2b_gstin = _valid("27AAGCB7383J1Z")
        c.execute("INSERT INTO customers (id, name) VALUES (1, 'RAMESH PATIL'), (2, 'SUNIL TEST AGENCIES')")
        c.execute("INSERT INTO suppliers (id, name, gstin) VALUES (1, 'LOCAL PHARMA', ?), (2, 'KARNATAKA PHARMA', ?),"
                  " (3, 'NO GSTIN TRADERS', '')", (SHOP_GSTIN, OTHER_STATE_SUPPLIER))
        meds = [(1, "DOLO 650", "30049099", 12, "Tablet"), (2, "CIPCAL SYRUP", "3004", 5, "Syrup"),
                (3, "COTTON ROLL", "", 12, "Other"), (4, "SALINE", "3004", 0, "Injection")]
        for mid, name, hsn, gst, kind in meds:
            c.execute("INSERT INTO medicines (id, name, hsn_code, gst_percent, type, stock_qty, mrp) "
                      "VALUES (?,?,?,?,?,100,10)", (mid, name, hsn, gst, kind))

        def sale(sid, no, day, cust, discount, lines, rounding=0.0):
            total = sum(a for _, _, a, _ in lines) - discount + rounding
            c.execute("INSERT INTO sales (id, bill_no, bill_date, customer_id, customer_name, discount, rounding,"
                      " total_amount) VALUES (?,?,?,?,?,?,?,?)",
                      (sid, f"SCB{no}/FY2026-27", day, cust, {1: "RAMESH PATIL", 2: "SUNIL TEST AGENCIES"}[cust],
                       discount, rounding, total))
            for mid, qty, amount, gst in lines:
                c.execute("INSERT INTO sales_items (sale_id, medicine_id, qty, rate, gst_percent, amount)"
                          " VALUES (?,?,?,?,?,?)", (sid, mid, qty, amount / qty, gst, amount))

        # bill_gst's own example: 258.00 of 12% lines, 8.00 discount -> 223.22 + 26.78
        sale(1, 1, "2026-09-02", 1, 8.0, [(1, 10, 129.0, 12), (1, 10, 129.0, 12)])
        sale(2, 2, "2026-09-05", 2, 0.0, [(2, 1, 105.0, 5), (4, 2, 40.0, 0)])     # B2B from 2026-09-03
        sale(3, 4, "2026-09-09", 1, 0.0, [(3, 4, 112.0, None)])                  # rate from master; no HSN
        sale(4, 5, "2026-08-31", 2, 0.0, [(2, 1, 105.0, 5)])                     # before the GSTIN: B2C
        sale(5, 3, "2026-09-08", 1, 0.0, [])                                     # an amount, no lines
        c.execute("UPDATE sales SET total_amount=500 WHERE id=5")
        c.execute("INSERT INTO sales_returns (id, return_no, sale_id, customer_id, return_date, refund_amount,"
                  " discount) VALUES (1,'SR1',2,2,'2026-09-10',105.0,0), (2,'SR2',1,1,'2026-09-11',64.5,0)")
        c.execute("INSERT INTO sales_return_items (return_id, medicine_id, qty, rate, amount)"
                  " VALUES (1,2,1,105,105), (2,1,5,12.9,64.5)")

        def purchase(pid, day, sup, bill_no, subtotal, gst, lines):
            c.execute("INSERT INTO purchases (id, purchase_no, supplier_id, bill_number,"
                      " purchase_date, subtotal, total_gst, cgst, sgst, total_amount) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (pid, f"P{pid}", sup, bill_no, day, subtotal, gst, gst / 2, gst / 2, subtotal + gst))
            for mid, qty, rate, taxable, tax in lines:
                c.execute("INSERT INTO purchase_items (purchase_id, medicine_id, qty, rate, gst_pct, taxable,"
                          " gst_amt, item_amount) VALUES (?,?,?,?,?,?,?,?)",
                          (pid, mid, qty, taxable / qty, rate, taxable, tax, taxable + tax))

        purchase(1, "2026-09-01", 1, "LP-77", 1000.0, 120.0, [(1, 10, 12, 1000.0, 120.0)])
        purchase(2, "2026-09-04", 2, "KA-9", 500.0, 25.0, [(2, 5, 5, 500.0, 25.0)])
        purchase(3, "2026-09-06", 3, "NG-1", 200.0, 0.0, [(4, 4, 0, 200.0, 0.0)])
        c.execute("INSERT INTO purchase_returns (id, return_no, purchase_id, supplier_id, return_date,"
                  " refund_amount) VALUES (1,'PR1',1,1,'2026-09-12',224.0)")
        c.execute("INSERT INTO purchase_return_items (return_id, medicine_id, qty, rate, amount)"
                  " VALUES (1,1,2,100,200)")
        c.commit()
        save_customer_gst(c, 2, self.b2b_gstin, "SUNIL TEST AGENCIES", since="2026-09-03")
        self.report = gr.build(c, "2026-09-01", "2026-09-30")


class TheSalesRegister(_Store):
    def test_a_discounted_bill_is_its_printed_figures(self):
        rows = [r for r in self.report["sales_register"] if r["bill_no"] == "SCB1"]
        self.assertEqual(1, len(rows))
        self.assertEqual((223.22, 13.39, 13.39), (rows[0]["taxable"], rows[0]["cgst"], rows[0]["sgst"]))

    def test_a_bill_before_the_period_is_not_in_it(self):
        self.assertNotIn("SCB5", {r["bill_no"] for r in self.report["sales_register"]})


class Gstr1(_Store):
    def test_a_customer_with_a_gstin_is_b2b_only_from_its_date(self):
        b2b = self.report["gstr1"]["b2b"]
        self.assertEqual({"SCB2"}, {r["invoice_no"] for r in b2b})
        five = next(r for r in b2b if r["rate"] == 5)
        self.assertEqual((100.0, 2.5, 2.5, 105.0), (five["taxable"], five["cgst"], five["sgst"], five["value"] - 40))
        self.assertEqual(self.b2b_gstin, five["gstin"])

    def test_a_return_on_a_b2b_bill_is_a_credit_note(self):
        cdnr = self.report["gstr1"]["cdnr"]
        self.assertEqual(["SR1"], [r["note_no"] for r in cdnr])
        self.assertEqual((100.0, 2.5, 2.5, "SCB2"), (cdnr[0]["taxable"], cdnr[0]["cgst"], cdnr[0]["sgst"],
                                                    cdnr[0]["invoice_no"]))

    def test_a_return_on_a_b2c_bill_lowers_b2cs(self):
        b2cs = {r["rate"]: r for r in self.report["gstr1"]["b2cs"]}
        # SCB1 223.22 + SCB4 100.00 (112 at 12%) - SR2 (5 of 20 tablets of SCB1, its discount share kept)
        sr2 = gr.note_tax(next(n for n in gr.load_period(self.conn, "2026-09-01", "2026-09-30").notes
                               if n.no == "SR2"))
        self.assertEqual(round(223.22 + 100.0 - float(sr2.taxable), 2), b2cs[12]["taxable"])
        self.assertEqual("INTRA", b2cs[12]["supply"])
        self.assertEqual("27", b2cs[12]["pos"])

    def test_hsn_is_split_b2b_and_b2c_and_a_missing_code_is_flagged(self):
        g1 = self.report["gstr1"]
        self.assertEqual({"3004"}, {r["hsn"] for r in g1["hsn_b2b"]})
        b2c = {r["hsn"]: r for r in g1["hsn_b2c"]}
        self.assertIn("30049099", b2c)
        self.assertEqual("TBS", b2c["30049099"]["uqc"])
        self.assertEqual(15.0, b2c["30049099"]["qty"])                 # 20 sold, 5 returned
        self.assertEqual("HSN nahi", b2c[""]["problem"])
        self.assertTrue(any(c["what"] == "HSN nahi" and c["where"] == "SCB4" for c in self.report["checks"]))

    def test_a_rate_taken_from_the_medicine_is_named(self):
        self.assertTrue(any(c["what"] == "GST % bill var navhta" and c["where"] == "SCB4"
                            for c in self.report["checks"]))

    def test_missing_bill_numbers_count_as_cancelled(self):
        self.conn.execute("DELETE FROM sales WHERE id=5")          # SCB3 deleted after it was made
        report = gr.build(self.conn, "2026-09-01", "2026-09-30")
        docs = [d for d in report["gstr1"]["docs"] if d["nature"] == "Invoices for outward supply"]
        self.assertEqual([{"nature": "Invoices for outward supply", "from": "SCB1", "to": "SCB4", "total": 4,
                           "cancelled": 1, "net_issued": 3}], docs)

    def test_a_bill_with_no_lines_is_named_not_guessed(self):
        self.assertTrue(any(c["what"] == "Bill var aushadh lines nahit" and c["where"] == "SCB3"
                            and "500" in c["detail"] for c in self.report["checks"]))
        self.assertNotIn("SCB3", {r["bill_no"] for r in self.report["sales_register"]})

    def test_the_portal_json(self):
        c = self.conn
        p = gr.load_period(c, "2026-09-01", "2026-09-30")
        js = gr.gstr1_json(p)
        self.assertEqual((SHOP_GSTIN, "092026"), (js["gstin"], js["fp"]))
        self.assertEqual(self.b2b_gstin, js["b2b"][0]["ctin"])
        self.assertEqual("05-09-2026", js["b2b"][0]["inv"][0]["idt"])
        self.assertTrue(all("camt" in r and "iamt" not in r for r in js["b2cs"]))
        self.assertEqual((4, 0), (js["doc_issue"]["doc_det"][0]["docs"][0]["totnum"],
                                  js["doc_issue"]["doc_det"][0]["docs"][0]["cancel"]))
        self.assertNotIn("", {r["hsn_sc"] for r in js["hsn"]["hsn_b2c"]})


class Gstr3bAndPurchases(_Store):
    def test_itc_is_the_registered_suppliers_bills_less_what_went_back(self):
        b = self.report["gstr3b"]
        self.assertEqual({"igst": 25.0, "cgst": 60.0, "sgst": 60.0, "cess": 0.0}, b["4(A)(5) All other ITC"])
        # PR1: 2 of 10 strips of LP-77 (1000 + 120): 200 taxable, 24 tax
        self.assertEqual({"igst": 0.0, "cgst": 12.0, "sgst": 12.0},
                         b["4(B)(2) Reversed: goods returned to suppliers"])
        self.assertEqual({"igst": 25.0, "cgst": 48.0, "sgst": 48.0, "cess": 0.0}, b["4(C) Net ITC available"])

    def test_outward_tax_is_net_of_credit_notes_and_nil_rated_apart(self):
        b = self.report["gstr3b"]
        g1 = self.report["gstr1"]
        cg = sum(r["cgst"] for t in ("b2b", "b2cs") for r in g1[t]) - sum(r["cgst"] for r in g1["cdnr"])
        self.assertAlmostEqual(cg, b["3.1(a) Outward taxable supplies"]["cgst"], places=2)
        self.assertEqual(40.0, b["3.1(c) Nil rated / exempted"]["taxable"])

    def test_a_supplier_from_another_state_is_igst(self):
        ka = next(r for r in self.report["purchase_register"] if r["bill_no"] == "KA-9")
        self.assertEqual((25.0, 0.0, 0.0), (ka["igst"], ka["cgst"], ka["sgst"]))
        ng = next(r for r in self.report["purchase_register"] if r["bill_no"] == "NG-1")
        self.assertTrue(ng["itc"].startswith("Nahi"))


class DocumentSeries(unittest.TestCase):
    def test_a_stray_number_is_its_own_run_not_a_billion_cancelled(self):
        p = gr.Period("2026-09-01", "2026-09-30", {"state": "27"})
        p.notes = [gr.Note(i, no, "2026-09-05", None, gr.ZERO, []) for i, no in
                   enumerate(["SR1", "SR2", "SR4", "SR1117777925"])]
        docs = gr.documents(p)
        self.assertEqual([("SR1", "SR4", 4, 1), ("SR1117777925", "SR1117777925", 1, 0)],
                         [(d["from"], d["to"], d["total"], d["cancelled"]) for d in docs])


class FiledAndCompared(_Store):
    def test_what_changed_after_filing_is_shown_bill_by_bill(self):
        c = self.conn
        saved = gr.save_filed(c, "2026-09-01", "2026-09-30", "filed with CA")
        self.assertEqual(1, len(gr.list_filed(c)))
        self.assertTrue(gr.compare_filed(c, saved["key"])["same"])
        c.execute("UPDATE sales_items SET amount=amount+11.2 WHERE sale_id=2 AND medicine_id=2")
        c.execute("UPDATE sales SET total_amount=total_amount+11.2 WHERE id=2")       # edited
        c.execute("DELETE FROM sales_items WHERE sale_id=3"); c.execute("DELETE FROM sales WHERE id=3")
        c.execute("INSERT INTO sales (id, bill_no, bill_date, customer_id, customer_name, total_amount)"
                  " VALUES (9, 'SCB9/FY2026-27', '2026-09-20', 1, 'RAMESH PATIL', 50)")
        c.execute("INSERT INTO sales_items (sale_id, medicine_id, qty, rate, gst_percent, amount)"
                  " VALUES (9, 1, 5, 10, 12, 50)")
        c.commit()
        diff = gr.compare_filed(c, saved["key"])
        self.assertFalse(diff["same"])
        what = {b["bill_no"]: b["what"] for b in diff["bills"]}
        self.assertTrue(what["SCB2"].startswith("Filed nantar badalla"))
        self.assertTrue(what["SCB4"].startswith("Filed nantar kadhla"))
        self.assertTrue(what["SCB9"].startswith("Filed nantar ala"))
        self.assertTrue(any(t["table"] == "b2b" for t in diff["tables"]))
        gr.delete_filed(c, saved["key"])
        self.assertEqual([], gr.list_filed(c))


class AShopWithoutAGstin(_Store):
    def test_every_report_works_and_nothing_is_refused(self):
        with mock.patch("core.pharmacy_profile_io.load_pharmacy_profile",
                        return_value={"name": "TEST MEDICAL", "gstin": "", "gst_enabled": 1}):
            report = gr.build(self.conn, "2026-09-01", "2026-09-30")
            js = gr.gstr1_json(gr.load_period(self.conn, "2026-09-01", "2026-09-30"))
        self.assertTrue(report["sales_register"])
        self.assertFalse(any("GSTIN" in c["what"] and "Dukan" in c["what"] for c in report["checks"]))
        self.assertEqual("", js["gstin"])
        self.assertEqual(0.0, next(r for r in report["tally"] if r["line"].startswith("PHARAK ("))["amount"])


class NothingIsWritten(_Store):
    def test_the_store_is_unchanged_by_a_report(self):
        before = self.conn.total_changes
        gr.build(self.conn, "2026-09-01", "2026-09-30")
        self.assertEqual(before, self.conn.total_changes)


class EveryGstReportOutAtOnce(_Store):
    """CSV / PDF / Print of every GST table at once, not only the tab on screen (3 Oct 2026)."""

    def _export(self, **extra):
        return gr.export(self.conn, {"from": "2026-09-01", "to": "2026-09-30", "tables": [],
                                     "page_layout": "landscape", **extra})

    def test_every_file_holds_every_table(self):
        import base64

        titles = [s["title"] for s in gr.sections(self.report) if s["rows"]]
        csv_text = base64.b64decode(self._export(format="csv")["content_base64"]).decode("utf-8-sig")
        for t in titles:
            self.assertIn(t, csv_text)
        pdf = self._export(format="pdf")
        self.assertTrue(pdf["ok"], pdf)
        self.assertTrue(base64.b64decode(pdf["content_base64"]).startswith(b"%PDF"))
        xlsx = self._export(format="xlsx")
        self.assertTrue(xlsx["ok"], xlsx)

    def test_every_table_goes_to_a_normal_printer(self):
        with mock.patch("core.desktop_export_service.print_pdf_on_printer", return_value="") as pr:
            res = self._export(print_to="printer")
        self.assertTrue(res["ok"], res)
        pr.assert_called_once()
        with open(res["path"], "rb") as fh:
            self.assertTrue(fh.read(4) == b"%PDF")

    def test_every_table_goes_to_the_dot_matrix(self):
        sent = []
        with mock.patch("core.dot_matrix_print.sys.platform", "win32"),                 mock.patch("core.printer_manager.PrinterManager.resolve_dot_matrix_printer", return_value="EPSON"),                 mock.patch("core.printer_manager.PrinterManager.print_raw_escp",
                           side_effect=lambda payload, printer, copies=1: sent.append(payload)):
            res = self._export(print_to="dot_matrix")
        self.assertTrue(res["ok"], res)
        self.assertEqual(1, len(sent))
        text = sent[0].decode("latin-1") if isinstance(sent[0], (bytes, bytearray)) else str(sent[0])
        for t in ("3B Summary", "Sales GST Register", "b2cs", "Purchase ITC Register"):
            self.assertIn(t.upper()[:10], text.upper())


if __name__ == "__main__":
    unittest.main()
