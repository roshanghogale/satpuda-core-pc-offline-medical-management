"""A counter sale merged into today's COUNTER SALE bill is paid in full by F7.

Every counter sale of the day goes into one COUNTER SALE bill. Each form is rounded to the
rupee and F7 takes that rounded amount in cash, so the bill's cash is the sum of every form's
rounded total. The merge wrote the bill with the LATEST form's rounding (and its discount)
applied to all the day's lines, so the bill total and the cash parted by the earlier forms'
rounding: store 4 SCB1412 ended 18.08 total / 18 paid ('Partial Payment') and COUNTER SALE went
715.24 -> 715.32; store 127 picked up a phantom 0.44 credit. Both merge paths did it: the
autosave tick + F7 (write_autosave_bill's counter branch) and a direct F7
(billing_service.append_counter_sale_today).

The merged bill is now what was already on it plus this form's own total: the discounts add up
in rupees and the rounding is what makes the lines come to that total. A discarded counter form
takes out exactly what it put in.

Offline runs through the real save_sale / autosave_sale on a temp store; Online patches the
server client. Nothing reaches a server.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from unittest import mock

from core import autosave_bill, autosave_session, billing_service, db_setup  # noqa: E402
from core import desktop_sales_service, sync_prefs  # noqa: E402
from core.calc_engine import calc_bill_summary  # noqa: E402

DAY = "2026-09-10"


def _medicine(conn, name, rate, stock=100):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(medicines)")}
    vals = {"name": name, "stock_qty": stock, "mrp": rate, "rate": rate, "unit": "1",
            "type": "Syrup", "batch_no": "B1", "expiry_date": "2028-12-01",
            "created_at": "2026-01-01 00:00:00"}
    use = {k: v for k, v in vals.items() if k in cols}
    mid = conn.execute(
        f"INSERT INTO medicines ({','.join(use)}) VALUES ({','.join('?' * len(use))})",
        list(use.values()),
    ).lastrowid
    conn.commit()
    return mid


def _item(mid, name, qty, rate):
    return {"id": mid, "name": name, "qty": qty, "rate": rate, "amount": round(qty * rate, 2),
            "medicine_discount": 0, "unit": "1", "type": "Syrup", "batch": "B1"}


class _CounterDay(unittest.TestCase):
    def setUp(self):
        self._sessions = tempfile.mktemp(suffix=".json")
        os.environ["SATPUDA_AUTOSAVE_SESSION_FILE"] = self._sessions
        self.addCleanup(os.environ.pop, "SATPUDA_AUTOSAVE_SESSION_FILE", None)
        self.addCleanup(lambda: os.path.exists(self._sessions) and os.remove(self._sessions))
        self._online = sync_prefs.is_online_mode
        sync_prefs.is_online_mode = lambda *a, **k: False
        self.addCleanup(setattr, sync_prefs, "is_online_mode", self._online)
        self.conn = sqlite3.connect(tempfile.mktemp(suffix=".db"))
        self.addCleanup(self.conn.close)
        db_setup.initialise(self.conn)
        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch.object(autosave_session, "_store_key", lambda: "test-store"),
            mock.patch.object(desktop_sales_service, "_has_pharmacy_profile", return_value=True),
            mock.patch.object(desktop_sales_service, "_try_save_sale_pdf", return_value=None),
            mock.patch.object(desktop_sales_service, "_next_hint", return_value=""),
            mock.patch("core.bill_save_prefs.resolve_sales_bill_save_dir", return_value=""),
            mock.patch("core.autosave_prefs.load_autosave_enabled", return_value=True),
        ):
            stack.enter_context(patch)
        self.a = _medicine(self.conn, "AARJE COLD", 6.17)
        self.b = _medicine(self.conn, "4X BREATH", 8.91)
        self.c = _medicine(self.conn, "DUDHGANGA", 0.90)
        self.d = _medicine(self.conn, "BIG SYRUP", 20.0)

    # -- the two ways a counter sale reaches the day bill ---------------------------------
    def f7(self, items, **extra):
        body = {"items": items, "customer_name": "", "payment_mode": "Cash", "pay_full": True,
                "bill_date": DAY, "discount_rs": 0}
        body.update(extra)
        res = desktop_sales_service.save_sale(self.conn, body)
        self.assertTrue(res.get("ok"), res)
        return res

    def tick(self, items, **extra):
        body = {"items": items, "customer_name": "", "payment_mode": "Cash", "cash_paid": 0,
                "bill_date": DAY, "discount_rs": 0}
        body.update(extra)
        res = desktop_sales_service.autosave_sale(self.conn, body)
        self.assertTrue(res.get("ok") and not res.get("skipped"), res)
        return res

    def tick_then_f7(self, items, **extra):
        t = self.tick(items, **extra)
        return self.f7(items, autosave_sale_id=t["autosave_sale_id"],
                       autosave_token=t["autosave_token"], **extra), t

    # -- what the books say -----------------------------------------------------------------
    def bills(self):
        return self.conn.execute(
            "SELECT id, bill_no, total_amount, amount_paid, due_amount, credit_amount, "
            "bill_cleared, discount, rounding FROM sales "
            "WHERE COALESCE(deleted,0)=0 AND COALESCE(is_autosave,0)=0").fetchall()

    def counter_sale(self):
        return self.conn.execute(
            "SELECT COALESCE(total_due,0), COALESCE(total_credit,0) FROM customers "
            "WHERE UPPER(name)='COUNTER SALE'").fetchone()

    def stock(self, mid):
        return float(self.conn.execute("SELECT stock_qty FROM medicines WHERE id=?",
                                       (mid,)).fetchone()[0])

    def assertPaidInFull(self, total):
        [bill] = self.bills()
        _id, _no, t, paid, due, credit, cleared, _disc, _round = bill
        self.assertAlmostEqual(float(t), total, places=2, msg=f"bill {bill}")
        self.assertAlmostEqual(float(paid), float(t), places=2, msg=f"paid != total: {bill}")
        self.assertAlmostEqual(float(due or 0), 0.0, places=2, msg=f"bill {bill}")
        self.assertAlmostEqual(float(credit or 0), 0.0, places=2, msg=f"bill {bill}")
        self.assertEqual(int(cleared or 0), 1, f"bill {bill}")
        due_c, credit_c = self.counter_sale()
        self.assertAlmostEqual(float(due_c), 0.0, places=2, msg="COUNTER SALE drifted into due")
        self.assertAlmostEqual(float(credit_c), 0.0, places=2, msg="COUNTER SALE got credit")
        return bill


class ThreeCounterSalesInOneDay(_CounterDay):
    def test_direct_f7_then_tick_and_f7_then_direct_f7(self):
        first = self.f7([_item(self.a, "AARJE COLD", 1, 6.17)])          # 6.17 -> 6
        bill_no = self.assertPaidInFull(6.0)[1]
        second, _t = self.tick_then_f7([_item(self.b, "4X BREATH", 1, 8.91)])  # 8.91 -> 9
        self.assertTrue(second.get("merged_counter"))
        self.assertEqual(self.assertPaidInFull(15.0)[1], bill_no)
        third = self.f7([_item(self.c, "DUDHGANGA", 3, 0.90)])           # 2.70 -> 3
        self.assertTrue(third.get("merged_counter"))
        bill = self.assertPaidInFull(18.0)
        self.assertEqual(bill[1], bill_no, "the day bill kept its number")
        self.assertEqual(int(first["sale_id"]), int(bill[0]))
        self.assertEqual((self.stock(self.a), self.stock(self.b), self.stock(self.c)),
                         (99.0, 99.0, 97.0))

    def test_ticking_the_same_form_again_before_f7_changes_nothing(self):
        self.f7([_item(self.a, "AARJE COLD", 1, 6.17)])
        t = self.tick([_item(self.b, "4X BREATH", 1, 8.91)])
        t = self.tick([_item(self.b, "4X BREATH", 1, 8.91)], autosave_sale_id=t["autosave_sale_id"],
                      autosave_token=t["autosave_token"])
        self.f7([_item(self.b, "4X BREATH", 1, 8.91)], autosave_sale_id=t["autosave_sale_id"],
                autosave_token=t["autosave_token"])
        self.assertPaidInFull(15.0)
        self.assertEqual(self.stock(self.b), 99.0)

    def test_a_later_form_with_a_rupee_discount_does_not_discount_the_earlier_sale(self):
        self.f7([_item(self.a, "AARJE COLD", 1, 6.17)])                  # 6
        self.f7([_item(self.d, "BIG SYRUP", 1, 20.0)], discount_rs=2)    # 20 - 2 = 18
        bill = self.assertPaidInFull(24.0)
        self.assertAlmostEqual(float(bill[7]), 2.0, places=2, msg="bill discount")

    def test_a_discarded_counter_form_takes_out_only_what_it_put_in(self):
        self.f7([_item(self.a, "AARJE COLD", 1, 6.17)])                  # 6, paid
        t = self.tick([_item(self.b, "4X BREATH", 1, 8.91)])             # not paid yet
        out = autosave_bill.discard_autosave_bill(self.conn, token=t["autosave_token"],
                                                  sale_id=t["autosave_sale_id"])
        self.assertTrue(out.get("ok"), out)
        self.assertPaidInFull(6.0)
        self.assertEqual(self.stock(self.b), 100.0)


class OnlineMergePaths(unittest.TestCase):
    """The same arithmetic on the server path, with the server client patched."""

    DAY_BILL = {"id": 77, "local_id": 77, "bill_no": "SCB1412/FY2026-27", "bill_date": DAY,
                "customer_id": 26, "customer_name": "COUNTER SALE", "cash_paid": 6.0,
                "online_paid": 0.0, "total_amount": 6.0, "discount": 0.0, "rounding": -0.17,
                "items": [{"medicine_id": 1, "qty": 1, "rate": 6.17, "amount": 6.17,
                           "medicine_name": "AARJE COLD"}]}

    def setUp(self):
        self._online = sync_prefs.is_online_mode
        sync_prefs.is_online_mode = lambda *a, **k: True
        self.addCleanup(setattr, sync_prefs, "is_online_mode", self._online)
        self.seen: dict = {}

        def fake_update(conn, sale_id, medicines, **kw):
            self.seen.update(kw)
            self.seen["sale_id"] = sale_id
            self.seen["medicines"] = [dict(m) for m in medicines]

        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch.object(billing_service, "update_existing_bill", side_effect=fake_update),
            mock.patch("core.server_crud.get_doc",
                       side_effect=lambda coll, sid, *a, **k: (
                           dict(self.DAY_BILL) if coll == "sales" and int(sid) == 77 else {})),
            mock.patch("core.online_catalog.medicine_by_id",
                       side_effect=lambda m: {"id": m, "name": f"MED{m}", "unit": "1"}),
            mock.patch("core.online_catalog.find_customer_by_id",
                       return_value={"id": 26, "name": "COUNTER SALE"}),
        ):
            stack.enter_context(patch)

    def written_total(self):
        kw = self.seen
        return calc_bill_summary(kw["medicines"], kw.get("discount_pct") or 0,
                                 kw.get("rounding") or 0,
                                 discount_rs=kw.get("discount_rs"))["total_amount"]

    def test_direct_f7_merge(self):
        form = [{"id": 2, "name": "4X BREATH", "qty": 1, "rate": 8.91, "amount": 8.91,
                 "medicine_discount": 0, "schedule": ""}]
        with mock.patch.object(billing_service, "find_todays_counter_sale_id", return_value=77):
            out = billing_service.append_counter_sale_today(
                None, 26, "COUNTER SALE", form, 0, 0.09, 9.0, 0.0, "", "", 0.0,
                discount_rs=0.0, bill_date=DAY)
        self.assertEqual(out, ("SCB1412/FY2026-27", 77))
        self.assertAlmostEqual(self.written_total(), 15.0, places=2)
        self.assertAlmostEqual(float(self.seen["cash_paid"]), 15.0, places=2)

    def test_tick_merge(self):
        sessions = tempfile.mktemp(suffix=".json")
        os.environ["SATPUDA_AUTOSAVE_SESSION_FILE"] = sessions
        self.addCleanup(os.environ.pop, "SATPUDA_AUTOSAVE_SESSION_FILE", None)
        self.addCleanup(lambda: os.path.exists(sessions) and os.remove(sessions))
        form = [{"id": 2, "name": "4X BREATH", "qty": 1, "rate": 8.91, "amount": 8.91,
                 "medicine_discount": 0, "schedule": ""}]
        with mock.patch.object(autosave_session, "_store_key", lambda: "test-store"), \
                mock.patch("core.store_query_client.list_sales",
                           lambda **kw: {"rows": [dict(self.DAY_BILL)]}), \
                mock.patch("core.online_mutation_queue.flush_now", return_value=True):
            res = autosave_bill.write_autosave_bill(
                None, customer_id=26, customer_name="COUNTER SALE", medicines=form,
                discount_pct=0, discount_rs=0.0, rounding=0.09, cash_paid=9.0,
                online_paid=0, previous_due=0, bill_date=DAY, final=True,
                bill_date_problems=[])
        self.assertEqual(res["sale_id"], 77)
        self.assertAlmostEqual(self.written_total(), 15.0, places=2)
        self.assertAlmostEqual(float(self.seen["cash_paid"]), 15.0, places=2)


if __name__ == "__main__":
    unittest.main()
