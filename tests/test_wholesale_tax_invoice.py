"""The wholesale sale: a B2B tax invoice (core/wholesale_sale.py, owner 18 Oct 2026).

Rates exclude GST and GST is added on top; a buyer in the shop's state pays CGST + SGST
halves, one elsewhere IGST. The bill keeps packs, free packs, scheme and taxable value per
line, leaves stock in the same units a retail bill does, and its invoice fields go up to
the server and come back. Everything runs on in-memory stores; no server is touched.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from core import db_setup, wholesale_sale as ws
from core.wholesale_schema import BUSINESS_MODE_KEY

_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin(state: str, pan: str = "ABCDE1234F") -> str:
    """A well-formed GSTIN (with its check character) for a made-up business."""
    body = f"{state}{pan}1Z"
    total = 0
    for i, ch in enumerate(body):
        v = _CHARS.index(ch) * (2 if i % 2 else 1)
        total += v // 36 + v % 36
    return body + _CHARS[(36 - total % 36) % 36]


SHOP_GSTIN = gstin("27", "AAAPS1111A")      # Maharashtra
LOCAL_BUYER = gstin("27", "BBBPB2222B")     # same state
OTHER_BUYER = gstin("24", "CCCPC3333C")     # Gujarat


def _offline():
    return [
        mock.patch("core.sync_prefs.is_online_mode", return_value=False),
        mock.patch("core.offline_first.runtime.is_active", return_value=False),
        # the open store is a wholesale store (core/wholesale_mode.py)
        mock.patch.dict(os.environ, {"SATPUDA_WHOLESALE_OVERRIDE": "1"}),
    ]


def _store(mode: str = "wholesale") -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    db_setup.initialise(conn)
    # initialise() can seed a profile from the checkout's config/pharmacy_profile.json
    conn.execute("DELETE FROM pharmacy_profile")
    conn.execute(
        "INSERT INTO pharmacy_profile (name, address, phone, gstin, dl_number) VALUES "
        "('TEST WHOLESALE AGENCY', 'Shop 1, Market Road, Testnagar', '9000000000', ?, "
        "'20B-111, 21B-112')", (SHOP_GSTIN,))
    conn.execute("INSERT INTO settings (name, value) VALUES (?, ?)", (BUSINESS_MODE_KEY, mode))
    conn.execute(
        "INSERT INTO customers (id, name, phone, address, party_type, dl_numbers, rate_tier, "
        "credit_limit, credit_days, total_due) VALUES "
        "(1, 'TEST MEDICAL ONE', '9111111111', 'Main Road, Testpur', 'medical', '20B-9, 21B-10', "
        "'B', 1000, 30, 0)")
    conn.execute(
        "INSERT INTO customers (id, name, party_type, state_code, rate_tier) VALUES "
        "(2, 'TEST MEDICAL TWO', 'medical', '24', 'A')")
    # a strip of 10 tablets (stock in tablets), and a syrup sold by the bottle
    conn.execute(
        "INSERT INTO medicines (id, name, type, unit, gst_percent, mrp, rate, rate_a, rate_b, "
        "rate_c, stock_qty, batch_no, expiry_date, hsn_code, created_at) VALUES "
        "(1, 'TESTOCIN 500', 'Tablet', '10', 12, 120, 70, 90, 85, 80, 500, 'TB01', "
        "'2028-04-01', '30049099', '2026-01-01')")
    conn.execute(
        "INSERT INTO medicines (id, name, type, unit, gst_percent, mrp, rate, stock_qty, "
        "batch_no, expiry_date, hsn_code, created_at) VALUES "
        "(2, 'TESTO SYRUP', 'Syrup', '100ml', 5, 60, 40, 50, 'SY01', '2027-12-01', "
        "'30045010', '2026-01-01')")
    conn.commit()
    from core.desktop_settings_service import _setting_set

    _setting_set(conn, "customer_gst:1", json.dumps({"gstin": LOCAL_BUYER, "since": "2026-01-01"}))
    conn.commit()
    return conn


def _lines():
    return [
        {"id": 1, "qty": 3, "free": 1, "rate": 85, "disc_pct": 10, "scheme_pct": 5,
         "gst_percent": 12, "rate_tier": "B"},
        {"id": 2, "qty": 4, "free": 0, "rate": 40, "disc_pct": 0, "scheme_pct": 0,
         "gst_percent": 5},
    ]


class TheSum(unittest.TestCase):
    def test_a_line_adds_gst_on_top_of_the_rate(self):
        line = ws.calc_line({"qty": 3, "rate": 85, "disc_pct": 10, "scheme_pct": 5,
                             "gst_percent": 12}, intra=True)
        self.assertEqual(line["gross"], 255.00)
        self.assertEqual(line["disc_amt"], 25.50)
        self.assertEqual(line["scheme_amt"], 11.48)        # 5% of 229.50
        self.assertEqual(line["taxable"], 218.02)
        self.assertEqual(line["gst_amt"], 26.16)           # 12% of 218.02
        self.assertEqual((line["cgst"], line["sgst"], line["igst"]), (13.08, 13.08, 0.0))
        self.assertEqual(line["amount"], 244.18)

    def test_same_state_splits_cgst_and_sgst(self):
        inv = ws.calc_invoice(_lines(), shop_state="27", pos="27")
        self.assertTrue(inv["intra_state"])
        self.assertEqual(inv["taxable_total"], 378.02)
        self.assertEqual(inv["cgst_total"], 17.08)
        self.assertEqual(inv["sgst_total"], 17.08)
        self.assertEqual(inv["igst_total"], 0.0)
        self.assertEqual(inv["pre_round_total"], 412.18)
        self.assertEqual(inv["grand_total"], 412.0)
        self.assertEqual(inv["round_off"], -0.18)
        self.assertEqual(
            [(h["hsn_code"], h["gst_percent"], h["taxable"], h["cgst"], h["sgst"])
             for h in inv["hsn_summary"]],
            [("", 5.0, 160.0, 4.0, 4.0), ("", 12.0, 218.02, 13.08, 13.08)])

    def test_an_odd_paisa_goes_to_cgst(self):
        line = ws.calc_line({"qty": 1, "rate": 10.25, "gst_percent": 12}, intra=True)
        self.assertEqual(line["gst_amt"], 1.23)
        self.assertEqual((line["cgst"], line["sgst"]), (0.62, 0.61))

    def test_another_state_pays_igst(self):
        inv = ws.calc_invoice(_lines(), shop_state="27", pos="24")
        self.assertFalse(inv["intra_state"])
        self.assertEqual((inv["cgst_total"], inv["sgst_total"]), (0.0, 0.0))
        self.assertEqual(inv["igst_total"], 34.16)
        self.assertEqual(inv["grand_total"], 412.0)
        self.assertEqual(inv["hsn_summary"][1]["igst_rate"], 12.0)

    def test_place_of_supply_is_gstin_then_state_then_shop(self):
        self.assertEqual(ws.place_of_supply(OTHER_BUYER, "27", "27"), "24")
        self.assertEqual(ws.place_of_supply("", "24", "27"), "24")
        self.assertEqual(ws.place_of_supply("", "", "27"), "27")
        self.assertTrue(ws.is_intra_state("27", ""))     # no shop GSTIN: CGST + SGST

    def test_amount_in_words(self):
        self.assertEqual(ws.amount_in_words(412), "Rupees Four Hundred Twelve Only")
        self.assertEqual(ws.amount_in_words(1234567.5),
                         "Rupees Twelve Lakh Thirty Four Thousand Five Hundred Sixty Seven "
                         "and Fifty Paise Only")
        self.assertEqual(ws.amount_in_words(10_00_00_000), "Rupees Ten Crore Only")


class SavingAndReading(unittest.TestCase):
    def setUp(self):
        for p in _offline():
            p.start()
            self.addCleanup(p.stop)
        self.conn = _store()
        self.addCleanup(self.conn.close)

    def _save(self, **over):
        body = {"invoice_mode": "wholesale", "customer_id": 1, "bill_date": "2026-10-18",
                "items": _lines(), "payment_mode": "Due", "transport": "TEST ROADWAYS",
                "lr_no": "LR-77", "eway_bill_no": "331000000001"}
        body.update(over)
        from core.desktop_sales_service import save_sale

        return save_sale(self.conn, body)

    def test_the_party_carries_gstin_dl_tier_and_credit(self):
        p = ws.party(self.conn, 1)
        self.assertEqual((p["gstin"], p["state_code"], p["rate_tier"]), (LOCAL_BUYER, "27", "B"))
        self.assertEqual((p["credit_limit"], p["credit_days"]), (1000.0, 30))
        self.assertEqual(ws.party(self.conn, 2)["state_code"], "24")

    def test_a_line_takes_the_partys_tier_rate_else_rate_a_else_asks(self):
        line = ws.build_line(self.conn, {"medicine_id": 1, "rate_tier": "B"})["line"]
        self.assertEqual((line["rate"], line["rate_tier"], line["units_per_pack"]), (85.0, "B", 10))
        self.assertEqual(line["stock_packs"], 50.0)
        self.assertNotIn("warning", line)
        # owner, 18 Oct 2026: at least Rate A is required; nothing is made up from the cost
        self.conn.execute("UPDATE medicines SET rate_b = NULL WHERE id = 1")
        a_used = ws.build_line(self.conn, {"medicine_id": 1, "rate_tier": "B"})["line"]
        self.assertEqual((a_used["rate"], a_used["rate_tier"]), (90.0, "A"))
        self.assertIn("Rate A used", a_used["warning"])
        syrup = ws.build_line(self.conn, {"medicine_id": 2, "rate_tier": "C"})["line"]
        self.assertEqual((syrup["rate"], syrup["rate_tier"]), (0.0, ""))
        self.assertTrue(syrup["rate_missing"])
        self.assertIn("Rate A is required", syrup["warning"])

    def test_save_stores_the_invoice_and_takes_stock_in_units(self):
        res = self._save()
        self.assertTrue(res["ok"], res)
        sid = res["sale_id"]
        s = self.conn.execute(
            "SELECT invoice_mode, buyer_gstin, buyer_dl, place_of_supply, taxable_total, "
            "cgst_total, sgst_total, igst_total, due_date, transport, lr_no, eway_bill_no, "
            "total_amount, rounding, due_amount FROM sales WHERE id=?", (sid,)).fetchone()
        self.assertEqual(s, ("wholesale", LOCAL_BUYER, "20B-9, 21B-10", "27", 378.02, 17.08,
                             17.08, 0.0, "2026-11-17", "TEST ROADWAYS", "LR-77", "331000000001",
                             412.0, -0.18, 412.0))
        items = self.conn.execute(
            "SELECT medicine_id, qty, free_qty, scheme_pct, taxable, gst_amt, rate_tier, "
            "batch_no, expiry_date, hsn_code, amount, item_discount FROM sales_items "
            "WHERE sale_id=? ORDER BY id", (sid,)).fetchall()
        self.assertEqual(items[0], (1, 40, 1.0, 5.0, 218.02, 26.16, "B", "TB01", "2028-04-01",
                                    "30049099", 244.18, 36.98))
        self.assertEqual(items[1][:6], (2, 4, 0.0, 0.0, 160.0, 8.0))
        # (3 + 1 free) strips of 10 and four bottles left the shelf
        stock = dict(self.conn.execute("SELECT id, stock_qty FROM medicines").fetchall())
        self.assertEqual(stock, {1: 460, 2: 46})
        self.assertEqual(self.conn.execute(
            "SELECT total_due FROM customers WHERE id=1").fetchone()[0], 412.0)

    def test_reload_gives_back_packs_rates_and_discounts(self):
        sid = self._save()["sale_id"]
        got = ws.load_sale(self.conn, sid)
        self.assertTrue(got["ok"], got)
        first, second = got["items"]
        self.assertEqual((first["qty"], first["free"], first["rate"], first["disc_pct"],
                          first["scheme_pct"]), (3, 1, 85.0, 10.0, 5.0))
        self.assertEqual((second["qty"], second["free"], second["rate"]), (4, 0, 40.0))
        self.assertEqual(got["due_date"], "2026-11-17")
        # the screen's own sum of the reloaded lines is the stored bill
        again = ws.calc_invoice(got["items"], shop_state="27", pos="27")
        self.assertEqual(again["grand_total"], got["total_amount"])
        self.assertEqual(again["taxable_total"], got["taxable_total"])

    def test_an_edit_puts_back_the_old_stock_and_keeps_the_invoice(self):
        sid = self._save()["sale_id"]
        lines = _lines()[:1]
        lines[0]["qty"] = 5
        res = self._save(editing_sale_id=sid, items=lines, lr_no="")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["sale_id"], sid)
        stock = dict(self.conn.execute("SELECT id, stock_qty FROM medicines").fetchall())
        self.assertEqual(stock, {1: 440, 2: 50})
        row = self.conn.execute(
            "SELECT invoice_mode, lr_no, transport FROM sales WHERE id=?", (sid,)).fetchone()
        self.assertEqual(row, ("wholesale", None, "TEST ROADWAYS"))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM sales_items WHERE sale_id=?", (sid,)).fetchone()[0], 1)

    def test_another_state_saves_igst(self):
        res = self._save(customer_id=2)
        self.assertTrue(res["ok"], res)
        row = self.conn.execute(
            "SELECT place_of_supply, cgst_total, sgst_total, igst_total, buyer_gstin, due_date "
            "FROM sales WHERE id=?", (res["sale_id"],)).fetchone()
        self.assertEqual(row, ("24", 0.0, 0.0, 34.16, None, "2026-10-18"))

    def test_crossing_the_credit_limit_warns_and_saves(self):
        self.conn.execute("UPDATE customers SET total_due=800 WHERE id=1")
        self.conn.commit()
        calc = ws.calc_sale(self.conn, {"customer_id": 1, "items": _lines(),
                                        "bill_date": "2026-10-18"})
        self.assertTrue(calc["warnings"] and "Credit limit" in calc["warnings"][0], calc)
        res = self._save()
        self.assertTrue(res["ok"], res)
        self.assertIn("Credit limit", res["warnings"][0])

    def test_more_than_the_stock_is_refused(self):
        lines = _lines()
        lines[1]["qty"] = 51
        res = self._save(items=lines)
        self.assertEqual(res.get("code"), "insufficient_stock", res)

    def test_a_retail_store_cannot_make_one(self):
        with mock.patch.dict(os.environ, {"SATPUDA_WHOLESALE_OVERRIDE": "0"}):
            self.assertEqual(self._save().get("code"), "not_wholesale")

    def test_a_retail_bill_is_saved_as_before(self):
        from core.desktop_sales_service import save_sale

        res = save_sale(self.conn, {
            "customer_name": "WALK IN TEST", "bill_date": "2026-10-18", "payment_mode": "Cash",
            "cash_paid": 24.0,
            "items": [{"id": 1, "name": "TESTOCIN 500", "qty": 2, "rate": 12.0, "amount": 24.0,
                       "gst_percent": 12}]})
        self.assertTrue(res["ok"], res)
        row = self.conn.execute(
            "SELECT invoice_mode, taxable_total, due_date FROM sales WHERE id=?",
            (res["sale_id"],)).fetchone()
        self.assertEqual(row, (None, None, None))
        self.assertEqual(self.conn.execute(
            "SELECT free_qty, taxable FROM sales_items WHERE sale_id=?",
            (res["sale_id"],)).fetchone(), (None, None))

    def test_the_retail_form_will_not_open_or_save_over_a_tax_invoice(self):
        from core.desktop_sales_service import load_sale, save_sale

        sid = self._save()["sale_id"]
        self.assertEqual(load_sale(self.conn, sid).get("code"), "wholesale_bill")
        res = save_sale(self.conn, {"editing_sale_id": sid, "customer_name": "TEST MEDICAL ONE",
                                    "items": [{"id": 1, "qty": 1, "rate": 12, "amount": 12}],
                                    "payment_mode": "Due"})
        self.assertEqual(res.get("code"), "wholesale_bill")


class UpToTheServerAndBack(unittest.TestCase):
    def setUp(self):
        for p in _offline():
            p.start()
            self.addCleanup(p.stop)
        self.conn = _store()
        self.addCleanup(self.conn.close)
        from core.desktop_sales_service import save_sale

        self.sid = save_sale(self.conn, {
            "invoice_mode": "wholesale", "customer_id": 1, "bill_date": "2026-10-18",
            "items": _lines(), "payment_mode": "Due", "transport": "TEST ROADWAYS",
            "lr_no": "LR-77"})["sale_id"]

    def test_both_push_builders_carry_the_fields(self):
        from core import server_entity_sync as fb
        from core.server_sync import _build_sales_docs

        one = fb.build_sale_payload(self.conn, self.sid)
        bulk = _build_sales_docs(self.conn, ids=[self.sid])[0]
        for doc in (one, bulk):
            self.assertEqual(doc["invoice_mode"], "wholesale")
            self.assertEqual(doc["buyer_gstin"], LOCAL_BUYER)
            self.assertEqual(doc["place_of_supply"], "27")
            self.assertEqual(doc["taxable_total"], 378.02)
            self.assertEqual(doc["due_date"], "2026-11-17")
            self.assertEqual(doc["eway_bill_no"], "")       # sent blank, so the server clears it
            line = doc["items"][0]
            self.assertEqual((line["free_qty"], line["scheme_pct"], line["taxable"],
                              line["gst_amt"], line["rate_tier"], line["hsn_code"]),
                             (1.0, 5.0, 218.02, 26.16, "B", "30049099"))

    def test_a_retail_document_is_unchanged(self):
        from core import server_entity_sync as fb
        from core.desktop_sales_service import save_sale

        res = save_sale(self.conn, {
            "customer_name": "WALK IN TEST", "bill_date": "2026-10-18", "payment_mode": "Cash",
            "cash_paid": 24.0, "items": [{"id": 1, "qty": 2, "rate": 12.0, "amount": 24.0}]})
        doc = fb.build_sale_payload(self.conn, res["sale_id"])
        for key in ws.SALE_FIELDS:
            self.assertNotIn(key, doc)
        for key in ("free_qty", "scheme_pct", "taxable", "gst_amt", "rate_tier"):
            self.assertNotIn(key, doc["items"][0])

    def test_a_pulled_sale_lands_with_its_invoice(self):
        from core import server_entity_sync as fb
        from core import server_live as sl
        from core.server_sync import _build_sales_docs

        doc = _build_sales_docs(self.conn, ids=[self.sid])[0]
        doc = json.loads(json.dumps(doc, default=str))   # as it travels
        other = _store()
        self.addCleanup(other.close)
        with fb.offline_first_apply():
            status = sl.apply_server_doc(other, "sales", doc)
        self.assertIn(status, ("applied", "inserted", "updated", "ok"), status)
        cols = ", ".join(ws.SALE_FIELDS)
        here = self.conn.execute(f"SELECT {cols} FROM sales WHERE id=?", (self.sid,)).fetchone()
        there = other.execute(f"SELECT {cols} FROM sales WHERE id=?", (self.sid,)).fetchone()
        self.assertEqual(there, here)
        lcols = ", ".join(ws.LINE_FIELDS)
        q = f"SELECT medicine_id, qty, {lcols} FROM sales_items WHERE sale_id=? ORDER BY id"
        self.assertEqual(other.execute(q, (self.sid,)).fetchall(),
                         self.conn.execute(q, (self.sid,)).fetchall())
        self.assertTrue(ws.load_sale(other, self.sid)["ok"])


class TheTaxInvoice(unittest.TestCase):
    def setUp(self):
        for p in _offline():
            p.start()
            self.addCleanup(p.stop)
        self.conn = _store()
        self.addCleanup(self.conn.close)
        from core.desktop_sales_service import save_sale

        self.save = lambda **kw: save_sale(self.conn, {
            "invoice_mode": "wholesale", "customer_id": 1, "bill_date": "2026-10-18",
            "items": _lines(), "payment_mode": "Due", "transport": "TEST ROADWAYS",
            "lr_no": "LR-77", **kw})["sale_id"]

    def test_it_prints_every_block(self):
        from bill_templates.wholesale_invoice import render_wholesale_invoice_html

        html = render_wholesale_invoice_html(ws.invoice_context(self.conn, self.save()))
        for text in ("TAX INVOICE", "TEST WHOLESALE AGENCY", SHOP_GSTIN, "20B-111, 21B-112",
                     "TEST MEDICAL ONE", LOCAL_BUYER, "20B-9, 21B-10", "Maharashtra (27)",
                     "Place of Supply", "Due Date", "17-11-2026", "TEST ROADWAYS", "LR-77",
                     "30049099", "TB01", "04/28", "CGST", "SGST", "Round Off", "-0.18",
                     "412.00", "Rupees Four Hundred Twelve Only", "For TEST WHOLESALE AGENCY",
                     "Authorised Signatory", "10 + 5 sch", "size: A4 portrait"):
            self.assertIn(text, html)
        self.assertNotIn("IGST %", html)
        self.assertNotIn("E-way Bill No.", html)

    def test_another_state_prints_igst_on_a5(self):
        from bill_templates.wholesale_invoice import render_wholesale_invoice_html

        html = render_wholesale_invoice_html(
            ws.invoice_context(self.conn, self.save(customer_id=2)), paper="A5")
        self.assertIn("IGST %", html)
        self.assertIn("Gujarat (24)", html)
        self.assertIn("Unregistered", html)
        self.assertNotIn("CGST %", html)
        self.assertIn("size: 210mm 148mm", html)

    def test_bank_details_print_when_the_shop_has_them(self):
        self.conn.execute("INSERT INTO settings (name, value) VALUES ('invoice_bank_details', "
                          "'Bank: TEST BANK\nA/c No: 000111222\nIFSC: TEST0000001')")
        self.conn.commit()
        from bill_templates.wholesale_invoice import render_wholesale_invoice_html

        html = render_wholesale_invoice_html(ws.invoice_context(self.conn, self.save()))
        self.assertIn("Bank Details", html)
        self.assertIn("TEST0000001", html)

    def test_save_pdf_print_and_preview_use_the_tax_invoice(self):
        from core import bill_output
        from core.desktop_sales_service import get_bill_preview

        sid = self.save()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(bill_output, "_try_pdf_via_browser", return_value=True) as pdf, \
                mock.patch("core.bill_save_prefs.resolve_sales_bill_save_dir", return_value=tmp), \
                mock.patch.object(bill_output, "resolve_bill_db_path", return_value=""):
            pages = bill_output.save_bill_pdf_pages(self.conn, sid, print_slot=1)
            self.assertEqual(len(pages), 1)
            html_path, pdf_path = pages[0]
            with open(html_path, encoding="utf-8") as fh:
                self.assertIn("TAX INVOICE", fh.read())
            self.assertTrue(pdf_path.endswith(".pdf"))
            self.assertIn(pdf.call_args.kwargs.get("paper_width_mm"), (210.0,))
            a6 = bill_output.save_bill_pdf_a6(self.conn, sid)
            self.assertTrue(a6[0].endswith(".html"))
        prev = get_bill_preview(self.conn, sid)
        self.assertTrue(prev["ok"], prev)
        self.assertEqual(prev["template"], "wholesale")
        self.assertIn("TAX INVOICE", prev["html"])


if __name__ == "__main__":
    unittest.main()
