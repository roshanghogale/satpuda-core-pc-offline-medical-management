"""A saved, edited or deleted purchase shows on the Sales screen at once.

Owner (23 Oct 2026, v1.0.11 offline-first on the store PC): "after updating the purchase or
editing it the rate is not changing in the sales". An edited purchase line carries the
medicine_id it was loaded with; the save matched that id by name + batch and returned early, so
the medicine (batch) row the Sales screen reads kept the old MRP, rate, GST and expiry. Only the
bill changed. The same happened to a new purchase whose line came with the batch's id.

Now every purchase line puts its MRP / rate / GST / expiry (and the other master fields it
carries) onto the batch row it stocks, on save and on edit; the change is noted for the server
(offline-first outbox) and a pull of the server's older copy does not undo it. A Tabs/Strip
correction (1 -> 10 as the owner did, or 10 -> 1 / 10 -> 15) reaches the batch when no other
purchase stocks it, so Sales divides the MRP by the right pack.

Runs on a temp offline-first store; no server is contacted.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from unittest import mock

from core import db_setup, desktop_purchase_service, desktop_sales_service
from core.offline_first import ids, schema
from core.offline_first.schema import meta_set

DEVICE = 3
BASE = DEVICE * 1_000_000_000
NAME = "DOLO 650"


def _make_store() -> str:
    fd, path = tempfile.mkstemp(suffix=".db", prefix="of_pur_rate_")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    db_setup.initialise(conn)
    conn.commit()
    schema.ensure_schema(conn)
    meta_set(conn, "device_no", DEVICE)
    meta_set(conn, "id_base", BASE)
    meta_set(conn, "id_max", BASE + 999_999_999)
    meta_set(conn, "install_id", "pc-test-install")
    conn.commit()
    conn.close()
    return path


def _line(*, qty=10, mrp=30.0, rate=20.0, batch="B1", expiry="10/27", gst=12, mid=None):
    return {"medicine_id": mid, "name": NAME, "type": "Tablet", "batch": batch,
            "expiry": expiry, "qty": qty, "free_qty": 0, "rate": rate, "mrp": mrp,
            "gst_pct": gst, "discount_pct": 0, "hsn_code": "3004", "manufacturer": "MICRO",
            "schedule": "", "content_drug": "", "unit": "10", "tablets_per_stripe": 10,
            "quantity_value": "10", "item_amount": 0, "amount": 0, "taxable": 0, "gst_amt": 0}


class PurchaseReachesSale(unittest.TestCase):
    def setUp(self):
        self.path = _make_store()
        self.conn = sqlite3.connect(
            self.path, check_same_thread=False, timeout=30, factory=ids.OfConnection
        )
        self.conn.execute("PRAGMA busy_timeout=30000")
        stack = ExitStack()
        self.addCleanup(self._cleanup)
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=False),
            mock.patch("core.online_guard.ensure_can_mutate", return_value=None),
            mock.patch("core.desktop_returns_service.edit_below_returned_error",
                       return_value=None),
        ):
            stack.enter_context(patch)

    def _cleanup(self):
        try:
            self.conn.close()
        except Exception:
            pass
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    # -- helpers ---------------------------------------------------------------------------
    def save(self, lines, *, editing=0, bill="INV1", allow_duplicate=False):
        body = {"supplier_name": "STG SUP", "purchase_date": "2026-10-20", "bill_number": bill,
                "items": lines, "cash_paid": 0, "online_paid": 0}
        if editing:
            body["editing_purchase_id"] = editing
        if allow_duplicate:
            body["allow_duplicate"] = True
        res = desktop_purchase_service.save_purchase_bill(self.conn, body)
        self.assertTrue(res.get("ok"), res)
        return int(res["purchase_id"])

    def loaded_lines(self, pid):
        """The lines as the Purchase screen gets them for an edit (they keep medicine_id)."""
        loaded = desktop_purchase_service.load_purchase(self.conn, pid)
        self.assertTrue(loaded.get("ok", True), loaded)
        items = (loaded.get("form") or {}).get("items") or []
        self.assertTrue(items, loaded)
        return [dict(it) for it in items]

    def batches(self, include_zero=False):
        return desktop_sales_service.list_batches_for_name(
            self.conn, NAME, bill_date="2026-10-23", include_zero_stock=include_zero
        )["batches"]

    def one_batch(self):
        got = self.batches()
        self.assertEqual(len(got), 1, got)
        return got[0]

    # -- tests -----------------------------------------------------------------------------
    def test_a_new_purchase_shows_its_mrp_and_stock(self):
        self.save([_line()])
        b = self.one_batch()
        self.assertEqual((b["batch"], b["mrp"], b["rate"], b["stock"]), ("B1", 30.0, 20.0, 100.0))

    def test_an_edited_mrp_and_rate_reach_the_sale_batch(self):
        pid = self.save([_line()])
        lines = self.loaded_lines(pid)
        self.assertTrue(lines[0].get("medicine_id"))
        lines[0].update(mrp=35.0, rate=22.0, gst_pct=5)
        self.save(lines, editing=pid)
        b = self.one_batch()
        self.assertEqual((b["mrp"], b["rate"], b["gst_percent"]), (35.0, 22.0, 5.0))
        self.assertEqual(b["stock"], 100.0)

    def test_an_edited_expiry_reaches_the_sale_batch(self):
        pid = self.save([_line()])
        lines = self.loaded_lines(pid)
        lines[0].update(expiry="03/28")
        self.save(lines, editing=pid)
        self.assertTrue(str(self.one_batch()["expiry"]).startswith("2028-03"))

    def test_an_edited_batch_moves_the_stock_to_the_new_batch(self):
        pid = self.save([_line()])
        lines = self.loaded_lines(pid)
        lines[0].update(batch="B2", mrp=33.0)
        self.save(lines, editing=pid)
        b = self.one_batch()
        self.assertEqual((b["batch"], b["mrp"], b["stock"]), ("B2", 33.0, 100.0))
        old = [x for x in self.batches(include_zero=True) if x["batch"] == "B1"]
        self.assertTrue(all(x["stock"] == 0 for x in old), old)

    def test_an_edited_qty_moves_stock_by_the_difference_once(self):
        pid = self.save([_line(qty=10)])
        lines = self.loaded_lines(pid)
        lines[0].update(qty=4)
        self.save(lines, editing=pid)
        self.assertEqual(self.one_batch()["stock"], 40.0)
        lines = self.loaded_lines(pid)
        lines[0].update(qty=6)
        self.save(lines, editing=pid)
        self.assertEqual(self.one_batch()["stock"], 60.0)

    def test_a_deleted_purchase_takes_its_stock_off_the_sale_screen(self):
        pid = self.save([_line()])
        res = desktop_purchase_service.delete_saved_purchase(self.conn, {"purchase_id": pid})
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.batches(), [])

    def test_a_new_purchase_of_the_same_batch_brings_its_new_mrp(self):
        pid = self.save([_line(qty=10, mrp=30.0)])
        mid = self.one_batch()["id"]
        # Picked from the batch list: the line comes with the batch's id.
        self.save([_line(qty=5, mrp=32.0, rate=21.0, mid=mid)], bill="INV2")
        b = self.one_batch()
        self.assertEqual((b["id"], b["mrp"], b["rate"], b["stock"]), (mid, 32.0, 21.0, 150.0))
        # And without the id (typed in by name + batch).
        self.save([_line(qty=5, mrp=34.0)], bill="INV3")
        b = self.one_batch()
        self.assertEqual((b["mrp"], b["stock"]), (34.0, 200.0))
        del pid

    def _set_pack(self, line, tps):
        line.update(unit=str(tps), tablets_per_stripe=tps, quantity_value=str(tps))
        line.pop("pack", None)
        return line

    def _sale_rate(self, mid):
        res = desktop_sales_service.build_line(
            self.conn, {"medicine_id": mid, "qty": 1, "bill_date": "2026-10-23"})
        self.assertTrue(res.get("ok"), res)
        return res

    def test_tabs_per_strip_1_corrected_to_10_reaches_the_sale(self):
        # Owner, 23 Oct 2026: saved with 1 tablet per strip by mistake, edited to 10.
        pid = self.save([self._set_pack(_line(qty=10, mrp=30.0), 1)])
        b = self.one_batch()
        self.assertEqual((b["unit"], b["stock"]), ("1", 10.0))
        lines = self.loaded_lines(pid)
        self._set_pack(lines[0], 10)
        self.save(lines, editing=pid)
        b = self.one_batch()
        self.assertEqual((b["unit"], b["stock"], b["mrp"]), ("10", 100.0, 30.0))
        line = self._sale_rate(b["id"])
        rate = float((line.get("line") or line).get("rate") or 0)
        self.assertAlmostEqual(rate, 3.0, places=2, msg=line)
        # Sent to the server: the batch row is in the outbox with unit 10.
        dirty = {(c, int(i)) for c, i in self.conn.execute(
            "SELECT collection, local_id FROM of_dirty")}
        self.assertIn(("medicines", int(b["id"])), dirty)

    def test_tabs_per_strip_10_corrected_to_1_reaches_the_sale(self):
        pid = self.save([self._set_pack(_line(qty=10, mrp=30.0), 10)])
        lines = self.loaded_lines(pid)
        self._set_pack(lines[0], 1)
        self.save(lines, editing=pid)
        b = self.one_batch()
        self.assertEqual((b["unit"], b["stock"]), ("1", 10.0))

    def test_the_edit_is_sent_to_the_server(self):
        pid = self.save([_line()])
        mid = self.one_batch()["id"]
        self.conn.execute("DELETE FROM of_dirty")
        self.conn.commit()
        lines = self.loaded_lines(pid)
        lines[0].update(mrp=36.0)
        self.save(lines, editing=pid)
        dirty = {(c, int(i)) for c, i in self.conn.execute(
            "SELECT collection, local_id FROM of_dirty")}
        self.assertIn(("medicines", int(mid)), dirty)
        self.assertIn(("purchases", int(pid)), dirty)

    def test_a_pull_does_not_undo_the_edit(self):
        from core.offline_first import outbox, pull, worker

        pid = self.save([self._set_pack(_line(qty=10, mrp=30.0), 1)])
        mid = int(self.one_batch()["id"])
        w = worker.open_worker_connection(self.path)
        self.addCleanup(w.close)
        with worker.write_batch(w):
            outbox.build_events(w, "pc-test-install")
        old_doc = outbox.build_doc(w, "medicines", mid)          # what the server holds now
        evs = outbox.pending_events(w)
        with worker.write_batch(w):
            outbox.apply_push_answer(w, {"last_seq": evs[-1]["seq"], "results": [
                {"seq": e["seq"], "outcome": "applied"} for e in evs]})

        lines = self.loaded_lines(pid)
        self._set_pack(lines[0], 10)
        lines[0].update(mrp=40.0)
        self.save(lines, editing=pid)
        # The server's older copy arrives before this PC's edit went up: not applied.
        stale = dict(old_doc, version=int(old_doc.get("version") or 1) + 1)
        with worker.write_batch(w, capture_paused=True):
            pull.apply_page(w, [{"collection": "medicines", "local_id": mid, "operation": "upsert",
                                 "doc": stale}], to_revision=5)
        b = self.one_batch()
        self.assertEqual((b["unit"], b["mrp"]), ("10", 40.0))
        # The edit goes up as the batch row with the new pack and MRP ...
        with worker.write_batch(w):
            outbox.build_events(w, "pc-test-install")
        sent = [e for e in outbox.pending_events(w) if e.get("collection") == "medicines"]
        self.assertTrue(sent)
        self.assertEqual((str(sent[-1]["doc"]["unit"]), float(sent[-1]["doc"]["mrp"])), ("10", 40.0))
        evs = outbox.pending_events(w)
        with worker.write_batch(w):
            outbox.apply_push_answer(w, {"last_seq": evs[-1]["seq"], "results": [
                {"seq": e["seq"], "outcome": "applied"} for e in evs]})
        # ... and the server's copy of it coming back keeps it.
        echo = dict(sent[-1]["doc"], version=int(stale["version"]) + 1)
        with worker.write_batch(w, capture_paused=True):
            pull.apply_page(w, [{"collection": "medicines", "local_id": mid, "operation": "upsert",
                                 "doc": echo}], to_revision=6)
        b = self.one_batch()
        self.assertEqual((b["unit"], b["mrp"]), ("10", 40.0))


if __name__ == "__main__":
    unittest.main()


class CopiedDownFromOnline(PurchaseReachesSale):
    """Store 148 (23 Oct 2026): purchases made Online on 21 Oct, copied down by the switch to
    offline-first, then Tabs/Strip corrected 1 -> 10 on the PC. The pulled lines kept no pack,
    so the edit took a line back by the batch's pack, showed the batch's pack on the screen and
    went up to the server with no pack at all."""

    MID, PID, SID = 258, 8, 31

    def copy_down(self, *, med_unit, line_tps, server_stock):
        from core.offline_first import pull, worker

        w = worker.open_worker_connection(self.path)
        self.addCleanup(w.close)
        med = {"id": self.MID, "local_id": self.MID, "name": NAME, "type": "Tablet",
               "unit": med_unit, "stock_qty": server_stock, "mrp": 30.0, "rate": 20.0,
               "gst_percent": 12, "batch_no": "B1", "expiry_date": "2027-10-01", "version": 2,
               "updated_at": "2026-10-21T06:00:00Z", "created_at": "2026-10-21T06:00:00Z"}
        sup = {"id": self.SID, "local_id": self.SID, "name": "STG SUP", "version": 1}
        pur = {"id": self.PID, "local_id": self.PID, "purchase_no": "8/FY2026-27",
               "supplier_id": self.SID, "supplier_name": "STG SUP",
               "purchase_date": "2026-10-21", "bill_number": "INV1", "subtotal": 200,
               "total_gst": 24, "cgst": 12, "sgst": 12, "total_amount": 224,
               "final_amount": 224, "need_to_pay": 224, "version": 1,
               "items": [{"medicine_id": self.MID, "name": NAME, "type": "Tablet", "qty": 10,
                          "free_qty": 0, "rate": 20, "mrp": 30, "gst_pct": 12,
                          "batch_no": "B1", "expiry_date": "2027-10-01", "item_amount": 224,
                          "unit": str(line_tps), "tablets_per_stripe": line_tps}]}
        with worker.write_batch(w, capture_paused=True):
            pull.apply_page(w, [{"collection": c, "local_id": d["id"], "operation": "upsert",
                                 "doc": d} for c, d in (("suppliers", sup), ("medicines", med),
                                                        ("purchases", pur))], to_revision=3)
            pull.set_stock_from_server(w, [[self.MID, server_stock, 0]])
        return w

    def edit_to_ten(self, w):
        from core.offline_first import outbox, worker

        lines = self.loaded_lines(self.PID)
        self.assertEqual((lines[0]["unit"], lines[0]["tablets_per_stripe"]), ("1", 1),
                         "the edit screen must show the pack the line was bought in")
        self._set_pack(lines[0], 10)
        lines[0]["mrp"] = 35.0
        self.save(lines, editing=self.PID)
        with worker.write_batch(w):
            outbox.build_events(w, "pc-test-install")
        return {e["collection"]: e for e in outbox.pending_events(w)}

    def check(self, evs):
        b = self.one_batch()
        self.assertEqual((b["unit"], b["stock"], b["mrp"]), ("10", 100.0, 35.0))
        rate = float(self._sale_rate(b["id"])["line"]["rate"])
        self.assertAlmostEqual(rate, 3.5, places=2)
        # Up to the server: the batch row, and the line WITH its pack.
        self.assertEqual((evs["medicines"]["doc"]["unit"], float(evs["medicines"]["doc"]["mrp"])),
                         ("10", 35.0))
        [line] = evs["purchases"]["doc"]["items"]
        self.assertEqual((line["unit"], line["tablets_per_stripe"]), ("10", 10))
        moved = sum(int(o["qty_delta"]) for o in evs["stock_operations"]["stock_ops"])
        self.assertEqual(moved, 90)

    def test_a_batch_of_its_own_from_online_takes_the_corrected_pack(self):
        w = self.copy_down(med_unit="1", line_tps=1, server_stock=10)
        self.check(self.edit_to_ten(w))

    def test_a_batch_already_on_ten_takes_back_only_what_the_line_stocked(self):
        # The batch row was already strips of 10 (the keep-the-pack rule kept it when the
        # line was typed as 1): the line stocked 10 tablets, not 100.
        w = self.copy_down(med_unit="10", line_tps=1, server_stock=10)
        self.check(self.edit_to_ten(w))


from tests.test_a_loose_purchase_line_stocks_its_own_pack import (  # noqa: E402
    MID as _ONLINE_MID,
    _Online,
    _screen_line,
)


class OnlineANewPurchaseOfAKnownBatch(_Online):
    def test_the_batch_takes_the_new_mrp_and_rate(self):
        line = _screen_line(3, 10, rate=49.98)
        line.update(medicine_id=_ONLINE_MID, mrp=64.5, gst_pct=12)
        _doc, med, moved = self.save([line])
        self.assertEqual(moved, 30)
        self.assertEqual((med["mrp"], med["rate"], med["gst_percent"]), (64.5, 49.98, 12.0))
