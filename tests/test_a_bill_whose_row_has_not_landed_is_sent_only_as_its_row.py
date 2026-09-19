"""A bill whose row has not reached the server is never written to except as that row.

When the server takes a sale's balance and stock but not its bill row, only the row may go
again. The autosave fix before this one stopped ticks only while the row was REFUSED. The
shop's Retry puts the row back to waiting; with the connection down it stays waiting, and
the next tick or F7 then took the normal edit path and queued an edit of the sale behind the
row. When the server refused the row again and the shop discarded it, that edit ran against
a sale the server never held: the stock came off a second time and a new REFUSED bill
appeared for the same sale.

Now, while a form's bill row waits (pending or refused), a tick or F7 writes nothing to that
bill; the form snapshot is still kept, so nothing typed is lost. Discarding a refused row
also drops whatever was queued behind it and the unfinished-sale record that pointed at it.
A row that lands hands its form the real bill, so the form carries on and the record stops
being offered as refused on every start.

Everything talks to the in-memory fake server, a temp queue file and a temp sessions file;
nothing reaches a server.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from unittest import mock

from core import autosave_bill, autosave_session, billing_service, desktop_sales_service  # noqa: E402
from core import online_mutation_queue as queue  # noqa: E402
from core import server_crud  # noqa: E402
from tests.test_a_partly_saved_bill_is_posted_once import LINE, _Counter  # noqa: E402


class _Form(_Counter):
    def setUp(self):
        super().setUp()
        folder = tempfile.mkdtemp(prefix="row_not_landed_")
        self.addCleanup(shutil.rmtree, folder, True)
        os.environ["SATPUDA_AUTOSAVE_SESSION_FILE"] = os.path.join(folder, "sessions.json")
        self.addCleanup(os.environ.pop, "SATPUDA_AUTOSAVE_SESSION_FILE", None)
        for patch in (
            mock.patch.object(autosave_session, "_store_key", return_value="test-store"),
            # F7 waits for the queue to drain before printing; drain it here instead of
            # letting the real waiter spin against a flusher the tests do not start.
            mock.patch.object(queue, "flush_now", side_effect=lambda **kw: self.flush() or True),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def tick(self, final=False, qty=1, cash=0.0):
        return autosave_bill.write_autosave_bill(
            None, token="form-1", customer_id=7, customer_name="RAM",
            medicines=[dict(LINE, qty=qty, amount=10.0 * qty)], cash_paid=cash,
            bill_date="2026-09-13", final=final,
        )

    def rows(self):
        return [(r["status"], r["op"], r["local_id"], queue._is_bill_row(r)) for r in self.queue()]

    def live_rows(self):
        return [row for row in self.rows() if row[0] in ("pending", "blocked")]

    def refused_first_tick(self):
        self.server.refuse = "sales_total_check"
        with self.assertRaises(server_crud.SaleRowNotSaved):
            self.tick()
        self.assertPostedOnce()
        [(status, _op, lid, is_row)] = self.rows()
        self.assertEqual((status, is_row), ("blocked", True))
        return lid

    def retried_while_offline(self):
        self.assertEqual(queue.retry_blocked(), 1)
        self.server.go_offline()
        self.flush()
        [(status, _op, _lid, is_row)] = self.rows()
        self.assertEqual((status, is_row), ("pending", True))


class ATickWhileTheRetriedRowWaits(_Form):
    def run_to_discard(self, final):
        self.refused_first_tick()
        self.retried_while_offline()

        with self.assertRaises(RuntimeError):
            self.tick(final=final, qty=2)
        self.assertEqual(len(self.live_rows()), 1,
                         "a tick queued an edit of a sale whose row never landed")

        self.server.network_back()
        self.flush()  # the rule still refuses the row
        self.assertEqual([r[0] for r in self.live_rows()], ["blocked"])
        self.assertEqual(queue.discard_blocked(), 1)
        self.flush()
        self.assertPostedOnce()
        self.assertEqual(self.server.sales, {})
        self.assertEqual(self.live_rows(), [], "a new refused bill appeared for the same sale")

    def test_a_timer_tick_writes_nothing(self):
        self.run_to_discard(final=False)

    def test_f7_writes_nothing(self):
        self.run_to_discard(final=True)

    def test_an_unchanged_tick_is_quiet_and_typed_work_is_kept(self):
        self.refused_first_tick()
        self.retried_while_offline()
        res = self.tick()  # nothing changed on the form: nothing to say, nothing written
        self.assertTrue(res.get("ok"), res)
        with self.assertRaises(autosave_bill.AutosaveBillWaiting):
            self.tick(qty=3)
        [held] = autosave_bill.list_recoverable()
        self.assertEqual(held["total"], 30.0, "the typed change was not kept on the form")
        self.assertEqual(len(self.live_rows()), 1)


class DiscardingARefusedRow(_Form):
    def test_edits_and_deletes_queued_behind_it_are_dropped(self):
        self.server.refuse = "sales_total_check"
        with self.assertRaises(server_crud.SaleRowNotSaved):
            self.save()
        [(_s, _o, temp_id, _r)] = self.rows()
        self.retried_while_offline()
        # A Sales History edit of the waiting bill, then a delete of it: both wait for the row.
        billing_service.update_existing_bill(
            None, temp_id, [dict(LINE, qty=2, amount=20.0)], 0, 0, 0.0, 0.0,
            "RAM", "", "", 0, bill_date="2026-09-13",
        )
        with mock.patch.object(desktop_sales_service, "_forget_autosave_session_for"):
            self.assertTrue(desktop_sales_service.delete_saved_sale(None, {"sale_id": temp_id})["ok"])
        self.assertEqual(len(self.live_rows()), 3)

        self.server.network_back()
        self.flush()  # refused again
        self.assertEqual(queue.discard_blocked(), 1)
        self.assertEqual(self.live_rows(), [], "work queued behind a discarded bill row was kept")
        self.flush()
        self.assertPostedOnce()
        self.assertEqual(self.server.sales, {})

    def test_its_unfinished_sale_record_is_cleared(self):
        self.refused_first_tick()
        self.assertEqual([r["bill_no"] for r in autosave_bill.list_recoverable()], ["REFUSED"])
        self.assertEqual(queue.discard_blocked(), 1)
        self.assertEqual(autosave_bill.list_recoverable(), [],
                         "a discarded refused bill was offered again on every start")


class TheOpenFormAfterItsRefusedRowIsDiscarded(_Form):
    """The shop discards the refused row in Settings → Sync while its form is still open.

    The form keeps its token, and a Tauri tab stays dirty after a refused tick, so its timer
    keeps ticking. Forgetting the form's record on Discard left that token owning nothing:
    the next tick or F7 saved the sale as a new bill, posting the balance and the stock a
    second time -- one more refused row while the rule held, a real second bill once it was
    lifted. The record now stays behind as "discarded" and nothing more is written from it.
    """

    def discard_while_open(self):
        lid = self.refused_first_tick()
        self.assertEqual(queue.discard_blocked(), 1)
        self.assertEqual(self.live_rows(), [])
        return lid

    def assertNothingMoreWasWritten(self):
        self.flush()
        self.assertPostedOnce()
        self.assertEqual(self.server.sales, {}, "the discarded sale was saved as a new bill")
        self.assertEqual(self.live_rows(), [], "a new refused bill appeared for the same sale")
        self.assertEqual(autosave_bill.list_recoverable(), [])

    def run_case(self, final, rule_lifted):
        self.discard_while_open()
        if rule_lifted:
            self.server.refuse = ""
        with self.assertRaises(autosave_bill.AutosaveBillDiscarded) as seen:
            self.tick(final=final)
        self.assertIn("Settings", str(seen.exception))
        self.assertNotIn("connection", str(seen.exception).lower())
        self.assertNothingMoreWasWritten()

    def test_a_tick_with_the_rule_still_on(self):
        self.run_case(final=False, rule_lifted=False)

    def test_f7_with_the_rule_still_on(self):
        self.run_case(final=True, rule_lifted=False)

    def test_a_tick_after_the_rule_is_lifted(self):
        self.run_case(final=False, rule_lifted=True)

    def test_f7_after_the_rule_is_lifted(self):
        self.run_case(final=True, rule_lifted=True)

    def test_a_form_that_lost_its_token_is_stopped_too(self):
        lid = self.discard_while_open()
        self.server.refuse = ""
        with self.assertRaises(autosave_bill.AutosaveBillDiscarded):
            autosave_bill.write_autosave_bill(
                None, token="", sale_id=lid, customer_id=7, customer_name="RAM",
                medicines=[dict(LINE)], cash_paid=0.0, bill_date="2026-09-13",
            )
        self.assertNothingMoreWasWritten()

    def test_the_record_outlives_a_busy_counter(self):
        self.discard_while_open()
        for n in range(autosave_session._MAX_SESSIONS + 20):  # a day of finished bills
            autosave_session.save_session(f"done-{n}", sale_id=900 + n, closed=True)
        self.server.refuse = ""
        with self.assertRaises(autosave_bill.AutosaveBillDiscarded):
            self.tick()
        self.assertNothingMoreWasWritten()

    def test_resume_says_it_was_discarded(self):
        self.discard_while_open()
        res = autosave_bill.resume_autosave_bill(None, token="form-1")
        self.assertFalse(res.get("ok"))
        self.assertEqual(res.get("code"), "discarded")
        self.assertNotIn("connection", (res.get("error") or "").lower())

    def test_clearing_the_form_lets_the_record_go_and_writes_nothing(self):
        self.discard_while_open()
        out = autosave_bill.discard_autosave_bill(None, token="form-1")
        self.assertTrue(out.get("ok"), out)
        self.assertFalse(out.get("deleted"))
        self.assertIsNone(autosave_session.load_session("form-1"))
        self.assertNothingMoreWasWritten()


class ARefusedRowOnTheRecoveryPrompt(_Form):
    def test_resume_and_discard_say_the_server_refused_it(self):
        self.refused_first_tick()
        res = autosave_bill.resume_autosave_bill(None, token="form-1")
        self.assertFalse(res.get("ok"))
        self.assertEqual(res.get("code"), "refused")
        self.assertIn("Settings", res.get("error") or "")
        self.assertNotIn("connection", (res.get("error") or "").lower())

        out = autosave_bill.discard_autosave_bill(None, token="form-1")
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("code"), "refused")
        self.assertNotIn("connection", (out.get("error") or "").lower())
        self.assertPostedOnce()
        self.assertEqual(len(autosave_bill.list_recoverable()), 1)


class ARowThatLands(_Form):
    def test_the_form_carries_on_with_the_real_bill(self):
        self.refused_first_tick()
        self.server.refuse = ""
        self.assertEqual(queue.retry_blocked(), 1)
        self.flush()
        self.assertEqual(list(self.server.sales), [5001])

        [held] = autosave_bill.list_recoverable()
        self.assertEqual((held["sale_id"], held["bill_no"]), (5001, "SCB7"))
        res = autosave_bill.resume_autosave_bill(None, token="form-1")
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(res.get("sale_id"), 5001)

        saved = self.tick(final=True, cash=10.0)
        self.assertEqual(saved.get("sale_id"), 5001)
        self.assertEqual(list(self.server.sales), [5001], "a second bill was made")
        self.assertPostedOnce(due=0.0, stock=9.0)
        self.assertEqual(autosave_bill.list_recoverable(), [])

    def test_a_row_that_waited_on_the_connection_also_hands_over_its_bill(self):
        # The number was taken and the retry lost the network: the row waits, nothing refused.
        self.server.taken.add("SCB7/FY2026-27")
        self.server.on_refusal = self.server.drop_pushes
        first = self.tick()
        self.assertLess(first["sale_id"], 0)
        with self.assertRaises(autosave_bill.AutosaveBillWaiting):
            self.tick(qty=2)
        self.assertEqual(len(self.live_rows()), 1)

        self.server.network_back()
        self.flush()
        self.assertEqual(list(self.server.sales), [5001])
        again = self.tick(qty=2)
        self.assertEqual(again.get("sale_id"), 5001)
        self.flush()
        self.assertEqual(list(self.server.sales), [5001])
        self.assertPostedOnce(due=20.0, stock=8.0)
