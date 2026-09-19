"""A purchase edit reads back only ITS OWN movements from the stock ledger.

A deleted purchase row is removed outright, so the next purchase saved takes the same local id.
The Online edit's ledger path summed every 'purchase' and 'purchase_edit' movement whose ref_id
was the bill's id -- the deleted bill's as well, and without that bill's 'purchase_delete'.
Staging, store 4, MOLICOLD 17485: a bill saved +5, was edited +3 and deleted -8 under local id
3026; the next purchase took 3026 and logged +35; its edit to 40 logged -3 (old side 5 + 3 + 35)
where +5 was due. Every medicine the two bills shared lost stock on every such edit.

Each movement carries its bill's client_uuid in its op_uuid ("purchase:<client_uuid>:med:<mid>:
..."), from the PC and from the phone alike, so only that key is this bill's. A bill with no
client_uuid of its own cannot be told apart from an earlier bill that held its id once one was
deleted there, and is then worked out from its lines.

The server client is patched; nothing reaches a server.
"""
from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest import mock

from core import purchase_service  # noqa: E402

CALC = {"cash_paid": 0, "online_paid": 0, "amount_paid": 0, "total_amount": 400,
        "final_amount": 400, "due": 400, "total_due": 400}

DELETED_BILL = "8cdfd714-5c1e-4f4e-9d0b-6a1f2e3c4b5a"
THIS_BILL = "80ddb67d-2a9b-4c8e-8f71-0b9d3e5a6c7d"
MOLICOLD = 17485


def _gel(qty):
    # Unit "1": every unit is one unit on the shelf, so the arithmetic below is the ledger's.
    return {"medicine_id": MOLICOLD, "name": "MOLICOLD GEL", "type": "Gel", "qty": qty,
            "free_qty": 0, "rate": 10, "batch_no": "M1", "batch": "M1", "expiry": "12/27",
            "expiry_date": "2027-12-01", "unit": "1", "tablets_per_stripe": 1}


def _op(op_uuid, qty, op, *, ref_id=3026, mid=MOLICOLD):
    return {"id": abs(hash(op_uuid)) % 10**9, "op_uuid": op_uuid, "medicine_id": mid, "op": op,
            "qty_delta": qty, "ref_collection": "purchases", "ref_id": ref_id,
            "created_at": "2026-09-14T04:00:00.000Z"}


def _deleted_bill_ops(key=DELETED_BILL):
    return [
        _op(f"purchase:{key}:med:{MOLICOLD}:v1", 5, "purchase"),
        _op(f"purchase:{key}:med:{MOLICOLD}:edit:v2", 3, "purchase_edit"),
        _op(f"purchase:{key}:med:{MOLICOLD}:delete:v1", -8, "purchase_delete"),
    ]


class _Edit(unittest.TestCase):
    STOCK = 35.0

    def setUp(self):
        self.ledger: list[dict] = []
        self.pulled: list[str] = []
        self.sent: list[dict] = []
        self.existing: dict = {}

        def get_doc(collection, local_id):
            if collection == "purchases":
                return dict(self.existing) if self.existing else None
            if collection == "medicines":
                return {"id": int(local_id), "name": "MOLICOLD GEL", "type": "Gel", "unit": "1",
                        "stock_qty": self.STOCK, "version": 3}
            return {"id": int(local_id), "name": "S PHARMA", "version": 1}

        def pull(token, collection, **kw):
            self.pulled.append(collection)
            if collection != "stock_operations":
                return [], {}
            return [dict(o) for o in self.ledger], {}

        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.server_crud.get_doc", side_effect=get_doc),
            mock.patch("core.online_catalog.medicine_by_id",
                       side_effect=lambda mid: get_doc("medicines", mid)),
            mock.patch("core.online_catalog.find_supplier_by_id",
                       return_value={"id": 279, "name": "S PHARMA"}),
            mock.patch("core.online_catalog.patch_docs", return_value=None),
            mock.patch("core.online_catalog._token", return_value="t"),
            mock.patch("core.server_api.pull_collection", side_effect=pull),
            mock.patch("core.server_crud._device_id", return_value="dev"),
            mock.patch("core.server_crud.save_new_purchase_online",
                       side_effect=lambda doc: self.sent.append(doc) or int(doc.get("id") or 0)),
            mock.patch.object(purchase_service, "_ensure_purchase_line_medicine_ids",
                              return_value=None),
            mock.patch.object(purchase_service, "hide_online_zero_stock_duplicates",
                              return_value=0),
        ):
            stack.enter_context(patch)

    def bill(self, lines, client_uuid=THIS_BILL):
        self.existing = {"id": 3026, "purchase_no": "111/FY2026-27", "purchase_date": "2026-09-14",
                         "version": 1, "supplier_id": 279, "items": lines}
        if client_uuid:
            self.existing["client_uuid"] = client_uuid

    def edit(self, lines, client_uuid=THIS_BILL):
        purchase_service.update_purchase_online_now(
            3026, 279, "INV-P2", "2026-09-14", dict(CALC), lines, client_uuid=client_uuid,
        )
        [doc] = self.sent
        return {int(m["id"]): m for m in doc["_medicines"]}

    @staticmethod
    def moved(med) -> int:
        return sum(int(op.get("qty_delta") or 0) for op in med.get("stock_ops") or [])


class AnEditOfABillWhoseIdADeletedBillHeld(_Edit):
    def setUp(self):
        super().setUp()
        # Two lines on MOLICOLD, so the edit reads the ledger: 30 + 5 = 35 on the shelf.
        self.bill([_gel(30), _gel(5)])
        self.ledger = _deleted_bill_ops() + [
            _op(f"purchase:{THIS_BILL}:med:{MOLICOLD}:v1", 35, "purchase"),
        ]

    def test_it_takes_back_only_what_this_bill_put_on_the_shelf(self):
        meds = self.edit([_gel(40)])
        self.assertIn("stock_operations", self.pulled, "the edit did not read the ledger at all")
        self.assertEqual(self.moved(meds[MOLICOLD]), 5,
                         "the deleted bill's movements under the same id were counted as this bill's")
        self.assertEqual(meds[MOLICOLD]["stock_qty"], 40.0)

    def test_this_bills_own_earlier_edit_still_counts(self):
        self.bill([_gel(30), _gel(10)])
        self.ledger.append(_op(f"purchase:{THIS_BILL}:med:{MOLICOLD}:edit:v2", 5, "purchase_edit"))
        meds = self.edit([_gel(45)])
        self.assertEqual(self.moved(meds[MOLICOLD]), 5)

    def test_another_live_bill_that_logged_this_id_is_not_counted_either(self):
        # A movement under this ref_id with some other bill's key (a bill re-filed under another
        # id, an import): it is not this bill's, deleted or not.
        self.ledger.append(_op(f"purchase:someone-else:med:{MOLICOLD}:v1", 12, "purchase"))
        meds = self.edit([_gel(40)])
        self.assertEqual(self.moved(meds[MOLICOLD]), 5)


class ABillWithNoClientUuidOfItsOwn(_Edit):
    def test_its_movements_under_its_id_count_when_no_bill_there_was_deleted(self):
        # The build that logged one of the two lines: the ledger, not the lines, is right.
        self.bill([_gel(30), _gel(5)], client_uuid="")
        self.ledger = [_op(f"purchase:3026:med:{MOLICOLD}:v1", 30, "purchase")]
        meds = self.edit([_gel(40)], client_uuid="")
        self.assertEqual(self.moved(meds[MOLICOLD]), 10)

    def test_a_deleted_bill_under_its_id_sends_it_back_to_its_lines(self):
        self.bill([_gel(30), _gel(5)], client_uuid="")
        self.ledger = _deleted_bill_ops(key="3026") + [
            _op(f"purchase::med:{MOLICOLD}:v1", 35, "purchase"),
        ]
        meds = self.edit([_gel(40)], client_uuid="")
        self.assertEqual(self.moved(meds[MOLICOLD]), 5,
                         "movements that may be the deleted bill's were trusted over the lines")


if __name__ == "__main__":
    unittest.main()
