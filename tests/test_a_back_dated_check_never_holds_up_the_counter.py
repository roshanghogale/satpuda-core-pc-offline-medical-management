"""The Online back-dated batch check never holds up the counter.

Which batches a bill dated in the past may use comes, Online, from two pulls of the store
(core.sale_availability). Four things made it block billing: every caller pulled on its own
(three at once made six pulls); each page could wait two minutes; a failure was not
remembered, so a store that could not answer made every added line wait again; and every
stock movement on any device -- each sale -- threw the answer away. Classic asked the store
on the Tk thread as each line was added. A page the server's keyset could not move past was
also taken for the end of the store, which hid batches the shop had.

Everything here patches the server client; nothing reaches a server.
"""
from __future__ import annotations

import ast
import os
import threading
import time
import unittest
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from core import online_catalog, sale_availability, server_crud, store_live_refresh  # noqa: E402

BILL_DATE = "2026-03-20"
MEDICINES = [
    {"id": 11, "name": "OLD TAB", "created_at": "2026-01-05T04:00:00.000Z"},
    {"id": 13, "name": "NEW ARRIVAL CAP", "created_at": "2026-06-01T05:00:00.000Z"},
]
PURCHASES = [{"id": 6, "purchase_date": "2026-06-01", "items": [{"medicine_id": 13}]}]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Store(unittest.TestCase):
    def setUp(self):
        sale_availability.invalidate()
        self.addCleanup(sale_availability.invalidate)
        self.pulls: list[tuple[str, float]] = []
        self.server_down = False
        self.gate: threading.Event | None = None

        def pull(token, collection, *, since=None, after_id=None, include_deleted=True,
                 limit=5000, timeout=120.0):
            self.pulls.append((collection, timeout))
            if self.gate is not None and collection == "purchases":
                self.gate.wait(10)
            if self.server_down:
                raise RuntimeError("server unreachable")
            docs = MEDICINES if collection == "medicines" else PURCHASES
            return [dict(d) for d in docs], {}

        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.server_api.pull_collection", side_effect=pull),
            mock.patch("core.online_catalog._token", return_value="t"),
        ):
            stack.enter_context(patch)

    def missing(self, as_of=BILL_DATE):
        return sale_availability.batches_missing_on_online(as_of)


class OneQuestionAtATime(_Store):
    def test_callers_at_the_same_moment_share_one_pull(self):
        self.gate = threading.Event()
        answers: list[frozenset] = []
        callers = [
            threading.Thread(target=lambda: answers.append(self.missing()))
            for _ in range(3)
        ]
        for t in callers:
            t.start()
        time.sleep(0.3)  # one caller is pulling, the other two have arrived
        self.gate.set()
        for t in callers:
            t.join(10)
        self.assertEqual(answers, [frozenset({13})] * 3)
        self.assertEqual(sorted(c for c, _ in self.pulls), ["medicines", "purchases"])

    def test_an_answer_worked_out_before_a_change_is_not_kept(self):
        changed: list[int] = []
        real = self.pulls

        def pull(token, collection, **kw):
            real.append((collection, kw.get("timeout")))
            if collection == "medicines" and not changed:
                changed.append(1)
                sale_availability.invalidate()  # a purchase lands while this is asked
            return [dict(d) for d in (MEDICINES if collection == "medicines" else PURCHASES)], {}

        with mock.patch("core.server_api.pull_collection", side_effect=pull):
            self.assertEqual(self.missing(), frozenset({13}))
            asked = len(self.pulls)
            self.missing()
        self.assertGreater(len(self.pulls), asked)


class AStoreThatCannotAnswer(_Store):
    def test_it_hides_nothing_and_is_not_asked_again_for_a_minute(self):
        self.server_down = True
        self.assertEqual(self.missing(), frozenset())
        asked = len(self.pulls)
        self.assertTrue(sale_availability.batch_existed_on(None, 13, BILL_DATE))
        self.assertEqual(self.missing(), frozenset())
        self.assertEqual(len(self.pulls), asked, "every added line waited for the store again")

        self.server_down = False
        with mock.patch("time.monotonic", return_value=time.monotonic() + 61):
            self.assertEqual(self.missing(), frozenset({13}))

    def test_each_pull_is_short_and_a_slow_store_hides_nothing(self):
        self.missing()
        self.assertTrue(self.pulls)
        self.assertTrue(all(0 < t <= 15 for _, t in self.pulls), self.pulls)

        sale_availability.invalidate()
        self.pulls.clear()
        clock = [1000.0]

        def slow(token, collection, **kw):
            self.pulls.append((collection, kw.get("timeout")))
            clock[0] += 16.0  # this one answer used up the whole check's time
            return [dict(d) for d in PURCHASES], {}

        with mock.patch("core.server_api.pull_collection", side_effect=slow), \
                mock.patch("time.monotonic", side_effect=lambda: clock[0]):
            self.assertEqual(self.missing(), frozenset())
        self.assertEqual([c for c, _ in self.pulls], ["purchases"])

    def test_a_page_the_store_cannot_move_past_hides_nothing(self):
        # 6000 medicines written by one bulk statement share one updated_at to the
        # microsecond; the server pages on it exactly but sends it to the millisecond, so
        # the second page is the first one again. This used to return 5000 of 6000.
        stamp = datetime(2026, 9, 13, 6, 0, 0, 123456, tzinfo=timezone.utc)
        rows = [{"local_id": i, "updated_at": stamp} for i in range(1, 6001)]

        def js_time(dt):
            return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"

        def keyset(token, collection, *, since=None, after_id=None, include_deleted=True,
                   limit=5000, timeout=120.0):
            if collection != "medicines":
                return [], {}
            start = datetime(1970, 1, 1, tzinfo=timezone.utc)
            cutoff = datetime.fromisoformat(since.replace("Z", "+00:00")) if since else start
            if after_id:
                picked = [r for r in rows if r["updated_at"] > cutoff
                          or (r["updated_at"] == cutoff and r["local_id"] > int(after_id))]
            else:
                picked = [r for r in rows if r["updated_at"] > cutoff - timedelta(seconds=5)]
            picked.sort(key=lambda r: (r["updated_at"], r["local_id"]))
            return [{"id": r["local_id"], "updated_at": js_time(r["updated_at"]),
                     "created_at": js_time(r["updated_at"])} for r in picked[:limit]], {}

        with mock.patch("core.server_api.pull_collection", side_effect=keyset):
            with self.assertRaises(RuntimeError):
                sale_availability._pull_all("medicines")
            self.assertEqual(self.missing(), frozenset())


class OnlyAPurchaseOrANewMedicineChangesTheAnswer(_Store):
    def test_stock_movements_and_sales_keep_the_answer(self):
        self.missing()
        asked = len(self.pulls)
        for change in (
            {"collection": "stock_operations", "local_id": 9, "operation": "upsert"},
            {"collection": "sales", "local_id": 5, "operation": "upsert"},
            {"collection": "medicines", "local_id": 11, "operation": "upsert"},
        ):
            store_live_refresh.emit({"changes": [change]})
        online_catalog.invalidate("medicines")
        online_catalog.invalidate("sales")
        self.missing()
        self.assertEqual(len(self.pulls), asked)

    def test_a_purchase_or_a_new_medicine_asks_again(self):
        self.missing()
        asked = len(self.pulls)
        store_live_refresh.emit(
            {"changes": [{"collection": "purchases", "local_id": 6, "operation": "upsert"}]})
        self.missing()
        self.assertGreater(len(self.pulls), asked)

        asked = len(self.pulls)
        store_live_refresh.emit(
            {"changes": [{"collection": "medicines", "local_id": 99, "operation": "upsert"}]})
        self.missing()
        self.assertGreater(len(self.pulls), asked)

        asked = len(self.pulls)
        with mock.patch("core.online_guard.ensure_can_mutate", return_value=None), \
                mock.patch.object(server_crud, "allocate_id", return_value=100), \
                mock.patch.object(server_crud, "upsert_docs", return_value={}), \
                mock.patch.object(server_crud, "_device_id", return_value="dev"), \
                mock.patch("core.online_catalog.patch_docs", return_value=None):
            server_crud.upsert_medicine_online({"name": "BRAND NEW SYRUP", "stock_qty": 5})
        self.missing()
        self.assertGreater(len(self.pulls), asked)

    def test_today_never_asks_the_store(self):
        today = date.today()
        self.assertEqual(self.missing(today), frozenset())
        self.assertTrue(sale_availability.batch_existed_on(None, 13, today))
        self.assertEqual(sale_availability.lines_unavailable_on(
            None, [{"id": 13, "name": "NEW ARRIVAL CAP", "batch": "B", "expiry": "2030-01-01"}],
            today), [])
        self.assertEqual(self.pulls, [])


class AddingALineDoesNotWait(_Store):
    def test_a_line_is_added_at_once_and_the_answer_is_worked_out_behind_it(self):
        self.gate = threading.Event()
        started = time.monotonic()
        self.assertTrue(sale_availability.batch_existed_on(None, 13, BILL_DATE, wait=False))
        self.assertLess(time.monotonic() - started, 1.0, "the counter waited for the store")
        self.gate.set()
        self.assertEqual(self.missing(), frozenset({13}))  # the background answer
        self.assertFalse(sale_availability.batch_existed_on(None, 13, BILL_DATE, wait=False))
        self.assertEqual(sorted(c for c, _ in self.pulls), ["medicines", "purchases"])

    def test_the_classic_counter_asks_without_waiting(self):
        path = os.path.join(ROOT, "ui", "billing", "billing_form.py")
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        add = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "add_medicine")
        calls = [n for n in ast.walk(add) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", getattr(n.func, "attr", "")) == "batch_existed_on"]
        self.assertEqual(len(calls), 1)
        waits = [k.value for k in calls[0].keywords if k.arg == "wait"]
        self.assertTrue(waits and isinstance(waits[0], ast.Constant) and waits[0].value is False,
                        "add_medicine asks the store on the Tk thread")


if __name__ == "__main__":
    unittest.main()
