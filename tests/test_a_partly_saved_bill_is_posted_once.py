"""A sale whose customer balance and stock reached the server is never posted twice.

The server applies a bundle one document at a time, each under its own savepoint. When the
bill row is refused -- its number taken by another device, or some other rule -- the
customer's balance and the stock have already committed. From that moment only the bill row
may go again. Three paths still sent the whole sale:

  * the direct save (F7/F8) retried the bill row under the next number, and when that retry
    lost the network the whole sale was queued: the replay took the balance from 10 to 20
    and the stock from 9 to 8 for one bill of 10;
  * the queue parked every refused bundle, so the unsaved bill left Sales History, and
    Retry replayed the whole sale again;
  * an edit moved into a new year did the same with its stock and balance change;
  * an autosave tick (or F7) on a form whose bill row the server refused found no session
    pointing at that bill and saved the sale again: the balance and the stock once more,
    and one more refused row, on every tick.

The refused number's own year also decided how far the next number jumped, even when that
number was untagged or another year's. Everything talks to a fake server held in memory and
a queue file in a temp folder; nothing reaches a server.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from contextlib import ExitStack
from datetime import date
from unittest import mock

from core import (  # noqa: E402
    autosave_bill,
    autosave_session,
    billing_service,
    desktop_sales_service,
    online_mutation_queue,
    server_crud,
)

TAKEN = 'duplicate key value violates unique constraint "uq_sales_live_bill_no"'
LINE = {"id": 1, "name": "MED", "qty": 1, "rate": 10.0, "amount": 10.0}


class _Server:
    """One customer (id 7), one medicine (id 1) and the bills: what the store holds."""

    def __init__(self):
        self.due = 0.0
        self.stock = 10.0
        self.sales: dict[int, dict] = {}
        self.taken: set[str] = set()  # numbers other devices hold
        self.every_number_taken = False
        self.refuse = ""  # a rule the server holds every bill row against
        self.allocated = {"fy_start_year": 2026, "fy_serial": 7, "bill_no": "SCB7/FY2026-27"}
        self.pushes_down = False
        self.allocations_down = False
        self.offline = False
        self.on_refusal = None
        self.on_sleep = None
        self.pushes: list[list[str]] = []

    # -- what the tests switch -------------------------------------------------
    def drop_pushes(self):
        self.pushes_down = True

    def drop_allocations(self):
        self.allocations_down = True

    def go_offline(self):
        self.offline = True

    def network_back(self):
        self.pushes_down = self.allocations_down = self.offline = False
        self.on_refusal = self.on_sleep = None

    # -- the server calls ------------------------------------------------------
    def push(self, bundle):
        self.pushes.append(sorted(bundle))
        if self.pushes_down or self.offline:
            raise OSError("timed out")
        for c in bundle.get("customers") or []:
            self.due = float(c.get("total_due") or 0)
        for m in bundle.get("medicines") or []:
            self.stock = float(m.get("stock_qty"))
        for s in bundle.get("sales") or []:
            refusal = None
            if self.refuse:
                refusal = {"error": f'new row violates check constraint "{self.refuse}"',
                           "constraint": self.refuse}
            elif self.every_number_taken or s["bill_no"] in self.taken:
                refusal = {"error": TAKEN, "constraint": "uq_sales_live_bill_no"}
            if refusal:
                if self.on_refusal:
                    self.on_refusal()
                raise server_crud.BundleDocumentRejected(
                    f"Server rejected sales/{s['id']}",
                    [{"collection": "sales", "id": s["id"], **refusal}],
                )
            self.sales[int(s["id"])] = dict(s)
        return {}

    def allocate(self, token, kind, date_s, **kw):
        if self.allocations_down or self.offline:
            raise RuntimeError("HTTP request timed out")
        return dict(self.allocated)

    def get_doc(self, collection, local_id):
        if collection == "sales":
            held = self.sales.get(int(local_id))
            return dict(held) if held else None
        if collection == "customers":
            return self.customer(local_id)
        return self.medicine(local_id)

    def customer(self, _cid):
        return {"id": 7, "name": "RAM", "total_due": self.due, "total_credit": 0}

    def medicine(self, mid):
        return {"id": int(mid), "name": "MED", "stock_qty": self.stock, "version": 1}

    def sleep(self, _seconds):
        if self.on_sleep:
            self.on_sleep()


class _Counter(unittest.TestCase):
    def setUp(self):
        self.server = server = _Server()
        folder = tempfile.mkdtemp(prefix="bill_row_")
        self.addCleanup(shutil.rmtree, folder, True)
        self.queue_file = os.path.join(folder, "store.json")
        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.online_guard.ensure_can_mutate", return_value=None),
            mock.patch("core.online_guard.is_cloud_reachable",
                       side_effect=lambda force=False: not server.offline),
            mock.patch("core.online_guard._emit_status", return_value=None),
            mock.patch("core.quick_sale_medicine.resolve_quick_sale_medicines", return_value=None),
            mock.patch("core.store_live_refresh.emit", return_value=None),
            # A tick checks its Bill Date; a past date asks the store. Nothing here is about
            # that check, and it must not reach for the network once the date is in the past.
            mock.patch("core.sale_availability.batches_missing_on_online",
                       return_value=frozenset()),
            mock.patch("core.sync_status.note_collection_change", return_value=None),
            mock.patch.object(server_crud, "_token", return_value="t"),
            mock.patch.object(server_crud, "_device_id", return_value="dev"),
            mock.patch.object(server_crud, "allocate_ids_map", return_value={"sales": 5001}),
            mock.patch.object(server_crud, "allocate_id", return_value=5001),
            mock.patch.object(server_crud, "get_doc", side_effect=server.get_doc),
            mock.patch.object(server_crud, "push_bundle", side_effect=server.push),
            mock.patch("core.online_catalog.medicine_by_id", side_effect=server.medicine),
            mock.patch("core.online_catalog.find_customer_by_id", side_effect=server.customer),
            mock.patch("core.online_catalog.patch_docs", return_value=None),
            mock.patch("core.server_api.allocate_fy", side_effect=server.allocate),
            mock.patch("core.server_api.store_token_for_active", return_value="t"),
            mock.patch.object(online_mutation_queue, "_queue_path", return_value=self.queue_file),
            mock.patch.object(online_mutation_queue, "kick_flush", return_value=None),
            mock.patch("time.sleep", side_effect=server.sleep),
        ):
            stack.enter_context(patch)

    def save(self):
        return billing_service.save_new_bill(
            None, 7, [dict(LINE)], 0, 0, 0.0, 0.0, "", "", 0,
            bill_date=date(2026, 9, 13), customer_name="RAM", sync=True,
        )

    def flush(self):
        self.server.pushes.clear()
        online_mutation_queue._flush_loop()

    def queue(self):
        if not os.path.exists(self.queue_file):
            return []
        with open(self.queue_file, encoding="utf-8") as fh:
            return json.load(fh)

    def shown(self):
        return online_mutation_queue.overlay_sales_dicts()

    def assertPostedOnce(self, due=10.0, stock=9.0):
        self.assertEqual((self.server.due, self.server.stock), (due, stock),
                         "the customer's balance or the stock was posted twice")


class ADirectSaveWhoseRetryLosesTheNetwork(_Counter):
    def test_a_retry_push_that_drops_queues_only_the_bill_row(self):
        self.server.taken.add("SCB7/FY2026-27")
        self.server.on_refusal = self.server.drop_pushes
        bill_no, sale_id = self.save()
        self.assertEqual(bill_no, "PENDING")
        self.assertLess(sale_id, 0)
        self.assertPostedOnce()
        self.assertEqual(self.server.sales, {})

        self.server.network_back()
        self.flush()
        self.assertEqual(self.server.pushes, [["sales"]])
        self.assertPostedOnce()
        self.assertEqual(
            [(s["id"], s["bill_no"]) for s in self.server.sales.values()],
            [(5001, "SCB8/FY2026-27")],
        )

    def test_a_number_request_that_drops_queues_only_the_bill_row(self):
        self.server.taken.add("SCB7/FY2026-27")
        self.server.on_refusal = self.server.drop_allocations
        self.assertEqual(self.save()[0], "PENDING")
        self.server.network_back()
        self.flush()
        self.assertEqual(self.server.pushes, [["sales"]])
        self.assertPostedOnce()
        self.assertEqual(len(self.server.sales), 1)

    def test_the_bill_stays_in_sales_history_until_it_is_on_the_server(self):
        self.server.taken.add("SCB7/FY2026-27")
        self.server.on_refusal = self.server.drop_pushes
        self.save()
        [row] = self.shown()
        self.assertTrue(row["pending"])
        self.assertEqual((row["customer_name"], row["total_amount"]), ("RAM", 10.0))
        self.server.network_back()
        self.flush()
        self.assertEqual(self.shown(), [])

    def test_an_edit_of_the_waiting_bill_edits_it_instead_of_sending_it_again(self):
        self.server.taken.add("SCB7/FY2026-27")
        self.server.on_refusal = self.server.drop_pushes
        _, temp_id = self.save()
        # The autosave tick (or the shop) edits the bill it was handed: now fully paid.
        billing_service.update_existing_bill(
            None, temp_id, [dict(LINE)], 0, 0, 10.0, 0.0, "RAM", "", "", 0,
            bill_date="2026-09-13",
        )
        self.server.network_back()
        self.flush()
        self.assertEqual(list(self.server.sales), [5001], "the bill was saved a second time")
        self.assertPostedOnce(due=0.0, stock=9.0)
        self.assertEqual(self.server.sales[5001]["cash_paid"], 10.0)

    def test_deleting_the_waiting_bill_does_not_strand_its_balance(self):
        self.server.taken.add("SCB7/FY2026-27")
        self.server.on_refusal = self.server.drop_pushes
        _, temp_id = self.save()
        with mock.patch.object(desktop_sales_service, "_forget_autosave_session_for"):
            res = desktop_sales_service.delete_saved_sale(None, {"sale_id": temp_id})
        self.assertTrue(res.get("ok"), res)
        # The bill row still goes first, then a real delete takes its balance and stock
        # back. Cancelling the queued row left both on the server with no bill at all.
        rows = [r for r in self.queue() if r["status"] == "pending"]
        self.assertEqual([(r["op"], r["local_id"] > 0) for r in rows],
                         [("upsert", False), ("delete", True)])
        self.assertEqual(rows[1]["local_id"], 5001)

    def test_a_sale_that_never_reached_the_server_still_goes_whole(self):
        self.server.pushes_down = True
        self.assertEqual(self.save()[0], "PENDING")
        self.assertEqual((self.server.due, self.server.stock), (0.0, 10.0))
        self.server.network_back()
        self.flush()
        self.assertEqual(self.server.pushes, [["customers", "medicines", "sales"]])
        self.assertPostedOnce()
        self.assertEqual(len(self.server.sales), 1)

    def test_a_bill_row_the_server_refuses_is_shown_refused_and_retried_alone(self):
        self.server.refuse = "sales_total_check"
        with self.assertRaises(server_crud.BundleDocumentRejected):
            self.save()
        self.assertPostedOnce()
        [row] = self.shown()
        self.assertTrue(row.get("refused"), row)
        self.assertIn("sales_total_check", row.get("refused_reason") or "")

        self.server.refuse = ""
        self.assertEqual(online_mutation_queue.retry_blocked(), 1)
        self.flush()
        self.assertEqual(self.server.pushes, [["sales"]])
        self.assertPostedOnce()
        self.assertEqual(len(self.server.sales), 1)
        self.assertEqual(self.shown(), [])


class AQueuedSaleWhoseBillRowIsRefused(_Counter):
    def setUp(self):
        super().setUp()
        row = {
            "id": "r1", "collection": "sales", "op": "upsert", "status": "pending",
            "attempts": 0, "local_id": -11, "client_uuid": "cu-1", "created_at": 0,
            "payload": {
                "customer_id": 7, "customer_name": "RAM", "bill_no": "PENDING",
                "bill_date": "2026-09-13", "total_amount": 10.0, "cash_paid": 0.0,
                "online_paid": 0.0, "client_uuid": "cu-1", "medicines": [dict(LINE)],
            },
        }
        with open(self.queue_file, "w", encoding="utf-8") as fh:
            json.dump([row], fh)

    def test_numbers_taken_every_time_keep_only_the_bill_row_waiting_and_shown(self):
        self.server.every_number_taken = True
        self.server.on_sleep = self.server.go_offline  # the connection drops while it backs off
        self.flush()
        self.assertEqual(self.server.pushes[0], ["customers", "medicines", "sales"])
        [row] = self.queue()
        self.assertEqual(row["status"], "pending")
        self.assertEqual(len(self.shown()), 1, "the unsaved bill left Sales History")
        self.assertPostedOnce()

        self.server.every_number_taken = False
        self.server.network_back()
        self.flush()
        self.assertEqual(self.server.pushes, [["sales"]])
        self.assertPostedOnce()
        [sale] = self.server.sales.values()
        self.assertEqual((sale["bill_no"], sale["client_uuid"]), ("SCB11/FY2026-27", "cu-1"))
        self.assertEqual(self.shown(), [])

    def test_another_refusal_is_parked_shown_and_retry_sends_only_the_bill_row(self):
        self.server.refuse = "sales_total_check"
        self.flush()
        [row] = self.queue()
        self.assertEqual(row["status"], "blocked")
        [shown] = self.shown()
        self.assertTrue(shown.get("refused"), shown)
        self.assertPostedOnce()

        self.server.refuse = ""
        self.assertEqual(online_mutation_queue.retry_blocked(), 1)
        self.flush()
        self.assertEqual(self.server.pushes, [["sales"]])
        self.assertPostedOnce()
        self.assertEqual([s["bill_no"] for s in self.server.sales.values()], ["SCB7/FY2026-27"])


class AnOnlineEditWhoseBillRowDidNotLand(_Counter):
    EXISTING = {
        "id": 42, "local_id": 42, "client_uuid": "cu-42", "version": 3, "customer_id": 7,
        "customer_name": "RAM", "bill_no": "SCB150/FY2025-26", "bill_date": "2026-03-31",
        "fy_start_year": 2025, "fy_serial": 150, "total_amount": 10.0, "amount_paid": 0.0,
        "items": [{"medicine_id": 1, "qty": 1, "rate": 10.0, "amount": 10.0}],
    }

    def test_an_edit_moved_into_a_taken_number_resends_only_the_bill_row(self):
        server = self.server
        server.sales[42] = dict(self.EXISTING)
        server.due, server.stock = 10.0, 9.0
        server.allocated = {"fy_start_year": 2026, "fy_serial": 4, "bill_no": "SCB4/FY2026-27"}
        server.taken.add("SCB4/FY2026-27")
        server.on_refusal = server.drop_pushes
        server.on_sleep = server.go_offline
        edit = {
            "id": "e1", "collection": "sales", "op": "upsert", "status": "pending",
            "attempts": 0, "local_id": 42, "client_uuid": "cu-e1", "created_at": 0,
            "payload": {
                "customer_id": 7, "customer_name": "RAM", "bill_no": "PENDING",
                "bill_date": "2026-04-02", "cash_paid": 0.0, "online_paid": 0.0,
                "discount_pct": 0, "rounding": 0,
                "medicines": [dict(LINE, qty=2, amount=20.0)],
            },
        }
        with open(self.queue_file, "w", encoding="utf-8") as fh:
            json.dump([edit], fh)

        self.flush()
        # One more of the medicine and 10 more due: committed with the refused bundle.
        self.assertPostedOnce(due=20.0, stock=8.0)
        self.assertEqual(server.sales[42]["bill_no"], "SCB150/FY2025-26")

        server.network_back()
        self.flush()
        self.assertEqual(server.pushes, [["sales"]])
        self.assertPostedOnce(due=20.0, stock=8.0)
        self.assertEqual(server.sales[42]["bill_no"], "SCB5/FY2026-27")


class AnAutosavedBillWhoseRowIsRefused(_Counter):
    """One form (one token) whose first bill row the server refuses by a rule."""

    def setUp(self):
        super().setUp()
        folder = tempfile.mkdtemp(prefix="autosave_refused_")
        self.addCleanup(shutil.rmtree, folder, True)
        os.environ["SATPUDA_AUTOSAVE_SESSION_FILE"] = os.path.join(folder, "sessions.json")
        self.addCleanup(os.environ.pop, "SATPUDA_AUTOSAVE_SESSION_FILE", None)
        patch = mock.patch.object(autosave_session, "_store_key", return_value="test-store")
        patch.start()
        self.addCleanup(patch.stop)
        self.server.refuse = "sales_total_check"

    def tick(self, final=False):
        return autosave_bill.write_autosave_bill(
            None, token="form-1", customer_id=7, customer_name="RAM",
            medicines=[dict(LINE)], cash_paid=0.0, bill_date="2026-09-13", final=final,
        )

    def test_later_ticks_and_the_save_do_not_post_the_sale_again(self):
        with self.assertRaises(server_crud.SaleRowNotSaved):
            self.tick()
        self.assertPostedOnce()
        for final in (False, False, True):
            with self.assertRaises(RuntimeError) as seen:
                self.tick(final=final)
            self.assertPostedOnce()
            self.assertIn("sales_total_check", str(seen.exception))
            self.assertIsInstance(seen.exception, autosave_bill.AutosaveBillRefused)
        self.assertEqual(self.server.sales, {})
        self.assertEqual([r["status"] for r in self.queue()], ["blocked"],
                         "each tick left one more refused bill")
        [row] = self.shown()
        self.assertTrue(row.get("refused"), row)
        [held] = autosave_bill.list_recoverable()
        self.assertEqual((held["bill_no"], held["customer_name"], held["total"]),
                         ("REFUSED", "RAM", 10.0))

    def test_once_the_shop_retries_it_the_form_still_holds_that_one_bill(self):
        with self.assertRaises(server_crud.SaleRowNotSaved):
            self.tick()
        self.server.refuse = ""
        self.assertEqual(online_mutation_queue.retry_blocked(), 1)
        self.flush()
        self.assertEqual(self.server.pushes, [["sales"]])
        self.assertPostedOnce()
        # The row landed and the form's record now names the real bill, so the save updates
        # that one bill. It used to be refused as unreadable, and the form could never be
        # finished; a second bill was never the answer either way.
        with mock.patch.object(online_mutation_queue, "flush_now",
                               side_effect=lambda **kw: self.flush() or True):
            saved = self.tick(final=True)
        self.assertEqual(saved["sale_id"], 5001)
        self.assertPostedOnce()
        self.assertEqual(len(self.server.sales), 1)


class TheNextFreeNumberBelongsToTheBillsYear(unittest.TestCase):
    def retry(self, bill_no, tried, allocated=13):
        refused = server_crud.BundleDocumentRejected(
            "x", [{"collection": "sales", "id": 9, "error": TAKEN, "constraint": None}])
        with mock.patch.object(server_crud, "_token", return_value="t"), \
                mock.patch.object(server_crud, "push_bundle", return_value={}), \
                mock.patch("core.server_api.allocate_fy", return_value={
                    "fy_start_year": 2026, "fy_serial": allocated}):
            row = server_crud.push_sale_under_free_number(
                {"id": 9, "bill_no": bill_no, "bill_date": "2026-09-13"}, "2026-09-13",
                tried, refused,
            )
        return row["bill_no"]

    def test_an_untagged_number_does_not_move_this_years_series(self):
        self.assertEqual(self.retry("SCB77", 77), "SCB13/FY2026-27")

    def test_another_years_number_does_not_move_it_either(self):
        self.assertEqual(self.retry("SCB150/FY2025-26", 150), "SCB13/FY2026-27")

    def test_a_number_of_this_year_is_still_stepped_past(self):
        self.assertEqual(self.retry("SCB13/FY2026-27", 13), "SCB14/FY2026-27")


if __name__ == "__main__":
    unittest.main()
