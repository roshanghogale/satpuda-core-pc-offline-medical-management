"""Reorder on offline-first, every step, and the same steps Online for parity.

Owner (25 Oct 2026, v1.0.11): "the reorder is not working" in offline-first; it worked in plain
Online / Offline. On the PC's own copy every step ran -- candidates, supplier tabs, save, edit,
Open Purchase, Mark Received -- but the orders never travelled:

  * the pull handed pending_orders (and stock_disposals, general_products) to sync_down_doc,
    which has no branch for them and answered "applied" without writing: an order saved on the
    phone or the other PC never reached this one;
  * the full copy (Go Offline / the offline-first switch / the store switch) wiped those three
    tables and listed only bills and masters: the shop's orders were gone after the switch;
  * a PC already on offline-first needs them fetched once (the worker's backfill);
  * the group name was MAX(id)+1 of the table: two devices on an empty table both made
    ROG<date>-0001, the pulled lines joined one group and editing it on one device deleted
    the other's lines.

Also here (the same audit of offline-first gaps):
  * the purchase just saved was read back as the newest purchase by id -- another device's
    purchase from a higher id range -- so its wholesale rates and returns landed on that bill;
  * villages / medicine types (dropdowns) and "Show shelf location" were never sent.

Runs on temp store files; no server is contacted.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from datetime import date
from unittest import mock

from core import db_setup
from core import desktop_purchase_service
from core import desktop_settings_service as dset
from core import reorder_service
from core.offline_first import ids, outbox, plain_docs, pull, schema, worker
from core.offline_first.schema import meta_get, meta_set

TODAY = date.today().strftime("%Y%m%d")


def make_store(device: int) -> str:
    fd, path = tempfile.mkstemp(suffix=".db", prefix=f"of_reorder_{device}_")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    db_setup.initialise(conn)
    conn.commit()
    schema.ensure_schema(conn)
    base = device * 1_000_000_000
    meta_set(conn, "device_no", device)
    meta_set(conn, "id_base", base)
    meta_set(conn, "id_max", base + 999_999_999)
    meta_set(conn, "install_id", f"pc-test-{device}")
    conn.commit()
    conn.close()
    return path


def till(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30, factory=ids.OfConnection)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def server_doc(conn, collection: str, local_id: int) -> dict:
    """What the server hands back for a record this PC sent (qty and quantity both ride)."""
    doc = dict(outbox.build_doc(conn, collection, local_id))
    if "quantity" in doc:
        doc["qty"] = doc["quantity"]
    doc["version"] = 1
    return doc


class _OfflineFirst(unittest.TestCase):
    DEVICE = 3

    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for patch in (
            mock.patch("core.sync_prefs.get_sync_mode", return_value="offline_first"),
            mock.patch("core.online_guard.ensure_can_mutate", return_value=None),
            mock.patch("core.desktop_returns_service.edit_below_returned_error", return_value=None),
        ):
            stack.enter_context(patch)
        self.paths: list[str] = []
        self.conn = self.open_store(self.DEVICE)
        self.seed(self.conn)

    def open_store(self, device: int) -> sqlite3.Connection:
        path = make_store(device)
        self.paths.append(path)
        conn = till(path)
        self.addCleanup(self._remove, conn, path)
        return conn

    @staticmethod
    def _remove(conn, path):
        try:
            conn.close()
        except Exception:
            pass
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except OSError:
                pass

    def seed(self, c):
        self.sup1 = c.execute("INSERT INTO suppliers (name, phone) VALUES ('ZZ AGENCY ONE','9')").lastrowid
        self.sup2 = c.execute("INSERT INTO suppliers (name, phone) VALUES ('ZZ AGENCY TWO','8')").lastrowid
        meds = {}
        for name, typ, stock, unit, rate in (("ZZ LOWMED", "Tablet", 2, "10", 20),
                                              ("ZZ GONE", "Syrup", 0, "100", 40),
                                              ("ZZ OTHER", "Tablet", 1, "10", 15),
                                              ("ZZ FULL", "Tablet", 900, "10", 5)):
            meds[name] = c.execute(
                "INSERT INTO medicines (name, type, stock_qty, unit, mrp, rate, batch_no, expiry_date) "
                "VALUES (?,?,?,?,?,?,?,?)", (name, typ, stock, unit, rate * 1.5, rate, "B1", "2030-01-31"),
            ).lastrowid
        p1 = c.execute("INSERT INTO purchases (bill_number, purchase_date, supplier_id, total_amount) "
                       "VALUES ('P1','2026-09-01',?,100)", (self.sup1,)).lastrowid
        p2 = c.execute("INSERT INTO purchases (bill_number, purchase_date, supplier_id, total_amount) "
                       "VALUES ('P2','2026-09-02',?,100)", (self.sup2,)).lastrowid
        for pid, name in ((p1, "ZZ LOWMED"), (p1, "ZZ GONE"), (p2, "ZZ OTHER"), (p1, "ZZ FULL")):
            c.execute("INSERT INTO purchase_items (purchase_id, medicine_id, qty, rate, amount) "
                      "VALUES (?,?,5,10,50)", (pid, meds[name]))
        c.commit()
        self.meds = meds

    def act(self, conn=None, **body):
        return dset.reorder_action(conn or self.conn, body)

    def save_tab(self, conn=None, tab=None, status="ordered"):
        tab = tab or self.act(conn, action="bulk_tabs")["tabs"][0]
        res = self.act(conn, action="save_supplier_draft", status=status,
                       header={"supplier_id": tab["supplier_id"],
                               "supplier_name_manual": tab["supplier_name"]},
                       lines=tab["lines"], group_id=tab.get("editing_group_id"))
        self.assertTrue(res.get("ok"), res)
        return res


class TheReorderStepsOnTheOfflineFirstCopy(_OfflineFirst):
    def test_candidates_are_the_low_and_out_of_stock_medicines_with_their_last_supplier(self):
        items = {i["medicine_name"]: i for i in self.act(action="candidates")["items"]}
        self.assertEqual(set(items), {"ZZ LOWMED", "ZZ GONE", "ZZ OTHER"})
        self.assertEqual(items["ZZ LOWMED"]["supplier_name"], "ZZ AGENCY ONE")
        self.assertEqual(items["ZZ OTHER"]["supplier_name"], "ZZ AGENCY TWO")
        self.assertEqual(items["ZZ GONE"]["current_stock"], 0)

    def test_supplier_tabs_group_by_the_last_supplier(self):
        tabs = {t["label"]: t for t in self.act(action="bulk_tabs")["tabs"]}
        self.assertEqual(set(tabs), {"ZZ AGENCY ONE", "ZZ AGENCY TWO"})
        self.assertEqual(tabs["ZZ AGENCY ONE"]["supplier_id"], self.sup1)
        self.assertEqual({ln["medicine_name"] for ln in tabs["ZZ AGENCY ONE"]["lines"]},
                         {"ZZ LOWMED", "ZZ GONE"})
        meds = self.act(action="supplier_medicines", supplier_id=self.sup1)["medicines"]
        self.assertIn("ZZ LOWMED", meds)
        line = self.act(action="build_line", supplier_id=self.sup1, medicine_name="ZZ LOWMED")["line"]
        self.assertEqual((line["pack_size"], line["current_stock"]), ("10", 2.0))

    def test_a_saved_order_is_in_this_devices_range_noted_for_the_server_and_listed(self):
        res = self.save_tab()
        ids_ = res["result"]["order_ids"]
        base = self.DEVICE * 1_000_000_000
        self.assertTrue(all(base < i < base + 1_000_000_000 for i in ids_), ids_)
        # The group is named after its first line's id: unique across devices.
        self.assertEqual(res["result"]["group_id"], f"ROG{TODAY}-{min(ids_)}")
        dirty = {i for c, i, _ in self.conn.execute("SELECT collection, local_id, op FROM of_dirty")
                 if c == "pending_orders"}
        self.assertEqual(dirty, set(ids_))
        doc = outbox.build_doc(self.conn, "pending_orders", ids_[0])
        self.assertEqual(doc["order_group_id"], res["result"]["group_id"])
        self.assertEqual(doc["status"], "ordered")
        self.assertTrue(str(doc["order_no"]).startswith(f"RO{self.DEVICE}-"), doc["order_no"])
        [group] = self.act(action="list")["groups"]
        self.assertEqual((group["supplier_name"], group["medicine_count"], group["status"]),
                         ("ZZ AGENCY ONE", 2, "ordered"))

    def test_edit_load_and_save_again_keeps_the_group(self):
        res = self.save_tab(status="draft")
        gid = res["result"]["group_id"]
        [tab] = self.act(action="load_group", group_id=gid)["tabs"]
        self.assertEqual(tab["editing_group_id"], gid)
        tab["lines"] = tab["lines"][:1]
        res2 = self.save_tab(tab=tab, status="ordered")
        self.assertEqual(res2["result"]["group_id"], gid)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM pending_orders WHERE order_group_id=?", (gid,)).fetchone()[0], 1)

    def test_open_purchase_and_its_bill_receive_the_order(self):
        res = self.save_tab()
        group = self.act(action="list")["groups"][0]
        prefill = self.act(action="purchase_prefill", order_id=group["first_id"])["prefill"]
        self.assertEqual(prefill["supplier_name"], "ZZ AGENCY ONE")
        self.assertIn(prefill["medicine_name"], ("ZZ LOWMED", "ZZ GONE"))
        # The Purchase screen saves the bill with the order id; the shop added the order's other
        # medicine to the same bill.
        lines = [_line(n) for n in ("ZZ LOWMED", "ZZ GONE")]
        body = {"supplier_name": prefill["supplier_name"], "purchase_date": "2026-10-25",
                "bill_number": "RX1", "items": lines, "cash_paid": 0, "online_paid": 0,
                "reorder_order_id": prefill["order_id"]}
        out = desktop_purchase_service.save_purchase_bill(self.conn, body)
        self.assertTrue(out.get("ok"), out)
        statuses = {r[0] for r in self.conn.execute(
            "SELECT status FROM pending_orders WHERE order_group_id=?", (res["result"]["group_id"],))}
        self.assertEqual(statuses, {"received"})
        self.assertEqual(self.act(action="list")["groups"][0]["status"], "received")

    def test_mark_received_and_cancel(self):
        a = self.save_tab()["result"]["group_id"]
        tab2 = [t for t in self.act(action="bulk_tabs")["tabs"] if t["label"] == "ZZ AGENCY TWO"][0]
        b = self.save_tab(tab=tab2)["result"]["group_id"]
        self.assertNotEqual(a, b)
        self.act(action="mark_received", group_id=a)
        self.act(action="cancel", group_id=b)
        groups = {g["group_id"]: g["status"] for g in self.act(action="list")["groups"]}
        self.assertEqual(groups, {a: "received"})       # a cancelled order leaves the list

    def test_the_alert_buttons_and_the_one_click_drafts(self):
        from core import desktop_alert_service as das

        nav = das._bulk_reorder(self.conn, {})
        self.assertTrue(nav.get("ok"), nav)
        res = self.act(action="create_grouped")
        self.assertEqual(res["result"]["groups"], {"ZZ AGENCY ONE": 2, "ZZ AGENCY TWO": 1})
        gids = {g["group_id"] for g in res["groups"]}
        self.assertEqual(len(gids), 2)


class OrdersTravelBetweenDevices(_OfflineFirst):
    def pull_into(self, conn, changes):
        conn.execute("UPDATE of_flags SET v='0' WHERE k='capture'")
        out = pull.apply_page(conn, changes, to_revision=99)
        conn.execute("UPDATE of_flags SET v='1' WHERE k='capture'")
        conn.commit()
        return out

    def test_an_order_from_another_device_is_pulled_and_listed(self):
        other = self.open_store(7)
        self.seed(other)
        res = self.save_tab(conn=other)
        changes = [{"collection": "pending_orders", "local_id": i, "operation": "upsert",
                    "doc": server_doc(other, "pending_orders", i)} for i in res["result"]["order_ids"]]
        self.pull_into(self.conn, changes)
        [group] = self.act(action="list")["groups"]
        self.assertEqual(group["group_id"], res["result"]["group_id"])
        self.assertEqual(group["medicine_count"], 2)
        # Pulled, not this PC's own change: nothing goes back up.
        self.assertFalse(self.conn.execute(
            "SELECT 1 FROM of_dirty WHERE collection='pending_orders'").fetchone())
        # Received on the other device: the status follows.
        for ch in changes:
            ch["doc"]["status"] = "received"
        self.pull_into(self.conn, changes)
        self.assertEqual(self.act(action="list")["groups"][0]["status"], "received")
        # Deleted there: gone here.
        self.pull_into(self.conn, [{"collection": "pending_orders", "local_id": changes[0]["local_id"],
                                    "operation": "delete", "doc": None}])
        self.assertEqual(self.act(action="list")["groups"][0]["medicine_count"], 1)

    def test_this_pcs_unsent_change_is_not_overwritten(self):
        oid = self.save_tab()["result"]["order_ids"][0]
        doc = server_doc(self.conn, "pending_orders", oid)
        doc["status"] = "cancelled"
        self.pull_into(self.conn, [{"collection": "pending_orders", "local_id": oid,
                                    "operation": "upsert", "doc": doc}])
        self.assertEqual(self.conn.execute("SELECT status FROM pending_orders WHERE id=?",
                                           (oid,)).fetchone()[0], "ordered")

    def test_two_devices_groups_never_merge_and_an_edit_keeps_the_others_lines(self):
        other = self.open_store(7)
        self.seed(other)
        mine = self.save_tab()["result"]
        theirs = self.save_tab(conn=other)["result"]
        self.assertNotEqual(mine["group_id"], theirs["group_id"])
        self.pull_into(self.conn, [{"collection": "pending_orders", "local_id": i, "operation": "upsert",
                                    "doc": server_doc(other, "pending_orders", i)}
                                   for i in theirs["order_ids"]])
        [tab] = self.act(action="load_group", group_id=mine["group_id"])["tabs"]
        tab["lines"] = tab["lines"][:1]
        self.save_tab(tab=tab)
        left = {i for (i,) in self.conn.execute("SELECT id FROM pending_orders")}
        self.assertTrue(set(theirs["order_ids"]) <= left, "editing my order deleted theirs")

    def test_write_offs_and_general_products_are_pulled_too(self):
        mid = self.meds["ZZ FULL"]
        self.pull_into(self.conn, [
            {"collection": "stock_disposals", "local_id": 7_000_000_011, "operation": "upsert",
             "doc": {"id": 7_000_000_011, "disposal_no": "SD7-1", "medicine_id": mid, "batch_no": "B1",
                     "qty": 4, "quantity": 4, "disposal_type": "expired", "reason": "Expired",
                     "expected_credit_note": True, "disposal_date": "2026-10-24", "version": 1}},
            {"collection": "general_products", "local_id": 7_000_000_012, "operation": "upsert",
             "doc": {"id": 7_000_000_012, "name": "ZZ SOAP", "rate": 30, "mrp": 35, "version": 1}},
        ])
        row = self.conn.execute("SELECT quantity, disposal_type, expected_credit_note FROM stock_disposals "
                                "WHERE id=7000000011").fetchone()
        self.assertEqual(tuple(row), (4.0, "expired", 1))
        from core.general_product_service import list_products

        self.assertIn("ZZ SOAP", [p["name"] for p in list_products(self.conn)])
        # The disposal row moves no stock: stock comes from the server's figure.
        self.assertEqual(self.conn.execute("SELECT stock_qty FROM medicines WHERE id=?",
                                           (mid,)).fetchone()[0], 900)

    def test_a_number_another_device_also_used_is_kept_not_dropped(self):
        self.conn.execute("INSERT INTO pending_orders (order_no, medicine_name, quantity, status) "
                          "VALUES ('RO202610250001', 'ZZ LOWMED', 5, 'draft')")
        self.conn.commit()
        self.conn.execute("DELETE FROM of_dirty")
        self.conn.commit()
        self.pull_into(self.conn, [{"collection": "pending_orders", "local_id": 7_000_000_020,
                                    "operation": "upsert",
                                    "doc": {"id": 7_000_000_020, "order_no": "RO202610250001",
                                            "medicine_name": "ZZ GONE", "qty": 3, "status": "draft"}}])
        row = self.conn.execute("SELECT order_no, quantity FROM pending_orders WHERE id=7000000020").fetchone()
        self.assertEqual(tuple(row), ("RO202610250001/7000000020", 3.0))


class TheCopyAndTheOneTimeFetch(_OfflineFirst):
    FETCHED = {
        "pending_orders": [{"id": 9_000_000_001, "order_no": "RO9-1", "medicine_name": "ZZ LOWMED",
                            "pack_size": "10", "qty": 12, "quantity": 12, "status": "ordered",
                            "order_group_id": "ROG20261020-9000000001", "supplier_id": None,
                            "supplier_name_manual": "PHONE AGENCY", "order_date": "2026-10-20",
                            "deleted": False}],
        "stock_disposals": [],
        "general_products": [{"id": 9_000_000_002, "name": "ZZ COMB", "rate": 10, "mrp": 12}],
    }

    def test_the_copy_brings_them_after_the_replace_pull_wiped_them(self):
        from core import server_live as sl

        pages = {k: [v] for k, v in self.FETCHED.items()}
        with mock.patch.object(sl, "_token", return_value="t"), \
                mock.patch.object(sl, "_iter_collection_pages",
                                  side_effect=lambda token, col, **kw: iter(pages[col])):
            n = plain_docs.copy_from_server(self.conn)
        self.assertEqual(n, 2)
        self.assertEqual(self.act(action="list")["groups"][0]["supplier_name"], "PHONE AGENCY")
        # The copy (online_migrate.download_store_for_offline) runs it after sync_down_all.
        import inspect

        from core import online_migrate

        src = inspect.getsource(online_migrate.download_store_for_offline)
        self.assertLess(src.index("sync_down_all("), src.index("copy_from_server(conn)"))

    def test_a_pc_already_on_offline_first_fetches_them_once(self):
        cycle = worker.SyncCycle(self.paths[0])
        self.addCleanup(cycle.close)
        mine = self.save_tab()["result"]["order_ids"][0]
        fetched = json.loads(json.dumps(self.FETCHED))
        stale = server_doc(self.conn, "pending_orders", mine)
        stale["status"] = "cancelled"
        fetched["pending_orders"].append(stale)
        fetched["pending_orders"].append({"id": 9_000_000_003, "medicine_name": "GONE ON SERVER",
                                          "qty": 1, "deleted": True})
        with mock.patch.object(worker.client, "_token", return_value="t"), \
                mock.patch.object(plain_docs, "fetch_from_server", return_value=fetched) as fetch:
            self.assertEqual(cycle.backfill_plain(), 3)
            self.assertEqual(cycle.backfill_plain(), 0)
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(meta_get(self.conn, worker.PLAIN_BACKFILL_KEY))
        names = {r[0] for r in self.conn.execute("SELECT medicine_name FROM pending_orders")}
        self.assertIn("ZZ LOWMED", names)
        self.assertNotIn("GONE ON SERVER", names)
        self.assertEqual(self.conn.execute("SELECT status FROM pending_orders WHERE id=?",
                                           (mine,)).fetchone()[0], "ordered")   # unsent: kept
        self.assertTrue(self.conn.execute("SELECT 1 FROM general_products WHERE name='ZZ COMB'").fetchone())
        self.assertFalse(self.conn.execute(
            "SELECT 1 FROM of_dirty WHERE collection IN ('general_products') "
            "OR (collection='pending_orders' AND local_id >= 9000000000)").fetchone())


class OtherOfflineFirstGaps(_OfflineFirst):
    def test_the_purchase_just_saved_is_this_pcs_not_a_higher_device_range_one(self):
        self.conn.execute("INSERT INTO purchases (id, bill_number, purchase_date, supplier_id, total_amount) "
                          "VALUES (7000000005, 'THEIRS', '2026-10-24', ?, 10)", (self.sup1,))
        self.conn.commit()
        out = desktop_purchase_service.save_purchase_bill(self.conn, {
            "supplier_name": "ZZ AGENCY ONE", "purchase_date": "2026-10-25", "bill_number": "MINE",
            "items": [_line("ZZ LOWMED")], "cash_paid": 0, "online_paid": 0})
        self.assertTrue(out.get("ok"), out)
        pid = int(out["purchase_id"])
        self.assertEqual(self.conn.execute("SELECT bill_number FROM purchases WHERE id=?",
                                           (pid,)).fetchone()[0], "MINE")

    def test_villages_and_shelf_location_are_sent(self):
        from core import sync_coordinator
        from core.village_service import save_villages

        self.conn.execute("DELETE FROM of_dirty")
        self.conn.commit()
        save_villages(self.conn, ["ZZ GAON", "ZZ WADI"], "ZZ GAON")
        sync_coordinator.after_shelf_settings_saved(self.conn)
        noted = {(c, i) for c, i, _ in self.conn.execute("SELECT collection, local_id, op FROM of_dirty")}
        self.assertTrue({("dropdowns", 0), ("shelf_settings", 0)} <= noted, noted)
        self.conn.execute("BEGIN IMMEDIATE")
        outbox.build_events(self.conn, "pc-test-3")
        self.conn.commit()
        events = {c: json.loads(p) for c, p in self.conn.execute(
            "SELECT collection, payload FROM of_events WHERE collection IN ('dropdowns','shelf_settings')")}
        self.assertEqual(events["dropdowns"]["doc"]["villages"][:2], ["ZZ GAON", "ZZ WADI"])
        self.assertIsNone(events["dropdowns"]["base_version"])
        self.assertIn("show_location", events["shelf_settings"]["doc"])


class OnlineParity(unittest.TestCase):
    """Online the engine's connection is an empty :memory: shell; candidates come from the
    store's shelf. The same steps answer the same way, and the group keeps the old name."""

    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        meds = [
            {"id": 11, "name": "ZZ LOWMED", "type": "Tablet", "unit": "10", "stock_qty": 2, "rate": 20, "mrp": 30},
            {"id": 12, "name": "ZZ GONE", "type": "Syrup", "unit": "100", "stock_qty": 0, "rate": 40, "mrp": 60},
        ]
        sections = {"low_stock": [("ZZ LOWMED", 2, "10", "ZZ AGENCY ONE")],
                    "out_of_stock": [("ZZ GONE", "100", 60, 40, "Syrup", "ZZ AGENCY ONE")]}
        for patch in (
            mock.patch("core.sync_prefs.get_sync_mode", return_value="online"),
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.alert_monitoring_service.online_visible_medicines", return_value=meds),
            mock.patch("core.alert_monitoring_service.online_stock_sections", return_value=sections),
            mock.patch("core.alert_monitoring_service.online_latest_supplier_by_medicine_id",
                       return_value={}),
            mock.patch("core.online_catalog.find_supplier_by_name",
                       return_value={"id": 301, "name": "ZZ AGENCY ONE"}),
            mock.patch("core.online_catalog.medicines_for_name", return_value=meds),
        ):
            stack.enter_context(patch)
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        db_setup.initialise(self.conn)

    def test_candidates_tabs_save_list_and_receive(self):
        items = {i["medicine_name"] for i in dset.reorder_action(self.conn, {"action": "candidates"})["items"]}
        self.assertEqual(items, {"ZZ LOWMED", "ZZ GONE"})
        [tab] = dset.reorder_action(self.conn, {"action": "bulk_tabs"})["tabs"]
        self.assertEqual((tab["label"], tab["supplier_id"]), ("ZZ AGENCY ONE", 301))
        res = dset.reorder_action(self.conn, {
            "action": "save_supplier_draft", "status": "ordered",
            "header": {"supplier_id": 301, "supplier_name_manual": "ZZ AGENCY ONE"},
            "lines": tab["lines"]})
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["result"]["group_id"], f"ROG{TODAY}-0001")
        [group] = res["groups"]
        self.assertEqual(group["medicine_count"], 2)
        first = group["first_id"]
        reorder_service.complete_pending_order_from_purchase(
            self.conn, first, [{"name": "ZZ LOWMED"}, {"name": "ZZ GONE"}])
        self.assertEqual(dset.reorder_action(self.conn, {"action": "list"})["groups"][0]["status"],
                         "received")


def _line(name: str) -> dict:
    return {"medicine_id": None, "name": name, "type": "Tablet", "batch": "NB1", "expiry": "10/28",
            "qty": 10, "free_qty": 0, "rate": 10.0, "mrp": 15.0, "gst_pct": 12, "discount_pct": 0,
            "hsn_code": "3004", "manufacturer": "", "schedule": "", "content_drug": "", "unit": "10",
            "tablets_per_stripe": 10, "quantity_value": "10", "item_amount": 0, "amount": 0,
            "taxable": 0, "gst_amt": 0}


if __name__ == "__main__":
    unittest.main()
