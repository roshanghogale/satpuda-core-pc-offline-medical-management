"""Import Bill in a wholesale store (owner, 18 Oct 2026: "image import must work in wholesale.
Rate A is filled manually if it is not present on the hard copy.").

* an imported line is mapped exactly as in retail -- MRP (kept: the wholesale invoice prints
  it), qty, free, rate, discount, GST %, HSN, batch, expiry, maker, pack;
* Rate A / B / C stay empty when the bill has no such columns, and each such line is marked;
  a bill that does print them keeps them; a retail store never gets them;
* the purchase is not saved until every line has Rate A; then it is, with MRP and the rates
  on the medicine batch, and a wholesale sale bills it at Rate A;
* a medicine with MRP but no Rate A still asks for Rate A on the sale side;
* the bill-photo footer checks a two-bill photo needed (slab GST, a foreign item count, a
  "GROUND FLOOR" address read as round-off).

Every bill here is made up. Throw-away SQLite stores, Offline, nothing reaches a server or
the AI service (the reply it would give is written out below).
"""
from __future__ import annotations

import copy
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db_setup  # noqa: E402
from core import gemini_bill_parser as gbp  # noqa: E402
from core import wholesale_rates as wr  # noqa: E402

# What the bill-reading service answers for a made-up three-line supplier bill.
GEMINI_REPLY = {
    "supplier": {"name": "TEST PHARMA DISTRIBUTORS", "address": "Shop 4, Test Market, Testpur",
                 "phone": "9000000001", "gstin": "", "dl_numbers": "20B-TEST-1",
                 "invoice_number": "TPD-0101", "invoice_date": "2026-10-15"},
    "totals": {"gross_amount": 1658.0, "total_cgst": 76.73, "total_sgst": 76.73,
               "net_payable": 1811.0, "product_discount": 0, "cash_discount": 0,
               "round_off": -0.46,
               "item_count": 3,
               "gst_slabs": [{"gst_pct": 5, "taxable": 650.0, "cgst": 16.25, "sgst": 16.25},
                             {"gst_pct": 12, "taxable": 1008.0, "cgst": 60.48, "sgst": 60.48}]},
    "items": [
        {"line_no": 1, "page": 1, "name": "TESTAMOL 650", "medicine_type": "", "schedule": "",
         "content_drug": "", "hsn_code": "30049099", "batch": "TA101", "expiry": "08/28",
         "pack": "15", "manufacturer": "TSTP", "qty": 20, "free_qty": 2, "rate": 21.40,
         "mrp": 32.50, "sgst_pct": 6, "cgst_pct": 6, "gst_pct": 12, "discount_pct": 0,
         "amount": 428.0, "rate_a": 0, "rate_b": 0, "rate_c": 0},
        {"line_no": 2, "page": 1, "name": "TESTO COUGH SYRUP", "medicine_type": "",
         "schedule": "", "content_drug": "", "hsn_code": "30049011", "batch": "SY202",
         "expiry": "11/27", "pack": "100ML", "manufacturer": "TSTL", "qty": 10, "free_qty": 0,
         "rate": 58.0, "mrp": 98.0, "sgst_pct": 6, "cgst_pct": 6, "gst_pct": 12,
         "discount_pct": 0, "amount": 580.0, "rate_a": 0, "rate_b": 0, "rate_c": 0},
        {"line_no": 3, "page": 1, "name": "TEST EXAM GLOVES", "medicine_type": "",
         "schedule": "", "content_drug": "", "hsn_code": "4015", "batch": "GL303",
         "expiry": "05/29", "pack": "1", "manufacturer": "TSTG", "qty": 100, "free_qty": 0,
         "rate": 6.5, "mrp": 15.0, "sgst_pct": 2.5, "cgst_pct": 2.5, "gst_pct": 5,
         "discount_pct": 0, "amount": 650.0, "rate_a": 0, "rate_b": 0, "rate_c": 0},
    ],
}

# Mapped like retail: these fields of every line must be the same in both kinds of store.
SAME_AS_RETAIL = ("name", "type", "batch", "expiry", "qty", "free_qty", "rate", "mrp",
                  "discount_pct", "gst_pct", "hsn_code", "manufacturer", "pack",
                  "quantity_value", "tablets_per_stripe", "amount", "item_amount")


class _Store(unittest.TestCase):
    """Offline, AppData in a temp folder; ``wholesale`` says what the open store is."""

    wholesale = True

    def setUp(self):
        self.appdata = tempfile.mkdtemp()
        for p in (
            mock.patch("core.sync_prefs.get_sync_mode", lambda: "offline"),
            mock.patch("core.sync_prefs.is_online_mode", return_value=False),
            mock.patch("core.license_manager._appdata_dir", lambda: self.appdata),
            mock.patch("core.online_catalog.medicine_by_id", return_value=None),
            mock.patch("core.server_crud.get_doc", return_value=None),
            mock.patch.dict(os.environ, {"SATPUDA_WHOLESALE_OVERRIDE": "1" if self.wholesale else "0"}),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.conn = sqlite3.connect(tempfile.mktemp(suffix=".db"), check_same_thread=False)
        db_setup.initialise(self.conn)
        self.addCleanup(self.conn.close)

    def import_bill(self, reply=None) -> dict:
        """Import Bill as the desktop runs it: start (the AI reply stands in for the photo),
        then apply."""
        from core import desktop_purchase_service as svc

        invoice = gbp._json_to_invoice(copy.deepcopy(reply or GEMINI_REPLY), "made_up_bill.pdf")
        fake = os.path.join(self.appdata, "made_up_bill.pdf")
        with open(fake, "wb") as f:
            f.write(b"%PDF-1.4 made up")
        with mock.patch("core.purchase_import_api.parse_invoice_worker", return_value=invoice):
            st = svc.start_purchase_import(self.conn, {"paths": [fake]})
        self.assertTrue(st.get("ok"), st)
        res = svc.apply_purchase_import(self.conn, {"import_token": st["import_token"]})
        self.assertTrue(res.get("ok"), res)
        return res

    def save(self, items, **body):
        from core import desktop_purchase_service as svc

        payload = {"supplier_name": "TEST PHARMA DISTRIBUTORS", "bill_number": "TPD-0101",
                   "purchase_date": "2026-10-15", "items": items, "cash_paid": 0, **body}
        return svc.save_purchase_bill(self.conn, payload)

    def purchase_count(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM purchases WHERE COALESCE(is_autosave,0)=0").fetchone()[0]


class RetailImport(_Store):
    wholesale = False

    def test_retail_lines_carry_no_rates_and_no_marks(self):
        reply = copy.deepcopy(GEMINI_REPLY)
        reply["items"][0]["rate_a"] = 25  # even if the reader filled it
        res = self.import_bill(reply)
        form = res["form"]
        self.assertNotIn("rate_a_needed", form)
        for it in form["items"]:
            for k in ("rate_a", "rate_b", "rate_c", "needs_rate_a"):
                self.assertNotIn(k, it)
        self.assertNotIn("Rate A", res["message"])
        # and a retail purchase still saves without any rate
        self.assertTrue(self.save(form["items"], import_bill_mode=True)["ok"])


class WholesaleImport(_Store):

    def retail_form(self) -> dict:
        with mock.patch.dict(os.environ, {"SATPUDA_WHOLESALE_OVERRIDE": "0"}):
            return self.import_bill()["form"]

    def test_lines_are_mapped_like_retail_and_keep_mrp(self):
        retail = self.retail_form()
        res = self.import_bill()
        form = res["form"]
        self.assertEqual(len(form["items"]), 3)
        for got, want in zip(form["items"], retail["items"]):
            self.assertEqual({k: got.get(k) for k in SAME_AS_RETAIL},
                             {k: want.get(k) for k in SAME_AS_RETAIL})
        tab = form["items"][0]
        self.assertEqual((tab["mrp"], tab["qty"], tab["free_qty"], tab["rate"], tab["gst_pct"]),
                         (32.5, 20.0, 2.0, 21.4, 12.0))
        self.assertEqual((tab["batch"], tab["expiry"], tab["hsn_code"], tab["manufacturer"]),
                         ("TA101", "08/28", "30049099", "TSTP"))
        self.assertEqual([i["mrp"] for i in form["items"]], [32.5, 98.0, 15.0])
        self.assertEqual(form["supplier_name"], "TEST PHARMA DISTRIBUTORS")
        self.assertEqual(form["bill_number"], "TPD-0101")

    def test_no_rate_columns_leaves_rates_empty_and_marks_every_line(self):
        res = self.import_bill()
        form = res["form"]
        self.assertEqual(form["rate_a_needed"], 3)
        for it in form["items"]:
            self.assertTrue(it["needs_rate_a"])
            for k in ("rate_a", "rate_b", "rate_c"):
                self.assertNotIn(k, it)
        self.assertIn("3 line(s) need Rate A", res["message"])
        self.assertEqual(wr.rate_a_missing_rows(form["items"]), [0, 1, 2])

    def test_a_bill_that_prints_rate_a_keeps_it(self):
        reply = copy.deepcopy(GEMINI_REPLY)
        reply["items"][1].update(rate_a=66.0, rate_b=64.5, rate_c=0)
        form = self.import_bill(reply)["form"]
        syrup = form["items"][1]
        self.assertEqual((syrup.get("rate_a"), syrup.get("rate_b"), syrup.get("rate_c")),
                         (66.0, 64.5, None))
        self.assertNotIn("needs_rate_a", syrup)
        self.assertEqual(form["rate_a_needed"], 2)

    def test_save_waits_for_rate_a_then_keeps_mrp_and_rates_on_the_batch(self):
        items = self.import_bill()["form"]["items"]
        refused = self.save(items, import_bill_mode=True)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["code"], "rate_a_required")
        self.assertEqual(refused["missing_rows"], [0, 1, 2])
        self.assertIn("3 line(s) need Rate A: TESTAMOL 650, TESTO COUGH SYRUP, TEST EXAM GLOVES",
                      refused["error"])
        self.assertEqual(self.purchase_count(), 0)  # nothing written

        items[0].update(rate_a=23.5, rate_b=23.0, rate_c=22.5)
        items[1]["rate_a"] = 64.0
        again = self.save(items, import_bill_mode=True)
        self.assertEqual((again["code"], again["missing_rows"]), ("rate_a_required", [2]))
        self.assertIn("1 line(s) need Rate A: TEST EXAM GLOVES", again["error"])
        self.assertEqual(self.purchase_count(), 0)

        items[2]["rate_a"] = 7.25
        ok = self.save(items, import_bill_mode=True)
        self.assertTrue(ok["ok"], ok)
        rows = self.conn.execute(
            "SELECT m.name, m.mrp, m.batch_no, pi.rate_a, pi.rate_b, pi.rate_c, m.rate_a "
            "FROM purchase_items pi JOIN medicines m ON m.id = pi.medicine_id "
            "WHERE pi.purchase_id=? ORDER BY pi.id", (ok["purchase_id"],)).fetchall()
        self.assertEqual(rows, [
            ("TESTAMOL 650", 32.5, "TA101", 23.5, 23.0, 22.5, 23.5),
            ("TESTO COUGH SYRUP", 98.0, "SY202", 64.0, None, None, 64.0),
            ("TEST EXAM GLOVES", 15.0, "GL303", 7.25, None, None, 7.25),
        ])

    def test_a_typed_line_without_rate_a_is_refused_too(self):
        res = self.save([{"name": "TEST TAB", "type": "Tablet", "batch": "B1", "expiry": "12/28",
                          "qty": 2, "rate": 100.0, "mrp": 140, "unit": "10", "gst_pct": 12}])
        self.assertEqual((res["ok"], res["code"], res["missing_rows"]), (False, "rate_a_required", [0]))


class WholesaleSaleOfImportedMedicines(_Store):
    """The sale side with medicines that came in through Import Bill."""

    def setUp(self):
        super().setUp()
        c = self.conn
        c.execute("DELETE FROM pharmacy_profile")
        c.execute("INSERT INTO pharmacy_profile (name, address, phone, gstin, dl_number) VALUES "
                  "('TEST WHOLESALE AGENCY', 'Shop 1, Testnagar', '9000000000', '', '20B-1')")
        c.execute("INSERT INTO customers (id, name, party_type, rate_tier) VALUES "
                  "(1, 'TEST MEDICAL ONE', 'medical', 'B')")
        c.commit()
        for p in (mock.patch("core.offline_first.runtime.is_active", return_value=False),):
            p.start()
            self.addCleanup(p.stop)

    def sale(self, lines):
        from core.desktop_sales_service import save_sale

        return save_sale(self.conn, {"invoice_mode": "wholesale", "customer_id": 1,
                                     "bill_date": "2026-10-18", "items": lines,
                                     "payment_mode": "Due"})

    def test_an_imported_line_sells_at_its_rate_a_and_shows_its_mrp(self):
        from core import wholesale_sale as ws

        items = self.import_bill()["form"]["items"]
        items[0]["rate_a"] = 23.5
        items[1]["rate_a"] = 64.0
        items[2]["rate_a"] = 7.25
        self.assertTrue(self.save(items, import_bill_mode=True)["ok"])
        mid = self.conn.execute("SELECT id FROM medicines WHERE name='TESTAMOL 650'").fetchone()[0]
        line = ws.build_line(self.conn, {"medicine_id": mid, "rate_tier": "B"})["line"]
        self.assertEqual((line["rate"], line["rate_tier"], line["mrp"]), (23.5, "A", 32.5))
        self.assertNotIn("rate_missing", line)
        res = self.sale([dict(line, qty=2)])
        self.assertTrue(res["ok"], res)

    def test_mrp_without_rate_a_asks_for_rate_a(self):
        from core import wholesale_sale as ws

        # a batch on the shelf with the bill's MRP and no Rate A (from before the rule)
        mid = self.conn.execute(
            "INSERT INTO medicines (name, type, unit, gst_percent, mrp, rate, stock_qty, batch_no, "
            "expiry_date, hsn_code) VALUES ('TEST OLD TAB', 'Tablet', '10', 12, 45, 30, 100, 'OT1', "
            "'2028-01-01', '30049099')").lastrowid
        self.conn.commit()
        line = ws.build_line(self.conn, {"medicine_id": mid, "rate_tier": "A"})["line"]
        self.assertEqual((line["rate"], line["mrp"]), (0.0, 45.0))
        self.assertTrue(line["rate_missing"])
        self.assertIn("Rate A is required", line["warning"])
        res = self.sale([dict(line, qty=1)])
        self.assertEqual((res["ok"], res["code"]), (False, "bad_rate"))
        self.assertIn("Rate A is required", res["error"])


class TheMarkingHelpers(unittest.TestCase):

    def test_needs_rate_a(self):
        self.assertTrue(wr.needs_rate_a({"name": "X", "rate_a": 0}))
        self.assertTrue(wr.needs_rate_a({"name": "X", "rate_a": ""}))
        self.assertTrue(wr.needs_rate_a({"name": "X", "rate_b": 5}))
        self.assertFalse(wr.needs_rate_a({"name": "X", "rate_a": "4.5"}))
        self.assertFalse(wr.needs_rate_a({"name": "X", "rateA": 4.5}))
        self.assertFalse(wr.needs_rate_a({"name": "", "rate_a": 0}))  # an empty row is not a line
        self.assertEqual(wr.rate_a_missing_rows([{"name": "A", "rate_a": 1}, {"name": "B"},
                                                 None, {"name": "C"}]), [1, 3])

    def test_the_refusal_names_three_then_counts(self):
        items = [{"name": f"TEST {n}"} for n in "PQRST"]
        ans = wr.rate_a_required_answer(items)
        self.assertEqual((ans["code"], ans["missing_count"]), ("rate_a_required", 5))
        self.assertIn("TEST P, TEST Q, TEST R and 2 more", ans["error"])
        self.assertIsNone(wr.rate_a_required_answer([{"name": "TEST P", "rate_a": 9}]))

    def test_mark_import_lines(self):
        lines = [{"name": "A", "rate_a": 10, "rate_b": "", "needs_rate_a": True},
                 {"name": "B", "rate_c": 3}, {"name": "C"}]
        self.assertEqual(wr.mark_import_lines(lines, wholesale=True), 2)
        self.assertEqual(lines[0], {"name": "A", "rate_a": 10.0})
        self.assertEqual(lines[1], {"name": "B", "rate_c": 3.0, "needs_rate_a": True})
        retail = [{"name": "A", "rate_a": 10, "needs_rate_a": True}]
        self.assertEqual(wr.mark_import_lines(retail, wholesale=False), 0)
        self.assertEqual(retail, [{"name": "A"}])


class TheBillPhotoFooter(unittest.TestCase):
    """Checks a photo of two overlapping bills needed (made-up figures)."""

    def reply(self, **totals):
        r = copy.deepcopy(GEMINI_REPLY)
        r["totals"].update(totals)
        return r

    def test_slab_gst_wins_when_the_footer_gst_belongs_to_another_bill(self):
        # the footer CGST / SGST of the bill behind; the slabs and net payable are this bill's
        inv = gbp._json_to_invoice(self.reply(total_cgst=250.0, total_sgst=250.0), "x.jpg")
        self.assertEqual((inv.total_cgst, inv.total_sgst), (76.73, 76.73))

    def test_footer_gst_that_adds_up_is_kept(self):
        inv = gbp._json_to_invoice(self.reply(total_cgst=80.0, total_sgst=80.0,
                                              net_payable=1817.54), "x.jpg")
        self.assertEqual((inv.total_cgst, inv.total_sgst), (80.0, 80.0))
        # neither adds up: nothing is guessed
        inv = gbp._json_to_invoice(self.reply(total_cgst=250.0, total_sgst=250.0,
                                              net_payable=3000.0), "x.jpg")
        self.assertEqual((inv.total_cgst, inv.total_sgst), (250.0, 250.0))

    def test_an_item_count_from_another_paper_is_dropped(self):
        inv = gbp._json_to_invoice(self.reply(item_count=30), "x.jpg")
        self.assertEqual(inv.expected_item_count, 0)
        self.assertFalse(any("rows were read" in s for s in inv.issues))

    def test_a_missing_page_still_warns(self):
        # the footer gross is above the rows read: a page is missing, the count stays
        inv = gbp._json_to_invoice(self.reply(item_count=30, gross_amount=5000.0), "x.jpg")
        self.assertEqual(inv.expected_item_count, 30)
        self.assertTrue(any("rows were read" in s for s in inv.issues))

    def test_ground_floor_in_an_address_is_not_a_round_off(self):
        from core.purchase_invoice_engine import extract_invoice_totals

        got = extract_invoice_totals("TEST AGENCIES\nSHOP NO1, GROUND FLOOR NO2, TEST ROAD\n"
                                     "Gross 1000.00\nRound Off 0.40\nNet Amt: 1050.00\n")
        self.assertEqual(got["round_off"], 0.40)
        got = extract_invoice_totals("TEST AGENCIES\nSHOP NO1, GROUND FLOOR NO2\nGross 1000.00\n")
        self.assertEqual(got["round_off"], 0.0)
        reply_text = json.dumps({"supplier": {"address": "GROUND FLOOR NO2"},
                                 "totals": {"round_off": 0}}, indent=2)
        self.assertEqual(extract_invoice_totals(reply_text)["round_off"], 0.0)


if __name__ == "__main__":
    unittest.main()
