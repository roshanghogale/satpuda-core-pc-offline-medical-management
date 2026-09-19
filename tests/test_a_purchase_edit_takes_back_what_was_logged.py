"""A purchase edit takes back exactly what that purchase put on the shelf, line for line.

Store 4 holds -7,567 units of false stock on 9 rows. Six PC purchases had several lines on
one medicine id; the build that saved them logged one line, and the edit took off all of
them. Medicine 17585: the save logged +3, the edit took off 4,905. The edit worked out "what
this purchase had added" again from the lines, which is not what the save had added.

Online, an edit now takes the old side from the stock ledger itself -- the purchase and
purchase_edit movements logged for that bill and that medicine -- and only a medicine with no
logged movement (a purchase from before the ledger) is worked out from its lines. A
purchase with several lines on one medicine id still adds all of them on save.

The server client is patched; nothing reaches a server.
"""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import ExitStack
from unittest import mock

from core import db_setup, purchase_service  # noqa: E402

CALC = {"cash_paid": 0, "online_paid": 0, "amount_paid": 0, "total_amount": 30,
        "final_amount": 30, "due": 30, "total_due": 30}


def _line(mid, qty, *, name="X GEL", kind="Gel", unit="1"):
    return {"medicine_id": mid, "name": name, "type": kind, "qty": qty, "free_qty": 0,
            "rate": 10, "batch_no": "B7", "batch": "B7", "expiry": "12/27",
            "expiry_date": "2027-12-01", "unit": unit, "tablets_per_stripe": 1}


def _op(op_uuid, mid, qty, *, ref_id=3010, op="purchase", ref="purchases"):
    return {"id": abs(hash(op_uuid)) % 10**9, "op_uuid": op_uuid, "medicine_id": mid, "op": op,
            "qty_delta": qty, "ref_collection": ref, "ref_id": ref_id,
            "created_at": "2026-08-23T06:00:00.000Z"}


class _Store(unittest.TestCase):
    STOCK = {17585: 3.0, 17601: 200.0}

    def setUp(self):
        self.ledger: list[dict] = []
        self.ledger_down = False
        self.pulled: list[tuple] = []
        self.sent: list[dict] = []
        self.existing: dict = {}

        def get_doc(collection, local_id):
            if collection == "purchases":
                return dict(self.existing) if self.existing else None
            if collection == "medicines":
                mid = int(local_id)
                return {"id": mid, "name": "X GEL", "type": "Gel", "unit": "1",
                        "stock_qty": self.STOCK.get(mid, 0.0), "version": 3}
            return {"id": int(local_id), "name": "S PHARMA", "version": 1}

        def pull(token, collection, **kw):
            self.pulled.append((collection, kw.get("since")))
            if self.ledger_down:
                raise RuntimeError("Cannot reach server")
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
            mock.patch("core.server_api.store_token_for_active", return_value="t"),
            mock.patch("core.server_api.allocate_fy",
                       return_value={"fy_start_year": 2026, "fy_serial": 95,
                                     "purchase_no": "95/FY2026-27"}),
            mock.patch("core.server_crud.allocate_id", return_value=3010),
            mock.patch("core.server_crud._device_id", return_value="dev"),
            mock.patch("core.server_crud.save_new_purchase_online",
                       side_effect=lambda doc: self.sent.append(doc) or int(doc.get("id") or 0)),
            mock.patch.object(purchase_service, "_ensure_purchase_line_medicine_ids",
                              return_value=None),
            mock.patch.object(purchase_service, "hide_online_zero_stock_duplicates",
                              return_value=0),
        ):
            stack.enter_context(patch)

    def edit(self, lines):
        purchase_service.update_purchase_online_now(
            3010, 279, "INV-3010", "2026-08-23", dict(CALC), [dict(x) for x in lines],
            client_uuid="p-3010",
        )
        [doc] = self.sent
        return {int(m["id"]): m for m in doc["_medicines"]}

    def moved(self, med) -> int:
        return sum(int(op.get("qty_delta") or 0) for op in med.get("stock_ops") or [])


class AnEditOfAPurchaseWhoseSaveLoggedOneLine(_Store):
    def setUp(self):
        super().setUp()
        # Two lines on one medicine id; the build that saved it logged only the first (+3).
        self.existing = {"id": 3010, "client_uuid": "p-3010", "purchase_no": "95/FY2026-27",
                         "purchase_date": "2026-08-23", "version": 2, "supplier_id": 279,
                         "items": [_line(17585, 3), _line(17585, 4905)]}
        self.ledger = [
            _op("purchase:p-3010:med:17585:v1", 17585, 3),
            _op("sale:s-1:med:17585:v1", 17585, -1, op="sale", ref="sales", ref_id=40001),
            _op("purchase:p-2999:med:17585:v1", 17585, 50, ref_id=2999),
        ]

    def test_it_takes_back_what_was_logged_not_what_the_lines_add_up_to(self):
        meds = self.edit([_line(17585, 3)])  # the 4,905 line was a mistake and is removed
        self.assertEqual(self.moved(meds[17585]), 0,
                         "the edit took off stock this purchase never put on the shelf")
        self.assertEqual(meds[17585]["stock_qty"], 3.0)
        self.assertEqual([c for c, _ in self.pulled], ["stock_operations"])

    def test_removing_every_line_takes_back_only_the_logged_three(self):
        meds = self.edit([_line(17601, 10, name="OTHER GEL")])
        self.assertEqual(self.moved(meds[17585]), -3)
        self.assertEqual(self.moved(meds[17601]), 10)

    def test_earlier_edits_of_the_bill_count_too(self):
        self.ledger.append(_op("purchase:p-3010:med:17585:edit:v2", 17585, 38, op="purchase_edit"))
        meds = self.edit([_line(17585, 3), _line(17585, 38)])
        self.assertEqual(self.moved(meds[17585]), 0, "an earlier edit's +38 was added again")


class WhenTheLedgerHasNothingOrCannotBeRead(_Store):
    def setUp(self):
        super().setUp()
        self.existing = {"id": 3010, "client_uuid": "p-3010", "purchase_no": "95/FY2026-27",
                         "purchase_date": "2026-08-23", "version": 2, "supplier_id": 279,
                         "items": [_line(17585, 3), _line(17585, 7)]}

    def test_a_purchase_from_before_the_ledger_is_worked_out_from_its_lines(self):
        meds = self.edit([_line(17585, 3)])
        self.assertEqual(self.moved(meds[17585]), -7)

    def test_an_unreadable_ledger_falls_back_to_the_lines(self):
        self.ledger_down = True
        meds = self.edit([_line(17585, 3)])
        self.assertEqual(self.moved(meds[17585]), -7)


class AnOrdinaryEditNeverWaitsOnTheLedger(_Store):
    """One line per medicine: the lines are exactly what the save logged, so nothing is read.

    The ledger has no per-bill query, so reading it pages the store's movements from the
    purchase date on -- up to 20 s on a slow store, on the Tk thread for a Classic Purchase
    History edit. Only a medicine with several lines on the stored bill can differ from its
    lines (the build that logged one of them), so only such a bill asks the ledger.
    """

    def setUp(self):
        super().setUp()
        self.existing = {"id": 3010, "client_uuid": "p-3010", "purchase_no": "95/FY2026-27",
                         "purchase_date": "2026-08-23", "version": 2, "supplier_id": 279,
                         "items": [_line(17585, 3), _line(17601, 10, name="OTHER GEL")]}
        self.ledger_down = True  # a store that would fail (or hang) if it were asked

    def test_the_edit_reads_no_ledger_and_moves_the_line_difference(self):
        meds = self.edit([_line(17585, 5), _line(17601, 10, name="OTHER GEL")])
        self.assertEqual(self.pulled, [], "an ordinary purchase edit waited on the stock ledger")
        self.assertEqual(self.moved(meds[17585]), 2)
        self.assertEqual(self.moved(meds[17601]), 0)

    def test_a_medicine_added_by_the_edit_reads_no_ledger_either(self):
        meds = self.edit([_line(17585, 3), _line(17601, 10, name="OTHER GEL"),
                          _line(17601, 4, name="OTHER GEL")])
        self.assertEqual(self.pulled, [])
        self.assertEqual(self.moved(meds[17601]), 4)


class ANewPurchaseWithSeveralLinesOnOneMedicine(_Store):
    def test_every_line_is_added(self):
        purchase_service.save_purchase_online_now(
            279, "2026-08-23", "INV-3010", dict(CALC),
            [_line(17597, 2), _line(17597, 1), _line(17597, 10)],
        )
        [doc] = self.sent
        [med] = doc["_medicines"]
        self.assertEqual(self.moved(med), 13)


class AnOfflinePurchaseEditWithTwoPacksOnOneMedicine(unittest.TestCase):
    """Offline: the save adds each line with its own pack; the edit took them back with one.

    The save sets the medicine's unit from each line in turn, so after it the medicine holds
    the LAST line's pack, and the edit reversed every line with that pack: 2 strips of 10 and
    1 strip of 15 went on as 35 tablets and came off as 45.
    """

    def setUp(self):
        self.conn = conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        conn.execute("INSERT INTO suppliers (id, name) VALUES (1, 'SHREE PHARMA')")
        conn.execute(
            "INSERT INTO medicines (id, name, type, unit, stock_qty, batch_no, expiry_date, "
            "created_at) VALUES (20, 'PARA TAB', 'Tablet', '10', 0, 'PB1', '2027-12-01', "
            "'2026-01-01 09:00:00')"
        )
        conn.commit()
        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.online_catalog.medicine_by_id", return_value=None),
            mock.patch("core.server_crud.get_doc", return_value=None),
        ):
            stack.enter_context(patch)

    @staticmethod
    def calc():
        keys = ("subtotal", "total_gst", "cgst", "sgst", "total_amount", "overall_discount",
                "rounding", "need_to_pay", "final_amount", "previous_due", "previous_credit",
                "due", "current_credit", "total_due", "bill_cleared", "account_cleared")
        return {k: 0 for k in keys}

    @staticmethod
    def lines():
        base = {"medicine_id": 20, "name": "PARA TAB", "type": "Tablet", "batch": "PB1",
                "expiry": "12/27", "free_qty": 0, "rate": 10, "mrp": 12}
        return [dict(base, qty=2, tablets_per_stripe=10, unit="10"),
                dict(base, qty=1, tablets_per_stripe=15, unit="15")]

    def stock(self):
        return self.conn.execute("SELECT stock_qty FROM medicines WHERE id=20").fetchone()[0]

    def test_an_unchanged_edit_leaves_the_stock_where_the_save_put_it(self):
        purchase_service.save_purchase(self.conn, 1, "2026-09-01", "INV-1", self.calc(), self.lines())
        self.assertEqual(self.stock(), 35)
        pid = self.conn.execute("SELECT id FROM purchases ORDER BY id DESC LIMIT 1").fetchone()[0]
        purchase_service.update_purchase(self.conn, pid, 1, "INV-1", "2026-09-01", self.calc(),
                                         self.lines())
        self.assertEqual(self.stock(), 35, "the edit took back a different quantity than the save added")


if __name__ == "__main__":
    unittest.main()
