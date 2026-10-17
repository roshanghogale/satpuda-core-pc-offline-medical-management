"""Two asks of 17 Oct 2026.

1. Print All (standard HTML print) has a copy dropdown: nothing / Customer Copy / Store Copy,
   printed just under each bill, outside its border. Only Print All sends it.
2. Purchase entry: leaving the MRP or Rate box shows a popup when any supplier last sold the
   same medicine (same pack, by MRP) for less. The old Tk screen checked only the same
   supplier; the Tauri page never checked.

Nothing here prints or reaches the server: the PDF step, the printer and the store query are
stubbed.
"""
from __future__ import annotations

import os
import re
import sqlite3
import unittest
from unittest import mock

from tests._bill_fixtures import discounted_bill_context, isolated_bill_settings

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _settings(paper: str, label: str = "") -> dict:
    from core.bill_config import apply_print_bill_layout, get_print_slot_settings, load_bill_print_settings

    slot = get_print_slot_settings(dict(load_bill_print_settings()), 2)
    slot["paper_size"] = paper
    slot["bill_copies"] = 1
    rs = apply_print_bill_layout(slot, print_slot_copies=1)
    rs["paper_size"] = paper
    rs["copy_footer_label"] = label
    return rs


class TheLabelOnTheBill(unittest.TestCase):
    def setUp(self):
        self.ctx = discounted_bill_context()

    def render(self, n: int, paper: str, label: str) -> str:
        from bill_templates.classic import render_classic_bill_html_multi

        with isolated_bill_settings():
            return render_classic_bill_html_multi([self.ctx] * n, _settings(paper, label))

    def test_each_bill_on_the_sheet_carries_it_after_its_invoice(self):
        html = self.render(2, "A5", "Store Copy")
        self.assertEqual(html.count('<div class="copy-tag">STORE COPY</div>'), 2)
        # outside the bordered invoice: the tag frame follows the invoice's closing div
        shells = re.findall(r'<section class="copy-shell[^"]*">(.*?)</section>', html, re.S)
        self.assertEqual(len(shells), 2)
        for body in shells:
            self.assertGreater(body.index("copy-tag-frame"), body.index("upright-invoice"))

    def test_rotated_a4_bills_turn_the_label_with_them(self):
        html = self.render(4, "A4", "Customer Copy")
        self.assertEqual(html.count("CUSTOMER COPY</div>"), 4)
        self.assertEqual(html.count('class="copy-tag-frame copy-tag-rotated"'), 4)

    def test_no_choice_prints_no_label(self):
        html = self.render(2, "A5", "")
        self.assertNotIn('class="copy-tag"', html.split("</style>", 1)[1])
        self.assertNotIn("has-copy-tag", html)

    def test_the_odd_last_bill_on_a5_is_upright_not_cut_off(self):
        # It used to fall to the A5 landscape single-bill layout, which the PDF step cut off.
        html = self.render(1, "A5", "Store Copy")
        self.assertIn("a5-pdf-two", html)
        self.assertIn("upright-invoice", html.split("</style>", 1)[1])
        self.assertIn("STORE COPY", html)


class PrintAllSendsIt(unittest.TestCase):
    def test_the_dropdown_value_reaches_the_batch(self):
        from core import desktop_sales_service as svc

        for sent, want in (("customer", "Customer Copy"), ("store", "Store Copy"), ("", ""), ("x", "")):
            with mock.patch("core.history_print.print_bills_batch", return_value=(1, [])) as batch, \
                 mock.patch("core.bill_output._is_dot_matrix_printer_mode", return_value=False):
                svc.print_all_sales(None, {"sale_ids": [5], "paper": "A5", "copy_label": sent})
            self.assertEqual(batch.call_args.kwargs.get("copy_label"), want, sent)

    def test_a5_batch_html_has_the_label(self):
        from core import history_print
        from core.printer_manager import PrinterManager

        ctx = discounted_bill_context()
        seen = []

        def fake_pdf(html_path, pdf_path, **kw):
            with open(html_path, encoding="utf-8") as fh:
                seen.append(fh.read())
            return True

        with isolated_bill_settings(), \
             mock.patch.object(history_print, "_dot_matrix_mode", return_value=False), \
             mock.patch("core.bill_output._load_sale_data", return_value=("SCB1", ctx, {})), \
             mock.patch("core.bill_output._try_pdf_via_browser", side_effect=fake_pdf), \
             mock.patch.object(PrinterManager, "get_printer_for_slot", return_value="P"), \
             mock.patch.object(PrinterManager, "print_pdf_silently"), \
             mock.patch.object(history_print.time, "sleep"):
            pages, failures = history_print.print_bills_batch(
                None, [1, 2, 3], paper="A5", slot=2, copy_label="Customer Copy"
            )
        self.assertEqual(failures, [])
        self.assertEqual(pages, 2)
        self.assertEqual(sum(h.count("CUSTOMER COPY</div>") for h in seen), 3)

    def test_a6_bills_get_it_in_their_own_settings(self):
        from core import history_print
        from core.printer_manager import PrinterManager

        with isolated_bill_settings(), \
             mock.patch.object(history_print, "_dot_matrix_mode", return_value=False), \
             mock.patch("core.bill_output.print_bill_with_slot") as one, \
             mock.patch.object(PrinterManager, "get_printer_for_slot", return_value="P"), \
             mock.patch.object(history_print.time, "sleep"):
            history_print.print_bills_batch(None, [7], paper="A6", slot=2, copy_label="Store Copy")
        self.assertEqual(one.call_args.kwargs["settings_override"]["copy_footer_label"], "Store Copy")

    def test_the_page_sends_it_and_offers_the_three_choices(self):
        with open(os.path.join(_REPO, "desktop", "src", "pages", "SalesHistoryPage.tsx"), encoding="utf-8") as fh:
            page = fh.read()
        self.assertIn("copy_label: printAllCopy", page)
        for text in ("No copy label", "Customer Copy", "Store Copy"):
            self.assertIn(text, page)

    def test_the_dialog_has_its_own_due_choice(self):
        with open(os.path.join(_REPO, "desktop", "src", "pages", "SalesHistoryPage.tsx"), encoding="utf-8") as fh:
            page = fh.read()
        self.assertIn("value={printAllDue}", page)
        self.assertIn("due: dueNow", page)
        self.assertIn("'All bills'", page)


class CheaperBefore(unittest.TestCase):
    ROWS = [
        {"supplier_name": "SUPPLIER C", "rate": 3.57, "mrp": 4.68, "bill_number": "12327", "purchase_date": "2026-07-25"},
        {"supplier_name": "GANESHWARI", "rate": 17.56, "mrp": 23.06, "bill_number": "6189", "purchase_date": "2025-07-22"},
        {"supplier_name": "SUPPLIER A", "rate": 3.54, "mrp": 4.65, "bill_number": "16285", "purchase_date": "2025-10-08"},
        {"supplier_name": "SUPPLIER B", "rate": 3.54, "mrp": 4.65, "bill_number": "A397", "purchase_date": "2026-05-08"},
    ]

    def test_cheaper_same_pack_suppliers_cheapest_first(self):
        from core.purchase_rate_check import cheaper_before

        got = cheaper_before(self.ROWS, rate=3.57, mrp=4.68)
        self.assertEqual([r["supplier_name"] for r in got], ["SUPPLIER A", "SUPPLIER B"])

    def test_another_pack_of_the_same_name_is_not_compared(self):
        from core.purchase_rate_check import cheaper_before

        # the 21-sachet box (MRP 23.06) at 17.56 is not "cheaper" than a 4-pack at 18
        got = cheaper_before(self.ROWS, rate=18.0, mrp=4.68)
        self.assertNotIn("GANESHWARI", [r["supplier_name"] for r in got])
        got = cheaper_before(self.ROWS, rate=18.0, mrp=23.0)
        self.assertEqual([r["supplier_name"] for r in got], ["GANESHWARI"])

    def test_same_or_lower_rate_is_quiet(self):
        from core.purchase_rate_check import cheaper_before

        self.assertEqual(cheaper_before(self.ROWS, rate=3.54, mrp=4.65), [])
        self.assertEqual(cheaper_before(self.ROWS, rate=3.544, mrp=4.65), [])
        self.assertEqual(cheaper_before(self.ROWS, rate=0, mrp=4.65), [])

    def test_offline_reads_the_last_purchase_from_each_supplier(self):
        from core.purchase_rate_check import rate_check

        conn = sqlite3.connect(":memory:")
        conn.executescript("""
            CREATE TABLE suppliers (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE medicines (id INTEGER PRIMARY KEY, name TEXT, mrp REAL);
            CREATE TABLE purchases (id INTEGER PRIMARY KEY, supplier_id INTEGER, purchase_date DATE, bill_number TEXT);
            CREATE TABLE purchase_items (id INTEGER PRIMARY KEY, purchase_id INTEGER, medicine_id INTEGER,
                                         rate REAL, mrp REAL DEFAULT 0);
            INSERT INTO suppliers VALUES (1, 'OLD'), (2, 'NEW');
            INSERT INTO medicines VALUES (10, 'DOLO 650', 30.0), (11, 'Dolo 650 ', 30.0);
            INSERT INTO purchases VALUES (100, 1, '2026-01-05', 'A1'), (101, 1, '2026-06-05', 'A2'),
                                         (102, 2, '2026-07-01', 'B1');
            -- OLD: 20 in January, 25 in June (the last one counts); NEW: 24, MRP on the medicine
            INSERT INTO purchase_items VALUES (1, 100, 10, 20.0, 30.0), (2, 101, 11, 25.0, 30.0),
                                              (3, 102, 10, 24.0, 0);
        """)
        with mock.patch("core.sync_prefs.is_online_mode", return_value=False):
            got = rate_check(conn, name="dolo 650", rate=26.0, mrp=30.0)
        self.assertTrue(got["ok"])
        self.assertEqual([(r["supplier_name"], r["rate"]) for r in got["cheaper"]], [("NEW", 24.0), ("OLD", 25.0)])
        self.assertEqual(got["cheaper"][0]["bill_number"], "B1")

    def test_online_asks_the_server(self):
        from core import purchase_rate_check as prc

        with mock.patch("core.sync_prefs.is_online_mode", return_value=True), \
             mock.patch("core.store_query_client._get", return_value={"rows": self.ROWS}) as get:
            got = prc.rate_check(None, name="ELECTRAL POWDER", rate=3.57, mrp=4.68)
        get.assert_called_once_with("/api/store/purchase-rates", {"name": "ELECTRAL POWDER"})
        self.assertEqual(len(got["cheaper"]), 2)

    def test_a_failed_lookup_is_no_popup(self):
        from core import purchase_rate_check as prc

        with mock.patch("core.sync_prefs.is_online_mode", return_value=True), \
             mock.patch("core.store_query_client._get", side_effect=RuntimeError("offline")):
            got = prc.rate_check(None, name="X", rate=3.0, mrp=4.0)
        self.assertEqual(got["cheaper"], [])

    def test_the_page_checks_on_leaving_mrp_and_rate(self):
        with open(os.path.join(_REPO, "desktop", "src", "pages", "PurchasePage.tsx"), encoding="utf-8") as fh:
            page = fh.read()
        self.assertEqual(page.count("onBlur={checkRateAgainstHistory}"), 6)
        self.assertIn("checkPurchaseRate(name, r, m)", page)


if __name__ == "__main__":
    unittest.main()
