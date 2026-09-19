"""An autosave tick on a back-dated tab writes no bill that the bill date refuses.

F7 checks every line against the Bill Date (a batch must have come in by then and not have
expired by then), but the autosave tick never did. On a tab dated 2026-03-20 holding a batch
bought in June, the first tick wrote a real bill SCB1/FY2025-26 with that batch, took it off
the shelf, and F7 refused afterwards while the bill and the stock stayed. Changing the Bill
Date only clears the entry fields, not the lines already on the bill.

Now a tick whose lines the bill date refuses writes nothing: a tab that already wrote a bill
keeps its last write (checked for its own date and paid for its own lines), and a tab that
never wrote makes no bill. The form snapshot is still kept -- the lines, the customer, the
payment -- so nothing typed is lost, and a restart offers it back. Writing the allowed lines
alone is not an answer: the payment typed for the whole form would turn into customer credit.
Classic runs its tick on the Tk thread, so it never waits on the store: until the answer for
that date is in, the tick holds the form and the answer is worked out behind it.

Offline cases use an in-memory store; Online cases patch the store pulls. Sessions go to a
temp file. Nothing reaches a server.
"""
from __future__ import annotations

import ast
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from datetime import date
from unittest import mock

from core import autosave_bill, autosave_session, db_setup, desktop_sales_service  # noqa: E402
from core import sale_availability  # noqa: E402

BILL_DATE = "2026-03-20"
TODAY = date.today().isoformat()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _line(mid, name, batch, rate):
    return {"id": mid, "name": name, "batch": batch, "expiry": "2027-12-01", "qty": 1,
            "rate": rate, "amount": rate, "medicine_discount": 0, "gst_percent": 0,
            "schedule": ""}


OLD = _line(1, "OLD STOCK TAB", "A1", 100.0)
NEW = _line(3, "NEW ARRIVAL CAP", "NA1", 500.0)


class _Sessions(unittest.TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp(prefix="held_tick_")
        self.addCleanup(shutil.rmtree, folder, True)
        os.environ["SATPUDA_AUTOSAVE_SESSION_FILE"] = os.path.join(folder, "sessions.json")
        self.addCleanup(os.environ.pop, "SATPUDA_AUTOSAVE_SESSION_FILE", None)
        patch = mock.patch.object(autosave_session, "_store_key", return_value="probe-store")
        patch.start()
        self.addCleanup(patch.stop)


class _Shop(_Sessions):
    """An offline shop: OLD STOCK TAB since January, NEW ARRIVAL CAP bought in June."""

    def setUp(self):
        super().setUp()
        self.conn = conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        conn.execute("INSERT INTO suppliers (id, name) VALUES (1, 'SHREE PHARMA')")
        for mid, name, batch, created, rate in (
            (1, "OLD STOCK TAB", "A1", "2026-01-10 09:00:00", 100.0),
            (3, "NEW ARRIVAL CAP", "NA1", "2026-06-01 10:00:00", 500.0),
        ):
            conn.execute(
                "INSERT INTO medicines (id, name, type, unit, gst_percent, mrp, rate, stock_qty, "
                "batch_no, expiry_date, created_at) VALUES (?, ?, 'TAB', '1', 0, ?, ?, 50, ?, "
                "'2027-12-01', ?)",
                (mid, name, rate, rate, batch, created),
            )
        for pid, pdate, mid in ((1, "2026-01-10", 1), (3, "2026-06-01", 3)):
            conn.execute(
                "INSERT INTO purchases (id, purchase_no, supplier_id, purchase_date, deleted, "
                "is_autosave) VALUES (?, ?, 1, ?, 0, 0)",
                (pid, f"{pid}/FY2025-26", pdate),
            )
            conn.execute(
                "INSERT INTO purchase_items (purchase_id, medicine_id, qty, rate) VALUES (?, ?, 50, 60)",
                (pid, mid),
            )
        conn.commit()

    def tick(self, items, bill_date=BILL_DATE, cash=600.0):
        return desktop_sales_service.autosave_sale(self.conn, {
            "items": [dict(i) for i in items], "bill_date": bill_date, "customer_name": "RAM",
            "payment_mode": "Cash", "cash_paid": cash, "online_paid": 0, "force": True,
            "autosave_token": "tok-1",
        })

    def sales(self):
        return self.conn.execute(
            "SELECT id, bill_date, total_amount FROM sales WHERE COALESCE(deleted,0)=0"
        ).fetchall()

    def stock(self, mid):
        return self.conn.execute("SELECT stock_qty FROM medicines WHERE id=?", (mid,)).fetchone()[0]


class ATauriTickOnABackDatedTab(_Shop):
    def test_it_writes_no_bill_and_keeps_the_form(self):
        res = self.tick([OLD, NEW])
        self.assertEqual(self.sales(), [], "the tick wrote a bill with a batch bought later")
        self.assertEqual((self.stock(1), self.stock(3)), (50, 50))
        self.assertTrue(res.get("ok"), res)
        self.assertTrue(res.get("skipped"), res)
        self.assertEqual(res.get("reason"), "not_available_on_date")
        self.assertFalse(res.get("autosave_sale_id"))
        self.assertTrue(any("NEW ARRIVAL CAP" in m for m in res.get("messages") or []), res)

        [kept] = autosave_bill.list_recoverable()
        self.assertEqual((kept["sale_id"], kept["items"], kept["total"]), (0, 2, 600.0))
        self.assertTrue(kept.get("held"))
        back = autosave_bill.resume_autosave_bill(self.conn, token="tok-1")
        self.assertTrue(back.get("ok"), back)
        self.assertEqual(len(back["form"]["medicines"]), 2)

    def test_once_the_later_batch_is_removed_the_tick_writes_the_bill(self):
        self.tick([OLD, NEW])
        res = self.tick([OLD], cash=100.0)
        self.assertTrue(res.get("autosave_sale_id"), res)
        [(sale_id, bill_date, total)] = self.sales()
        self.assertEqual((str(bill_date)[:10], total), (BILL_DATE, 100.0))
        self.assertEqual((self.stock(1), self.stock(3)), (49, 50))
        [kept] = autosave_bill.list_recoverable()
        self.assertEqual(kept["sale_id"], sale_id)
        self.assertFalse(kept.get("held"))

    def test_a_tab_that_already_wrote_keeps_its_last_write(self):
        first = self.tick([OLD, NEW], bill_date=TODAY)
        sale_id = first["autosave_sale_id"]
        self.assertTrue(sale_id)
        self.assertEqual((self.stock(1), self.stock(3)), (49, 49))

        res = self.tick([OLD, NEW], bill_date=BILL_DATE)  # the Bill Date moved back
        self.assertTrue(res.get("skipped"), res)
        [(sid, bill_date, total)] = self.sales()
        self.assertEqual((sid, str(bill_date)[:10], total), (sale_id, TODAY, 600.0))
        self.assertEqual((self.stock(1), self.stock(3)), (49, 49))
        [kept] = autosave_bill.list_recoverable()
        self.assertEqual(kept["sale_id"], sale_id)
        rec = autosave_session.load_session("tok-1")
        self.assertEqual(rec["form"]["bill_date"], BILL_DATE, "the typed Bill Date was not kept")

    def test_discarding_a_held_form_touches_no_bill(self):
        self.tick([OLD, NEW])
        out = autosave_bill.discard_autosave_bill(self.conn, token="tok-1")
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(autosave_bill.list_recoverable(), [])
        self.assertEqual(self.sales(), [])


class TheTauriTickAsksAboutTheDateOutsideTheSaleLock(_Shop):
    """Every save needs the sale write lock; the date check can wait on the store for 15 s.

    The tick held the lock while it asked, so F7 on every other tab -- today's bills too --
    waited behind a back-dated tab's check, once a minute on a slow store. save_sale asks
    before it takes the lock; the tick now does the same, and asks only once.
    """

    def test_another_tab_can_take_the_lock_while_the_tick_checks(self):
        real = sale_availability.lines_unavailable_on
        free_while_checking: list[bool] = []

        def check(conn, medicines, bill_date, **kw):
            got: list[bool] = []

            def other_tab():
                took = desktop_sales_service._SALE_WRITE_LOCK.acquire(blocking=False)
                if took:
                    desktop_sales_service._SALE_WRITE_LOCK.release()
                got.append(took)

            worker = threading.Thread(target=other_tab)
            worker.start()
            worker.join(5)
            free_while_checking.append(bool(got and got[0]))
            return real(conn, medicines, bill_date, **kw)

        with mock.patch.object(sale_availability, "lines_unavailable_on", side_effect=check):
            res = self.tick([OLD, NEW])
        self.assertTrue(res.get("skipped"), res)
        self.assertEqual(res.get("reason"), "not_available_on_date")
        self.assertEqual(free_while_checking, [True],
                         "the tick held the sale write lock while it waited on the date check")
        self.assertEqual(self.sales(), [])

    def test_a_tick_the_date_allows_still_writes_once(self):
        with mock.patch.object(sale_availability, "lines_unavailable_on",
                               wraps=sale_availability.lines_unavailable_on) as check:
            res = self.tick([OLD], cash=100.0)
        self.assertTrue(res.get("autosave_sale_id"), res)
        self.assertEqual(check.call_count, 1, "the date was asked about twice")
        self.assertEqual(len(self.sales()), 1)


class TheVerifierHarnessCase(_Shop):
    """scratchpad/verify4/desk_autosave_backdated.py, as a test: stock 50 stays 50."""

    def test_autosave_then_f7(self):
        body = {"items": [OLD, NEW], "bill_date": BILL_DATE, "customer_name": "RAM",
                "payment_mode": "Cash", "cash_paid": 600.0, "online_paid": 0, "force": True,
                "autosave_token": "tok-probe"}
        res = desktop_sales_service.autosave_sale(self.conn, dict(body))
        with mock.patch.object(desktop_sales_service, "_has_pharmacy_profile", return_value=True):
            f7 = desktop_sales_service.save_sale(
                self.conn, dict(body, autosave_sale_id=res.get("autosave_sale_id")))
        self.assertEqual(f7.get("code"), "not_available_on_date")
        self.assertEqual(self.sales(), [])
        self.assertEqual(self.stock(3), 50)


class AnOnlineClassicTick(_Sessions):
    """Classic asks without waiting. Until the answer for the date is in, the form is held."""

    def setUp(self):
        super().setUp()
        sale_availability.invalidate()
        self.addCleanup(sale_availability.invalidate)
        medicines = [{"id": 1, "created_at": "2026-01-05T04:00:00.000Z"},
                     {"id": 3, "created_at": "2026-06-01T05:00:00.000Z"}]
        purchases = [{"id": 6, "purchase_date": "2026-06-01", "items": [{"medicine_id": 3}]}]

        def pull(token, collection, **kw):
            return [dict(d) for d in (medicines if collection == "medicines" else purchases)], {}

        self.created = []
        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.server_api.pull_collection", side_effect=pull),
            mock.patch("core.online_catalog._token", return_value="t"),
            mock.patch("core.billing_service.save_new_bill",
                       side_effect=lambda *a, **k: self.created.append(a) or ("SCB9", 77)),
        ):
            stack.enter_context(patch)

    def tick(self, items):
        return autosave_bill.write_autosave_bill(
            None, token="classic-1", customer_id=7, customer_name="RAM",
            medicines=[dict(i) for i in items], cash_paid=600.0, bill_date=BILL_DATE,
            availability_wait=False,
        )

    def test_nothing_is_written_before_or_after_the_answer_while_a_later_batch_is_on(self):
        first = self.tick([OLD, NEW])
        self.assertTrue(first.get("held"), first)
        self.assertEqual(self.created, [], "the tick wrote before the store had answered")
        for _ in range(100):
            if sale_availability.answer_ready(BILL_DATE):
                break
            time.sleep(0.05)
        self.assertTrue(sale_availability.answer_ready(BILL_DATE))
        again = self.tick([OLD, NEW])
        self.assertTrue(again.get("held"), again)
        self.assertEqual(self.created, [])
        written = self.tick([OLD])
        self.assertFalse(written.get("held"), written)
        self.assertEqual(len(self.created), 1)

    def test_the_classic_tick_asks_without_waiting(self):
        path = os.path.join(ROOT, "ui", "billing", "billing_session.py")
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", getattr(node.func, "attr", "")) == "write_autosave_bill"
        ]
        self.assertTrue(calls)
        for call in calls:
            waits = [kw for kw in call.keywords if kw.arg == "availability_wait"]
            self.assertTrue(waits, "Classic autosave waits on the store on the Tk thread")
            self.assertIs(getattr(waits[0].value, "value", None), False)


if __name__ == "__main__":
    unittest.main()
