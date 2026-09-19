"""A purchase edit whose second push gets no answer is read back, never replayed blindly.

A supplier payment's cascade moves a bill past an edit on its way, so the store skips the edit
while the stock movements in its bundle are applied; the PC then puts the edit over the store's
copy with a second push (R1, land_skipped_purchase_edit). A network error on that second push
escaped as a raw error. The online queue takes a raw error for "nothing reached the server" and
replays the edit: the replay is worked out again from the store's copy at the next version, under
new op_uuids, and moved the stock a second time (verifier probe v10/gaps, DAM 5 OINT 600 -> 598
for an edit of -1).

Now a push with no answer is followed by a read of the store's copy, and the same verdict as for a
skipped push decides: the edit is there (done), the store still holds the copy it was worked out
from (push it again), or it cannot be told (PurchaseEditNotSaved: the shop is told, the queue parks
it, and nothing is replayed). A refused second push is not raw either.

The fake store of test_a_purchase_edit_is_not_lost_to_a_payment stands in for server-live. Nothing
reaches a server.
"""
from __future__ import annotations

import unittest
from unittest import mock

from core import online_mutation_queue as omq
from core import server_crud
from tests.test_a_purchase_edit_is_not_lost_to_a_payment import CU, _Race  # noqa: E402


class _Flaky(_Race):
    def run_edit(self, *, fail_calls=(), land_then_fail=(), answers=None, pulls_fail_after=None):
        """The edit with a payment landing first; push call n (1-based) fails as told."""
        self.store.before_next_bundle.append(lambda: self.store.pay(4137.0))
        real_push, real_pull = self.store.push_bundle, self.store.pull_doc
        self.attempts = {"push": 0, "pull": 0}

        def push(token, bundle, **kw):
            self.attempts["push"] += 1
            n = self.attempts["push"]
            if answers and n in answers:
                return answers[n]
            if n in land_then_fail:
                real_push(token, bundle, **kw)
                raise TimeoutError("The read operation timed out")
            if (n > 1) if fail_calls == "all_after_first" else (n in fail_calls):
                raise TimeoutError("The read operation timed out")
            return real_push(token, bundle, **kw)

        def pull(token, collection, local_id, **kw):
            # Reads of the purchase only: the edit also reads each medicine it moves.
            if collection == "purchases":
                self.attempts["pull"] += 1
                if pulls_fail_after is not None and self.attempts["pull"] > pulls_fail_after:
                    raise ConnectionError("[Errno 65] No route to host")
            return real_pull(token, collection, local_id, **kw)

        with mock.patch("core.server_api.push_bundle", side_effect=push), \
                mock.patch("core.server_api.pull_doc", side_effect=pull):
            return self.edit()


class NoAnswerToThePushThatPutsTheEditOverTheStoresCopy(_Flaky):
    def test_the_store_is_read_again_and_the_edit_lands_once(self):
        number = self.run_edit(fail_calls=(2,))
        self.assertEqual(number, "83/FY2026-27")
        self.assertEqual(self.lines(), [(17512, 59.0), (17513, 20.0)])
        self.assertEqual(self.stock(17512), 599.0, "the edit's stock moved more than once")
        self.assertEqual(self.moves(17512), {f"purchase:{CU}:med:17512:edit:v4": -1})
        self.assertEqual(self.due(2997), (0.0, True, True))

    def test_a_push_that_landed_without_an_answer_is_taken_as_landed(self):
        self.run_edit(land_then_fail=(2,))
        self.assertEqual(len(self.purchase_pushes()), 2, "a landed edit was pushed again")
        self.assertEqual(self.lines(), [(17512, 59.0), (17513, 20.0)])
        self.assertEqual(self.stock(17512), 599.0)

    def test_a_store_that_stops_answering_is_parked_and_never_replayed(self):
        # Read 1 is the edit's own read, read 2 the one before the second push; after that the
        # store cannot be reached at all.
        with self.assertRaises(server_crud.PurchaseEditNotSaved) as caught:
            self.run_edit(fail_calls=(2,), pulls_fail_after=2)
        exc = caught.exception
        self.assertTrue(omq._is_permanent_refusal(exc),
                        "the queue would replay a half-landed edit and move its stock again")
        message = str(exc)
        self.assertIn("83", message)
        self.assertIn("DAM 5 OINT", message, "the medicine whose stock may be off is not named")
        # Whether the edit landed is not known, so nothing is taken back on a guess.
        self.assertEqual(self.stock(17512), 599.0)
        self.assertEqual(self.lines(), [(17512, 60.0), (17513, 20.0)])

    def test_every_later_push_unanswered_stops_and_names_the_stock(self):
        with self.assertRaises(server_crud.PurchaseEditNotSaved) as caught:
            self.run_edit(fail_calls="all_after_first")
        self.assertLessEqual(self.attempts["push"], 6, "it kept pushing")
        self.assertIn("DAM 5 OINT", str(caught.exception))
        self.assertTrue(omq._is_permanent_refusal(caught.exception))
        self.assertEqual(self.stock(17512), 599.0)


class ARefusedPushThatPutsTheEditOverTheStoresCopy(_Flaky):
    def test_it_is_a_not_saved_edit_whose_stock_is_put_back(self):
        refused = {"purchases": {"results": [{"id": 2997, "status": "failed",
                                              "error": "purchases_store_pk_purchase_no_key"}],
                                 "upserted": 0, "skipped": 0, "failed": 1}}
        with self.assertRaises(server_crud.PurchaseEditNotSaved) as caught:
            self.run_edit(answers={2: refused})
        message = str(caught.exception)
        self.assertIn("purchase_no", message, "the server's own words are not in the message")
        self.assertTrue(omq._is_permanent_refusal(caught.exception))
        # The store holds the copy the edit was worked out from (reads 1 and 2), so the edit's
        # movement is certainly its own: it is taken back.
        self.assertEqual(self.stock(17512), 600.0)
        self.assertEqual(self.lines(), [(17512, 60.0), (17513, 20.0)])


if __name__ == "__main__":
    unittest.main()
