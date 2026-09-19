"""Clearing a form while its autosave tick waits on the Bill Date check writes nothing more.

The Tauri tick asks about the Bill Date before it takes the sale write lock, so F7 on other
tabs does not wait behind a slow store (up to 15 s for a back-dated Online form). Clear takes
the lock at once. When Clear landed during that wait it deleted the bill and dropped the
form's record; the tick then found no record and saved the cleared sale as a new bill -- the
same bill number again, the stock off a second time, and a record for the recovery prompt to
offer. A form whose refused bill row had been discarded in Settings -> Sync posted its sale a
second time the same way.

Now a tick that started before its form was cleared writes nothing once it has the lock. A
tick that starts after the Clear, and another tab's tick, still write, and Clear still does not
wait for the date check.

In-memory store, the fake server of the partly-saved-bill tests, temp session and queue files.
Nothing reaches a server.
"""
from __future__ import annotations

import sqlite3
import threading
import unittest
from unittest import mock

from core import autosave_bill, autosave_session, desktop_sales_service  # noqa: E402
from core import sale_availability  # noqa: E402
from tests import test_a_bill_whose_row_has_not_landed_is_sent_only_as_its_row as row_tests  # noqa: E402
from tests.test_a_back_dated_autosave_writes_nothing_it_cannot_sell import (  # noqa: E402
    BILL_DATE,
    NEW,
    OLD,
    TODAY,
    _Shop,
)
from tests.test_a_bill_whose_row_has_not_landed_is_sent_only_as_its_row import (  # noqa: E402
    LINE,
    _Form,
)


class _SlowDateCheck:
    """The Bill Date check of the next tick waits until Clear has come back (a slow store)."""

    def __init__(self):
        self.armed = True
        self.checking = threading.Event()
        self.cleared = threading.Event()
        self.clear_waited = False

    def __call__(self, conn, medicines, bill_date, **kw):
        if self.armed:
            self.armed = False
            self.checking.set()
            if not self.cleared.wait(10):
                self.clear_waited = True
        return []


class _ClearDuringTick:
    def clear_during_tick(self, tick, clear):
        slow = _SlowDateCheck()
        out: dict = {}

        def run_tick():
            try:
                out["tick"] = tick()
            except BaseException as exc:  # noqa: BLE001
                out["tick_error"] = exc

        with mock.patch.object(sale_availability, "lines_unavailable_on", side_effect=slow):
            worker = threading.Thread(target=run_tick, name="autosave-tick")
            worker.start()
            self.assertTrue(slow.checking.wait(10), "the tick never reached its date check")
            out["clear"] = clear()
            slow.cleared.set()
            worker.join(30)
        self.assertFalse(worker.is_alive(), "the tick never finished")
        self.assertFalse(slow.clear_waited, "Clear waited for the tick's date check")
        self.assertNotIn("tick_error", out)
        return out["tick"], out["clear"]


class _SharedShop(_Shop):
    """The offline shop, on a connection the tick's thread may use too."""

    def setUp(self):
        super().setUp()
        shared = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.backup(shared)
        self.conn = shared
        self.addCleanup(shared.close)

    def body(self, **kw):
        body = {"items": [dict(OLD)], "bill_date": BILL_DATE, "customer_name": "RAM",
                "payment_mode": "Cash", "cash_paid": 100.0, "online_paid": 0, "force": True,
                "autosave_token": "tok-1"}
        body.update(kw)
        return body

    def autosave(self, **kw):
        return desktop_sales_service.autosave_sale(self.conn, self.body(**kw))

    def clear(self, token="tok-1", sale_id=0):
        return desktop_sales_service.discard_autosave(
            self.conn, {"autosave_sale_id": sale_id, "autosave_token": token})

    def assertNoRecord(self, token="tok-1"):
        self.assertIsNone(autosave_session.load_session(token),
                          "the cleared form left a record for the recovery prompt")
        self.assertEqual(autosave_bill.list_recoverable(), [])


class ClearWhileTheTickChecksTheBillDate(_ClearDuringTick, _SharedShop):
    def test_the_cleared_bill_stays_gone(self):
        first = self.autosave()
        sale_id = first.get("autosave_sale_id")
        self.assertTrue(sale_id, first)
        self.assertEqual(self.stock(1), 49)

        tick, cleared = self.clear_during_tick(
            lambda: self.autosave(autosave_sale_id=sale_id,
                                  items=[dict(OLD, qty=2, amount=200.0)], cash_paid=200.0),
            lambda: self.clear(sale_id=sale_id),
        )
        self.assertTrue(cleared.get("deleted"), cleared)
        self.assertEqual(self.sales(), [], "the cleared sale was saved again as a new bill")
        self.assertEqual(self.stock(1), 50, "the stock came off again after Clear")
        self.assertNoRecord()
        self.assertTrue(tick.get("ok"), tick)
        self.assertTrue(tick.get("skipped"), tick)
        self.assertFalse(tick.get("autosave_sale_id"), tick)

    def test_a_first_tick_cleared_before_its_bill_exists_makes_no_bill(self):
        tick, cleared = self.clear_during_tick(lambda: self.autosave(), lambda: self.clear())
        self.assertTrue(cleared.get("ok"), cleared)
        self.assertEqual(self.sales(), [], "a form cleared during its first tick got a bill")
        self.assertEqual(self.stock(1), 50)
        self.assertNoRecord()
        self.assertFalse(tick.get("autosave_sale_id"), tick)

    def test_a_held_form_cleared_during_its_next_tick_makes_no_bill(self):
        held = self.autosave(items=[dict(OLD), dict(NEW)], cash_paid=600.0)
        self.assertEqual(held.get("reason"), "not_available_on_date", held)
        tick, cleared = self.clear_during_tick(lambda: self.autosave(), lambda: self.clear())
        self.assertTrue(cleared.get("ok"), cleared)
        self.assertEqual(self.sales(), [])
        self.assertEqual((self.stock(1), self.stock(3)), (50, 50))
        self.assertNoRecord()
        self.assertFalse(tick.get("autosave_sale_id"), tick)


class WhatAClearDoesNotStop(_ClearDuringTick, _SharedShop):
    def test_a_tick_that_starts_after_the_clear_still_writes(self):
        first = self.autosave()
        self.clear(sale_id=first["autosave_sale_id"])
        self.assertEqual(self.sales(), [])
        again = self.autosave()
        self.assertTrue(again.get("autosave_sale_id"), again)
        self.assertEqual(len(self.sales()), 1)
        self.assertEqual(self.stock(1), 49)

    def test_another_tabs_tick_on_the_same_counter_bill_still_writes(self):
        counter = {"customer_name": "COUNTER SALE", "bill_date": TODAY}
        a = self.autosave(autosave_token="tab-a", **counter)
        day = a.get("autosave_sale_id")
        self.assertTrue(day, a)
        b = self.autosave(autosave_token="tab-b", items=[dict(NEW)], cash_paid=500.0, **counter)
        self.assertEqual(b.get("autosave_sale_id"), day, b)

        tick, cleared = self.clear_during_tick(
            lambda: self.autosave(autosave_token="tab-b", autosave_sale_id=day,
                                  items=[dict(NEW, qty=2, amount=1000.0)], cash_paid=1000.0,
                                  **counter),
            lambda: self.clear(token="tab-a", sale_id=day),
        )
        self.assertTrue(cleared.get("deleted"), cleared)
        self.assertEqual(tick.get("autosave_sale_id"), day, "tab B's tick was dropped by tab A's Clear")
        self.assertTrue(tick.get("updated"), tick)
        [(sale_id, _date, total)] = self.sales()
        self.assertEqual((sale_id, total), (day, 1000.0))
        self.assertEqual((self.stock(1), self.stock(3)), (50, 48))
        self.assertIsNotNone(autosave_session.load_session("tab-b"))
        self.assertIsNone(autosave_session.load_session("tab-a"))


class ClearWhileADiscardedFormTicks(_ClearDuringTick, _Form):
    """Its refused bill row was discarded in Settings -> Sync; the form is cleared mid-tick."""

    # Borrowed, not inherited: inheriting that class would run its tests a second time here.
    _discarded = row_tests.TheOpenFormAfterItsRefusedRowIsDiscarded
    discard_while_open = _discarded.discard_while_open
    assertNothingMoreWasWritten = _discarded.assertNothingMoreWasWritten

    def test_the_sale_is_not_posted_again(self):
        lid = self.discard_while_open()
        self.server.refuse = ""  # the rule is lifted: a new save would land as a second bill
        body = {"items": [dict(LINE, qty=2, amount=20.0)], "bill_date": "2026-09-13",
                "customer_name": "RAM", "payment_mode": "Cash", "cash_paid": 0.0,
                "online_paid": 0, "force": True, "autosave_token": "form-1",
                "autosave_sale_id": lid}
        with mock.patch("core.customer_service.get_or_create_customer", return_value=7):
            tick, cleared = self.clear_during_tick(
                lambda: desktop_sales_service.autosave_sale(None, dict(body)),
                lambda: desktop_sales_service.discard_autosave(
                    None, {"autosave_sale_id": lid, "autosave_token": "form-1"}),
            )
        self.assertEqual(cleared.get("reason"), "discarded", cleared)
        self.assertFalse(tick.get("autosave_sale_id"), tick)
        self.assertNothingMoreWasWritten()


if __name__ == "__main__":
    unittest.main()
