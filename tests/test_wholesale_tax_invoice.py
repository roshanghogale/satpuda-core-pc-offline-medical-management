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

    def test_the_bills_phone_address_go_onto_the_party_and_gstin_dl_onto_the_bill(self):
        # owner, 18 Oct 2026: the wholesale bill has the retail bill's Phone / Address boxes
        # (they update the party, as on retail) and GSTIN / DL boxes in the doctor's places
        res = self._save(customer_phone="9222222222", customer_address="",
                         buyer_dl="20B-99, 21B-100")
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.conn.execute(
            "SELECT phone, address FROM customers WHERE id=1").fetchone(),
            ("9222222222", "Main Road, Testpur"))       # an empty box never wipes one
        self.assertEqual(self.conn.execute(
            "SELECT buyer_dl FROM sales WHERE id=?", (res["sale_id"],)).fetchone(),
            ("20B-99, 21B-100",))
        self.assertEqual(ws.party(self.conn, 1)["dl_numbers"], "20B-9, 21B-10")
        self.assertTrue(self._save(customer_address="New Road, Testpur")["ok"])
        self.assertEqual(self.conn.execute(
            "SELECT phone, address FROM customers WHERE id=1").fetchone(),
            ("9222222222", "New Road, Testpur"))


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
    """The GST invoice laid out like the shop's own wholesale invoice (owner, 18 Oct 2026):
    always A5 landscape, two to an A4 sheet."""

    def setUp(self):
        for p in _offline():
            p.start()
            self.addCleanup(p.stop)
        self.conn = _store()
        self.addCleanup(self.conn.close)
        self.conn.execute("UPDATE medicines SET manufacturer='TESTCARE' WHERE id=1")
        self.conn.execute("UPDATE medicines SET mrp=0 WHERE id=2")
        self.conn.commit()
        from core.desktop_sales_service import save_sale

        self.save = lambda **kw: save_sale(self.conn, {
            "invoice_mode": "wholesale", "customer_id": 1, "bill_date": "2026-10-18",
            "items": _lines(), "payment_mode": "Due", "transport": "TEST ROADWAYS",
            "lr_no": "LR-77", **kw})["sale_id"]

    def _html(self, sid, paper="A5", **over):
        from bill_templates.wholesale_invoice import render_wholesale_invoice_html

        inv = ws.invoice_context(self.conn, sid)
        inv.update(over)
        return render_wholesale_invoice_html(inv, paper=paper)

    def _return_in_bill(self, sid, amount=50.0):
        """A return of one TESTO SYRUP settled in this bill (allocations, as
        core/return_credit records them)."""
        self.conn.execute(
            "INSERT INTO sales_returns (id, return_no, customer_id, return_date, refund_amount, "
            "allocations) VALUES (1, 'SR1-7', 1, '2026-10-17', ?, ?)",
            (amount, json.dumps([{"bill_id": sid, "amount": amount}])))
        self.conn.execute("INSERT INTO sales_return_items (return_id, medicine_id, qty, rate, "
                          "amount) VALUES (1, 2, 1, ?, ?)", (amount, amount))
        self.conn.commit()

    def test_it_prints_every_block(self):
        html = self._html(self.save())
        # header: the shop, the title with the payment, the buyer
        for text in ("TEST WHOLESALE AGENCY", "Phone : 9000000000", "D.L. : 20B-111, 21B-112",
                     f"GSTIN : {SHOP_GSTIN}", "GST INVOICE", "(CREDIT)", "Invoice No.",
                     "18-10-2026", "Due Date", "17-11-2026", "TEST ROADWAYS", "LR-77",
                     "Buyer Name", "M/s TEST MEDICAL ONE", "MAIN ROAD, TESTPUR",
                     "PHONE NO. : 9111111111", "D.L.No. : 20B-9, 21B-10",
                     f"Party GSTIN : {LOCAL_BUYER}"):
            self.assertIn(text, html)
        # the item table's columns, in the reference invoice's order
        heads = ["S.", "PRODUCT NAME", "MFG.", "PACK", "HSN", "BATCH", "EXP.", "MRP", "QTY",
                 "FREE", "RATE", "SGST%", "CGST%", "GROSS AMT."]
        self.assertIn("".join(f"<th>{h}</th>" for h in heads), html)
        self.assertNotIn("IGST%", html)
        # a line: MFG, PACK, HSN, batch, expiry, the batch's MRP, packs, free, pack rate,
        # half the GST each side, packs x rate
        row = ("<td class='c'>1</td><td class='t'>TESTOCIN 500</td><td class='l'>TESTCARE</td>"
               "<td class='l'>1x10</td><td class='c'>30049099</td><td class='l'>TB01</td>"
               "<td class='c'>04/28</td><td class='n'>120.00</td><td class='n'>3</td>"
               "<td class='n'>1</td><td class='n'>85.00</td><td class='n'>6.00</td>"
               "<td class='n'>6.00</td><td class='n'>255.00</td>")
        self.assertIn(row, html)
        # no MRP on the syrup's batch: blank, never 0.00
        self.assertIn("<td class='c'>12/27</td><td class='n'></td><td class='n'>4</td>", html)
        self.assertIn("<tr class='filler'>", html)            # the rules run to the footer
        # GST table: a row for every standard rate, zeros blank
        self.assertIn("<th>GST%</th><th>TAXABLE</th><th>SGST</th><th>CGST</th><th>TOTAL</th>",
                      html)
        self.assertIn("<tr><td>0.00</td><td class='n'></td><td class='n'></td>"
                      "<td class='n'></td><td class='n'></td></tr>", html)
        self.assertIn("<tr><td>5.00</td><td class='n'>160.00</td><td class='n'>4.00</td>"
                      "<td class='n'>4.00</td><td class='n'>168.00</td></tr>", html)
        self.assertIn("<tr><td>12.00</td><td class='n'>218.02</td><td class='n'>13.08</td>"
                      "<td class='n'>13.08</td><td class='n'>244.18</td></tr>", html)
        for pct in ("18.00", "28.00"):
            self.assertIn(f"<tr><td>{pct}</td>", html)
        # totals, as the engine stored them
        for label, value in (("SUB TOTAL", "415.00"), ("Discount", "36.98"), ("Bill Disc", ""),
                             ("SGST Value", "17.08"), ("CGST Value", "17.08"),
                             ("CR/DR", "-0.18"), ("CR/DR NOTE", "0.00")):
            self.assertIn(f"<tr><td>{label}</td><td class='n'>{value}</td></tr>", html)
        self.assertIn("<tr class='party'><td>PARTY TOTAL</td><td class='n'>412.00</td></tr>", html)
        for text in ("Rs. Four Hundred Twelve only", "Declaration :-",
                     "Subject to TESTNAGAR jurisdiction only.",
                     "Total Item / Quantity : 2 / 7", "LEDGER BAL. : 412.00",
                     "Sign of Customer", "For TEST WHOLESALE AGENCY", "size: 210mm 148mm"):
            self.assertIn(text, html)
        self.assertNotIn("BANK DETAILS", html)                 # none set up

    def test_the_shops_own_declaration(self):
        self.conn.execute("INSERT INTO settings (name, value) VALUES ('invoice_declaration', "
                          "'TEST TERMS. Subject to {city} courts.')")
        self.conn.commit()
        self.assertIn("TEST TERMS. Subject to TESTNAGAR courts.", self._html(self.save()))

    def test_another_state_prints_igst_and_a_paid_bill_says_cash(self):
        html = self._html(self.save(customer_id=2, payment_mode="Cash", pay_full=True))
        for text in ("IGST%", "<th>IGST</th>", "IGST Value", "34.16", "(CASH)",
                     "Place of Supply : Gujarat (24)", "Paid Rs 412.00", "LEDGER BAL. : 0.00"):
            self.assertIn(text, html)
        self.assertNotIn("SGST%", html)
        self.assertNotIn("CGST Value", html)
        self.assertIn("<td class='n'>12.00</td><td class='n'>255.00</td>", html)
        # the due date is the bill date for a cash party: not printed twice
        self.assertNotIn("Due Date", html)

    def test_bank_details_from_the_settings_text(self):
        self.conn.execute("INSERT INTO settings (name, value) VALUES ('invoice_bank_details', "
                          "'Bank: TEST BANK\nBranch: TESTNAGAR BR.\nA/c No: 000111222\n"
                          "IFSC: TEST0000001')")
        self.conn.commit()
        html = self._html(self.save())
        for text in ("BANK DETAILS", "TEST BANK (TESTNAGAR BR.)", "A/C NO. : 000111222",
                     "IFSC CODE : TEST0000001"):
            self.assertIn(text, html)

    def test_the_profiles_bank_fields_win(self):
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(pharmacy_profile)")}
        for col in ("bank_name", "bank_account_no", "bank_ifsc", "bank_branch"):
            if col not in cols:
                self.conn.execute(f"ALTER TABLE pharmacy_profile ADD COLUMN {col} TEXT")
        self.conn.execute("UPDATE pharmacy_profile SET bank_name='PROFILE BANK', "
                          "bank_account_no='999888777', bank_ifsc='PROF0000009', "
                          "bank_branch='MAIN BR.'")
        self.conn.execute("INSERT INTO settings (name, value) VALUES ('invoice_bank_details', "
                          "'Bank: SETTINGS BANK')")
        self.conn.commit()
        html = self._html(self.save())
        for text in ("PROFILE BANK (MAIN BR.)", "A/C NO. : 999888777", "IFSC CODE : PROF0000009"):
            self.assertIn(text, html)
        self.assertNotIn("SETTINGS BANK", html)

    def test_a_return_used_in_the_bill_prints_its_rows_and_the_note(self):
        sid = self.save()
        self._return_in_bill(sid)
        inv = ws.invoice_context(self.conn, sid)
        self.assertEqual((inv["returns_total"], inv["party_total"]), (50.0, 362.0))
        html = self._html(sid)
        self.assertIn("Return SR1-7 17-10-2026", html)
        self.assertIn("<td class='t'>TESTO SYRUP</td>", html)
        self.assertIn("Return total", html)
        self.assertEqual(html.count(">-50.00<"), 3)   # the medicine, its total, the CR/DR NOTE
        self.assertIn("<tr><td>CR/DR NOTE</td><td class='n'>-50.00</td></tr>", html)
        self.assertIn("<tr class='party'><td>PARTY TOTAL</td><td class='n'>362.00</td></tr>", html)
        self.assertIn("Rs. Three Hundred Sixty Two only", html)
        self.assertIn("LEDGER BAL. : 362.00", html)

    def test_a_return_settled_through_the_screen_prints_in_strips(self):
        from core import desktop_returns_service as returns_svc

        self.conn.execute("UPDATE customers SET credit_limit=0 WHERE id=1")
        self.conn.commit()
        first = self.save(payment_mode="Cash", pay_full=True, items=[
            {"id": 1, "qty": 10, "rate": 90, "gst_percent": 12, "rate_tier": "A"}])
        loaded = returns_svc.load_sales_bill_for_return(self.conn, first)
        it = loaded["items"][0]
        ret = returns_svc.save_sales_return(self.conn, {
            "sale_id": first, "customer_id": 1, "settle_mode": "ledger", "discount": 0,
            "items": [{"medicine_id": 1, "qty": 2 * it["tablets_per_stripe"], "rate": it["rate"],
                       "orig_qty": it["orig_qty"], "orig_amount": it["amount"],
                       "type": it["type"], "is_tablet": it["is_tablet"]}]})
        self.assertTrue(ret.get("ok"), ret)                     # 2 strips: 201.60 credit
        sid = self.save(apply_returns=[ret["return_id"]])
        inv = ws.invoice_context(self.conn, sid)
        self.assertEqual((inv["returns_total"], inv["party_total"]), (201.6, 210.4))
        html = self._html(sid)
        self.assertIn(f"Return {ret['return_no']}", html)
        # the medicine that came back, 2 strips (not 20 tablets), minus its amount
        self.assertIn("<td class='t'>TESTOCIN 500</td>", html)
        self.assertIn("<td class='n'>2</td>", html)
        self.assertIn(">-201.60<", html)
        self.assertIn("<tr><td>CR/DR NOTE</td><td class='n'>-201.60</td></tr>", html)
        self.assertIn("<tr class='party'><td>PARTY TOTAL</td><td class='n'>210.40</td></tr>", html)

    def test_a_receipt_that_cleared_the_bill_is_shown(self):
        sid = self.save()
        self.conn.execute(
            "INSERT INTO customer_payments (customer_id, payment_date, amount, payment_mode, "
            "allocations, receipt_no) VALUES (1, '2026-10-19', 100, 'Cash', ?, 'R-15')",
            (json.dumps([{"bill_id": sid, "amount": 100}]),))
        self.conn.commit()
        html = self._html(sid)
        self.assertIn("Paid Rs 100.00 (Rcpt R-15)", html)
        # the ledger balance is the one after this bill, not after later payments
        self.assertIn("LEDGER BAL. : 412.00", html)

    def test_the_signature_is_signed_over_the_shops_name(self):
        sid = self.save()
        pic = "data:image/png;base64,iVBORw0KGgo="
        html = self._html(sid, signature_src=pic, signature_look="transform: rotate(-3deg);")
        self.assertIn(f"<img class='sign-img' src='{pic}' alt='' "
                      "style='transform: rotate(-3deg);'><span>For TEST WHOLESALE AGENCY</span>",
                      html)
        self.assertNotIn("class='sign-img'", self._html(sid, signature_src=""))
        with mock.patch("core.bill_config.load_bill_print_settings",
                        return_value={"print_signature_image": False}):
            self.assertEqual(ws.invoice_context(self.conn, sid)["signature_src"], "")

    def test_a4_takes_two_invoices_a_sheet_a5_one(self):
        from bill_templates.wholesale_invoice import render_wholesale_invoices_html

        invs = [ws.invoice_context(self.conn, self.save()) for _ in range(3)]
        a4 = render_wholesale_invoices_html(invs, paper="A4")
        self.assertEqual(a4.count("<div class='sheet'>"), 2)
        self.assertEqual(a4.count("<div class='half'>"), 3)
        self.assertEqual(a4.count("<div class='cut'></div>"), 1)   # only between two halves
        self.assertIn("size: 210mm 297mm", a4)
        a5 = render_wholesale_invoices_html(invs, paper="A5")
        self.assertEqual(a5.count("<div class='sheet'>"), 3)
        self.assertNotIn("<div class='cut'>", a5)
        # one invoice on an A4 slot: the top half of a sheet
        one = render_wholesale_invoices_html(invs[:1], paper="A4")
        self.assertEqual((one.count("<div class='sheet'>"), one.count("<div class='half'>")),
                         (1, 1))
        for paper, want in (("A4", "A4"), ("A5", "A5"), ("A6", "A5"), ("", "A5")):
            self.assertEqual(ws.invoice_paper({"paper_size": paper}), want)

    def test_a_long_bill_goes_on_over_more_boxes(self):
        from bill_templates import wholesale_invoice as tpl

        inv = ws.invoice_context(self.conn, self.save())
        inv["lines"] = [dict(inv["lines"][0]) for _ in range(tpl.ROWS_PER_PAGE + 1)]
        html = tpl.render_wholesale_invoices_html([inv], paper="A5")
        self.assertEqual(html.count("<div class='sheet'>"), 2)
        self.assertIn("Continued on next page", html)
        self.assertEqual(html.count("PARTY TOTAL"), 1)          # the totals on the last box
        self.assertIn("1 of 2", html)

    def test_save_pdf_print_and_preview_use_the_invoice(self):
        from core import bill_output
        from core.desktop_sales_service import get_bill_preview

        sid = self.save()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(bill_output, "_try_pdf_via_browser", return_value=True) as pdf, \
                mock.patch("core.bill_save_prefs.resolve_sales_bill_save_dir", return_value=tmp), \
                mock.patch.object(bill_output, "resolve_bill_db_path", return_value=""):
            pages = bill_output.save_bill_pdf_pages(
                self.conn, sid, print_slot=1, settings_override={"paper_size": "A5"})
            self.assertEqual(len(pages), 1)
            html_path, pdf_path = pages[0]
            with open(html_path, encoding="utf-8") as fh:
                self.assertIn("GST INVOICE", fh.read())
            self.assertTrue(pdf_path.endswith(".pdf"))
            self.assertEqual((pdf.call_args.kwargs.get("paper_width_mm"),
                              pdf.call_args.kwargs.get("paper_height_mm")), (210.0, 148.0))
            bill_output.save_bill_pdf_pages(
                self.conn, sid, print_slot=1, settings_override={"paper_size": "A4"})
            self.assertEqual(pdf.call_args.kwargs.get("paper_height_mm"), 297.0)
            a6 = bill_output.save_bill_pdf_a6(self.conn, sid)
            self.assertTrue(a6[0].endswith(".html"))
        prev = get_bill_preview(self.conn, sid)
        self.assertTrue(prev["ok"], prev)
        self.assertEqual(prev["template"], "wholesale")
        self.assertIn("GST INVOICE", prev["html"])

    def test_print_all_puts_two_invoices_on_an_a4_sheet(self):
        from core import bill_output, history_print

        ids = [self.save() for _ in range(3)]
        seen = []

        def fake_pdf(html_path, pdf_path, **kw):
            with open(html_path, encoding="utf-8") as fh:
                seen.append((fh.read(), kw.get("paper_height_mm")))
            with open(pdf_path, "wb") as fh:
                fh.write(b"%PDF-1.4\n")
            return True

        with mock.patch.object(bill_output, "_try_pdf_via_browser", side_effect=fake_pdf), \
                mock.patch.object(bill_output, "resolve_bill_db_path", return_value=""), \
                mock.patch.object(history_print, "_dot_matrix_mode", return_value=False), \
                mock.patch.object(history_print, "_remove_later"), \
                mock.patch("core.printer_manager.PrinterManager.get_printer_for_slot",
                           return_value="TEST PRINTER"), \
                mock.patch("core.printer_manager.PrinterManager.print_pdf_silently") as printed, \
                mock.patch.object(history_print.time, "sleep"):
            pages, failures = history_print.print_bills_batch(
                self.conn, ids, paper="A4", copy_label="STORE COPY")
            self.assertEqual(failures, [])
            self.assertEqual((pages, printed.call_count), (1, 1))
            html, height = seen[-1]
            self.assertEqual(height, 297.0)
            self.assertEqual(html.count("<div class='sheet'>"), 2)   # 3 invoices, 2 sheets
            self.assertEqual(html.count("STORE COPY"), 3)
            history_print.print_bills_batch(self.conn, ids, paper="A5")
            html, height = seen[-1]
            self.assertEqual((html.count("<div class='sheet'>"), height), (3, 148.0))


if __name__ == "__main__":
    unittest.main()
