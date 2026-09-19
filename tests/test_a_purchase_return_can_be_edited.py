"""A saved purchase return can be edited without stock or money going wrong.

Returns used to say "Saved returns cannot be changed". An edit is now: take the
old return back in full, then save the corrected lines as a new return on the
same bill (core.desktop_returns_service.replace_purchase_return). These tests pin
the parts that touch the shelf: every unit moves exactly once, nothing is
reversed before the new lines are known to be valid, and a failed save puts the
original back.
"""
import sqlite3
import unittest
from unittest import mock

from core import desktop_returns_service as svc


class _Shop(unittest.TestCase):
    def setUp(self):
        from core import db_setup

        self._offline = mock.patch("core.sync_prefs.is_online_mode", return_value=False)
        self._offline.start()
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db_setup.initialise(self.conn)
        cur = self.conn.cursor()
        cur.execute("INSERT INTO suppliers (id, name) VALUES (1, 'ZZ SUPPLIER')")
        cur.execute(
            "INSERT INTO medicines (id, name, batch_no, type, unit, stock_qty, mrp, rate) "
            "VALUES (1, 'ZZ SYRUP', 'S1', 'Syrup', '100ml', 10, 50, 30)"
        )
        cur.execute(
            "INSERT INTO medicines (id, name, batch_no, type, unit, stock_qty, mrp, rate) "
            "VALUES (2, 'ZZ TABLET', 'T1', 'Tablet', '1x10', 100, 5, 3)"
        )
        cur.execute(
            "INSERT INTO purchases (id, purchase_no, supplier_id, purchase_date, "
            "total_amount, final_amount) VALUES (1, 'P1', 1, '2026-09-01', 600, 600)"
        )
        cur.execute(
            "INSERT INTO purchase_items (purchase_id, medicine_id, qty, rate, amount) "
            "VALUES (1, 1, 10, 30, 300)"
        )
        cur.execute(
            "INSERT INTO purchase_items (purchase_id, medicine_id, qty, rate, amount) "
            "VALUES (1, 2, 10, 30, 300)"
        )
        self.conn.commit()

    def tearDown(self):
        self._offline.stop()
        self.conn.close()

    def stock(self, mid):
        return float(self.conn.execute("SELECT stock_qty FROM medicines WHERE id=?", (mid,)).fetchone()[0])

    def active_returns(self):
        return self.conn.execute(
            "SELECT id FROM purchase_returns WHERE purchase_id=1 AND COALESCE(deleted,0)=0"
        ).fetchall()

    def returned_qty(self, rid):
        return float(
            self.conn.execute(
                "SELECT COALESCE(SUM(qty),0) FROM purchase_return_items WHERE return_id=?", (rid,)
            ).fetchone()[0]
        )

    def save(self, mid, qty, **extra):
        line = {"medicine_id": mid, "qty": qty, "rate": 30}
        line.update(extra)
        res = svc.save_purchase_return(
            self.conn, {"purchase_id": 1, "supplier_id": 1, "items": [line], "reason": "Expired"}
        )
        self.assertTrue(res.get("ok"), res)
        return int(res["return_id"])


class AnEditMovesEachUnitOnce(_Shop):
    def test_changing_the_quantity_moves_stock_by_the_difference_only(self):
        rid = self.save(1, 3)
        self.assertEqual(self.stock(1), 7)
        res = svc.replace_purchase_return(
            self.conn, {"return_id": rid, "items": [{"medicine_id": 1, "qty": 5, "rate": 30}]}
        )
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.stock(1), 5, "3 back, 5 out -- not 3 out twice")
        live = self.active_returns()
        self.assertEqual(len(live), 1, "the old return is gone, only the corrected one is live")
        self.assertEqual(self.returned_qty(live[0][0]), 5)
        self.assertEqual(res.get("replaced_return_id"), rid)

    def test_a_tablet_line_reloaded_from_history_keeps_its_strip_size(self):
        rid = self.save(2, 2, type="Tablet", is_tablet=True, tablets_per_stripe=10)
        self.assertEqual(self.stock(2), 80, "2 strips of 10")
        # What Returns sends after loading the return from history: no pack facts.
        res = svc.replace_purchase_return(
            self.conn,
            {"return_id": rid, "items": [{"medicine_id": 2, "qty": 1, "rate": 30,
                                          "type": "", "is_tablet": False, "tablets_per_stripe": 1}]},
        )
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.stock(2), 90, "20 back, 1 strip (10) out -- never 1 tablet")


class NothingIsReversedUntilTheNewLinesAreValid(_Shop):
    def test_more_than_the_bill_allows_changes_nothing(self):
        rid = self.save(1, 3)
        res = svc.replace_purchase_return(
            self.conn, {"return_id": rid, "items": [{"medicine_id": 1, "qty": 11, "rate": 30}]}
        )
        self.assertFalse(res.get("ok"))
        self.assertIn("nothing was changed", res.get("error", ""))
        self.assertEqual(self.stock(1), 7)
        self.assertEqual([r[0] for r in self.active_returns()], [rid])

    def test_an_edit_cannot_move_to_another_bill(self):
        rid = self.save(1, 3)
        res = svc.replace_purchase_return(
            self.conn,
            {"return_id": rid, "purchase_id": 999, "items": [{"medicine_id": 1, "qty": 2, "rate": 30}]},
        )
        self.assertFalse(res.get("ok"))
        self.assertEqual(self.stock(1), 7)
        self.assertEqual([r[0] for r in self.active_returns()], [rid])

    def test_an_empty_edit_is_refused_not_turned_into_a_delete(self):
        rid = self.save(1, 3)
        res = svc.replace_purchase_return(self.conn, {"return_id": rid, "items": []})
        self.assertFalse(res.get("ok"))
        self.assertEqual(self.stock(1), 7)


class AReturnStillUploadingIsNotEdited(_Shop):
    def test_a_queued_return_is_refused_and_nothing_moves(self):
        self.save(1, 3)
        with mock.patch.object(svc, "delete_purchase_return") as delete:
            res = svc.replace_purchase_return(
                self.conn, {"return_id": -4, "items": [{"medicine_id": 1, "qty": 2, "rate": 30}]}
            )
        self.assertFalse(res.get("ok"))
        self.assertIn("still being sent", res.get("error", ""))
        delete.assert_not_called()
        self.assertEqual(self.stock(1), 7)


class AFailedSavePutsTheOriginalBack(_Shop):
    def test_the_original_lines_are_saved_back(self):
        rid = self.save(1, 3)
        real = svc.save_purchase_return
        calls = []

        def first_fails(conn, body):
            calls.append(body)
            if len(calls) == 1:
                return {"ok": False, "error": "boom"}
            return real(conn, body)

        with mock.patch.object(svc, "save_purchase_return", side_effect=first_fails):
            res = svc.replace_purchase_return(
                self.conn, {"return_id": rid, "items": [{"medicine_id": 1, "qty": 5, "rate": 30}]}
            )
        self.assertFalse(res.get("ok"))
        self.assertTrue(res.get("restored"), res)
        self.assertEqual(self.stock(1), 7, "back to exactly where it was before the edit")
        live = self.active_returns()
        self.assertEqual(len(live), 1)
        self.assertEqual(self.returned_qty(live[0][0]), 3)


class BulkSaveSaysWhichReturnItMade(_Shop):
    def test_each_saved_return_carries_its_id_for_print_and_edit(self):
        res = svc.bulk_purchase_save(
            self.conn,
            {"purchase_groups": [{"purchase_id": 1, "supplier_id": 1, "supplier_name": "ZZ SUPPLIER",
                                  "bill_number": "P1", "items": [{"medicine_id": 1, "qty": 2, "rate": 30}]}],
             "writeoff_lines": []},
        )
        self.assertTrue(res.get("ok"), res)
        saved = res.get("saved") or []
        self.assertEqual(len(saved), 1)
        self.assertGreater(int(saved[0].get("return_id") or 0), 0)
        self.assertEqual(saved[0].get("supplier_name"), "ZZ SUPPLIER")
        self.assertEqual(self.stock(1), 8)


if __name__ == "__main__":
    unittest.main()


class OnlineTheReturnIsReadFromTheServer(unittest.TestCase):
    """At an Online shop the engine's sqlite is empty; details must come from
    the server doc, or from the upload queue right after Save."""

    DOC = {
        "return_no": "PR7", "return_date": "2026-09-11", "purchase_no": "P1",
        "supplier_name": "ZZ SUPPLIER", "refund_amount": 90, "discount": 5, "reason": "Expired",
        "purchase_id": 1, "supplier_id": 1,
        "items": [{"medicine_id": 1, "name": "ZZ SYRUP", "batch_no": "S1", "qty": 3, "rate": 30}],
    }

    def setUp(self):
        self._online = mock.patch("core.sync_prefs.is_online_mode", return_value=True)
        self._online.start()
        self.conn = sqlite3.connect(":memory:")

    def tearDown(self):
        self._online.stop()
        self.conn.close()

    def test_details_come_from_the_server_doc(self):
        with mock.patch("core.online_mutation_queue.pending_rows", return_value=[]), \
             mock.patch("core.server_crud.get_doc", return_value=dict(self.DOC)) as get_doc:
            d = svc.get_purchase_return_details(self.conn, 7)
        get_doc.assert_called_once_with("purchase_returns", 7)
        self.assertTrue(d.get("ok"), d)
        self.assertEqual((d["return_no"], d["purchase_id"], d["supplier"], d["discount"]), ("PR7", 1, "ZZ SUPPLIER", 5.0))
        self.assertEqual([(i["medicine_id"], i["qty"], i["batch"]) for i in d["items"]], [(1, 3.0, "S1")])

    def test_a_return_still_in_the_upload_queue_is_found(self):
        queued = [{"local_id": -4, "op": "upsert", "payload": dict(self.DOC, return_no="PR4")}]
        with mock.patch("core.online_mutation_queue.pending_rows", return_value=queued), \
             mock.patch("core.server_crud.get_doc", return_value=None) as get_doc:
            d = svc.get_purchase_return_details(self.conn, -4)
        self.assertTrue(d.get("ok"), d)
        self.assertEqual(d["return_no"], "PR4")
        get_doc.assert_not_called()

    def test_a_return_being_deleted_is_not_offered(self):
        queued = [{"local_id": 7, "op": "delete", "payload": {"id": 7}}]
        with mock.patch("core.online_mutation_queue.pending_rows", return_value=queued), \
             mock.patch("core.server_crud.get_doc", return_value=dict(self.DOC)):
            d = svc.get_purchase_return_details(self.conn, 7)
        self.assertFalse(d.get("ok"))

    def test_an_unreadable_server_is_an_error_not_an_empty_return(self):
        with mock.patch("core.online_mutation_queue.pending_rows", return_value=[]), \
             mock.patch("core.server_crud.get_doc", return_value=None):
            d = svc.get_purchase_return_details(self.conn, 7)
        self.assertFalse(d.get("ok"))


class OnlineSavePdfUsesTheServerCopy(unittest.TestCase):
    """Save PDF used to read only local SQL, so every Online shop got
    "Return not found". It now renders from the server doc and the catalog."""

    DOC = OnlineTheReturnIsReadFromTheServer.DOC

    def setUp(self):
        from core import db_setup

        self.conn = sqlite3.connect(":memory:")
        db_setup.initialise(self.conn)  # profile table for the letterhead
        self._online = mock.patch("core.sync_prefs.is_online_mode", return_value=True)
        self._online.start()
        self.written = []
        self._save = mock.patch(
            "core.document_output.save_supplier_document",
            side_effect=lambda html, name: (self.written.append((html, name)) or ("x.pdf", "x.html")),
        )
        self._save.start()
        self._sup = mock.patch("core.online_catalog.find_supplier_by_id",
                               return_value={"name": "ZZ SUPPLIER", "phone": "9999900000", "address": "Main Rd"})
        self._sup.start()
        self._med = mock.patch("core.online_catalog.medicine_by_id",
                               return_value={"name": "ZZ SYRUP", "batch_no": "S1", "expiry_date": "2026-12-31", "mrp": 50})
        self._med.start()

    def tearDown(self):
        for p in (self._med, self._sup, self._save, self._online):
            p.stop()
        self.conn.close()

    def test_a_pdf_is_made_from_the_server_doc(self):
        with mock.patch("core.online_mutation_queue.pending_rows", return_value=[]), \
             mock.patch("core.server_crud.get_doc", return_value=dict(self.DOC)):
            res = svc.save_purchase_return_pdf(self.conn, 7)
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(res.get("path"), "x.pdf")
        html, name = self.written[0]
        self.assertEqual(name, "PR7")
        for text in ("PR7", "ZZ SUPPLIER", "ZZ SYRUP", "S1"):
            self.assertIn(text, html)

    def test_a_return_still_uploading_can_be_printed(self):
        queued = [{"local_id": -4, "op": "upsert", "payload": dict(self.DOC, return_no="PR4")}]
        with mock.patch("core.online_mutation_queue.pending_rows", return_value=queued), \
             mock.patch("core.server_crud.get_doc", return_value=None):
            res = svc.save_purchase_return_pdf(self.conn, -4)
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.written[0][1], "PR4")
