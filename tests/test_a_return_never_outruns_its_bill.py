"""Hostile checks on the returns fixes, money first.

* The 500-row blind spot: a return the old single page could not see is counted,
  a full page is never taken as the end, queued returns still count, and an
  unreachable server is not "nothing returned" -- at load AND at save.
* A purchase return is checked against its bill when it is SAVED, Online and
  Offline. Every screen that saves one (Returns -> Purchase, the bulk tab, the
  alert popup's Return, Inventory's Return expired) stays loaded, so a stale
  screen could send the same strips back twice.
* The expired/near-expiry prefill caps a bill line by the shelf in the BILL's
  units: stock_qty counts tablets, a purchase line counts strips.
* The overall discount % becomes rupees with Python's rounding, as Classic does.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import desktop_returns_service as drs  # noqa: E402
from tests import test_alert_popup_return_and_reorder as ap  # noqa: E402
from tests import test_returns_list_is_read_to_the_end as rl  # noqa: E402

REAL_PENDING = drs._pending_returned_qty
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESKTOP = os.path.join(ROOT, "desktop")


class TheCapHidesAReturnMadeTheSameDay(rl._Online):
    """Exactly 500 later returns, all on the day the bill's own return was made
    and all with higher ids: the old single page was full and ended one row
    short of it."""

    def setUp(self):
        rows = [{"id": 1000 + i, "sale_id": 6, "return_date": rl.day(-1)} for i in range(500)]
        rows.append({"id": 1, "sale_id": 5, "return_date": rl.day(-1)})
        self.start(rows, {1: {"items": [{"medicine_id": 7, "qty": 4}]}}, {("sales", 5): rl.SALE})

    def test_the_old_page_never_held_it(self):
        self.assertNotIn(5, [r["sale_id"] for r in self.server(limit=500)["rows"]])

    def test_the_lookup_counts_it_at_load_and_at_save(self):
        res = drs.load_sales_bill_for_return(self.conn, 5)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["items"][0]["remaining_qty"], 6.0)
        self.assertIn("Cannot return more than 6",
                      drs._sales_return_request_error(self.conn, 5, rl.sale_body(7), True))
        # The full first page was followed by a dated second one.
        self.assertEqual(self.server.calls[0], (500, "", ""))
        self.assertEqual(self.server.calls[1], (5000, "", rl.day(-1)))


class QueuedReturnsStillCount(rl._Online):
    """7 of 10 went back long ago (past row 500) and 2 more sit in the upload
    queue: 1 is left."""

    def setUp(self):
        rows, docs = rl.busy_shop("sale_id", 5, [(-390, 4), (-380, 3)])
        self.start(rows, docs, {("sales", 5): rl.SALE})
        queued = [{"op": "upsert",
                   "payload": {"sale_id": 5, "items": [{"medicine_id": 7, "qty": 2}]}}]
        for p in (
            mock.patch.object(drs, "_pending_returned_qty", REAL_PENDING),
            mock.patch("core.online_mutation_queue.pending_rows",
                       side_effect=lambda collection=None:
                       queued if collection == "sales_returns" else []),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_on_top_of_the_ones_past_row_500(self):
        res = drs.load_sales_bill_for_return(self.conn, 5)
        self.assertEqual(res["items"][0]["remaining_qty"], 1.0)
        self.assertIn("Cannot return more than 1",
                      drs._sales_return_request_error(self.conn, 5, rl.sale_body(2), True))
        self.assertEqual(drs._sales_return_request_error(self.conn, 5, rl.sale_body(1), True), "")


class AnUnreachableServerAtSaveIsNotALicence(rl._Online):
    def test_a_sales_return_is_refused_when_its_bill_cannot_be_read(self):
        # get_doc answers None for a dead link (server_crud.get_doc), and the
        # returns list times out. The save used to go through unchecked.
        self.start([], {}, {}, list_side_effect=RuntimeError("timed out"))
        res = drs.save_sales_return(self.conn, rl.sale_body(1))
        self.assertFalse(res["ok"])
        self.assertIn("Could not read this bill", res["error"])


class OnlineAPurchaseReturnIsCheckedAtSave(unittest.TestCase):
    """B-77 bought 60 strips; 45 went back long ago, past row 500."""

    def setUp(self):
        ap.OnlineTheButtonsReadTheStore.setUp(self)
        self.enqueue = mock.MagicMock(return_value={"local_id": -9})
        for p in (
            mock.patch("core.online_mutation_queue.enqueue", self.enqueue),
            mock.patch("core.online_catalog.find_supplier_by_id", return_value={}),
            mock.patch.object(drs, "_return_id_for", return_value=(-9, True)),
        ):
            p.start()
            self.addCleanup(p.stop)

    def body(self, qty):
        return {"purchase_id": 77, "supplier_id": 4,
                "items": [{"medicine_id": 13, "name": "ZZ EXPMED", "qty": qty, "rate": 7.5}]}

    def test_more_than_the_bill_has_left_is_refused(self):
        res = drs.save_purchase_return(self.conn, self.body(16))
        self.assertFalse(res["ok"])
        self.assertIn("Cannot return more than 15", res["error"])
        self.enqueue.assert_not_called()

    def test_what_is_left_goes_back(self):
        self.assertTrue(drs.save_purchase_return(self.conn, self.body(15))["ok"])
        self.enqueue.assert_called_once()

    def test_a_bill_that_cannot_be_read_is_not_zero(self):
        with mock.patch("core.server_crud.get_doc", return_value=None):
            res = drs.save_purchase_return(self.conn, self.body(1))
        self.assertFalse(res["ok"])
        self.assertIn("Could not read this bill", res["error"])
        self.enqueue.assert_not_called()

    def test_the_alert_prefill_offers_the_shelf_in_strips(self):
        pre = drs.bulk_purchase_prefill(self.conn)
        self.assertEqual(ap._items(pre, "B-77"), [(13, "X1", 5.0)])


class OfflineAPurchaseReturnIsCheckedAtSave(unittest.TestCase):
    def setUp(self):
        ap.OfflineTheButtonsReadTheDatabase.setUp(self)
        for p in (
            mock.patch("core.sync_prefs.is_online_mode", return_value=False),
            mock.patch.object(drs, "_guard_mutate", return_value=None),
        ):
            p.start()
            self.addCleanup(p.stop)

    def body(self, qty):
        return {"purchase_id": 77, "supplier_id": 4,
                "items": [{"medicine_id": 13, "qty": qty, "rate": 7.5, "type": "Tablet",
                           "unit": "10", "is_tablet": True, "tablets_per_stripe": 10}]}

    def stock(self):
        return self.conn.execute("SELECT stock_qty FROM medicines WHERE id=13").fetchone()[0]

    def test_a_stale_screen_cannot_send_the_same_strips_twice(self):
        self.assertTrue(drs.save_purchase_return(self.conn, self.body(5))["ok"])
        res = drs.save_purchase_return(self.conn, self.body(15))
        self.assertFalse(res["ok"])
        self.assertIn("Cannot return more than 10", res["error"])

    def test_the_expired_prefill_sends_back_the_shelf_and_no_more(self):
        pre = drs.bulk_purchase_prefill(self.conn)
        self.assertEqual(ap._items(pre, "B-77"), [(13, "X1", 5.0)])
        self.assertEqual(ap._items(pre, "B-88"), [(14, "N1", 3.0)])
        groups = [g for g in pre["purchase_groups"] if g["bill_number"] == "B-77"]
        res = drs.bulk_purchase_save(self.conn, {"purchase_groups": groups, "writeoff_lines": []})
        self.assertEqual(res.get("errors"), [])
        # 5 strips x 7.50, and the 50 tablets are gone -- not -100 on the shelf.
        self.assertEqual([s["refund_amount"] for s in res["saved"]], [37.5])
        self.assertEqual(self.stock(), 0)

    def test_the_bulk_save_refuses_more_than_the_bill_allows(self):
        grp = {"purchase_id": 77, "supplier_id": 4, "bill_number": "B-77",
               "items": self.body(16)["items"]}
        res = drs.bulk_purchase_save(self.conn, {"purchase_groups": [grp], "writeoff_lines": []})
        self.assertEqual(res.get("saved"), [])
        self.assertIn("Cannot return more than 15", " ".join(res.get("errors") or []))
        self.assertEqual(self.stock(), 50)


class ARefusedBillInABulkSaveIsSaidOutLoud(unittest.TestCase):
    """bulk_purchase_save answers ok with the saved bills and an `errors` list
    for the refused ones; Returns -> bulk showed only "Saved: ..." and cleared
    the list, so a refused bill read as sent back."""

    def test_the_bulk_tab_shows_the_errors(self):
        with open(os.path.join(DESKTOP, "src", "pages", "ReturnsPage.tsx"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("const res = await saveBulkPurchaseReturn(")
        handler = src[i:src.index("setBulkData(null)", i)]
        self.assertIn("res.errors", handler)
        self.assertIn("Not saved:", handler)


class TheOverallDiscountPercentIsClassicsRupees(unittest.TestCase):
    """billing_form._on_disc_pct_change: round(subtotal * pct / 100, 2). The
    Tauri field used Math.round(x * 100) / 100, a paisa off on 42,501 of 720,000
    subtotal/percent pairs -- and the engine bills discount_rs, not the %."""

    CASES = [(1, 1.5), (1, 4.5), (1, 12.5), (490.25, 5), (1234.56, 2.5), (99.99, 12.5),
             (0.3, 50), (2999.95, 19.5)]

    def test_the_sales_page_uses_it(self):
        with open(os.path.join(DESKTOP, "src", "pages", "SalesPage.tsx"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("overallDiscFromPct(subtotal, pct)", src)
        self.assertNotIn("Math.round(((subtotal * pct) / 100) * 100) / 100", src)

    def test_the_paisa_matches_classic(self):
        node = shutil.which("node")
        esbuild = os.path.join(DESKTOP, "node_modules", ".bin", "esbuild")
        if not node or not os.path.exists(esbuild):
            raise unittest.SkipTest("node and desktop/node_modules are needed")
        with tempfile.TemporaryDirectory() as tmp:
            bundle = os.path.join(tmp, "m.mjs")
            subprocess.run([esbuild, "src/salesMargin.ts", "--bundle", "--format=esm",
                            f"--outfile={bundle}", "--log-level=error"],
                           cwd=DESKTOP, check=True, timeout=60)
            runner = os.path.join(tmp, "run.mjs")
            with open(runner, "w", encoding="utf-8") as f:
                f.write("import { overallDiscFromPct } from './m.mjs'\n"
                        f"console.log(JSON.stringify({json.dumps(self.CASES)}"
                        ".map(([s, p]) => overallDiscFromPct(s, p))))\n")
            out = subprocess.run([node, runner], capture_output=True, text=True,
                                 check=True, timeout=60).stdout
        self.assertEqual(json.loads(out), [round(s * p / 100, 2) for s, p in self.CASES])


if __name__ == "__main__":
    unittest.main()
