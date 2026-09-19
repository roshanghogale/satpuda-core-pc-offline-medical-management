"""The owner's four decisions of 2026-09-11, pinned.

1. Strips with their tablet count: fractional strips are fine (the exact
   shelf-to-bill conversion, never rounded down), one whole tablet is the
   smallest unit, and every return screen shows the tablets next to the strips.
2. One medicine is one line: Sales merges the same medicine id, Purchase merges
   the same name AND batch. Old bills that carry a medicine twice are not
   rewritten; the return screens show them as ONE line (total qty, returnable =
   total less returned, refund at the amount-weighted average rate).
3. Edit after a return: the edit screen shows the returns, and no line may go
   below what was returned (or be removed) -- refused on screen and at save.
4. Online reorder groups by supplier like Classic: the newest purchase that
   carries the medicine and names a supplier, read from what the server serves.

No network, no saves: every server read is patched.
"""
import io
import os
import sqlite3
import unittest
from unittest import mock

from core import desktop_returns_service as drs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = ("desktop", "src", "pages")


def src(*parts):
    with io.open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _returns_db(kind="sale"):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE medicines (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO medicines VALUES (7, 'PARA 500')")
    if kind == "sale":
        conn.execute("CREATE TABLE sales_returns (id INTEGER PRIMARY KEY, sale_id INT, deleted INT)")
        conn.execute("CREATE TABLE sales_return_items (return_id INT, medicine_id INT, qty REAL)")
        conn.executemany("INSERT INTO sales_returns VALUES (?,?,?)", [(1, 1, 0), (2, 1, 1)])
        conn.executemany(
            "INSERT INTO sales_return_items VALUES (?,?,?)", [(1, 7, 3.0), (2, 7, 50.0)]
        )
    else:
        conn.execute(
            "CREATE TABLE purchase_returns (id INTEGER PRIMARY KEY, purchase_id INT, deleted INT)"
        )
        conn.execute("CREATE TABLE purchase_return_items (return_id INT, medicine_id INT, qty REAL)")
        conn.execute("INSERT INTO purchase_returns VALUES (1, 4, 0)")
        conn.execute("INSERT INTO purchase_return_items VALUES (1, 7, 1.5)")
    return conn


class StripsWithTheirTablets(unittest.TestCase):
    def test_the_shelf_conversion_keeps_fractional_strips(self):
        line = {"is_tablet": True, "tablets_per_stripe": 10}
        self.assertEqual(drs._shelf_in_bill_units(25, line), 2.5)

    def test_a_part_tablet_is_refused_a_fractional_strip_is_not(self):
        self.assertEqual(
            drs._part_tablet_error("PARA", 2.5, is_tablet=True, tps=10, in_tablets=False), ""
        )
        why = drs._part_tablet_error("PARA", 2.55, is_tablet=True, tps=10, in_tablets=False)
        self.assertIn("2.55 strips = 25.5 tablets", why)
        self.assertIn("whole tablets", why)
        self.assertIn("whole tablets", drs._part_tablet_error(
            "PARA", 2.5, is_tablet=True, tps=1, in_tablets=True))

    def test_both_purchase_return_saves_and_the_sales_check_refuse_a_part_tablet(self):
        text = src("core", "desktop_returns_service.py")
        self.assertGreaterEqual(text.count("part = _part_tablet_error("), 3)

    def test_the_tablet_note_says_both(self):
        tc = src(*PAGES, "tabletCount.ts")
        self.assertIn("'strips'} = ${fmt(q * t)} tablets", tc)
        self.assertIn("tablets = ${fmt(q / t)} strips", tc)
        self.assertNotIn("Math.floor(q", tc)  # never rounds a strip count down

    def test_every_return_screen_shows_the_tablets(self):
        for page, want in (
            ("ExpiredReturnDialog.tsx", 2),
            ("ReturnsPage.tsx", 4),
            ("SalesReturnDialog.tsx", 3),
        ):
            text = src(*PAGES, page)
            self.assertIn("from './tabletCount'", text, page)
            self.assertGreaterEqual(text.count("tabletCountNote("), want, page)
        self.assertIn("partTabletProblem(r.name, q", src(*PAGES, "ExpiredReturnDialog.tsx"))
        self.assertIn("partTabletProblem(item.name, qty, item.is_tablet",
                      src(*PAGES, "ReturnsPage.tsx"))
        self.assertIn("'tablet')", src(*PAGES, "salesReturnLogic.ts"))


class OldBillsWithRepeatedLines(unittest.TestCase):
    DOC = {
        "bill_no": "S-9",
        "bill_date": "2026-09-01",
        "customer_id": 0,
        "items": [
            {"medicine_id": 7, "medicine_name": "PARA 500", "qty": 5, "rate": 10, "amount": 50},
            {"medicine_id": 7, "medicine_name": "PARA 500", "qty": 5, "rate": 10, "amount": 40},
        ],
    }

    def test_the_sales_return_screen_shows_one_line(self):
        with mock.patch("core.sync_prefs.is_online_mode", return_value=True), \
                mock.patch("core.server_crud.get_doc", return_value=dict(self.DOC)), \
                mock.patch("core.online_catalog.medicine_by_id",
                           return_value={"type": "Tablet", "unit": "10"}), \
                mock.patch("core.online_catalog.find_customer_by_id", return_value={}), \
                mock.patch("core.customer_service.get_customer_due", return_value=(0, 0)), \
                mock.patch.object(drs, "_bill_returned_map", return_value={7: 3.0}), \
                mock.patch.object(drs, "_pending_returned_qty", return_value=0.0):
            out = drs.load_sales_bill_for_return(None, 9)
        self.assertTrue(out["ok"], out)
        self.assertEqual(len(out["items"]), 1)
        it = out["items"][0]
        self.assertEqual(it["orig_qty"], 10)
        self.assertEqual(it["remaining_qty"], 7)  # was 2 + 2 before
        self.assertEqual(it["rate"], 10.0)  # the bill rate, as one line's
        self.assertEqual(it["amount"], 90)
        # The refund is amount-weighted: 3 back of 10 that cost 90 in all is 27,
        # the same on Android (ReturnLines.salesEffectiveRate).
        from core.calc_engine import calc_return_refund
        refund = calc_return_refund(
            [{"qty": 3, "rate": it["rate"], "orig_qty": it["orig_qty"], "amount": it["amount"]}]
        )["refund_amount"]
        self.assertEqual(refund, 27.0)
        self.assertEqual(it["tablets_per_stripe"], 10)
        self.assertTrue(it["is_tablet"])

    def test_the_purchase_return_screen_shows_one_line(self):
        doc = {"items": [
            {"medicine_id": 7, "name": "PARA", "qty": 2, "rate": 30, "amount": 60, "type": "Tablet", "unit": "10"},
            {"medicine_id": 7, "name": "PARA", "qty": 1, "rate": 36, "amount": 36, "type": "Tablet", "unit": "10"},
            {"medicine_id": 8, "name": "COUGH", "qty": 4, "rate": 50, "amount": 200, "type": "Syrup"},
        ]}
        with mock.patch("core.online_catalog.medicine_by_id", return_value={}), \
                mock.patch.object(drs, "_pending_returned_qty", return_value=0.0):
            items = drs._online_purchase_return_items(4, doc, {7: 1.5})
        self.assertEqual([i["medicine_id"] for i in items], [7, 8])
        para = items[0]
        self.assertEqual(para["orig_qty"], 3)
        self.assertEqual(para["remaining_qty"], 1.5)
        self.assertEqual(para["rate"], 32.0)
        self.assertEqual(para["merged_lines"], 2)
        self.assertNotIn("merged_lines", items[1])

    def test_a_merged_purchase_line_is_credited_at_the_bill_rate(self):
        # A purchase refund is qty x rate. A stored amount carrying GST must not
        # lift the merged rate: 2 @ 30 + 1 @ 36 with 5% GST in the amounts is
        # still 32 a strip, as two separate lines would credit (and Android does).
        doc = {"items": [
            {"medicine_id": 7, "name": "PARA", "qty": 2, "rate": 30, "amount": 63, "type": "Tablet", "unit": "10"},
            {"medicine_id": 7, "name": "PARA", "qty": 1, "rate": 36, "amount": 37.8, "type": "Tablet", "unit": "10"},
        ]}
        with mock.patch("core.online_catalog.medicine_by_id", return_value={}), \
                mock.patch.object(drs, "_pending_returned_qty", return_value=0.0):
            items = drs._online_purchase_return_items(4, doc, {})
        self.assertEqual(items[0]["rate"], 32.0)
        self.assertEqual(items[0]["remaining_qty"], 3)


class OneMedicineOneLine(unittest.TestCase):
    def test_a_sales_edit_that_lands_on_another_line_adds_to_it(self):
        text = src(*PAGES, "SalesPage.tsx")
        self.assertIn("const merged = combineSalesLines(existing, line)", text)
        self.assertIn("j === dupAt ? [combineSalesLines(m, line)] : [m]", text)

    def test_purchase_merges_same_name_and_batch_only(self):
        rules = src(*PAGES, "billLineRules.ts")
        self.assertIn("n(a) === n(b) && bt(a) === bt(b)", rules)
        page = src(*PAGES, "PurchasePage.tsx")
        self.assertIn("all.findIndex((m) => samePurchaseLine(m, line))", page)
        self.assertIn("i !== at && samePurchaseLine(m, edited)", page)
        self.assertNotIn("patchTab({ items: [...tabRef.current.items, line] })", page)


class EditAfterAReturn(unittest.TestCase):
    def test_returned_on_bill_reads_the_live_returns_only(self):
        conn = _returns_db()
        self.assertEqual(drs.returned_on_bill(conn, "sale", 1, online=False), {7: ("PARA 500", 3.0)})
        self.assertEqual(drs.returned_on_bill(conn, "sale", 2, online=False), {})

    def test_the_edit_cannot_cut_below_or_remove_a_returned_line(self):
        conn = _returns_db()
        with mock.patch("core.sync_prefs.is_online_mode", return_value=False):
            ok = drs.edit_below_returned_error(conn, "sale", 1, [{"id": 7, "qty": 3}], id_key="id")
            low = drs.edit_below_returned_error(conn, "sale", 1, [{"id": 7, "qty": 2}], id_key="id")
            gone = drs.edit_below_returned_error(conn, "sale", 1, [{"id": 8, "qty": 9}], id_key="id")
        self.assertEqual(ok, "")
        self.assertIn("cannot go below 3", low)
        self.assertIn("cannot be removed", gone)

    def test_the_purchase_edit_is_held_to_the_supplier_return(self):
        conn = _returns_db("purchase")
        with mock.patch("core.sync_prefs.is_online_mode", return_value=False):
            why = drs.edit_below_returned_error(
                conn, "purchase", 4, [{"medicine_id": 7, "qty": 1, "name": "PARA"}],
                id_key="medicine_id")
        self.assertIn("PARA: 1.5 already returned", why)

    def test_unreadable_returns_online_refuse_the_edit(self):
        with mock.patch("core.sync_prefs.is_online_mode", return_value=True), \
                mock.patch("core.server_crud.get_doc", return_value=None), \
                mock.patch("core.online_mutation_queue.pending_by_local_id", return_value=None):
            why = drs.edit_below_returned_error(None, "sale", 1, [{"id": 7, "qty": 9}], id_key="id")
        self.assertIn("Could not read the returns", why)

    def test_the_edit_screen_is_told(self):
        conn = _returns_db()
        lines = [{"id": 7, "name": "PARA 500", "qty": 10}]
        with mock.patch("core.sync_prefs.is_online_mode", return_value=False):
            info = drs.edit_returns_info(conn, "sale", 1, lines, id_key="id")
        self.assertEqual(lines[0]["returned_qty"], 3.0)
        self.assertEqual(info["returned_by_medicine"], {"7": 3.0})
        self.assertIn("PARA 500 3", info["returns_note"])

    def test_save_sale_refuses_before_writing(self):
        from core import desktop_sales_service as dss

        conn = _returns_db()
        body = {"editing_sale_id": 1, "items": [{"id": 7, "name": "PARA 500", "qty": 1, "rate": 2}]}
        with mock.patch("core.sync_prefs.is_online_mode", return_value=False), \
                mock.patch.object(dss, "_has_pharmacy_profile", return_value=True):
            out = dss.save_sale(conn, body)
        self.assertEqual(out.get("code"), "below_returned", out)

    def test_save_purchase_refuses_before_writing(self):
        from core import desktop_purchase_service as dps

        conn = _returns_db("purchase")
        body = {"editing_purchase_id": 4, "supplier_name": "ALPHA", "items": [
            {"medicine_id": 7, "name": "PARA", "qty": 1, "rate": 30, "batch": "B1", "type": "Tablet"}]}
        with mock.patch("core.sync_prefs.is_online_mode", return_value=False):
            out = dps.save_purchase_bill(conn, body)
        self.assertEqual(out.get("code"), "below_returned", out)

    def test_both_loads_carry_the_returns(self):
        # the offline load and the online load, each for a saved (not autosaved) bill
        for module in ("desktop_sales_service.py", "desktop_purchase_service.py"):
            self.assertEqual(
                src("core", module).count("if is_autosave else _returns_on_edit(conn,"), 2, module
            )

    def test_both_edit_screens_show_and_hold_it(self):
        for page, guard in (("SalesPage.tsx", "returnsBlock(next)"),
                            ("PurchasePage.tsx", "purchaseReturnsBlock(next)")):
            text = src(*PAGES, page)
            self.assertIn("data-returns-note", text, page)
            self.assertIn("returned</div>".replace("</div>", ""), text, page)
            self.assertIn("returnedByMed: loaded.returned_by_medicine || {}", text, page)
            self.assertGreaterEqual(text.count(guard), 2, page)
        sales = src(*PAGES, "SalesPage.tsx")
        self.assertNotIn("items: tab.items.filter((_, j) => j !== i)", sales)
        self.assertNotIn("items: tab.items.filter((_, j) => j !== idx)", sales)


class OnlineReorderBySupplier(unittest.TestCase):
    MEDS = [
        {"id": 1, "name": "PARA", "unit": "10", "stock_qty": 5, "type": "Tablet", "rate": 2},
        {"id": 2, "name": "PARA", "unit": "10", "stock_qty": 8, "type": "Tablet", "rate": 2},
        {"id": 3, "name": "COUGH", "unit": "100ml", "stock_qty": 0, "type": "Syrup", "rate": 40},
    ]
    SECTIONS = {
        "low_stock": [("PARA", 13, "10", "")],
        "out_of_stock": [("COUGH", "100ml", 50.0, 40.0, "Syrup", "")],
        "expired": [],
        "near_expiry": [],
    }
    PURCHASES = [
        {"id": 10, "purchase_date": "2026-08-01", "supplier_id": 1, "items": [{"medicine_id": 2}]},
        {"id": 11, "purchase_date": "2026-09-01", "supplier_id": 2, "items": [{"medicine_id": 1}]},
        {"id": 12, "purchase_date": "2026-09-02", "supplier_id": 0, "items": [{"medicine_id": 2}]},
        {"id": 13, "purchase_date": "2026-07-01", "supplier_id": 3, "items": [{"medicine_id": 3}]},
    ]
    NAMES = {1: "ALPHA", 2: "BETA", 3: "GAMMA"}

    def _run(self, fn):
        with mock.patch("core.sync_prefs.is_online_mode", return_value=True), \
                mock.patch("core.alert_monitoring_service.online_visible_medicines",
                           return_value=list(self.MEDS)), \
                mock.patch("core.alert_monitoring_service.online_stock_sections",
                           return_value=self.SECTIONS), \
                mock.patch("core.online_catalog._pull_sync_pages",
                           return_value=[dict(p) for p in self.PURCHASES]), \
                mock.patch("core.online_catalog.find_supplier_by_id",
                           side_effect=lambda sid: {"name": self.NAMES.get(int(sid), "")}), \
                mock.patch("core.online_catalog.find_supplier_by_name",
                           side_effect=lambda n, **k: {"id": 1, "name": n}), \
                mock.patch("core.reorder_service.suggest_order_quantity", return_value=1):
            return fn(sqlite3.connect(":memory:"))

    def test_each_item_gets_its_classic_supplier(self):
        from core.reorder_service import collect_reorder_candidates

        items = self._run(collect_reorder_candidates)
        got = {i["medicine_name"]: i["supplier_name"] for i in items}
        # PARA: the row with the most stock is id 2; its newest purchase that
        # names a supplier is #10 (#12 names none, #11 is the other row).
        self.assertEqual(got, {"PARA": "ALPHA", "COUGH": "GAMMA"})

    def test_the_reorder_tabs_are_by_supplier(self):
        from core.desktop_settings_service import _reorder_bulk_tabs

        tabs = self._run(_reorder_bulk_tabs)
        self.assertEqual([t["label"] for t in tabs], ["ALPHA", "GAMMA"])
        self.assertFalse(any(t["offline"] for t in tabs))


if __name__ == "__main__":
    unittest.main()
