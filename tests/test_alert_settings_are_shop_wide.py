"""The alert thresholds and the reorder default are the shop's, not one PC's (25 Oct 2026).

low_stock_<type>, near_expiry_<type>, customer_due_min_amount, customer_due_min_days and
reorder_default_qty lived on one PC: offline-first never sent them, Online kept them in a
per-PC mirror file and never pushed them. Now offline-first notes them (settings triggers
v10), Online saves them on the server first and reads the server's rows, and a mode switch
carries them (core/store_kv_carry.py).

Temp store files and temp folders only; no server is contacted, port 8765 is never used.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from core import settings_mirror, shop_alert_settings, store_kv_carry, sync_prefs
from core.offline_first import outbox, pull, schema
from tests.test_offline_first_pc_local import make_store, till

TYPES = ["TAB", "SYRUP"]
NEW_NAMES = ("low_stock_tab", "near_expiry_syrup", "customer_due_min_amount",
             "customer_due_min_days", "reorder_default_qty")


class Base(unittest.TestCase):
    mode = sync_prefs.MODE_OFFLINE_FIRST

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="shop_alerts_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        for p in (
            mock.patch.object(settings_mirror, "_mirror_path", lambda: os.path.join(self.dir, "mirror.json")),
            mock.patch.object(sync_prefs, "get_sync_mode", lambda: self.mode),
            mock.patch.object(sync_prefs, "is_online_mode", lambda: self.mode == sync_prefs.MODE_ONLINE),
            mock.patch("core.layout_config.get_med_types", lambda: list(TYPES)),
            mock.patch("core.server_live._token", lambda: "test-token"),
        ):
            p.start()
            self.addCleanup(p.stop)
        shop_alert_settings.forget()
        self.addCleanup(shop_alert_settings.forget)

    def value(self, conn, name):
        r = conn.execute("SELECT value FROM settings WHERE name=?", (name,)).fetchone()
        return None if r is None else r[0]


class TheLists(unittest.TestCase):
    def test_every_name_is_shop_wide_and_carried(self):
        for name in NEW_NAMES:
            self.assertTrue(pull._shop_wide(name), name)
            self.assertTrue(store_kv_carry._carried(name), name)
            self.assertTrue(shop_alert_settings.is_alert_setting(name), name)
        # This PC's own rows stay its own.
        for name in ("startup_migration_x", "sync_cursor", "show_location", "customer_villages"):
            self.assertFalse(shop_alert_settings.is_alert_setting(name), name)
            self.assertFalse(pull._shop_wide(name), name)

    def test_trigger_version(self):
        self.assertIn(schema._SETTINGS_TRIGGER_VERSION, ("v10", "v11"))   # v11: page summaries
        self.assertIn("of_insert_settings_v9", schema._RETIRED_SETTINGS_TRIGGERS)
        self.assertIn("of_update_settings_v9", schema._RETIRED_SETTINGS_TRIGGERS)


class OfflineFirst(Base):
    def setUp(self):
        super().setUp()
        self.path = make_store()
        self.conn = till(self.path)
        self.addCleanup(self._close)

    def _close(self):
        self.conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    def dirty(self):
        return sorted(n for (n,) in self.conn.execute(
            "SELECT s.name FROM of_dirty d JOIN settings s ON s.id = d.local_id WHERE d.collection='settings'"))

    def test_a_save_goes_up_like_every_shop_wide_row(self):
        from core.desktop_settings_service import save_thresholds

        out = save_thresholds(self.conn, {
            "low_stock": {"tab": "25", "syrup": "4"}, "near_expiry": {"tab": "2", "syrup": "6"},
            "customer_due_min_amount": "500", "customer_due_min_days": "30", "reorder_default_qty": "12"})
        self.assertEqual(out["low_stock"]["tab"], "25")
        self.assertEqual(self.dirty(), sorted([
            "low_stock_tab", "low_stock_syrup", "near_expiry_tab", "near_expiry_syrup",
            "customer_due_min_amount", "customer_due_min_days", "reorder_default_qty"]))
        outbox.build_events(self.conn, "pc-test-install")
        docs = [json.loads(p)["doc"] for (p,) in self.conn.execute("SELECT payload FROM of_events ORDER BY seq")]
        self.assertIn({"name": "reorder_default_qty", "value": "12"}, docs)
        self.assertIn({"name": "low_stock_tab", "value": "25"}, docs)

    def test_another_devices_values_come_down(self):
        from core.alert_thresholds import load_due_alert_settings, load_thresholds
        from core.reorder_service import reorder_default_qty

        bundle = [{"name": "low_stock_tab", "value": "40"}, {"name": "near_expiry_syrup", "value": "5"},
                  {"name": "customer_due_min_amount", "value": "1000"},
                  {"name": "customer_due_min_days", "value": "15"},
                  {"name": "reorder_default_qty", "value": "20"},
                  {"name": "startup_migration_done", "value": "1"}]
        with schema.capture_paused(self.conn):
            pull._apply_settings_bundle(self.conn, bundle, set())
        self.conn.commit()
        low, near = load_thresholds(self.conn)
        self.assertEqual(low["tab"], 40.0)
        self.assertEqual(near["syrup"], 5.0)
        self.assertEqual(load_due_alert_settings(self.conn), {"min_amount": 1000.0, "min_days": 15.0})
        self.assertEqual(reorder_default_qty(self.conn), 20.0)
        self.assertIsNone(self.value(self.conn, "startup_migration_done"))   # not the shop's
        self.assertEqual(self.dirty(), [])                                  # not sent back

    def test_a_pc_on_v9_upgrades_without_losing_or_resending(self):
        from core.desktop_settings_service import _setting_set

        # The store as a v9 build left it: v9 triggers, and this PC's own thresholds (never sent).
        for ev in ("insert", "update"):
            self.conn.execute(f"DROP TRIGGER IF EXISTS of_{ev}_settings_{schema._SETTINGS_TRIGGER_VERSION}")
        with mock.patch.object(schema, "_SETTINGS_TRIGGER_VERSION", "v9"), \
                mock.patch.object(schema, "SHOP_WIDE_SETTING_PREFIXES",
                                  tuple(p for p in schema.SHOP_WIDE_SETTING_PREFIXES
                                        if p not in shop_alert_settings.PREFIXES)), \
                mock.patch.object(schema, "SHOP_WIDE_SETTING_NAMES",
                                  tuple(n for n in schema.SHOP_WIDE_SETTING_NAMES
                                        if n not in shop_alert_settings.NAMES)):
            for stmt in schema.trigger_sql(self.conn):
                if "_settings_v9" in stmt:
                    self.conn.execute(stmt)
        _setting_set(self.conn, "low_stock_tab", "33")
        _setting_set(self.conn, "reorder_default_qty", "7")
        _setting_set(self.conn, "regular_meds:9", "[1]")       # shop-wide under v9 too
        self.conn.commit()
        self.assertEqual(self.dirty(), ["regular_meds:9"])

        schema.ensure_schema(self.conn)                         # the next start

        names = {n for (n,) in self.conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        self.assertIn(f"of_insert_settings_{schema._SETTINGS_TRIGGER_VERSION}", names)
        self.assertIn(f"of_update_settings_{schema._SETTINGS_TRIGGER_VERSION}", names)
        self.assertNotIn("of_insert_settings_v9", names)
        self.assertNotIn("of_update_settings_v9", names)
        self.assertEqual(self.dirty(), ["regular_meds:9"])      # nothing noted again
        self.assertEqual(self.value(self.conn, "low_stock_tab"), "33")   # nothing lost
        self.assertEqual(self.value(self.conn, "reorder_default_qty"), "7")
        # The next save goes up.
        _setting_set(self.conn, "low_stock_tab", "35")
        self.conn.commit()
        self.assertEqual(self.dirty(), ["low_stock_tab", "regular_meds:9"])
        schema.ensure_schema(self.conn)                         # a second start changes nothing
        self.assertEqual(self.dirty(), ["low_stock_tab", "regular_meds:9"])

    def test_no_server_call_offline_first(self):
        with mock.patch("core.server_api._request", side_effect=AssertionError("no server")):
            self.assertFalse(shop_alert_settings.refresh_from_server(self.conn))
            self.assertEqual(shop_alert_settings.push_to_server({"low_stock_tab": "1"}), 0)


class Online(Base):
    mode = sync_prefs.MODE_ONLINE

    def setUp(self):
        super().setUp()
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, value TEXT)")
        self.addCleanup(self.conn.close)

    def test_a_save_goes_to_the_server_in_one_request_or_is_refused(self):
        from core.desktop_settings_service import save_thresholds

        body = {"low_stock": {"tab": "25", "syrup": "4"}, "near_expiry": {"tab": "2", "syrup": "6"},
                "customer_due_min_amount": "500", "customer_due_min_days": "30", "reorder_default_qty": "12"}
        with mock.patch("core.server_api.push_settings_kv", side_effect=RuntimeError("server down")), \
                mock.patch("core.server_api._request", return_value={"ok": False}):
            with self.assertRaises(RuntimeError):
                save_thresholds(self.conn, body)
        self.assertIsNone(self.value(self.conn, "low_stock_tab"))       # nothing half-saved
        sent = []
        with mock.patch("core.server_api.push_settings_kv", lambda tok, s: sent.append(s)), \
                mock.patch("core.server_api._request", return_value={"ok": False}):
            out = save_thresholds(self.conn, body)
        self.assertEqual(len(sent), 1)
        rows = {d["name"]: d["value"] for d in sent[0]["settings"]}
        self.assertEqual(rows, {"low_stock_tab": "25", "low_stock_syrup": "4", "near_expiry_tab": "2",
                                "near_expiry_syrup": "6", "customer_due_min_amount": "500",
                                "customer_due_min_days": "30", "reorder_default_qty": "12"})
        self.assertEqual(out["reorder_default_qty"], "12")
        self.assertEqual(self.value(self.conn, "low_stock_tab"), "25")
        # Kept in the per-store mirror too, for a start with no internet.
        self.assertEqual(settings_mirror.load_all().get("low_stock_tab"), "25")

    def test_reads_take_the_shops_values_from_the_server(self):
        from core.alert_thresholds import load_due_alert_settings, load_thresholds
        from core.desktop_settings_service import get_thresholds
        from core.reorder_service import reorder_default_qty

        # This PC's own old values (the per-PC mirror) ...
        self.conn.execute("INSERT INTO settings (name, value) VALUES ('low_stock_tab', '10')")
        self.conn.execute("INSERT INTO settings (name, value) VALUES ('near_expiry_syrup', '3')")
        calls = []

        def server(method, path, **kw):
            calls.append(path)
            self.assertEqual(method, "GET")
            self.assertIn("prefix=low_stock_,near_expiry_", path)
            self.assertIn("names=customer_due_min_amount,customer_due_min_days,reorder_default_qty", path)
            return {"ok": True, "data": [{"id": 0, "settings": [
                {"name": "low_stock_tab", "value": "40"}, {"name": "customer_due_min_amount", "value": "1000"},
                {"name": "customer_due_min_days", "value": "15"}, {"name": "reorder_default_qty", "value": "20"},
                {"name": "home_banner_image", "value": "data:..."}]}]}

        with mock.patch("core.server_api._request", server):
            t = get_thresholds(self.conn)
            low, near = load_thresholds(self.conn)
            due = load_due_alert_settings(self.conn)
            qty = reorder_default_qty(self.conn)
        self.assertEqual(t["low_stock"]["tab"], "40")
        self.assertEqual(t["near_expiry"]["syrup"], "3")          # the server has none: kept
        self.assertEqual(t["customer_due_min_amount"], "1000")
        self.assertEqual(low["tab"], 40.0)
        self.assertEqual(due, {"min_amount": 1000.0, "min_days": 15.0})
        self.assertEqual(qty, 20.0)
        self.assertEqual(len(calls), 1)                           # remembered for a minute
        self.assertIsNone(self.value(self.conn, "home_banner_image"))

        # Internet gone: the copy this PC keeps answers.
        shop_alert_settings.forget()
        with mock.patch("core.server_api._request", side_effect=RuntimeError("no internet")):
            self.assertEqual(get_thresholds(self.conn)["low_stock"]["tab"], "40")
            self.assertEqual(reorder_default_qty(self.conn), 20.0)

    def test_a_store_without_rows_on_the_server_keeps_this_pcs_values(self):
        self.conn.execute("INSERT INTO settings (name, value) VALUES ('reorder_default_qty', '9')")
        with mock.patch("core.server_api._request", return_value={"ok": True, "data": [{"id": 0, "settings": []}]}):
            self.assertTrue(shop_alert_settings.refresh_from_server(self.conn))
        self.assertEqual(self.value(self.conn, "reorder_default_qty"), "9")


class ModeSwitch(Base):
    mode = sync_prefs.MODE_OFFLINE

    def test_the_switch_carries_them_both_ways(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, value TEXT)")
        conn.execute("INSERT INTO settings (name, value) VALUES ('low_stock_tab', '25')")
        conn.execute("INSERT INTO settings (name, value) VALUES ('reorder_default_qty', '12')")
        conn.execute("INSERT INTO settings (name, value) VALUES ('sync_cursor', 'x')")
        sent = []
        with mock.patch("core.server_live.push_settings_kv", lambda n, v: sent.append((n, v)) or True):
            store_kv_carry.push_from_local(conn)
        self.assertEqual(sorted(sent), [("low_stock_tab", "25"), ("reorder_default_qty", "12")])

        down = sqlite3.connect(":memory:")
        self.addCleanup(down.close)
        rows = {"ok": True, "data": [{"id": 0, "settings": [
            {"name": "near_expiry_tab", "value": "4"}, {"name": "customer_due_min_days", "value": "30"},
            {"name": "show_location", "value": "1"}]}]}
        with mock.patch("core.server_api._request", return_value=rows):
            store_kv_carry.pull_to_local(down)
        self.assertEqual(self.value(down, "near_expiry_tab"), "4")
        self.assertEqual(self.value(down, "customer_due_min_days"), "30")
        self.assertIsNone(self.value(down, "show_location"))


if __name__ == "__main__":
    unittest.main()
