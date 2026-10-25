"""The store's App Login works on every PC of the store, offline-first and with no internet.

Owner, 25 Oct 2026: "the username and password for the store is not working in offline
first". The login (Settings > Pharmacy Profile > App Login) lived only in one PC's
app_login.dat: the other PCs of the store -- a second counter, the laptop, a PC set up
again for offline-first -- had no copy, so the shop's username and password were refused
there or never asked. It is now the store's settings row ``app_login`` (a salted hash),
sent between devices like every other shop-wide row and checked against this PC's own copy.

Everything runs on temp store files and temp config folders; no server is contacted and
port 8765 is never used. The passwords are test values made up here.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from core import login_prefs, settings_mirror, sync_prefs
from core.desktop_login_service import get_app_login_status, verify_app_login
from core.offline_first import pull, schema
from tests.test_offline_first_pc_local import make_store, till

USER = "counter-test"
PASS = "test-pass-4821"
OTHER_PASS = "test-pass-9999"


class _Pc:
    """One PC: its own config folder (app_login.dat, settings mirror) and its own store file."""

    def __init__(self, case: unittest.TestCase, mode: str = sync_prefs.MODE_OFFLINE_FIRST):
        self.dir = tempfile.mkdtemp(prefix="of_login_pc_")
        case.addCleanup(shutil.rmtree, self.dir, True)
        self.path = make_store()
        self.conn = till(self.path)
        case.addCleanup(self._close)
        self.mode = mode
        self.case = case

    def _close(self):
        try:
            self.conn.close()
        except Exception:
            pass
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    def on(self):
        """Make this PC the one the code runs on (config folder, mirror, mode)."""
        self.patches = [
            mock.patch.object(login_prefs, "_config_dir", lambda: self.dir),
            mock.patch.object(settings_mirror, "_mirror_path", lambda: os.path.join(self.dir, "mirror.json")),
            mock.patch.object(sync_prefs, "get_sync_mode", lambda: self.mode),
            # tests/__init__.py pins is_online_mode to False for the whole suite.
            mock.patch.object(sync_prefs, "is_online_mode", lambda: self.mode == sync_prefs.MODE_ONLINE),
        ]
        for x in self.patches:
            x.start()
        self.case.addCleanup(self.off)
        return self

    def off(self):
        for x in getattr(self, "patches", []):
            x.stop()
        self.patches = []

    def row(self):
        r = self.conn.execute("SELECT value FROM settings WHERE name='app_login'").fetchone()
        return None if r is None else json.loads(r[0])

    def dirty_settings(self):
        return [n for (n,) in self.conn.execute(
            "SELECT s.name FROM of_dirty d JOIN settings s ON s.id = d.local_id "
            "WHERE d.collection = 'settings'")]


def _off_server():
    """Every server call fails: offline-first with the internet down."""
    def no_server(*_a, **_k):
        raise RuntimeError("no server in this test")
    return [
        mock.patch("core.offline_first.client._call", no_server),
        mock.patch("core.store_images._kv_rows_cached", lambda names=(): None),
        mock.patch("core.server_live.push_settings_kv", no_server),
    ]


class Base(unittest.TestCase):
    def setUp(self):
        for p in _off_server():
            p.start()
            self.addCleanup(p.stop)


class OfflineFirst(Base):
    def test_a_login_saved_on_one_pc_works_on_another_with_no_internet(self):
        a = _Pc(self).on()
        login_prefs.save_login_prefs(enabled=True, username=USER, password=PASS, conn=a.conn)

        row = a.row()
        self.assertTrue(row["enabled"])
        self.assertEqual(row["username"], USER)
        self.assertNotIn(PASS, json.dumps(row))           # a salted hash, never the password
        self.assertEqual(len(row["salt"]), 32)
        self.assertIn("app_login", a.dirty_settings())    # goes up like any shop-wide row
        self.assertEqual(verify_app_login({"username": USER, "password": PASS}, a.conn)["ok"], True)

        # PC B: its own config folder (no app_login.dat at all) and its own copy of the store;
        # the row comes down in the server's settings bundle.
        a.off()
        b = _Pc(self).on()
        self.assertFalse(os.path.exists(os.path.join(b.dir, "app_login.dat")))
        with b.conn:
            pull._apply_settings_bundle(b.conn, [{"name": "app_login", "value": json.dumps(row)}], set())
        st = get_app_login_status(b.conn)
        self.assertEqual(st, {"ok": True, "enabled": True, "username_hint": USER})
        self.assertTrue(verify_app_login({"username": USER, "password": PASS}, b.conn)["ok"])
        self.assertFalse(verify_app_login({"username": USER, "password": OTHER_PASS}, b.conn)["ok"])
        self.assertFalse(verify_app_login({"username": "someone", "password": PASS}, b.conn)["ok"])

    def test_the_pharmacy_profile_save_makes_it_the_stores(self):
        from core.desktop_settings_service import get_pharmacy, save_pharmacy

        a = _Pc(self).on()
        # Only the login part: the profile, bill print file and printers are not this test's.
        with mock.patch("core.pharmacy_profile_io.save_pharmacy_profile"),                 mock.patch("core.pharmacy_profile_io.load_pharmacy_profile", lambda conn: {}),                 mock.patch("core.bill_config.load_bill_print_settings", lambda: {}),                 mock.patch("core.printer_manager.PrinterManager.get_installed_printers", lambda: []),                 mock.patch("core.printer_manager.PrinterManager.load_settings", lambda: {}):
            out = save_pharmacy(a.conn, {"login": {"enabled": True, "username": USER, "password": PASS}})
            self.assertEqual(out["login"], {"enabled": True, "username": USER, "password": ""})
            self.assertTrue(a.row()["enabled"])
            self.assertEqual(get_pharmacy(a.conn)["login"]["username"], USER)

    def test_a_pcs_own_login_still_works_and_becomes_the_stores_once_typed(self):
        a = _Pc(self).on()
        # Before this build: the login only in this PC's file, the store has no row.
        login_prefs._write_file({"enabled": True, "username": USER, "salt": "ab" * 16,
                                 "password_hash": login_prefs._hash_password(PASS, "ab" * 16),
                                 "iterations": login_prefs._ITERATIONS})
        self.assertIsNone(a.row())
        self.assertTrue(get_app_login_status(a.conn)["enabled"])
        self.assertFalse(verify_app_login({"username": USER, "password": OTHER_PASS}, a.conn)["ok"])
        self.assertIsNone(a.row())                         # a wrong try changes nothing
        self.assertTrue(verify_app_login({"username": USER, "password": PASS}, a.conn)["ok"])
        self.assertEqual(a.row()["username"], USER)        # now the store's
        self.assertIn("app_login", a.dirty_settings())

    def test_a_pc_with_no_login_saving_its_profile_does_not_switch_the_store_login_off(self):
        a = _Pc(self).on()
        login_prefs.save_login_prefs(enabled=False, username="", password=None,
                                     keep_existing_password=True, conn=a.conn)
        self.assertIsNone(a.row())                         # nothing written over the store

    def test_the_store_switching_the_login_off_wins_over_a_pcs_old_file(self):
        a = _Pc(self).on()
        login_prefs._write_file({"enabled": True, "username": USER, "salt": "cd" * 16,
                                 "password_hash": login_prefs._hash_password(PASS, "cd" * 16)})
        with a.conn:
            a.conn.execute("INSERT INTO settings (name, value) VALUES ('app_login', ?)",
                           (json.dumps({"enabled": False, "username": USER}),))
        self.assertFalse(get_app_login_status(a.conn)["enabled"])
        self.assertTrue(verify_app_login({}, a.conn)["authenticated"])

    def test_changing_the_password_keeps_the_username_and_drops_the_old_password(self):
        a = _Pc(self).on()
        login_prefs.save_login_prefs(enabled=True, username=USER, password=PASS, conn=a.conn)
        login_prefs.save_login_prefs(enabled=True, username=USER, password=OTHER_PASS, conn=a.conn)
        self.assertFalse(login_prefs.verify_login(USER, PASS, a.conn))
        self.assertTrue(login_prefs.verify_login(USER, OTHER_PASS, a.conn))
        # Re-saved with the password left blank: the hash stays.
        login_prefs.save_login_prefs(enabled=True, username=USER, password=None,
                                     keep_existing_password=True, conn=a.conn)
        self.assertTrue(login_prefs.verify_login(USER, OTHER_PASS, a.conn))

    def test_the_row_is_shop_wide_and_carried_between_modes(self):
        from core import store_kv_carry

        self.assertIn("app_login", schema.SHOP_WIDE_SETTING_NAMES)
        self.assertTrue(pull._shop_wide("app_login"))
        self.assertTrue(store_kv_carry._carried("app_login"))
        a = _Pc(self)
        names = {n for (n,) in a.conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        self.assertIn(f"of_insert_settings_{schema._SETTINGS_TRIGGER_VERSION}", names)
        self.assertEqual(schema._SETTINGS_TRIGGER_VERSION, "v11")
        self.assertNotIn("of_insert_settings_v10", names)


class Online(Base):
    def _shell(self, case_pc: _Pc) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, value TEXT)")
        self.addCleanup(conn.close)
        return conn

    def test_online_with_the_internet_down_uses_this_pcs_copy_of_the_store_row(self):
        pc = _Pc(self, mode=sync_prefs.MODE_ONLINE).on()
        shell = self._shell(pc)
        salt = "ef" * 16
        row = {"enabled": True, "username": USER, "salt": salt,
               "password_hash": login_prefs._hash_password(PASS, salt), "iterations": login_prefs._ITERATIONS}
        # The server answers once: its row is kept on this PC.
        with mock.patch("core.store_images._kv_rows_cached",
                        lambda names=(): [{"name": "app_login", "value": json.dumps(row)}]):
            self.assertTrue(verify_app_login({"username": USER, "password": PASS}, shell)["ok"])
        self.assertEqual(json.loads(shell.execute(
            "SELECT value FROM settings WHERE name='app_login'").fetchone()[0])["username"], USER)
        # Internet gone (the patch from setUp answers None): still the same login.
        self.assertTrue(get_app_login_status(shell)["enabled"])
        self.assertTrue(verify_app_login({"username": USER, "password": PASS}, shell)["ok"])
        self.assertFalse(verify_app_login({"username": USER, "password": OTHER_PASS}, shell)["ok"])

    def test_online_save_goes_to_the_server_or_is_refused(self):
        pc = _Pc(self, mode=sync_prefs.MODE_ONLINE).on()
        shell = self._shell(pc)
        # No server: refused, and nothing half-saved on this PC.
        with self.assertRaises(RuntimeError):
            login_prefs.save_login_prefs(enabled=True, username=USER, password=PASS, conn=shell)
        self.assertIsNone(shell.execute("SELECT 1 FROM settings WHERE name='app_login'").fetchone())
        self.assertFalse(os.path.exists(os.path.join(pc.dir, "app_login.dat")))
        sent = []
        with mock.patch("core.server_live.push_settings_kv", lambda n, v: sent.append((n, v)) or True):
            login_prefs.save_login_prefs(enabled=True, username=USER, password=PASS, conn=shell)
        self.assertEqual(sent[0][0], "app_login")
        self.assertNotIn(PASS, sent[0][1])
        self.assertEqual(json.loads(sent[0][1])["username"], USER)


class ThroughTheEngine(Base):
    """The login screen's two calls, on the real engine handler, offline-first, no server."""

    def test_status_and_verify_answer_from_the_stores_row(self):
        import core.desktop_api as d

        a = _Pc(self).on()
        login_prefs.save_login_prefs(enabled=True, username=USER, password=PASS, conn=a.conn)
        saved = dict(d._db)
        self.addCleanup(lambda: (d._db.clear(), d._db.update(saved)))
        d._db.update({"conn": a.conn, "path": a.path, "server_only": False, "migrate_gate": {}})
        srv = d._ReusableHTTPServer(("127.0.0.1", 0), d._DesktopApiHandler)
        port = srv.server_address[1]
        self.assertNotEqual(port, 8765)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        # The PC's own file is gone (a second PC, a reinstall): the store's row answers.
        os.remove(os.path.join(a.dir, "app_login.dat"))

        def call(method, path, body=None):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}", method=method,
                data=json.dumps(body).encode() if body is not None else None,
                headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return r.status, json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")

        self.assertEqual(call("GET", "/api/login/status"),
                         (200, {"ok": True, "enabled": True, "username_hint": USER}))
        self.assertEqual(call("POST", "/api/login/verify", {"username": USER, "password": PASS}),
                         (200, {"ok": True, "authenticated": True}))
        code, body = call("POST", "/api/login/verify", {"username": USER, "password": OTHER_PASS})
        self.assertEqual(code, 401)
        self.assertEqual(body["error"], "Invalid username or password.")


if __name__ == "__main__":
    unittest.main()
