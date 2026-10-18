"""Returns and payments in a wholesale store (owner, 18 Oct 2026: "all the return and payment
with receipt number logic is there [in wholesale too]; showing return medicines on the bill is
the same").

A wholesale tax invoice is a sale like any other: a return of it is the party's credit, offered
in the party's next wholesale bill and settled there the way retail settles it
(core/return_credit.py), and a receipt clears its bills (core/bill_clearing.py). Everything runs
on in-memory stores; no server is touched.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import desktop_returns_service as returns_svc  # noqa: E402
from core import desktop_sales_service as sales_svc  # noqa: E402
from core import return_credit  # noqa: E402
from core import wholesale_sale as ws  # noqa: E402
from core import wholesale_sale_api  # noqa: E402
from tests.test_wholesale_tax_invoice import _offline, _store  # noqa: E402

DAY = "2026-10-18"


def _line(qty, rate=90, free=0, disc=0):
    """Strips of TESTOCIN 500 (10 tablets a strip, GST 12% on top)."""
    return {"id": 1, "qty": qty, "free": free, "rate": rate, "disc_pct": disc, "scheme_pct": 0,
            "gst_percent": 12, "rate_tier": "A"}


class _Store(unittest.TestCase):
    def setUp(self):
        for p in _offline():
            p.start()
            self.addCleanup(p.stop)
        self.conn = _store()
        self.addCleanup(self.conn.close)
        # no credit limit in the way of these sums
        self.conn.execute("UPDATE customers SET credit_limit=0 WHERE id=1")
        self.conn.commit()

    def bill(self, lines, **over):
        body = {"invoice_mode": "wholesale", "customer_id": 1, "bill_date": DAY, "items": lines,
                "payment_mode": "Due"}
        body.update(over)
        return sales_svc.save_sale(self.conn, body)

    def give_back(self, sale_id, strips, medicine_id=1):
        """A return as the Returns page sends it: qty in the units the bill holds (tablets),
        the line's own rate and amount."""
        loaded = returns_svc.load_sales_bill_for_return(self.conn, sale_id)
        self.assertTrue(loaded["ok"], loaded)
        it = next(x for x in loaded["items"] if x["medicine_id"] == medicine_id)
        res = returns_svc.save_sales_return(self.conn, {
            "sale_id": sale_id, "customer_id": loaded["customer_id"], "settle_mode": "ledger",
            "discount": 0,
            "items": [{"medicine_id": medicine_id, "qty": strips * it["tablets_per_stripe"],
                       "rate": it["rate"], "orig_qty": it["orig_qty"], "orig_amount": it["amount"],
                       "type": it["type"], "is_tablet": it["is_tablet"]}],
        })
        self.assertTrue(res.get("ok"), res)
        return res

    def sale_row(self, sid):
        return self.conn.execute(
            "SELECT total_amount, amount_paid, due_amount, previous_credit FROM sales WHERE id=?",
            (sid,)).fetchone()

    def balance(self):
        return self.conn.execute(
            "SELECT total_due, total_credit FROM customers WHERE id=1").fetchone()

    def allocations(self, rid):
        row = self.conn.execute("SELECT allocations FROM sales_returns WHERE id=?", (rid,)).fetchone()
        return json.loads(row[0]) if row and row[0] else []

    def open_left(self):
        return {r["return_no"]: r["left"] for r in return_credit.open_returns(self.conn, "customer", 1)}


class AWholesaleReturn(_Store):
    def test_strips_go_back_at_the_lines_gst_inclusive_amount(self):
        first = self.bill([_line(10, disc=10)], payment_mode="Cash", pay_full=True)
        self.assertTrue(first["ok"], first)
        # 10 strips x 90 less 10% = 810 taxable + 97.20 GST = 907.20; the bill keeps no
        # overall discount for the Returns page to take off again
        self.assertEqual(self.conn.execute(
            "SELECT COALESCE(discount,0), total_amount FROM sales WHERE id=?",
            (first["sale_id"],)).fetchone(), (0.0, 907.0))
        loaded = returns_svc.load_sales_bill_for_return(self.conn, first["sale_id"])
        it = loaded["items"][0]
        self.assertEqual((it["orig_qty"], it["amount"], it["tablets_per_stripe"], it["is_tablet"]),
                         (100.0, 907.2, 10, True))
        stock_before = self.conn.execute("SELECT stock_qty FROM medicines WHERE id=1").fetchone()[0]
        ret = self.give_back(first["sale_id"], 2)
        self.assertEqual(ret["refund_amount"], 181.44)          # 2 of 10 strips of 907.20
        self.assertEqual(self.conn.execute("SELECT stock_qty FROM medicines WHERE id=1").fetchone()[0],
                         stock_before + 20)
        self.assertEqual(self.balance(), (0.0, 181.44))
        self.assertEqual(self.open_left(), {ret["return_no"]: 181.44})
        # a second return cannot outrun the bill
        res = returns_svc.save_sales_return(self.conn, {
            "sale_id": first["sale_id"], "customer_id": 1,
            "items": [{"medicine_id": 1, "qty": 90, "rate": it["rate"], "orig_qty": 100,
                       "orig_amount": 907.2, "is_tablet": True}]})
        self.assertFalse(res.get("ok"))

    def test_the_returns_page_is_told_to_count_strips_and_the_slip_shows_strips(self):
        first = self.bill([_line(10, disc=10)], payment_mode="Cash", pay_full=True)
        loaded = returns_svc.load_sales_bill_for_return(self.conn, first["sale_id"])
        self.assertEqual(loaded["invoice_mode"], "wholesale")
        ret = self.give_back(first["sale_id"], 2)
        # stored as always: tablets, the line's rate per tablet
        self.assertEqual(self.conn.execute(
            "SELECT qty FROM sales_return_items WHERE return_id=?", (ret["return_id"],)).fetchone()[0], 20)
        from core.return_details import sales_return_details

        d = sales_return_details(self.conn, ret["return_id"])
        self.assertEqual(d["qty_unit"], "strip")
        it = d["items"][0]
        self.assertEqual((it["qty"], it["units"], it["units_per_pack"], it["rate"]), (2.0, 20.0, 10, 90.72))
        self.assertEqual(d["refund_amount"], 181.44)
        from core.document_output import render_sales_return_html

        html = render_sales_return_html(self.conn, ret["return_id"])
        self.assertIn("90.72", html)

    def test_a_retail_bill_return_stays_in_tablets(self):
        from core.billing_service import save_new_bill

        _no, sid = save_new_bill(self.conn, 2, [{
            "id": 1, "name": "TESTOCIN 500", "qty": 20, "rate": 12, "amount": 240,
            "medicine_discount": 0, "unit": "10", "type": "Tablet", "batch": "TB01",
            "gst_percent": 12}], 0, 0, 240, 0, "", "", 0, bill_date=DAY)
        loaded = returns_svc.load_sales_bill_for_return(self.conn, sid)
        self.assertEqual(loaded["invoice_mode"], "")
        ret = self.give_back(sid, 1)
        from core.return_details import sales_return_details

        d = sales_return_details(self.conn, ret["return_id"])
        self.assertEqual(d["qty_unit"], "")
        self.assertEqual((d["items"][0]["qty"], d["items"][0]["rate"]), (10.0, 12.0))


class ASettledReturnInTheNextBill(_Store):
    def setUp(self):
        super().setUp()
        first = self.bill([_line(10)], payment_mode="Cash", pay_full=True)   # 1008.00 paid
        self.assertTrue(first["ok"], first)
        self.first = first["sale_id"]
        self.ret = self.give_back(self.first, 2)                          # 201.60 credit
        self.assertEqual(self.ret["refund_amount"], 201.6)
        self.rid = self.ret["return_id"]

    def test_the_screen_is_offered_the_return_in_strips(self):
        status, res = wholesale_sale_api.handle_get(
            "/api/wholesale/sale/open-returns", {"customer_id": ["1"]}, self.conn)
        self.assertEqual(status, 200, res)
        self.assertEqual([(r["return_id"], r["left"]) for r in res["returns"]], [(self.rid, 201.6)])
        item = res["returns"][0]["items"][0]
        self.assertEqual((item["name"], item["qty"], item["units"], item["amount"], item["batch_no"]),
                         ("TESTOCIN 500", 2, 20, 201.6, "TB01"))

    def test_the_ticked_return_pays_part_of_a_bill_on_due(self):
        # 3 strips x 90 = 270 + 32.40 GST = 302.40 -> 302.00
        calc = ws.calc_sale(self.conn, {"customer_id": 1, "items": [_line(3)],
                                        "payment_mode": "Due", "returns_total": 201.6})
        self.assertEqual((calc["grand_total"], calc["returns_total"], calc["net_total"]),
                         (302.0, 201.6, 100.4))
        res = self.bill([_line(3)], apply_returns=[self.rid])
        self.assertTrue(res["ok"], res)
        sid = res["sale_id"]
        self.assertEqual(res["returns_used"],
                         [{"return_id": self.rid, "return_no": self.ret["return_no"], "amount": 201.6}])
        self.assertEqual((res["returns_total"], res["net_total"]), (201.6, 100.4))
        self.assertEqual(self.allocations(self.rid),
                         [{"bill_id": sid, "bill_no": res["bill_no"], "amount": 201.6}])
        total, paid, due, prev_credit = self.sale_row(sid)
        self.assertEqual((total, paid, due, prev_credit), (302.0, 0.0, 100.4, 0.0))
        self.assertEqual(self.balance(), (100.4, 0.0))
        # never offered again
        self.assertEqual(self.open_left(), {})
        self.assertEqual(return_credit.open_returns_for_party(self.conn, "customer", party_id=1)["returns"], [])
        # the bill knows its return, with the medicines that came back
        rets = return_credit.returns_in_bill(self.conn, "customer", sid)
        self.assertEqual([(r["return_no"], r["amount"], r["refund"]) for r in rets],
                         [(self.ret["return_no"], 201.6, 201.6)])
        self.assertEqual((rets[0]["items"][0]["name"], rets[0]["items"][0]["qty"],
                          rets[0]["items"][0]["batch_no"]), ("TESTOCIN 500", 20, "TB01"))
        # the ledger: the return paid the new bill, nothing else is open
        from core.bill_clearing import open_bills

        self.assertEqual([(b["bill_id"], b["owes"]) for b in open_bills(self.conn, "customer", 1)],
                         [(sid, 100.4)])

    def test_cash_for_the_net_clears_the_bill(self):
        res = self.bill([_line(3)], payment_mode="Cash", cash_paid=100.4, apply_returns=[self.rid])
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.sale_row(res["sale_id"])[:3], (302.0, 100.4, 0.0))
        self.assertEqual(self.balance(), (0.0, 0.0))

    def test_paid_in_full_means_the_net(self):
        res = self.bill([_line(3)], payment_mode="Cash", pay_full=True, apply_returns=[self.rid])
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.sale_row(res["sale_id"])[:3], (302.0, 100.4, 0.0))
        self.assertEqual(self.balance(), (0.0, 0.0))

    def test_credit_bigger_than_the_bill_is_used_in_part(self):
        # 1 strip: 90 + 10.80 = 100.80 -> 101.00
        res = self.bill([_line(1)], apply_returns=[self.rid])
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["returns_total"], 101.0)
        self.assertEqual(self.sale_row(res["sale_id"])[2], 0.0)
        self.assertEqual(self.open_left(), {self.ret["return_no"]: 100.6})
        self.assertEqual(self.balance(), (0.0, 100.6))
        # and the rest goes to the bill after
        nxt = self.bill([_line(1)], apply_returns=[self.rid])
        self.assertEqual(nxt["returns_total"], 100.6)
        self.assertEqual(self.sale_row(nxt["sale_id"])[2], 0.4)
        self.assertEqual(self.open_left(), {})
        self.assertEqual([a["amount"] for a in self.allocations(self.rid)], [101.0, 100.6])

    def test_unticked_returns_stay_open(self):
        res = self.bill([_line(1)])
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["returns_used"], [])
        self.assertEqual(self.allocations(self.rid), [])
        self.assertEqual(return_credit.returns_in_bill(self.conn, "customer", res["sale_id"]), [])

    def test_a_used_up_return_refuses_the_save(self):
        self.assertTrue(self.bill([_line(3)], apply_returns=[self.rid])["ok"])
        res = self.bill([_line(3)], apply_returns=[self.rid])
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "returns_changed")

    def test_the_edit_screen_shows_the_return_and_a_re_save_keeps_it(self):
        res = self.bill([_line(3)], apply_returns=[self.rid])
        sid = res["sale_id"]
        status, loaded = wholesale_sale_api.handle_get(
            "/api/wholesale/sale/load", {"sale_id": [str(sid)]}, self.conn)
        self.assertEqual(status, 200, loaded)
        self.assertEqual([(r["return_no"], r["amount"]) for r in loaded["returns_in_bill"]],
                         [(self.ret["return_no"], 201.6)])
        # the medicines that came back, counted in strips like the bill's lines
        item = loaded["returns_in_bill"][0]["items"][0]
        self.assertEqual((item["qty"], item["units"], item["units_per_pack"], item["rate"],
                          item["amount"]), (2, 20, 10, 100.8, 201.6))
        calc = ws.calc_sale(self.conn, {"customer_id": 1, "items": [_line(3)],
                                        "editing_sale_id": sid, "returns_settled": 201.6})
        self.assertEqual(calc["net_total"], 100.4)
        again = self.bill([_line(3)], editing_sale_id=sid, apply_returns=[self.rid])
        self.assertTrue(again["ok"], again)
        self.assertEqual(len(self.allocations(self.rid)), 1)
        self.assertEqual(self.sale_row(sid)[2], 100.4)
        self.assertEqual(self.balance(), (100.4, 0.0))

    def test_deleting_the_bill_frees_the_credit(self):
        res = self.bill([_line(3)], apply_returns=[self.rid])
        self.assertEqual(self.open_left(), {})
        out = sales_svc.delete_saved_sale(self.conn, {"sale_id": res["sale_id"]})
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(self.open_left(), {self.ret["return_no"]: 201.6})


class AReceiptInAWholesaleStore(_Store):
    def test_a_receipt_with_its_number_clears_the_wholesale_bill(self):
        from core import desktop_settings_service as settings

        first = self.bill([_line(3)])                                       # 302.00 on due
        second = self.bill([_line(1)])                                      # 101.00 on due
        self.assertEqual(self.balance(), (403.0, 0.0))
        out = settings.save_payment(self.conn, {
            "kind": "customer", "party": "TEST MEDICAL ONE", "cash": 101, "online": 0,
            "date": DAY, "receipt_no": "R-17",
            "allocations": [{"bill_id": second["sale_id"], "amount": 101}]})
        self.assertTrue(out.get("ok", True), out)
        row = self.conn.execute(
            "SELECT amount, receipt_no FROM customer_payments WHERE id=?", (out["payment_id"],)
        ).fetchone()
        self.assertEqual(row, (101.0, "R-17"))
        self.assertEqual(self.balance(), (302.0, 0.0))
        self.assertEqual(self.conn.execute(
            "SELECT total_due, bill_cleared FROM sales WHERE id=?", (second["sale_id"],)).fetchone(),
            (0.0, 1))
        self.assertEqual(self.conn.execute(
            "SELECT bill_cleared FROM sales WHERE id=?", (first["sale_id"],)).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
