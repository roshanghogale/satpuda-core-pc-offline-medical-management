"""Bill heading: Tax Invoice or Bill of Supply (owner, 22 Oct 2026).

A composition dealer, or a shop selling exempt goods, issues a Bill of Supply (Rule 49 of
the CGST Rules): headed BILL OF SUPPLY, no GST rate or amount, no CGST/SGST/IGST lines. A
composition dealer's also says "Composition taxable person, not eligible to collect tax on
supplies". The shop picks it once (core/bill_heading.py, two shop-wide settings rows) and
every bill prints it; the amounts printed stay the bill's own.
"""
from __future__ import annotations

import re
import sqlite3
import unittest
from dataclasses import replace
from unittest import mock

from core import bill_heading
from core.bill_config import DEFAULT_BILL_PRINT_SETTINGS
from tests._bill_fixtures import discounted_bill_context, isolated_bill_settings

DECL = "Composition taxable person, not eligible to collect tax on supplies"


def _offline():
    return mock.patch("core.sync_prefs.is_online_mode", return_value=False)


def _settings(**extra) -> dict:
    # the composition line is off unless the shop ticks it (owner, 29 Oct 2026); these
    # tests are about where it prints, so it is ticked -- TheLineIsOffByDefault checks the rest
    return {**DEFAULT_BILL_PRINT_SETTINGS, "show_composition_line": True, **extra}


def _ctx(heading: str = "", reason: str = ""):
    with _offline():
        ctx = discounted_bill_context()
    return replace(ctx, bill_heading=heading, bill_supply_reason=reason)


def _money_rows(html: str) -> dict[str, str]:
    """The totals table's rows as {label: amount}."""
    return dict(re.findall(r'<tr(?: class="total")?><td>([^<]+)</td><td class="r">([-\d.]+)</td></tr>', html))


class TheSetting(unittest.TestCase):
    def test_values(self):
        self.assertEqual(bill_heading.clean_heading(""), "tax_invoice")
        self.assertEqual(bill_heading.clean_heading("nonsense"), "tax_invoice")
        self.assertEqual(bill_heading.clean_heading(" Bill_Of_Supply "), "bill_of_supply")
        self.assertEqual(bill_heading.clean_heading('"bill_of_supply"'), "bill_of_supply")
        self.assertEqual(bill_heading.clean_reason(""), "composition")
        self.assertEqual(bill_heading.clean_reason("exempt"), "exempt")
        self.assertEqual(bill_heading.COMPOSITION_DECLARATION, DECL)
        info = bill_heading.heading_info("bill_of_supply", "")
        self.assertTrue(info["bill_of_supply"])
        self.assertEqual(info["supply_declaration"], DECL)
        self.assertEqual(bill_heading.heading_info("bill_of_supply", "exempt")["supply_declaration"], "")
        self.assertEqual(bill_heading.heading_info("tax_invoice", "composition")["supply_declaration"], "")

    def test_saved_as_shop_wide_settings_rows(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, value TEXT)")
        with _offline(), mock.patch("core.settings_mirror.remember", lambda *a: None):
            self.assertFalse(bill_heading.load_bill_heading(conn)["bill_of_supply"])
            bill_heading.save_bill_heading(conn, "bill_of_supply", "exempt")
            got = bill_heading.load_bill_heading(conn)
        self.assertEqual((got["bill_heading"], got["bill_supply_reason"]), ("bill_of_supply", "exempt"))
        rows = dict(conn.execute("SELECT name, value FROM settings").fetchall())
        self.assertEqual(rows, {"profile_bill_heading": "bill_of_supply",
                                "profile_bill_supply_reason": "exempt"})

    def test_the_rows_travel_to_the_other_devices(self):
        from core import store_kv_carry
        from core.offline_first import schema

        self.assertIn("profile_bill_", schema.SHOP_WIDE_SETTING_PREFIXES)
        self.assertIn("profile_bill_", store_kv_carry.PREFIXES)
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, value TEXT)")
        schema.ensure_schema(conn)
        conn.execute("INSERT INTO settings (name, value) VALUES ('profile_bill_heading', 'bill_of_supply')")
        dirty = conn.execute("SELECT collection FROM of_dirty").fetchall()
        conn.close()
        self.assertEqual(dirty, [("settings",)])

    def test_a_bill_read_from_the_store_carries_it(self):
        from core.bill_context import _build_bill_context
        from tests._bill_fixtures import DISCOUNTED_ITEMS, PROFILE, bill_info

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, value TEXT)")
        conn.execute("INSERT INTO settings (name, value) VALUES ('profile_bill_heading', 'bill_of_supply')")
        with _offline(), isolated_bill_settings():
            ctx = _build_bill_context(PROFILE, bill_info(), DISCOUNTED_ITEMS, "Cash", conn.cursor())
        self.assertEqual((ctx.bill_heading, ctx.bill_supply_reason), ("bill_of_supply", "composition"))


class TheRetailBill(unittest.TestCase):
    def _render(self, ctx, **extra):
        from core.bill_config import render_bill_html

        with isolated_bill_settings():
            return render_bill_html(ctx, _settings(**extra))

    def test_a_tax_invoice_is_unchanged(self):
        for template in ("classic", "legacy"):
            self.assertEqual(self._render(_ctx("tax_invoice"), template=template),
                             self._render(_ctx(""), template=template))

    def test_composition_dealer(self):
        for template in ("classic", "legacy"):
            for footer in ("center", "right"):
                with self.subTest(template=template, footer=footer):
                    tax = self._render(_ctx("tax_invoice"), template=template, signature_position=footer)
                    bos = self._render(_ctx("bill_of_supply", "composition"), template=template,
                                       signature_position=footer)
                    self.assertIn('<div class="inv-title">BILL OF SUPPLY</div>', bos)
                    for gone in ("TAX INVOICE", "GST INVOICE", "<td>GST</td>", "Taxable", "SGST",
                                 "CGST", "IGST"):
                        self.assertNotIn(gone, bos, gone)
                    self.assertEqual(bos.count(DECL), 1)
                    # the money is the bill's own: Total and DISC as on the tax invoice
                    want = {k: v for k, v in _money_rows(tax).items() if k != "GST"}
                    self.assertEqual(_money_rows(bos), want)
                    self.assertEqual(want["Total"], "250.00")
                    self.assertIn("GSTIN : 27ABCDE1234F1Z5", bos)   # the GSTIN stays

    def test_exempt_goods_print_no_declaration(self):
        bos = self._render(_ctx("bill_of_supply", "exempt"))
        self.assertIn("BILL OF SUPPLY", bos)
        self.assertNotIn(DECL, bos)
        self.assertNotIn("<td>GST</td>", bos)
        self.assertNotIn("SGST", bos)

    def test_the_declaration_prints_with_the_gst_strip_off(self):
        for footer in ("center", "right"):
            bos = self._render(_ctx("bill_of_supply"), show_gst_strip=False, signature_position=footer)
            self.assertIn(DECL, bos, footer)

    def test_the_settings_dict_can_carry_it(self):
        bos = self._render(_ctx(""), bill_heading="bill_of_supply")
        self.assertIn("BILL OF SUPPLY", bos)
        self.assertIn(DECL, bos)


class TheDotMatrixSlip(unittest.TestCase):
    def _text(self, ctx, **extra):
        from core.dot_matrix_print import format_bill_text, printed_text

        with isolated_bill_settings():
            return printed_text(format_bill_text(ctx, _settings(paper_size="A5", **extra)))

    @staticmethod
    def _flat(text: str) -> str:
        return " ".join(re.sub(r"[|+\-=]", " ", text).split())

    def test_the_old_slip(self):
        tax = self._text(_ctx("tax_invoice"), dot_matrix_style="classic")
        bos = self._text(_ctx("bill_of_supply"), dot_matrix_style="classic")
        self.assertIn("GST INVOICE", tax)
        self.assertIn("BILL OF SUPPLY", bos)
        for gone in ("GST INVOICE", "TAX INVOICE", "SGST", "CGST"):
            self.assertNotIn(gone, bos, gone)
        self.assertNotRegex(bos, r"GST\s+26\.78")
        self.assertIn(DECL, self._flat(bos))
        self.assertRegex(bos, r"Total\s+250\.00")
        self.assertRegex(bos, r"DISC\s+8\.00")
        exempt = self._text(_ctx("bill_of_supply", "exempt"), dot_matrix_style="classic")
        self.assertIn("BILL OF SUPPLY", exempt)
        self.assertNotIn("Composition", exempt)

    def test_the_new_slip(self):
        bos = self._text(_ctx("bill_of_supply"), dot_matrix_style="compact")
        self.assertIn(DECL, self._flat(bos))
        self.assertRegex(bos, r"Total\s+250\.00")
        self.assertNotIn("SGST", bos)
        exempt = self._text(_ctx("bill_of_supply", "exempt"), dot_matrix_style="compact")
        self.assertNotIn("Composition", exempt)
        self.assertEqual(exempt, self._text(_ctx("tax_invoice"), dot_matrix_style="compact"))


class TheWholesaleInvoice(unittest.TestCase):
    def setUp(self):
        from tests import test_wholesale_tax_invoice as wt

        for p in wt._offline():
            p.start()
            self.addCleanup(p.stop)
        ticked = mock.patch("core.bill_config.load_bill_print_settings",
                            return_value=_settings())
        ticked.start()
        self.addCleanup(ticked.stop)
        self.conn = wt._store()
        self.addCleanup(self.conn.close)
        from core.desktop_sales_service import save_sale

        self.sid = save_sale(self.conn, {
            "invoice_mode": "wholesale", "customer_id": 1, "bill_date": "2026-10-18",
            "items": wt._lines(), "payment_mode": "Due"})["sale_id"]

    def _inv(self):
        from core import wholesale_sale as ws

        return ws.invoice_context(self.conn, self.sid)

    def _html(self, inv):
        from bill_templates.wholesale_invoice import render_wholesale_invoice_html

        return render_wholesale_invoice_html(inv, paper="A5")

    @staticmethod
    def _totals(html: str) -> dict[str, float]:
        tot = html[html.index("<table class='tot'>"):]
        return {k: float(v or 0) for k, v in
                re.findall(r"<tr(?: class='party')?><td>([^<]+)</td><td class='n'>([-\d.]*)</td></tr>", tot)}

    def test_the_store_setting_reaches_the_invoice(self):
        self.assertFalse(self._inv()["bill_of_supply"])
        self.conn.execute("INSERT INTO settings (name, value) VALUES ('profile_bill_heading', 'bill_of_supply')")
        inv = self._inv()
        self.assertTrue(inv["bill_of_supply"])
        self.assertEqual(inv["supply_declaration"], DECL)

    def test_composition_dealer(self):
        tax_inv = self._inv()
        tax = self._html(tax_inv)
        self.conn.execute("INSERT INTO settings (name, value) VALUES ('profile_bill_heading', 'bill_of_supply')")
        bos = self._html(self._inv())
        self.assertIn("<div class='title'>BILL OF SUPPLY</div>", bos)
        self.assertIn("<title>BILL OF SUPPLY", bos)
        for gone in ("GST INVOICE", "SGST", "CGST", "IGST", "<table class='gst'>"):
            self.assertNotIn(gone, bos, gone)
        self.assertEqual(bos.count(DECL), 1)
        self.assertIn("HSN", bos)
        self.assertIn("GSTIN", bos)
        t, b = self._totals(tax), self._totals(bos)
        self.assertEqual(b["PARTY TOTAL"], t["PARTY TOTAL"])
        self.assertAlmostEqual(t["PARTY TOTAL"], float(tax_inv["party_total"]), places=2)
        # the printed sums still add up: the tax is inside SUB TOTAL and Discount
        self.assertAlmostEqual(b["SUB TOTAL"] - b["Discount"] - b["Bill Disc"] + b["CR/DR"]
                               + b["CR/DR NOTE"], b["PARTY TOTAL"], places=2)
        # a line without a discount: RATE and GROSS AMT with its 5% tax in them
        self.assertIn("<td class='n'>42.00</td><td class='n'>168.00</td></tr>", bos)

    def test_exempt_goods(self):
        self.conn.execute("INSERT INTO settings (name, value) VALUES ('profile_bill_heading', 'bill_of_supply')")
        self.conn.execute("INSERT INTO settings (name, value) VALUES ('profile_bill_supply_reason', 'exempt')")
        bos = self._html(self._inv())
        self.assertIn("BILL OF SUPPLY", bos)
        self.assertNotIn(DECL, bos)
        self.assertNotIn("SGST", bos)

    def test_a_tax_invoice_is_unchanged(self):
        inv = self._inv()
        plain = {k: v for k, v in inv.items() if k not in ("bill_of_supply", "supply_declaration")}
        self.assertEqual(self._html(inv), self._html(plain))


class TheLineIsOffByDefault(unittest.TestCase):
    """Owner, 29 Oct 2026: "the line is of no use" -- a Bill of Supply prints without the
    composition line until the shop ticks it in Settings; the shop is still a composition
    dealer for the GST screens."""

    def test_the_setting_is_off(self):
        self.assertFalse(DEFAULT_BILL_PRINT_SETTINGS["show_composition_line"])
        self.assertEqual(bill_heading.supply_declaration(_ctx("bill_of_supply", "composition"),
                                                         dict(DEFAULT_BILL_PRINT_SETTINGS)), "")

    def test_no_bill_prints_it(self):
        from core.bill_config import render_bill_html
        from core.dot_matrix_print import format_bill_text, printed_text

        ctx = _ctx("bill_of_supply", "composition")
        plain = dict(DEFAULT_BILL_PRINT_SETTINGS)
        with isolated_bill_settings():
            html = render_bill_html(ctx, plain)
            slips = [printed_text(format_bill_text(ctx, {**plain, "paper_size": "A5", "dot_matrix_style": s}))
                     for s in ("classic", "compact")]
            slips.append(printed_text(format_bill_text(ctx, {**plain, "paper_size": "A6", "template": "bill_of_supply"})))
        self.assertIn("BILL OF SUPPLY", html)
        for text in [html] + slips:
            self.assertNotIn("Composition", text)

    def test_still_a_composition_dealer_for_gst(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO settings (name, value) VALUES ('profile_bill_heading', 'bill_of_supply')")
        from core.gst_screen import is_composition

        with _offline():
            self.assertTrue(is_composition(conn))
        conn.close()


if __name__ == "__main__":
    unittest.main()
