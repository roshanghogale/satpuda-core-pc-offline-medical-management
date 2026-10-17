"""Returns are kept as the party's credit and settled inside their next bill (owner, 17 Oct 2026).

"Returns are saved as credit (cash/online refund optional); when billing a party who has return
credit not used yet, show a popup listing those returns to select; the bill gets a row 'Return
<no> <date>' followed by the returned medicines; the totals show Total, Return total, Net to pay.
Purchases the same: the agency settles returns inside the next purchase bill."

The server and the web are the reference (server-live webStore.js openReturns / returnsForBill /
returnsInBill). Everything here runs on a scratch store; nothing reaches a server.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import bill_output, db_setup, return_credit  # noqa: E402
from core import desktop_purchase_service as purchase_svc  # noqa: E402
from core import desktop_returns_service as returns_svc  # noqa: E402
from core import desktop_sales_service as sales_svc  # noqa: E402
from core import server_entity_sync as fb  # noqa: E402
from core.bill_config import render_bill_html  # noqa: E402
from tests._bill_fixtures import PROFILE, isolated_bill_settings  # noqa: E402

DAY = "2026-10-17"
CUSTOMER = "TEST CUSTOMER A"
SUPPLIER = "TEST AGENCY"


def _medicine(conn, name, rate, stock=100):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(medicines)")}
    vals = {"name": name, "stock_qty": stock, "mrp": rate, "rate": rate, "unit": "1",
            "type": "Syrup", "batch_no": "B1", "expiry_date": "2028-12-01",
            "gst_percent": 12, "created_at": "2026-01-01 00:00:00"}
    use = {k: v for k, v in vals.items() if k in cols}
    mid = conn.execute(
        f"INSERT INTO medicines ({','.join(use)}) VALUES ({','.join('?' * len(use))})",
        list(use.values()),
    ).lastrowid
    conn.commit()
    return mid


def _item(mid, name, qty, rate):
    return {"id": mid, "name": name, "qty": qty, "rate": rate, "amount": round(qty * rate, 2),
            "medicine_discount": 0, "unit": "1", "type": "Syrup", "batch": "B1", "gst_percent": 12}


class _Shop(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.sync_prefs.is_online_mode", lambda *a, **k: False),
            mock.patch.object(sales_svc, "_has_pharmacy_profile", return_value=True),
            mock.patch.object(sales_svc, "_try_save_sale_pdf", return_value=None),
            mock.patch.object(sales_svc, "_next_hint", return_value=""),
            mock.patch("core.bill_save_prefs.resolve_sales_bill_save_dir", return_value=""),
            mock.patch("core.autosave_prefs.load_autosave_enabled", return_value=False),
            mock.patch("core.online_catalog.medicine_by_id", return_value=None),
            mock.patch("core.server_crud.get_doc", return_value=None),
        ):
            stack.enter_context(patch)
        self.path = tempfile.mktemp(suffix=".db")
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.addCleanup(self.conn.close)
        db_setup.initialise(self.conn)
        self.syrup = _medicine(self.conn, "COUGH SYRUP", 10.0)
        self.tab = _medicine(self.conn, "PAIN TAB", 20.0)

    # -- sales ----------------------------------------------------------------------------
    def sell(self, items, **extra):
        body = {"items": items, "customer_name": CUSTOMER, "payment_mode": "Cash", "pay_full": True,
                "bill_date": DAY, "discount_rs": 0, "auto_rounding": False, "rounding": 0}
        body.update(extra)
        res = sales_svc.save_sale(self.conn, body)
        self.assertTrue(res.get("ok"), res)
        return res

    def customer_id(self):
        return self.conn.execute("SELECT id FROM customers WHERE name=?", (CUSTOMER,)).fetchone()[0]

    def give_back(self, sale_id, qty, settle="ledger", mid=None, rate=10.0):
        res = returns_svc.save_sales_return(self.conn, {
            "sale_id": sale_id, "customer_id": self.customer_id(), "settle_mode": settle,
            "items": [{"medicine_id": mid or self.syrup, "qty": qty, "rate": rate}],
        })
        self.assertTrue(res.get("ok"), res)
        return res

    def sale_row(self, sale_id):
        return self.conn.execute(
            "SELECT total_amount, amount_paid, due_amount, previous_credit FROM sales WHERE id=?",
            (sale_id,)).fetchone()

    def customer_balance(self):
        return self.conn.execute(
            "SELECT total_due, total_credit FROM customers WHERE id=?", (self.customer_id(),)
        ).fetchone()

    def allocations(self, table, rid):
        row = self.conn.execute(f"SELECT allocations FROM {table} WHERE id=?", (rid,)).fetchone()
        return json.loads(row[0]) if row and row[0] else []

    def open_left(self, kind="customer", pid=None):
        pid = pid or (self.customer_id() if kind == "customer" else self.supplier_id())
        return {r["return_no"]: r["left"] for r in return_credit.open_returns(self.conn, kind, pid)}

    # -- purchases ------------------------------------------------------------------------
    def supplier_id(self):
        return self.conn.execute("SELECT id FROM suppliers WHERE name=?", (SUPPLIER,)).fetchone()[0]

    def buy(self, bill, qty=2, paid_in_full=False, **extra):
        line = {"medicine_id": self.tab, "name": "PAIN TAB", "type": "Syrup", "batch": "B1",
                "expiry": "12/28", "qty": qty, "rate": 50.0, "mrp": 100.0, "unit": "1", "gst_pct": 0}
        payload = {"supplier_name": SUPPLIER, "bill_number": bill, "purchase_date": DAY,
                   "items": [line], "cash_paid": 0, "rounding": 0}
        if paid_in_full:
            calc = purchase_svc.calc_purchase(self.conn, dict(payload, skip_party_due=True))["calc"]
            payload["cash_paid"] = calc["final_amount"]
        payload.update(extra)
        res = purchase_svc.save_purchase_bill(self.conn, payload)
        self.assertTrue(res.get("ok"), res)
        return res


class TheReturnIsCredit(_Shop):
    def test_a_return_is_kept_as_credit_and_offered_with_its_medicines(self):
        s1 = self.sell([_item(self.syrup, "COUGH SYRUP", 3, 10.0)])
        ret = self.give_back(s1["sale_id"], 1)
        mode = self.conn.execute("SELECT settle_mode FROM sales_returns WHERE id=?",
                                 (ret["return_id"],)).fetchone()[0]
        self.assertEqual(mode, "ledger")
        offered = return_credit.open_returns(self.conn, "customer", self.customer_id())
        self.assertEqual(len(offered), 1)
        self.assertEqual(offered[0]["left"], 10.0)
        self.assertEqual(offered[0]["amount"], 10.0)
        self.assertEqual(offered[0]["items"], [{"name": "COUGH SYRUP", "qty": 1, "amount": 10.0,
                                                "batch_no": "B1", "expiry": "2028-12-01", "mrp": 10.0}])
        self.assertTrue(offered[0]["bill_no"].startswith("SCB"))
        # the engine route the popup reads
        via_route = return_credit.open_returns_for_party(self.conn, "customer", party=CUSTOMER)
        self.assertEqual([r["return_id"] for r in via_route["returns"]], [ret["return_id"]])

    def test_credit_partly_used_by_an_older_due_offers_only_what_is_left(self):
        s1 = self.sell([_item(self.syrup, "COUGH SYRUP", 3, 10.0)])
        self.sell([_item(self.tab, "PAIN TAB", 1, 20.0)], payment_mode="Due", pay_full=False)
        ret = self.give_back(s1["sale_id"], 3)  # 30 back; 20 of it clears the due bill
        self.assertEqual(self.open_left(), {ret["return_no"]: 10.0})
        offered = return_credit.open_returns(self.conn, "customer", self.customer_id())
        self.assertEqual(offered[0]["amount"], 30.0)

    def test_a_cash_or_online_refund_is_never_offered(self):
        s1 = self.sell([_item(self.syrup, "COUGH SYRUP", 3, 10.0)])
        cash = self.give_back(s1["sale_id"], 1, settle="cash")
        online = self.give_back(s1["sale_id"], 1, settle="online")
        refunds = self.conn.execute(
            "SELECT reference_no, amount FROM customer_payments WHERE amount < 0 ORDER BY id").fetchall()
        self.assertEqual(refunds, [(cash["return_no"], -10.0), (online["return_no"], -10.0)])
        self.assertEqual(self.open_left(), {})

    def test_money_handed_back_for_a_ledger_return_comes_off_it(self):
        s1 = self.sell([_item(self.syrup, "COUGH SYRUP", 3, 10.0)])
        ret = self.give_back(s1["sale_id"], 1)
        self.conn.execute(
            "INSERT INTO customer_payments (customer_id, payment_date, amount, payment_mode, reference_no) "
            "VALUES (?, ?, -3, 'cash', ?)", (self.customer_id(), DAY, ret["return_no"]))
        self.conn.commit()
        self.assertEqual(self.open_left(), {ret["return_no"]: 7.0})

    def test_the_counter_sale_is_never_offered_returns(self):
        s1 = self.sell([_item(self.syrup, "COUGH SYRUP", 3, 10.0)], customer_name="")
        cid = self.conn.execute("SELECT customer_id FROM sales WHERE id=?", (s1["sale_id"],)).fetchone()[0]
        returns_svc.save_sales_return(self.conn, {
            "sale_id": s1["sale_id"], "customer_id": cid,
            "items": [{"medicine_id": self.syrup, "qty": 1, "rate": 10.0}]})
        self.assertEqual(return_credit.open_returns(self.conn, "customer", cid), [])


class ASaleSettlesTheTickedReturns(_Shop):
    def setUp(self):
        super().setUp()
        s1 = self.sell([_item(self.syrup, "COUGH SYRUP", 3, 10.0)])
        self.s1 = s1["sale_id"]
        self.ret = self.give_back(self.s1, 1)
        self.assertEqual(self.customer_balance(), (0.0, 10.0))

    def test_paid_in_full_means_the_net_and_the_return_pays_the_rest(self):
        s2 = self.sell([_item(self.tab, "PAIN TAB", 2, 20.0)], apply_returns=[self.ret["return_id"]])
        sid = s2["sale_id"]
        self.assertEqual(self.allocations("sales_returns", self.ret["return_id"]),
                         [{"bill_id": sid, "bill_no": s2["bill_no"], "amount": 10.0}])
        total, paid, due, prev_credit = self.sale_row(sid)
        self.assertEqual((total, paid, due), (40.0, 30.0, 0.0))
        # the credit these returns were is not counted again as "Credit Used"
        self.assertEqual(prev_credit, 0.0)
        self.assertEqual(self.customer_balance(), (0.0, 0.0))
        self.assertEqual(self.open_left(), {})
        rets = return_credit.returns_in_bill(self.conn, "customer", sid)
        self.assertEqual([(r["return_no"], r["amount"], r["refund"]) for r in rets],
                         [(self.ret["return_no"], 10.0, 10.0)])
        self.assertEqual(rets[0]["items"][0]["name"], "COUGH SYRUP")
        details = sales_svc.get_bill_details(self.conn, sid)
        self.assertEqual((details["returns_total"], details["net_total"]), (10.0, 30.0))

    def test_two_returns_settle_in_one_bill(self):
        # owner, 18 Oct 2026: "multiple returns select karata yayla have"
        ret2 = self.give_back(self.s1, 1)
        self.assertEqual(self.open_left(), {self.ret["return_no"]: 10.0, ret2["return_no"]: 10.0})
        s2 = self.sell([_item(self.tab, "PAIN TAB", 2, 20.0)],
                       apply_returns=[self.ret["return_id"], ret2["return_id"]])
        sid = s2["sale_id"]
        total, paid, due, _prev = self.sale_row(sid)
        self.assertEqual((total, paid, due), (40.0, 20.0, 0.0))
        rets = return_credit.returns_in_bill(self.conn, "customer", sid)
        self.assertEqual([r["return_no"] for r in rets], [self.ret["return_no"], ret2["return_no"]])
        self.assertEqual(self.open_left(), {})

    def test_a_bill_on_due_takes_only_what_it_leaves_unpaid(self):
        s2 = self.sell([_item(self.syrup, "COUGH SYRUP", 1, 6.0)], payment_mode="Due", pay_full=False,
                       apply_returns=[self.ret["return_id"]])
        self.assertEqual(self.allocations("sales_returns", self.ret["return_id"])[0]["amount"], 6.0)
        self.assertEqual(self.sale_row(s2["sale_id"])[2], 0.0)
        self.assertEqual(self.open_left(), {self.ret["return_no"]: 4.0})
        self.assertEqual(self.customer_balance(), (0.0, 4.0))

    def test_cash_for_the_whole_bill_leaves_the_return_open(self):
        s2 = self.sell([_item(self.tab, "PAIN TAB", 1, 20.0)], pay_full=False, cash_paid=20,
                       apply_returns=[self.ret["return_id"]])
        self.assertEqual(self.allocations("sales_returns", self.ret["return_id"]), [])
        self.assertEqual(return_credit.returns_in_bill(self.conn, "customer", s2["sale_id"]), [])
        self.assertEqual(self.open_left(), {self.ret["return_no"]: 10.0})

    def test_a_return_with_no_credit_left_refuses_the_save(self):
        self.sell([_item(self.tab, "PAIN TAB", 1, 20.0)], apply_returns=[self.ret["return_id"]])
        res = sales_svc.save_sale(self.conn, {
            "items": [_item(self.tab, "PAIN TAB", 1, 20.0)], "customer_name": CUSTOMER,
            "payment_mode": "Cash", "pay_full": True, "bill_date": DAY,
            "apply_returns": [self.ret["return_id"]]})
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "returns_changed")

    def test_the_preview_shows_return_total_and_net(self):
        res = sales_svc.calc_sale(self.conn, {
            "items": [_item(self.tab, "PAIN TAB", 2, 20.0)], "cash_paid": 0, "skip_party_due": True,
            "previous_due": 0, "previous_credit": 10, "returns_total": 10, "rounding": 0,
            "auto_rounding": False})
        self.assertEqual((res["returns_total"], res["net_total"]), (10.0, 30.0))
        self.assertEqual(res["payment"]["due_amount"], 30.0)
        self.assertEqual(res["payment"].get("credit_applied", 0), 0)

    def test_editing_the_bill_keeps_its_return(self):
        s2 = self.sell([_item(self.tab, "PAIN TAB", 2, 20.0)], apply_returns=[self.ret["return_id"]])
        loaded = sales_svc.load_sale(self.conn, s2["sale_id"])
        self.assertEqual([r["return_no"] for r in loaded["returns_in_bill"]], [self.ret["return_no"]])
        preview = sales_svc.calc_sale(self.conn, {
            "items": [_item(self.tab, "PAIN TAB", 2, 20.0)], "cash_paid": 30, "skip_party_due": True,
            "previous_due": 0, "previous_credit": 0, "returns_settled": 10, "rounding": 0,
            "auto_rounding": False})
        self.assertEqual((preview["net_total"], preview["payment"]["due_amount"]), (30.0, 0.0))
        self.sell([_item(self.tab, "PAIN TAB", 2, 20.0)], editing_sale_id=s2["sale_id"],
                  pay_full=False, cash_paid=30, edit_previous_due=0)
        self.assertEqual(self.sale_row(s2["sale_id"])[2], 0.0)
        self.assertEqual(len(self.allocations("sales_returns", self.ret["return_id"])), 1)
        self.assertEqual(self.customer_balance(), (0.0, 0.0))

    def test_deleting_the_bill_frees_the_credit(self):
        s2 = self.sell([_item(self.tab, "PAIN TAB", 2, 20.0)], apply_returns=[self.ret["return_id"]])
        self.assertEqual(self.open_left(), {})
        res = sales_svc.delete_saved_sale(self.conn, {"sale_id": s2["sale_id"]})
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.open_left(), {self.ret["return_no"]: 10.0})
        self.assertEqual(self.customer_balance(), (0.0, 10.0))


class AnAutosavedBillSettlesThemToo(_Shop):
    """With autosave on, the bill is real before it is finished: its customer's return credit
    is read as it was before that bill."""

    def setUp(self):
        super().setUp()
        from core import autosave_session

        sessions = tempfile.mktemp(suffix=".json")
        os.environ["SATPUDA_AUTOSAVE_SESSION_FILE"] = sessions
        self.addCleanup(os.environ.pop, "SATPUDA_AUTOSAVE_SESSION_FILE", None)
        self.addCleanup(lambda: os.path.exists(sessions) and os.remove(sessions))
        for patch in (mock.patch.object(autosave_session, "_store_key", lambda: "test-store"),
                      mock.patch("core.autosave_prefs.load_autosave_enabled", return_value=True)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_finishing_an_autosaved_bill_settles_the_ticked_return(self):
        s1 = self.sell([_item(self.syrup, "COUGH SYRUP", 3, 10.0)])
        ret = self.give_back(s1["sale_id"], 1)
        items = [_item(self.tab, "PAIN TAB", 2, 20.0)]
        tick = sales_svc.autosave_sale(self.conn, {
            "items": items, "customer_name": CUSTOMER, "payment_mode": "Cash", "cash_paid": 0,
            "bill_date": DAY, "discount_rs": 0})
        self.assertTrue(tick.get("ok") and not tick.get("skipped"), tick)
        out = return_credit.open_returns_for_party(self.conn, "customer", party=CUSTOMER,
                                                   bill_id=tick["autosave_sale_id"])
        self.assertEqual([r["left"] for r in out["returns"]], [10.0])
        done = self.sell(items, autosave_sale_id=tick["autosave_sale_id"],
                         autosave_token=tick["autosave_token"], apply_returns=[ret["return_id"]])
        self.assertEqual([a["bill_id"] for a in self.allocations("sales_returns", ret["return_id"])],
                         [done["sale_id"]])
        self.assertEqual(self.sale_row(done["sale_id"])[:3], (40.0, 30.0, 0.0))
        self.assertEqual(self.customer_balance(), (0.0, 0.0))


class APurchaseSettlesTheAgencysReturns(_Shop):
    def test_a_purchase_return_is_settled_inside_the_next_purchase(self):
        p1 = self.buy("INV-1", paid_in_full=True)
        ret = returns_svc.save_purchase_return(self.conn, {
            "purchase_id": p1["purchase_id"], "supplier_id": self.supplier_id(),
            "items": [{"medicine_id": self.tab, "qty": 1, "rate": 50.0, "type": "Syrup", "unit": "1"}]})
        self.assertTrue(ret.get("ok"), ret)
        mode = self.conn.execute("SELECT settle_mode FROM purchase_returns WHERE id=?",
                                 (ret["return_id"],)).fetchone()[0]
        self.assertEqual(mode, "ledger")
        self.assertEqual(self.open_left("supplier"), {ret["return_no"]: 50.0})
        preview = purchase_svc.calc_purchase(self.conn, {
            "supplier_name": SUPPLIER, "skip_party_due": True, "returns_total": 50, "rounding": 0,
            "items": [{"medicine_id": self.tab, "name": "PAIN TAB", "qty": 4, "rate": 50.0, "mrp": 100.0,
                       "unit": "1", "type": "Syrup", "gst_pct": 0}]})
        self.assertEqual(preview["calc"]["returns_total"], 50.0)
        self.assertEqual(preview["calc"]["net_after_returns"], preview["calc"]["final_amount"] - 50)
        p2 = self.buy("INV-2", qty=4, apply_returns=[ret["return_id"]])
        self.assertEqual(p2["returns_used"], 50.0)
        alloc = self.allocations("purchase_returns", ret["return_id"])
        self.assertEqual([(a["bill_id"], a["amount"]) for a in alloc], [(p2["purchase_id"], 50.0)])
        final = self.conn.execute("SELECT COALESCE(final_amount,total_amount), due_amount FROM purchases "
                                  "WHERE id=?", (p2["purchase_id"],)).fetchone()
        self.assertEqual(final[1], round(final[0] - 50, 2))
        due = self.conn.execute("SELECT total_due, total_credit FROM suppliers WHERE id=?",
                                (self.supplier_id(),)).fetchone()
        self.assertEqual(due, (round(final[0] - 50, 2), 0.0))
        self.assertEqual(self.open_left("supplier"), {})
        loaded = purchase_svc.load_purchase(self.conn, p2["purchase_id"])
        self.assertEqual([r["return_no"] for r in loaded["returns_in_bill"]], [ret["return_no"]])

    def test_editing_a_purchase_return_keeps_the_purchase_it_was_settled_in(self):
        p1 = self.buy("INV-1", paid_in_full=True)
        ret = returns_svc.save_purchase_return(self.conn, {
            "purchase_id": p1["purchase_id"], "supplier_id": self.supplier_id(),
            "items": [{"medicine_id": self.tab, "qty": 1, "rate": 50.0, "type": "Syrup", "unit": "1"}]})
        p2 = self.buy("INV-2", qty=4, apply_returns=[ret["return_id"]])
        fixed = returns_svc.replace_purchase_return(self.conn, {
            "return_id": ret["return_id"],
            "items": [{"medicine_id": self.tab, "qty": 1, "rate": 40.0}]})
        self.assertTrue(fixed.get("ok"), fixed)
        alloc = self.allocations("purchase_returns", fixed["return_id"])
        self.assertEqual([a["bill_id"] for a in alloc], [p2["purchase_id"]])


class TheReturnDocumentRoundTrip(_Shop):
    def setUp(self):
        super().setUp()
        s1 = self.sell([_item(self.syrup, "COUGH SYRUP", 3, 10.0)])
        self.ret = self.give_back(s1["sale_id"], 1)
        self.rid = self.ret["return_id"]
        self.s2 = self.sell([_item(self.tab, "PAIN TAB", 2, 20.0)], apply_returns=[self.rid])

    def test_the_document_always_carries_allocations_and_settle_mode(self):
        doc = fb.build_sales_return_payload(self.conn, self.rid)
        self.assertEqual(doc["settle_mode"], "ledger")
        self.assertEqual(doc["allocations"],
                         [{"bill_id": self.s2["sale_id"], "bill_no": self.s2["bill_no"], "amount": 10.0}])
        p1 = self.buy("INV-1", paid_in_full=True)
        pr = returns_svc.save_purchase_return(self.conn, {
            "purchase_id": p1["purchase_id"], "supplier_id": self.supplier_id(),
            "items": [{"medicine_id": self.tab, "qty": 1, "rate": 50.0, "type": "Syrup", "unit": "1"}]})
        pdoc = fb.build_purchase_return_payload(self.conn, pr["return_id"])
        self.assertEqual((pdoc["settle_mode"], pdoc["allocations"]), ("ledger", []))

    def test_offline_first_sends_the_return_again_when_a_bill_uses_it(self):
        from core.offline_first import outbox, schema

        schema.ensure_schema(self.conn)
        s3_items = [_item(self.syrup, "COUGH SYRUP", 1, 5.0)]
        fresh = self.give_back(self.s2["sale_id"], 1, mid=self.tab, rate=20.0)
        self.conn.execute("DELETE FROM of_dirty")
        self.conn.commit()
        self.sell(s3_items, payment_mode="Due", pay_full=False, apply_returns=[fresh["return_id"]])
        dirty = {(c, int(i)) for c, i in self.conn.execute("SELECT collection, local_id FROM of_dirty")}
        self.assertIn(("sales_returns", fresh["return_id"]), dirty)
        doc = outbox.build_doc(self.conn, "sales_returns", fresh["return_id"])
        self.assertEqual([a["amount"] for a in doc["allocations"]], [5.0])
        self.assertEqual(doc["settle_mode"], "ledger")

    def pull(self, doc):
        # The offline-first worker writes pulled pages this way (core/offline_first/pull.py);
        # each one a newer version than the PC holds.
        self._v = getattr(self, "_v", int(doc.get("version") or 1)) + 1
        with fb.offline_first_apply():
            return fb.sync_down_doc(self.conn, "sales_returns", str(self.rid), dict(doc, version=self._v))

    def test_a_pull_stores_them_and_an_older_document_keeps_the_local_ones(self):
        doc = fb.build_sales_return_payload(self.conn, self.rid)
        older = {k: v for k, v in doc.items() if k not in ("allocations", "settle_mode")}
        self.assertEqual(self.pull(older), "applied")
        self.assertEqual(len(self.allocations("sales_returns", self.rid)), 1)
        self.assertEqual(self.conn.execute("SELECT settle_mode FROM sales_returns WHERE id=?",
                                           (self.rid,)).fetchone()[0], "ledger")
        moved = dict(doc, allocations=[{"bill_id": 999, "bill_no": "X", "amount": 4}], settle_mode="cash")
        self.assertEqual(self.pull(moved), "applied")
        self.assertEqual(self.allocations("sales_returns", self.rid),
                         [{"bill_id": 999, "bill_no": "X", "amount": 4.0}])
        self.assertEqual(self.conn.execute("SELECT settle_mode FROM sales_returns WHERE id=?",
                                           (self.rid,)).fetchone()[0], "cash")
        self.assertEqual(self.pull(dict(doc, allocations=None, settle_mode="")), "applied")
        self.assertEqual(self.allocations("sales_returns", self.rid), [])
        self.assertEqual(self.conn.execute("SELECT settle_mode FROM sales_returns WHERE id=?",
                                           (self.rid,)).fetchone()[0], "cash")


class TheBillShowsTheReturns(_Shop):
    def setUp(self):
        super().setUp()
        s1 = self.sell([_item(self.syrup, "COUGH SYRUP", 3, 10.0)])
        self.ret = self.give_back(s1["sale_id"], 1)

    def ctx(self, sale_id):
        with isolated_bill_settings(), mock.patch(
            "core.pharmacy_profile_io.fetch_pharmacy_profile_row", return_value=PROFILE
        ):
            _, ctx, settings = bill_output._load_sale_data(self.conn, sale_id)
            html = render_bill_html(ctx, settings)
        return ctx, settings, html

    def test_the_printed_bill_has_return_rows_and_the_totals(self):
        plain = self.sell([_item(self.tab, "PAIN TAB", 2, 20.0)], pay_full=False, cash_paid=40)
        s2 = self.sell([_item(self.tab, "PAIN TAB", 2, 20.0)], apply_returns=[self.ret["return_id"]])
        base, _s, _h = self.ctx(plain["sale_id"])
        ctx, _settings, html = self.ctx(s2["sale_id"])
        self.assertEqual(ctx.returns_total, 10.0)
        self.assertEqual([r.kind for r in ctx.returns], ["return", "medicine", "total"])
        self.assertEqual(len(ctx.items), 1)  # the return is never an item
        # GST and the Amount sum are the bill's own
        self.assertEqual((ctx.sub_total, ctx.gst_amount, ctx.taxable_amount, ctx.grand_total),
                         (base.sub_total, base.gst_amount, base.taxable_amount, base.grand_total))
        # a return is dated the day it was made
        self.assertIn(f"Return {self.ret['return_no']} {date.today():%d-%m-%Y}", html)
        self.assertIn("COUGH SYRUP", html)
        self.assertIn("-10.00", html)
        self.assertIn("Return total", html)
        self.assertIn("Net to pay", html)
        self.assertIn("30.00", html)
        self.assertNotIn("used in this bill", html)
        # owner, 18 Oct 2026: the Return row is centred, without the customer's name, and
        # the medicine that came back is a row like an item (batch, expiry, MRP)
        head = html.split('class="ret-head"')[1].split("</tr>")[0]
        self.assertIn('class="c ret-text"', head)
        self.assertNotIn("TEST CUSTOMER", head)
        line = html.split('class="ret-line"')[1].split("</tr>")[0]
        self.assertIn("COUGH SYRUP", line)
        self.assertIn("B1", line)
        self.assertIn("-10.00", line)

    def test_part_of_a_return_says_how_much_of_it(self):
        s2 = self.sell([_item(self.syrup, "COUGH SYRUP", 1, 6.0)], payment_mode="Due", pay_full=False,
                       apply_returns=[self.ret["return_id"]])
        _ctx, _settings, html = self.ctx(s2["sale_id"])
        self.assertIn("(₹6.00 of ₹10.00 used in this bill)", html)

    def test_the_dot_matrix_slip_has_them_too(self):
        import core.dot_matrix_print as dmp
        from tests.test_dot_matrix_paper_geometry import shop_settings

        s2 = self.sell([_item(self.tab, "PAIN TAB", 2, 20.0)], apply_returns=[self.ret["return_id"]])
        ctx, _settings, _html = self.ctx(s2["sale_id"])
        merged = dmp._settings_for_ctx(shop_settings(paper_size="A5"), ctx)
        text = dmp.printed_text(dmp.format_bill_text(ctx, merged))
        self.assertIn(f"Return {self.ret['return_no']}", text)
        line = [ln for ln in text.splitlines() if "COUGH SYRUP" in ln]
        self.assertTrue(line and "-10.00" in line[0] and "B1" in line[0], text)
        self.assertIn("-10.00", text)
        self.assertIn("Return total", text)
        net = [line for line in text.splitlines() if "Net to pay" in line]
        self.assertTrue(net and "30.00" in net[0], text)
        # the Amount column of the items is untouched
        self.assertIn("40.00", text)

    def test_carry_forward_pages_count_the_return_rows(self):
        s2 = self.sell([_item(self.tab, "PAIN TAB", 2, 20.0), _item(self.syrup, "COUGH SYRUP", 1, 10.0)],
                       apply_returns=[self.ret["return_id"]])
        ctx, _settings, _html = self.ctx(s2["sale_id"])
        pages = bill_output._carry_forward_contexts(ctx, 3)
        self.assertEqual(len(pages), 2)
        self.assertEqual((len(pages[0].items), len(pages[0].returns)), (2, 1))
        self.assertEqual((len(pages[1].items), len(pages[1].returns)), (0, 2))  # medicine, total
        self.assertTrue(pages[0].is_continued)
        self.assertEqual(pages[0].page_subtotal, 50.0)
        self.assertEqual(pages[1].returns_total, 10.0)


if __name__ == "__main__":
    unittest.main()
