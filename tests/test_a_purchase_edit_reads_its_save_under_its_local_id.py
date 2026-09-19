"""A purchase edit still reads the save a bill logged under its local id.

The round that stopped a deleted bill's movements from being counted (R3) kept only the movements
whose op_uuid carries the bill's client_uuid. But a bill saved before it had a client_uuid logged
its save under its local id ("purchase:83:med:..."), and the row got a client_uuid later: 98
bill-medicines on live hold their save that way (store 1 bill 83, client_uuid 226224a4..., key
"83"). For such a bill with several lines on one medicine the edit then fell back to the lines,
which is exactly what the ledger reading was added to avoid (store 4, 17585: +3 logged, -4,905
taken back by the lines).

The local-id key counts again, by the rule already used for a bill with no client_uuid: only when
no deleted bill sits under this id. A deleted purchase row is removed outright and its id is used
again, so movements under the local id may be the deleted bill's; a medicine with a purchase_delete
there -- or a delete of another bill under this id -- is left to its lines.

The server client is patched; nothing reaches a server.
"""
from __future__ import annotations

import unittest

from tests.test_a_purchase_edit_counts_only_its_own_ledger import (  # noqa: E402
    DELETED_BILL,
    MOLICOLD,
    THIS_BILL,
    _deleted_bill_ops,
    _Edit,
    _gel,
    _op,
)


class ABillWhoseSaveWasLoggedUnderItsLocalId(_Edit):
    def test_the_save_under_its_local_id_is_what_it_put_on_the_shelf(self):
        # The build that logged one of the two lines, before the row had a client_uuid.
        self.bill([_gel(30), _gel(5)])
        self.ledger = [_op(f"purchase:3026:med:{MOLICOLD}:v1", 30, "purchase")]
        meds = self.edit([_gel(40)])
        self.assertEqual(self.moved(meds[MOLICOLD]), 10,
                         "the save logged under the bill's local id was ignored")

    def test_its_later_edits_under_its_client_uuid_add_to_that_save(self):
        self.bill([_gel(30), _gel(10)])
        self.ledger = [
            _op(f"purchase:3026:med:{MOLICOLD}:v1", 30, "purchase"),
            _op(f"purchase:{THIS_BILL}:med:{MOLICOLD}:edit:v2", 5, "purchase_edit"),
        ]
        meds = self.edit([_gel(45)])
        self.assertEqual(self.moved(meds[MOLICOLD]), 10)


class ADeletedBillUnderTheReusedLocalIdStillDoesNotCount(_Edit):
    def test_a_deleted_bill_keyed_on_the_local_id(self):
        self.bill([_gel(30), _gel(5)])
        self.ledger = _deleted_bill_ops(key="3026") + [
            _op(f"purchase:{THIS_BILL}:med:{MOLICOLD}:v1", 35, "purchase"),
        ]
        meds = self.edit([_gel(40)])
        self.assertEqual(self.moved(meds[MOLICOLD]), 5,
                         "a deleted bill's movements under the reused id were counted")

    def test_a_deleted_bill_that_saved_under_the_local_id_and_was_deleted_under_its_uuid(self):
        # The same history as this bill's, on the bill that held the id before: saved before it
        # had a client_uuid, deleted after. Its delete is not under the local id.
        self.bill([_gel(30), _gel(5)])
        self.ledger = [
            _op(f"purchase:3026:med:{MOLICOLD}:v1", 5, "purchase"),
            _op(f"purchase:{DELETED_BILL}:med:{MOLICOLD}:delete:v1", -5, "purchase_delete"),
            _op(f"purchase:{THIS_BILL}:med:{MOLICOLD}:v1", 35, "purchase"),
        ]
        meds = self.edit([_gel(40)])
        self.assertEqual(self.moved(meds[MOLICOLD]), 5)

    def test_its_own_save_under_an_id_a_deleted_bill_used_goes_to_its_lines(self):
        self.bill([_gel(30), _gel(5)])
        self.ledger = _deleted_bill_ops(key="3026") + [
            _op(f"purchase:3026:med:{MOLICOLD}:p2:v1", 30, "purchase"),
        ]
        meds = self.edit([_gel(40)])
        self.assertEqual(self.moved(meds[MOLICOLD]), 5,
                         "movements that may be the deleted bill's were trusted over the lines")


if __name__ == "__main__":
    unittest.main()
