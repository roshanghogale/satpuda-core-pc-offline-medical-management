"""View details of a saved return, and the sales return slip (owner, 18 Oct 2026).

"The purchase return View details looks bad: a header (return no, date, supplier, original
purchase), the medicines as a table, totals, and a Credit part saying how the credit stands:
open credit left and the purchases it was settled in, or settled in cash/online. Sales returns
the same, with a PDF / Print slip like the purchase return's. And check that a sales return kept
as credit is offered and settled in the next sales bill exactly like a purchase return in the
next purchase."

Everything runs on a scratch store; nothing is printed and nothing reaches a server.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import document_output, return_credit, return_details  # noqa: E402
from core import desktop_returns_service as returns_svc  # noqa: E402
from tests._bill_fixtures import PROFILE, isolated_bill_settings  # noqa: E402
from tests.test_returns_as_party_credit import CUSTOMER, DAY, _item, _Shop  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(*parts: str) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class _Returns(_Shop):
    def buy_and_return(self, qty=1):
        p1 = self.buy("INV-1", paid_in_full=True)
        ret = returns_svc.save_purchase_return(self.conn, {
            "purchase_id": p1["purchase_id"], "supplier_id": self.supplier_id(),
            "items": [{"medicine_id": self.tab, "qty": qty, "rate": 50.0, "type": "Syrup", "unit": "1"}]})
        self.assertTrue(ret.get("ok"), ret)
        return p1, ret

    def sell_and_return(self, settle="ledger"):
        s1 = self.sell([_item(self.syrup, "COUGH SYRUP", 3, 10.0)])
        return s1, self.give_back(s1["sale_id"], 1, settle=settle)

    def slip(self, return_id):
        with isolated_bill_settings(), \
                mock.patch("core.pharmacy_profile_io.fetch_pharmacy_profile_row", return_value=PROFILE), \
                mock.patch("core.bill_context._signature_to_base64", return_value=""):
            return document_output.render_sales_return_html(self.conn, return_id)


class APurchaseReturnsDetails(_Returns):
    def test_header_lines_totals_and_open_credit(self):
        p1, ret = self.buy_and_return()
        d = returns_svc.get_purchase_return_details(self.conn, ret["return_id"])
        self.assertTrue(d["ok"], d)
        # what Edit / Load Purchase read is still there
        for key in ("return_no", "return_date", "purchase_no", "supplier", "refund_amount",
                    "reason", "purchase_id", "supplier_id", "items"):
            self.assertIn(key, d)
        self.assertEqual(d["supplier"], "TEST AGENCY")
        self.assertEqual(d["purchase_entry_no"], p1.get("purchase_no") or d["purchase_entry_no"])
        self.assertEqual(d["supplier_bill_no"], "INV-1")
        self.assertEqual(d["purchase_date"], DAY)
        line = d["items"][0]
        self.assertEqual((line["name"], line["batch"], line["expiry"], line["qty"], line["rate"],
                          line["amount"]), ("PAIN TAB", "B1", "2028-12-01", 1.0, 50.0, 50.0))
        self.assertEqual((d["subtotal"], d["discount_amount"], d["refund_amount"]), (50.0, 0.0, 50.0))
        c = d["credit"]
        self.assertEqual((c["settle_mode"], c["refund"], c["left"], c["settled"], c["paid_out"]),
                         ("ledger", 50.0, 50.0, [], 0.0))

    def test_settled_in_the_next_purchase_names_that_purchase(self):
        _p1, ret = self.buy_and_return()
        p2 = self.buy("INV-2", qty=4, apply_returns=[ret["return_id"]])
        c = returns_svc.get_purchase_return_details(self.conn, ret["return_id"])["credit"]
        self.assertEqual(c["left"], 0.0)
        self.assertEqual(c["settled_total"], 50.0)
        [s] = c["settled"]
        self.assertEqual((s["bill_id"], s["date"], s["amount"], s["how"]),
                         (p2["purchase_id"], DAY, 50.0, "chosen"))
        self.assertEqual(s["supplier_bill_no"], "INV-2")
        self.assertTrue(s["bill_no"])

    def test_a_discount_shows_in_the_totals(self):
        p1 = self.buy("INV-1", paid_in_full=True)
        ret = returns_svc.save_purchase_return(self.conn, {
            "purchase_id": p1["purchase_id"], "supplier_id": self.supplier_id(), "discount": 10,
            "items": [{"medicine_id": self.tab, "qty": 2, "rate": 50.0, "type": "Syrup", "unit": "1"}]})
        d = returns_svc.get_purchase_return_details(self.conn, ret["return_id"])
        self.assertEqual(d["subtotal"], 100.0)
        self.assertEqual(round(d["subtotal"] - d["discount_amount"], 2), d["refund_amount"])
        self.assertEqual(d["discount_pct"], 10.0)


class ASalesReturnsDetails(_Returns):
    def test_header_lines_totals_and_open_credit(self):
        s1, ret = self.sell_and_return()
        d = returns_svc.get_sales_return_details(self.conn, ret["return_id"])
        self.assertTrue(d["ok"], d)
        self.assertEqual((d["return_no"], d["customer"], d["sale_id"]),
                         (ret["return_no"], CUSTOMER, s1["sale_id"]))
        self.assertEqual(d["bill_no"], s1["bill_no"].split("/")[0])
        self.assertEqual(d["bill_date"], DAY)
        self.assertEqual(d["settle_mode"], "ledger")
        line = d["items"][0]
        self.assertEqual((line["name"], line["batch"], line["expiry"], line["qty"], line["rate"],
                          line["amount"]), ("COUGH SYRUP", "B1", "2028-12-01", 1.0, 10.0, 10.0))
        self.assertEqual((d["subtotal"], d["discount_amount"], d["refund_amount"]), (10.0, 0.0, 10.0))
        self.assertEqual((d["credit"]["left"], d["credit"]["settled"]), (10.0, []))

    def test_settled_in_the_next_bill_names_that_bill(self):
        _s1, ret = self.sell_and_return()
        s2 = self.sell([_item(self.tab, "PAIN TAB", 2, 20.0)], apply_returns=[ret["return_id"]])
        c = returns_svc.get_sales_return_details(self.conn, ret["return_id"])["credit"]
        self.assertEqual(c["left"], 0.0)
        self.assertEqual([(s["bill_id"], s["bill_no"], s["date"], s["amount"], s["how"])
                          for s in c["settled"]],
                         [(s2["sale_id"], s2["bill_no"].split("/")[0], DAY, 10.0, "chosen")])

    def test_an_older_due_it_paid_reads_as_such(self):
        s1 = self.sell([_item(self.syrup, "COUGH SYRUP", 3, 10.0)])
        due = self.sell([_item(self.tab, "PAIN TAB", 1, 20.0)], payment_mode="Due", pay_full=False)
        ret = self.give_back(s1["sale_id"], 3)  # 30 back; 20 of it clears the due bill
        c = returns_svc.get_sales_return_details(self.conn, ret["return_id"])["credit"]
        self.assertEqual([(s["bill_id"], s["amount"], s["how"]) for s in c["settled"]],
                         [(due["sale_id"], 20.0, "oldest")])
        self.assertEqual(c["left"], 10.0)

    def test_refunded_in_cash_says_how_much_went_back(self):
        _s1, ret = self.sell_and_return(settle="cash")
        c = returns_svc.get_sales_return_details(self.conn, ret["return_id"])["credit"]
        self.assertEqual((c["settle_mode"], c["paid_out"], c["paid_out_mode"], c["left"]),
                         ("cash", 10.0, "cash", 0.0))
        _s1, online = self.sell_and_return(settle="online")
        c = returns_svc.get_sales_return_details(self.conn, online["return_id"])["credit"]
        self.assertEqual((c["settle_mode"], c["paid_out"], c["paid_out_mode"]), ("online", 10.0, "online"))

    def test_an_unknown_or_deleted_return_is_not_found(self):
        self.assertFalse(returns_svc.get_sales_return_details(self.conn, 999)["ok"])
        _s1, ret = self.sell_and_return()
        self.assertTrue(returns_svc.delete_sales_return(self.conn, {"id": ret["return_id"]}).get("ok"))
        self.assertFalse(returns_svc.get_sales_return_details(self.conn, ret["return_id"])["ok"])


class TheSalesReturnSlip(_Returns):
    def test_header_medicines_totals_and_credit_line(self):
        _s1, ret = self.sell_and_return()
        html = self.slip(ret["return_id"])
        self.assertIn("SALES RETURN", html)
        self.assertNotIn("PURCHASE RETURN", html)
        self.assertIn(ret["return_no"], html)
        self.assertIn(CUSTOMER, html)
        self.assertIn("COUGH SYRUP", html)
        self.assertIn("B1", html)
        self.assertIn("10.00", html)
        self.assertIn("CREDIT AMT", html)
        self.assertIn("GOODS RETURNED - CREDIT TO CUSTOMER", html)
        self.assertIn("Rs.10.00 open", html)

    def test_settled_in_a_bill_names_it(self):
        _s1, ret = self.sell_and_return()
        s2 = self.sell([_item(self.tab, "PAIN TAB", 2, 20.0)], apply_returns=[ret["return_id"]])
        html = self.slip(ret["return_id"])
        self.assertIn(f"{s2['bill_no'].split('/')[0]} Rs.10.00", html)
        self.assertNotIn("Rs.10.00 open", html)

    def test_refunded_in_cash_or_online(self):
        _s1, cash = self.sell_and_return(settle="cash")
        html = self.slip(cash["return_id"])
        self.assertIn("GOODS RETURNED - REFUNDED IN CASH", html)
        self.assertIn("REFUND AMT", html)
        self.assertIn("CASH Rs.10.00", html)
        self.assertNotIn("CREDIT TO CUSTOMER", html)
        _s1, online = self.sell_and_return(settle="online")
        self.assertIn("GOODS RETURNED - REFUNDED ONLINE", self.slip(online["return_id"]))

    def test_saved_through_the_saved_files_folder_and_printed_when_asked(self):
        _s1, ret = self.sell_and_return()
        written = []
        with isolated_bill_settings(), \
                mock.patch("core.pharmacy_profile_io.fetch_pharmacy_profile_row", return_value=PROFILE), \
                mock.patch("core.bill_context._signature_to_base64", return_value=""), \
                mock.patch.object(document_output, "save_customer_document",
                                  side_effect=lambda html, name: (written.append((html, name))
                                                                  or ("x.pdf", "x.html"))), \
                mock.patch.object(return_details, "print_pdf", return_value="") as printer, \
                mock.patch("core.saved_files.remember") as remember:
            res = returns_svc.save_sales_return_pdf(self.conn, ret["return_id"])
            self.assertEqual((res["ok"], res["path"], res["return_no"]), (True, "x.pdf", ret["return_no"]))
            printer.assert_not_called()
            remember.assert_any_call("x.pdf")
            res = returns_svc.save_sales_return_pdf(self.conn, ret["return_id"], print_it=True)
            self.assertTrue(res["printed"], res)
            printer.assert_called_once_with("x.pdf")
            printer.return_value = "Printer is off"
            res = returns_svc.save_sales_return_pdf(self.conn, ret["return_id"], print_it=True)
            self.assertEqual((res["ok"], res["printed"], res["print_error"]), (True, False, "Printer is off"))
        self.assertEqual(written[0][1], ret["return_no"])
        self.assertIn("SALES RETURN", written[0][0])

    def test_the_purchase_return_pdf_prints_the_same_way(self):
        _p1, ret = self.buy_and_return()
        with mock.patch("core.document_output.render_purchase_return_html", return_value="<html/>"), \
                mock.patch("core.document_output.save_supplier_document", return_value=("y.pdf", "y.html")), \
                mock.patch.object(return_details, "print_pdf", return_value="") as printer:
            res = returns_svc.save_purchase_return_pdf(self.conn, ret["return_id"])
            self.assertEqual(res["path"], "y.pdf")
            printer.assert_not_called()
            res = returns_svc.save_purchase_return_pdf(self.conn, ret["return_id"], print_it=True)
            self.assertTrue(res["printed"])
            printer.assert_called_once_with("y.pdf")


class TheCreditIsSettledTheSameWayOnBothSides(_Returns):
    """A sales return kept as credit is offered and settled in the customer's next bill
    exactly as a purchase return is in the supplier's next purchase."""

    def test_offered_then_settled_then_gone_on_both_sides(self):
        _s1, sret = self.sell_and_return()
        _p1, pret = self.buy_and_return()
        # offered: the route behind the Sales / Purchase popup
        cust = return_credit.open_returns_for_party(self.conn, "customer", party=CUSTOMER)
        supp = return_credit.open_returns_for_party(self.conn, "supplier", party="TEST AGENCY")
        self.assertEqual([(r["return_id"], r["left"]) for r in cust["returns"]], [(sret["return_id"], 10.0)])
        self.assertEqual([(r["return_id"], r["left"]) for r in supp["returns"]], [(pret["return_id"], 50.0)])
        # settled on save of the next bill / purchase, ticked in the popup
        s2 = self.sell([_item(self.tab, "PAIN TAB", 2, 20.0)], apply_returns=[sret["return_id"]])
        p2 = self.buy("INV-2", qty=4, apply_returns=[pret["return_id"]])
        self.assertEqual(self.allocations("sales_returns", sret["return_id"]),
                         [{"bill_id": s2["sale_id"], "bill_no": s2["bill_no"].split("/")[0], "amount": 10.0}])
        self.assertEqual([(a["bill_id"], a["amount"]) for a in self.allocations("purchase_returns", pret["return_id"])],
                         [(p2["purchase_id"], 50.0)])
        self.assertEqual([r["return_no"] for r in return_credit.returns_in_bill(self.conn, "customer", s2["sale_id"])],
                         [sret["return_no"]])
        self.assertEqual([r["return_no"] for r in return_credit.returns_in_bill(self.conn, "supplier", p2["purchase_id"])],
                         [pret["return_no"]])
        # no longer offered, and the dialogs say where it went
        self.assertEqual(return_credit.open_returns_for_party(self.conn, "customer", party=CUSTOMER)["returns"], [])
        self.assertEqual(return_credit.open_returns_for_party(self.conn, "supplier", party="TEST AGENCY")["returns"], [])
        for kind, rid, bill in (("customer", sret["return_id"], s2["sale_id"]),
                                ("supplier", pret["return_id"], p2["purchase_id"])):
            c = return_details.credit_status(self.conn, kind, rid)
            self.assertEqual((c["left"], [s["bill_id"] for s in c["settled"]], c["settled"][0]["how"]),
                             (0.0, [bill], "chosen"), kind)
        # the sale's own figures: the return paid what the counter did not
        self.assertEqual(self.sale_row(s2["sale_id"])[:3], (40.0, 30.0, 0.0))

    def test_a_return_saved_without_the_customer_is_still_theirs(self):
        s1 = self.sell([_item(self.syrup, "COUGH SYRUP", 3, 10.0)])
        res = returns_svc.save_sales_return(self.conn, {
            "sale_id": s1["sale_id"], "items": [{"medicine_id": self.syrup, "qty": 1, "rate": 10.0}]})
        self.assertTrue(res.get("ok"), res)
        offered = return_credit.open_returns_for_party(self.conn, "customer", party=CUSTOMER)["returns"]
        self.assertEqual([r["return_id"] for r in offered], [res["return_id"]])
        s2 = self.sell([_item(self.tab, "PAIN TAB", 1, 20.0)], apply_returns=[res["return_id"]])
        self.assertEqual(self.sale_row(s2["sale_id"])[:3], (20.0, 10.0, 0.0))


class TheScreenAndTheRoutes(unittest.TestCase):
    def test_the_engine_routes_are_wired(self):
        api = _src("core", "desktop_api.py")
        self.assertIn('if path == "/api/returns/sales/return":', api)
        self.assertIn("ret_svc.get_sales_return_details(conn, rid)", api)
        self.assertIn('elif path == "/api/returns/sales/return/pdf":', api)
        self.assertIn("ret_svc.save_sales_return_pdf(", api)
        self.assertEqual(api.count('print_it=bool(body.get("print"))'), 2)

    def test_both_lists_open_the_dialog_the_same_ways(self):
        page = _src("desktop", "src", "pages", "ReturnsPage.tsx")
        for call in ("viewSalesReturnDetails(id)", "viewPurchaseReturnDetails(id)"):
            self.assertIn(call, page)  # double-click
        self.assertEqual(page.count("onRowContextMenu="), 2)
        self.assertEqual(page.count('label="View Details"'), 2)
        self.assertIn("<ReturnDetailsDialog", page)
        dlg = _src("desktop", "src", "pages", "ReturnDetailsDialog.tsx")
        for text in ("Save PDF", "Print", "Edit Return", "Delete Return", "Close", "Open credit left",
                     "Medicines returned", "Subtotal", "Discount"):
            self.assertIn(text, dlg)
        self.assertIn("import '../styles/returns.css'", dlg)


if __name__ == "__main__":
    unittest.main()
