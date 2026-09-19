"""The alert popup's Return and Reorder buttons, end to end, in both modes.

StartupAlertsDialog.tsx runs 'bulk_return' / 'bulk_reorder' and, per row,
'row_action' through /api/startup/alerts/action (desktop_startup_service); the
Settings -> Alert & Monitoring panel runs the same handlers through
desktop_alert_service. Each answers a `navigate` the shell follows: Returns ->
bulk tab with the prefill, or Settings -> Reorder -> New, whose panel then asks
reorder_action('bulk_tabs') for the supplier tabs.

Online the engine's connection is an empty :memory: shell. The popup's own rows
got an Online branch; the actions behind its buttons did not:
  * Reorder by Supplier answered "No low or out-of-stock medicines to reorder."
    -- its candidates were SQL -- and the tabs it would open were SQL too;
  * Return on a row found no batch (SQL) and fell through to a write-off form
    with a quantity of 0;
  * Return by Purchase built its list without the bill's earlier returns, so it
    offered the whole shelf against a bill that had already gone back, and its
    failures came back without an "empty" flag, onto a blank screen.
"""
import os
import sqlite3
import sys
import unittest
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db_setup  # noqa: E402
from core import desktop_alert_service as das  # noqa: E402
from core import desktop_returns_service as drs  # noqa: E402
from core import desktop_settings_service as dset  # noqa: E402
from core import desktop_startup_service as dss  # noqa: E402
from tests.test_returns_list_is_read_to_the_end import FakeReturnsList, busy_shop  # noqa: E402

TODAY = date.today()


def d(n: int) -> str:
    """An ISO date n days out that is not the 1st (the 1st reads as month-only)."""
    x = TODAY + timedelta(days=n)
    if x.day == 1:
        x += timedelta(days=1)
    return x.isoformat()


MEDS = [
    dict(id=11, name="ZZ LOWMED", type="Tablet", batch_no="L1", expiry_date=d(400),
         stock_qty=3, unit="10", mrp=10, rate=8, is_hidden=0),
    dict(id=12, name="ZZ GONE", type="Syrup", batch_no="G1", expiry_date=d(400),
         stock_qty=0, unit="100", mrp=50, rate=40, is_hidden=0),
    dict(id=13, name="ZZ EXPMED", type="Tablet", batch_no="X1", expiry_date=d(-40),
         stock_qty=50, unit="10", mrp=10, rate=8, is_hidden=0),
    dict(id=14, name="ZZ NEARMED", type="Tablet", batch_no="N1", expiry_date=d(20),
         stock_qty=30, unit="10", mrp=10, rate=8, is_hidden=0),
    dict(id=15, name="ZZ NOBILL", type="Tablet", batch_no="W1", expiry_date=d(-10),
         stock_qty=4, unit="10", mrp=10, rate=8, is_hidden=0),
    dict(id=16, name="ZZ FINE", type="Tablet", batch_no="F1", expiry_date=d(500),
         stock_qty=500, unit="10", mrp=10, rate=8, is_hidden=0),
]
BILLS = {
    77: dict(id=77, bill_number="B-77", purchase_date=d(-500), supplier_id=4,
             supplier_name="ZZ SUPPLIER ONE",
             items=[dict(medicine_id=13, medicine_name="ZZ EXPMED", batch_no="X1", qty=60,
                         rate=7.5, amount=450, type="Tablet", unit="10")]),
    88: dict(id=88, bill_number="B-88", purchase_date=d(-100), supplier_id=5,
             supplier_name="ZZ SUPPLIER TWO",
             items=[dict(medicine_id=14, medicine_name="ZZ NEARMED", batch_no="N1", qty=40,
                         rate=6.0, amount=240, type="Tablet", unit="10")]),
}
PINFO = {
    13: dict(purchase_id=77, bill_number="B-77", purchase_date=d(-500), supplier_id=4,
             supplier_name="ZZ SUPPLIER ONE", qty=60, rate=7.5),
    14: dict(purchase_id=88, bill_number="B-88", purchase_date=d(-100), supplier_id=5,
             supplier_name="ZZ SUPPLIER TWO", qty=40, rate=6.0),
}
# Of the 60 on B-77, 20 + 25 already went back -- long ago, so on a shop with
# 1,500 later returns they sit past row 500 of the store's returns list.
OLD_RETURNS_77 = [(-450, 20), (-420, 25)]


def _items(pre, bill):
    for g in pre["purchase_groups"]:
        if g["bill_number"] == bill:
            return [(i["medicine_id"], i["batch"], i["qty"]) for i in g["items"]]
    return None


class OnlineTheButtonsReadTheStore(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        db_setup.initialise(self.conn)
        rows, ret_docs = busy_shop("purchase_id", 77, OLD_RETURNS_77, medicine_id=13)
        self.server = FakeReturnsList(rows)

        def get_doc(col, lid):
            if col == "purchases":
                b = BILLS.get(int(lid))
                return dict(b) if b else None
            if col == "purchase_returns":
                return ret_docs.get(int(lid))
            return None

        def by_name(name):
            key = (name or "").strip().lower()
            return [dict(m) for m in MEDS if m["name"].lower() == key]

        self.patches = {
            "online": mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            "meds": mock.patch("core.online_catalog.medicines",
                               return_value=[dict(m) for m in MEDS]),
            "by_id": mock.patch("core.online_catalog.medicine_by_id", side_effect=lambda mid: next(
                (dict(m) for m in MEDS if m["id"] == int(mid)), None)),
            "by_name": mock.patch("core.online_catalog.medicines_for_name", side_effect=by_name),
            "supplier": mock.patch("core.online_catalog.find_supplier_by_name", return_value=None),
            "bill": mock.patch("core.stock_disposal_service.lookup_batch_purchase_online",
                               side_effect=lambda mid, name="", batch="": PINFO.get(int(mid))),
            "doc": mock.patch("core.server_crud.get_doc", side_effect=get_doc),
            "returns": mock.patch("core.store_query_client.list_purchase_returns",
                                  side_effect=self.server),
            "queued": mock.patch.object(drs, "_pending_returned_qty", return_value=0.0),
        }
        for p in self.patches.values():
            p.start()
            self.addCleanup(p.stop)

    # -- Return ------------------------------------------------------------

    def test_return_by_purchase_opens_each_bill_with_what_it_still_allows(self):
        res = dss.startup_alert_action(self.conn, {"action": "bulk_return",
                                                   "title": "Expired Medicines"})
        self.assertTrue(res["ok"], res)
        nav = res["navigate"]
        self.assertEqual((nav["page"], nav["returnsTab"]), ("returns", "bulk"))
        pre = nav["returnsBulkPrefill"]
        self.assertTrue(pre["ok"])
        self.assertFalse(pre["empty"])
        self.assertEqual([g["bill_number"] for g in pre["purchase_groups"]], ["B-77", "B-88"])
        # 60 strips bought, 45 already back (past row 500): 15 may go back, but
        # the shelf holds 50 tablets = 5 strips of 10, so 5 -- not 15, not 50.
        # B-88: 30 tablets on the shelf = 3 strips (the bill still allows 40).
        self.assertEqual(_items(pre, "B-77"), [(13, "X1", 5.0)])
        self.assertEqual(_items(pre, "B-88"), [(14, "N1", 3.0)])
        self.assertEqual([w["medicine_name"] for w in pre["writeoff_lines"]], ["ZZ NOBILL"])
        # The group carries what the save needs.
        g77 = pre["purchase_groups"][0]
        self.assertEqual((g77["purchase_id"], g77["supplier_id"]), (77, 4))
        self.assertEqual(g77["items"][0]["rate"], 7.5)

    def test_one_returns_listing_serves_every_bill(self):
        dss.startup_alert_action(self.conn, {"action": "bulk_return"})
        self.assertEqual(len(self.server.calls), 2)  # 500, then one 5000 back to the end

    def test_the_settings_panel_button_is_the_same_action(self):
        res = das.alert_action(self.conn, {"action": "bulk_return"})
        self.assertEqual(_items(res["navigate"]["returnsBulkPrefill"], "B-77"),
                         [(13, "X1", 5.0)])

    def test_return_on_one_popup_row_opens_that_batch(self):
        res = dss.startup_alert_action(self.conn, {
            "action": "row_action", "title": "Expired Medicines", "action_kind": "return",
            "values": ["ZZ EXPMED", "Tablet", "X1", "", 40]})
        self.assertTrue(res["ok"], res)
        pre = res["navigate"]["returnsBulkPrefill"]
        self.assertEqual([g["bill_number"] for g in pre["purchase_groups"]], ["B-77"])
        self.assertEqual(_items(pre, "B-77"), [(13, "X1", 5.0)])
        self.assertEqual(pre["writeoff_lines"], [])

    def test_return_on_a_settings_near_expiry_row(self):
        res = das.alert_action(self.conn, {
            "action": "row_action", "section": "near_expiry",
            "values": ["ZZ NEARMED", "N1", "", 20, 30, "", ""]})
        self.assertTrue(res["ok"], res)
        self.assertEqual(_items(res["navigate"]["returnsBulkPrefill"], "B-88"),
                         [(14, "N1", 3.0)])

    def test_a_batch_with_no_bill_goes_to_the_write_off_list(self):
        res = dss.startup_alert_action(self.conn, {
            "action": "row_action", "title": "Expired Medicines", "action_kind": "return",
            "values": ["ZZ NOBILL", "Tablet", "W1", "", 10]})
        pre = res["navigate"]["returnsBulkPrefill"]
        self.assertEqual(pre["purchase_groups"], [])
        self.assertEqual([(w["medicine_id"], w["quantity"]) for w in pre["writeoff_lines"]],
                         [(15, 4.0)])

    def test_a_row_the_shelf_no_longer_has_opens_the_write_off_form_with_its_stock(self):
        res = dss.startup_alert_action(self.conn, {
            "action": "row_action", "title": "Expired Medicines", "action_kind": "return",
            "values": ["ZZ FINE", "Tablet", "F1", "", 0]})
        nav = res["navigate"]
        self.assertEqual(nav["returnsTab"], "disposal")
        self.assertEqual(nav["disposalPrefill"]["available_qty"], 500.0)

    def test_an_unreadable_returns_list_is_an_error_not_an_uncapped_bill(self):
        self.patches["returns"].stop()
        with mock.patch("core.store_query_client.list_purchase_returns",
                        side_effect=RuntimeError("timed out")):
            res = dss.startup_alert_action(self.conn, {"action": "bulk_return"})
        self.patches["returns"].start()
        self.assertFalse(res["ok"])
        self.assertIn("could not be read", res["error"])

    # -- Reorder -----------------------------------------------------------

    def test_reorder_by_supplier_opens_the_low_and_out_of_stock_items(self):
        res = dss.startup_alert_action(self.conn, {"action": "bulk_reorder",
                                                   "title": "Low Stock Alerts"})
        self.assertTrue(res["ok"], res)
        nav = res["navigate"]
        self.assertEqual((nav["page"], nav["settingsTab"], nav["settingsToggle"]),
                         ("settings", "reorder", "new"))
        self.assertTrue(nav["bulkReorderLoad"])
        tabs = dset.reorder_action(self.conn, {"action": "bulk_tabs"})
        self.assertTrue(tabs["ok"], tabs)
        lines = {ln["medicine_name"]: ln for t in tabs["tabs"] for ln in t["lines"]}
        self.assertEqual(sorted(lines), ["ZZ GONE", "ZZ LOWMED", "ZZ NOBILL"])
        self.assertEqual(lines["ZZ LOWMED"]["unit_price"], 8.0)
        self.assertEqual(lines["ZZ GONE"]["unit_price"], 40.0)
        self.assertGreater(lines["ZZ LOWMED"]["quantity"], 0)

    def test_reorder_on_one_popup_row(self):
        res = dss.startup_alert_action(self.conn, {
            "action": "row_action", "title": "Low Stock Alerts", "action_kind": "reorder",
            "values": ["ZZ LOWMED", "Tablet", "L1", 3, "10"]})
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["navigate"]["reorderMedicinePrefill"]["medicine_name"], "ZZ LOWMED")

    def test_an_unreadable_store_is_an_error_not_an_empty_shelf(self):
        self.patches["meds"].stop()
        with mock.patch("core.online_catalog.medicines", return_value=[]), \
                mock.patch("core.online_catalog.last_error", return_value="timed out"):
            ret = dss.startup_alert_action(self.conn, {"action": "bulk_return"})
            reo = dss.startup_alert_action(self.conn, {"action": "bulk_reorder"})
        self.patches["meds"].start()
        self.assertFalse(ret["ok"])
        self.assertIn("Could not read the store", ret["error"])
        self.assertFalse(reo["ok"])
        self.assertIn("Could not read the store", reo["error"])


def _ins(conn, table, **vals):
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    vals = {k: v for k, v in vals.items() if k in cols}
    conn.execute(
        f"INSERT INTO {table} ({', '.join(vals)}) VALUES ({', '.join('?' * len(vals))})",
        tuple(vals.values()),
    )


class OfflineTheButtonsReadTheDatabase(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        db_setup.initialise(self.conn)
        drs._ensure_return_tables(self.conn)
        c = self.conn
        for m in MEDS:
            _ins(c, "medicines", **m)
        _ins(c, "suppliers", id=4, name="ZZ SUPPLIER ONE")
        _ins(c, "suppliers", id=5, name="ZZ SUPPLIER TWO")
        for pid, b in BILLS.items():
            _ins(c, "purchases", id=pid, bill_number=b["bill_number"],
                 purchase_date=b["purchase_date"], supplier_id=b["supplier_id"])
            for it in b["items"]:
                _ins(c, "purchase_items", purchase_id=pid, medicine_id=it["medicine_id"],
                     batch_no=it["batch_no"], qty=it["qty"], rate=it["rate"],
                     amount=it["amount"])
        _ins(c, "purchase_returns", id=1, return_no="PR-1", purchase_id=77, supplier_id=4,
             return_date=d(-450))
        _ins(c, "purchase_return_items", return_id=1, medicine_id=13, qty=45, rate=7.5,
             amount=337.5)
        c.commit()

    def test_return_by_purchase(self):
        res = dss.startup_alert_action(self.conn, {"action": "bulk_return"})
        self.assertTrue(res["ok"], res)
        pre = res["navigate"]["returnsBulkPrefill"]
        self.assertEqual([g["bill_number"] for g in pre["purchase_groups"]], ["B-77", "B-88"])
        self.assertEqual(_items(pre, "B-77"), [(13, "X1", 5.0)])
        self.assertEqual(_items(pre, "B-88"), [(14, "N1", 3.0)])
        self.assertEqual([w["medicine_name"] for w in pre["writeoff_lines"]], ["ZZ NOBILL"])

    def test_return_on_one_popup_row(self):
        res = dss.startup_alert_action(self.conn, {
            "action": "row_action", "title": "Expired Medicines", "action_kind": "return",
            "values": ["ZZ EXPMED", "Tablet", "X1", "", 40]})
        self.assertEqual(_items(res["navigate"]["returnsBulkPrefill"], "B-77"),
                         [(13, "X1", 5.0)])

    def test_reorder_by_supplier(self):
        res = dss.startup_alert_action(self.conn, {"action": "bulk_reorder"})
        self.assertTrue(res["ok"], res)
        self.assertTrue(res["navigate"]["bulkReorderLoad"])
        tabs = dset.reorder_action(self.conn, {"action": "bulk_tabs"})
        names = sorted(ln["medicine_name"] for t in tabs["tabs"] for ln in t["lines"])
        self.assertEqual(names, ["ZZ GONE", "ZZ LOWMED", "ZZ NOBILL"])


if __name__ == "__main__":
    unittest.main()
