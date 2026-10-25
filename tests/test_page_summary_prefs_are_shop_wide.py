"""What each page's summary shows is the shop's choice, not one PC's (25 Oct 2026).

One settings row, page_summary_prefs: offline-first notes it (settings triggers v11), Online saves
it on the server first and reads the server's row, a mode switch carries it. A save changes only
the pages / figures it names, so the phone's own pages stay in the row.

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

from core import page_summary_prefs as psp
from core import settings_mirror, store_kv_carry, sync_prefs
from core.offline_first import outbox, pull, schema
from tests.test_offline_first_pc_local import make_store, till

NAME = psp.SETTING_NAME


class Base(unittest.TestCase):
    mode = sync_prefs.MODE_OFFLINE_FIRST

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="page_summaries_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        for p in (
            mock.patch.object(settings_mirror, "_mirror_path", lambda: os.path.join(self.dir, "mirror.json")),
            mock.patch.object(sync_prefs, "get_sync_mode", lambda: self.mode),
            mock.patch.object(sync_prefs, "is_online_mode", lambda: self.mode == sync_prefs.MODE_ONLINE),
            mock.patch("core.server_live._token", lambda: "test-token"),
        ):
            p.start()
            self.addCleanup(p.stop)
        psp.forget()
        self.addCleanup(psp.forget)

    def value(self, conn, name=NAME):
        r = conn.execute("SELECT value FROM settings WHERE name=?", (name,)).fetchone()
        return None if r is None else r[0]


class TheRow(unittest.TestCase):
    def test_shop_wide_carried_and_v11(self):
        self.assertTrue(pull._shop_wide(NAME))
        self.assertTrue(store_kv_carry._carried(NAME))
        self.assertIn(NAME, schema.SHOP_WIDE_SETTING_NAMES)
        self.assertEqual(schema._SETTINGS_TRIGGER_VERSION, "v11")
        self.assertIn("of_insert_settings_v10", schema._RETIRED_SETTINGS_TRIGGERS)
        self.assertIn("of_update_settings_v10", schema._RETIRED_SETTINGS_TRIGGERS)

    def test_nothing_set_shows_everything_and_junk_is_ignored(self):
        self.assertEqual(psp.clean(""), {"version": 1, "pages": {}})
        self.assertEqual(psp.clean("{not json"), {"version": 1, "pages": {}})
        self.assertEqual(psp.clean({"pages": [1, 2]}), {"version": 1, "pages": {}})
        got = psp.clean({"pages": {"sales_history": {"hidden": "yes", "off": ["profit", "Bad Key", 3]},
                                   "inventory": {"hidden": False, "off": []},
                                   "Home Page": {"hidden": True}}})
        # "yes" is not true (a broken row never hides); a page hiding nothing is dropped.
        self.assertEqual(got, {"version": 1, "pages": {"sales_history": {"off": ["profit"]}}})

    def test_a_change_keeps_what_it_does_not_name(self):
        cur = {"pages": {"customers": {"off": ["net"]},                       # the phone's own page
                         "ledger": {"off": ["billed", "opening"]},            # billed: phone only
                         "home": {"hidden": True}}}
        out = psp.merge(cur, {"pages": {
            "ledger": {"hidden": False, "on": {"opening": True, "closing": False}},
            "home": {"hidden": False},
            "sales_history": {"hidden": True, "on": {"profit": False}}}})
        self.assertEqual(out["pages"], {
            "customers": {"off": ["net"]},
            "ledger": {"off": ["billed", "closing"]},
            "sales_history": {"hidden": True, "off": ["profit"]}})


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
        with mock.patch("core.server_api._request", side_effect=AssertionError("no server")):
            out = psp.save(self.conn, {"pages": {"sales_history": {"on": {"profit": False}}}})
        self.assertEqual(out["pages"], {"sales_history": {"off": ["profit"]}})
        self.assertEqual(self.dirty(), [NAME])
        outbox.build_events(self.conn, "pc-test-install")
        docs = [json.loads(p)["doc"] for (p,) in self.conn.execute("SELECT payload FROM of_events ORDER BY seq")]
        self.assertIn({"name": NAME, "value": psp.dumps(out)}, docs)

    def test_another_devices_choice_comes_down(self):
        row = psp.dumps({"pages": {"inventory": {"hidden": True}}})
        with schema.capture_paused(self.conn):
            pull._apply_settings_bundle(self.conn, [{"name": NAME, "value": row}], set())
        self.conn.commit()
        self.assertEqual(psp.load(self.conn)["pages"], {"inventory": {"hidden": True}})
        self.assertEqual(self.dirty(), [])                                  # not sent back

    def test_a_pc_on_v10_upgrades_without_losing_or_resending(self):
        from core.desktop_settings_service import _setting_set

        for ev in ("insert", "update"):
            self.conn.execute(f"DROP TRIGGER IF EXISTS of_{ev}_settings_{schema._SETTINGS_TRIGGER_VERSION}")
        with mock.patch.object(schema, "_SETTINGS_TRIGGER_VERSION", "v10"), \
                mock.patch.object(schema, "SHOP_WIDE_SETTING_NAMES",
                                  tuple(n for n in schema.SHOP_WIDE_SETTING_NAMES if n != NAME)):
            for stmt in schema.trigger_sql(self.conn):
                if "_settings_v10" in stmt:
                    self.conn.execute(stmt)
        _setting_set(self.conn, NAME, psp.dumps({"pages": {"home": {"hidden": True}}}))
        _setting_set(self.conn, "reorder_default_qty", "7")                 # shop-wide under v10 too
        self.conn.commit()
        self.assertEqual(self.dirty(), ["reorder_default_qty"])

        schema.ensure_schema(self.conn)                                     # the next start

        names = {n for (n,) in self.conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        self.assertIn("of_insert_settings_v11", names)
        self.assertIn("of_update_settings_v11", names)
        self.assertNotIn("of_insert_settings_v10", names)
        self.assertNotIn("of_update_settings_v10", names)
        self.assertEqual(self.dirty(), ["reorder_default_qty"])             # nothing noted again
        self.assertEqual(psp.load(self.conn)["pages"], {"home": {"hidden": True}})   # nothing lost
        psp.save(self.conn, {"pages": {"home": {"hidden": False}}})
        self.assertEqual(self.dirty(), [NAME, "reorder_default_qty"])       # the next save goes up


class Online(Base):
    mode = sync_prefs.MODE_ONLINE

    def setUp(self):
        super().setUp()
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, value TEXT)")
        self.addCleanup(self.conn.close)

    def test_a_save_goes_to_the_server_first_or_is_refused(self):
        change = {"pages": {"purchase_history": {"hidden": True}}}
        with mock.patch("core.server_api.push_settings_kv", side_effect=RuntimeError("server down")), \
                mock.patch("core.server_api._request", return_value={"ok": False}):
            with self.assertRaises(RuntimeError):
                psp.save(self.conn, change)
        self.assertIsNone(self.value(self.conn))                            # nothing half-saved
        sent = []
        with mock.patch("core.server_api.push_settings_kv", lambda tok, s: sent.append(s)), \
                mock.patch("core.server_api._request", return_value={"ok": False}):
            out = psp.save(self.conn, change)
        self.assertEqual(sent, [{"settings": [{"name": NAME, "value": psp.dumps(out)}]}])
        self.assertEqual(json.loads(self.value(self.conn))["pages"], {"purchase_history": {"hidden": True}})
        self.assertEqual(settings_mirror.load_all().get(NAME), psp.dumps(out))

    def test_reads_take_the_shops_choice_from_the_server(self):
        self.conn.execute("INSERT INTO settings (name, value) VALUES (?, ?)",
                          (NAME, psp.dumps({"pages": {"home": {"hidden": True}}})))   # this PC's old one
        server_row = psp.dumps({"pages": {"sales_history": {"off": ["profit"]}, "customers": {"off": ["net"]}}})
        calls = []

        def server(method, path, **kw):
            calls.append(path)
            self.assertEqual((method, path), ("GET", f"/api/sync/settings/kv?prefix={NAME}"))
            return {"ok": True, "data": [{"id": 0, "settings": [
                {"name": NAME, "value": server_row}, {"name": "home_banner_image", "value": "data:..."}]}]}

        with mock.patch("core.server_api._request", server):
            first = psp.load(self.conn)
            psp.load(self.conn)
        self.assertEqual(first["pages"], {"sales_history": {"off": ["profit"]}, "customers": {"off": ["net"]}})
        self.assertEqual(len(calls), 1)                                     # remembered for a minute
        self.assertIsNone(self.value(self.conn, "home_banner_image"))
        psp.forget()
        with mock.patch("core.server_api._request", side_effect=RuntimeError("no internet")):
            self.assertEqual(psp.load(self.conn)["pages"]["sales_history"], {"off": ["profit"]})

    def test_no_row_on_the_server_keeps_this_pcs(self):
        self.conn.execute("INSERT INTO settings (name, value) VALUES (?, ?)",
                          (NAME, psp.dumps({"pages": {"home": {"hidden": True}}})))
        with mock.patch("core.server_api._request", return_value={"ok": True, "data": [{"id": 0, "settings": []}]}):
            self.assertEqual(psp.load(self.conn)["pages"], {"home": {"hidden": True}})


class ModeSwitch(Base):
    mode = sync_prefs.MODE_OFFLINE

    def test_the_switch_carries_it_both_ways(self):
        row = psp.dumps({"pages": {"returns": {"off": ["write_offs"]}}})
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, value TEXT)")
        conn.execute("INSERT INTO settings (name, value) VALUES (?, ?)", (NAME, row))
        sent = []
        with mock.patch("core.server_live.push_settings_kv", lambda n, v: sent.append((n, v)) or True):
            store_kv_carry.push_from_local(conn)
        self.assertEqual(sent, [(NAME, row)])
        down = sqlite3.connect(":memory:")
        self.addCleanup(down.close)
        with mock.patch("core.server_api._request",
                        return_value={"ok": True, "data": [{"id": 0, "settings": [{"name": NAME, "value": row}]}]}):
            store_kv_carry.pull_to_local(down)
        self.assertEqual(self.value(down), row)


if __name__ == "__main__":
    unittest.main()
