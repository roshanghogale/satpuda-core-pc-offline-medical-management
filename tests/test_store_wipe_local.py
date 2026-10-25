"""Settings > Danger Zone > "Delete all data" on the PC, with no server (core/store_wipe.py).

  * Offline: the store file is emptied -- every table, not the old list of sixteen (payments,
    returns, the cash register and drafts used to survive) -- and this PC's own settings rows
    are kept.
  * Offline-first: the server's wipe reaches this PC's copy through the pull (a page with a
    higher wipe_generation) or through a refused push ("store_wiped"): the copy and its unsent
    changes go, the registration stays, and the next pull starts at the wipe.
  * Online: the server is asked, then the caches in memory and the snapshot on disk go.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from core import db_setup, store_wipe
from core.offline_first import client, ids, schema, worker
from core.offline_first.schema import meta_get, meta_set

DEVICE = 4
BASE = DEVICE * 1_000_000_000
_SKIP = {"sqlite_sequence"}


def _fill(conn: sqlite3.Connection, table: str, row_id: int) -> bool:
    """One row in any table: every NOT NULL column without a default gets a value."""
    cols = conn.execute(f"PRAGMA table_info([{table}])").fetchall()
    names, vals = [], []
    for _cid, name, ctype, notnull, dflt, pk in cols:
        if pk and str(ctype).upper().startswith("INT"):
            names.append(name)
            vals.append(row_id)
        elif notnull and dflt is None:
            names.append(name)
            vals.append(1 if any(t in str(ctype).upper() for t in ("INT", "REAL", "NUM", "FLOA", "DOUB")) else f"x{row_id}")
    if not names:
        names, vals = [cols[0][1]], [f"x{row_id}"]
    try:
        conn.execute(f"INSERT INTO [{table}] ({','.join(names)}) VALUES ({','.join('?' * len(vals))})", vals)
        return True
    except sqlite3.Error:
        return False


def make_store(*, offline_first: bool) -> str:
    fd, path = tempfile.mkstemp(suffix=".db", prefix="wipe_test_")
    os.close(fd)
    conn = sqlite3.connect(path)
    db_setup.initialise(conn)
    from core.cash_register import ensure_tables

    ensure_tables(conn)
    conn.commit()
    if offline_first:
        schema.ensure_schema(conn)
        meta_set(conn, "device_no", DEVICE)
        meta_set(conn, "id_base", BASE)
        meta_set(conn, "id_max", BASE + 999_999_999)
        meta_set(conn, "install_id", "pc-wipe-test")
        meta_set(conn, "server_last_seq", 40)
        meta_set(conn, "pull_cursor", 500)
        conn.execute("INSERT INTO of_id_counters (tbl, next_id) VALUES ('sales', ?)", (BASE + 17,))
        conn.commit()
    conn.close()
    conn = sqlite3.connect(path)
    filled = []
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
        if t in _SKIP or t.startswith("of_") or t in ("settings", "app_meta"):
            continue
        if _fill(conn, t, 7):
            filled.append(t)
    # Opening the store may already have given it a default profile row: the shop has one profile.
    conn.execute("DELETE FROM pharmacy_profile WHERE id <> 7")
    for name, value in (("regular_meds:7", "[1,2]"), ("customer_gst:7", "27AAA"), ("doctor_margin_rules", "{}"),
                        ("profile_bank_name", "SBI"), ("printer_name", "HP"), ("bill_dues_follow_payments_v1", "1")):
        conn.execute("INSERT OR REPLACE INTO settings (name, value) VALUES (?, ?)", (name, value))
    conn.commit()
    conn.close()
    return path


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]


def _settings(conn):
    return sorted(r[0] for r in conn.execute("SELECT name FROM settings"))


class Cleanup(unittest.TestCase):
    def tearDown(self):
        for p in getattr(self, "paths", []):
            for s in ("", "-wal", "-shm"):
                try:
                    os.remove(p + s)
                except OSError:
                    pass

    def store(self, **kw):
        p = make_store(**kw)
        self.paths = getattr(self, "paths", []) + [p]
        return p


class OfflineWipe(Cleanup):
    def test_every_table_is_emptied_and_this_pcs_settings_stay(self):
        path = self.store(offline_first=False)
        conn = sqlite3.connect(path)
        for t in ("sales", "customer_payments", "supplier_payments", "sales_returns", "purchase_returns",
                  "cash_days", "cash_expenses", "purchase_drafts", "medicines", "customers", "pharmacy_profile"):
            self.assertEqual(_count(conn, t), 1, t)
        from core import desktop_settings_service as dss

        with mock.patch.object(dss, "_wipe_mode", return_value="offline"):
            preview = dss.system_action(conn, {"action": "danger_wipe_preview"})
            self.assertIn("ONLY this PC", preview["confirm_text"])
            self.assertEqual(preview["local_counts"]["sales"], 1)
            refused = dss.system_action(conn, {"action": "danger_wipe", "confirm": "DELETE ALL TABLES",
                                               "password": "wrong", "_heavy_sync": True})
            self.assertFalse(refused["ok"])
            self.assertEqual(_count(conn, "sales"), 1)
            res = dss.system_action(conn, {"action": "danger_wipe", "confirm": "DELETE ALL TABLES",
                                           "password": "satpuda core", "_heavy_sync": True})
        self.assertTrue(res["ok"], res)
        self.assertTrue(res["needs_restart"])
        for t in store_wipe.business_tables(conn):
            self.assertEqual(_count(conn, t), 0, f"{t} still has rows")
        self.assertEqual(_count(conn, "pharmacy_profile"), 1)        # profile kept by default
        names = _settings(conn)
        for kept in ("bill_dues_follow_payments_v1", "printer_name", "profile_bank_name"):
            self.assertIn(kept, names)
        for gone in ("regular_meds:7", "customer_gst:7", "doctor_margin_rules"):
            self.assertNotIn(gone, names)
        # ids start again at 1
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sqlite_sequence WHERE name='sales'").fetchone()[0], 0)
        conn.close()

    def test_also_profile(self):
        path = self.store(offline_first=False)
        conn = sqlite3.connect(path)
        store_wipe.empty_store_file(conn, also_profile=True)
        conn.commit()
        self.assertEqual(_count(conn, "pharmacy_profile"), 0)
        names = _settings(conn)
        self.assertIn("printer_name", names)
        self.assertIn("bill_dues_follow_payments_v1", names)
        self.assertNotIn("profile_bank_name", names)
        self.assertNotIn("regular_meds:7", names)
        conn.close()


class FakeServer:
    """client.* as the server answers after "Delete all data" (wipe 1 at revision 900)."""

    def __init__(self):
        self.pushed = []
        self.cursors = []

    def changes(self, after, limit=200):
        self.cursors.append(after)
        return {"changes": [], "has_more": False, "to_revision": after,
                "wipe_generation": 1, "wipe_revision": 900}

    def push(self, events, wipe_generation=None):
        self.pushed.append((len(events), wipe_generation))
        if int(wipe_generation or 0) < 1:
            raise RuntimeError("store_wiped: all data of this store was deleted on the server.")
        return {"last_seq": 40 + len(events), "results": [
            {"seq": e["seq"], "event_uuid": e["event_uuid"], "outcome": "applied"} for e in events]}

    def sync_status(self):
        return {"head_revision": 900, "wipe_generation": 1, "wipe_revision": 900}

    def stock(self, ids=None):
        return {"stock": []}

    def patch(self):
        return mock.patch.multiple(client, changes=self.changes, push=self.push, sync_status=self.sync_status,
                                   stock=self.stock, install_id=lambda: "pc-wipe-test")


class OfflineFirstPicksUpTheWipe(Cleanup):
    def _unsent(self, path):
        till = sqlite3.connect(path, factory=ids.OfConnection)
        till.execute("UPDATE customers SET name='edited on this PC' WHERE id=7")
        till.commit()
        till.close()

    def _check_cleared(self, path):
        conn = sqlite3.connect(path)
        for t in ("sales", "sales_items", "medicines", "customers", "customer_payments", "cash_days",
                  "purchase_returns", "of_dirty", "of_events", "of_stock_journal", "of_number_blocks"):
            self.assertEqual(_count(conn, t), 0, t)
        self.assertEqual(schema.unsent_work(conn), 0)
        # the registration stays; the next pull starts at the wipe
        self.assertEqual(meta_get(conn, "device_no"), str(DEVICE))
        self.assertEqual(meta_get(conn, "install_id"), "pc-wipe-test")
        self.assertEqual(meta_get(conn, "server_last_seq"), "40")
        self.assertEqual(meta_get(conn, "wipe_generation"), "1")
        self.assertEqual(conn.execute("SELECT next_id FROM of_id_counters WHERE tbl='sales'").fetchone()[0], BASE + 17)
        self.assertEqual(conn.execute("SELECT v FROM of_flags WHERE k='capture'").fetchone()[0], "1")
        self.assertEqual(_count(conn, "pharmacy_profile"), 1)
        self.assertIn("printer_name", _settings(conn))
        self.assertNotIn("regular_meds:7", _settings(conn))
        conn.close()

    def test_a_pull_page_with_a_later_wipe_clears_the_copy(self):
        path = self.store(offline_first=True)
        self._unsent(path)
        srv = FakeServer()
        cycle = worker.SyncCycle(path)
        try:
            with srv.patch():
                got = cycle.pull()
        finally:
            cycle.close()
        self.assertEqual(got, 0)
        self._check_cleared(path)
        self.assertEqual(srv.cursors, [500, 900])      # then pulls again from the wipe
        conn = sqlite3.connect(path)
        self.assertEqual(meta_get(conn, "pull_cursor"), "900")
        conn.close()

    def test_a_refused_push_clears_the_copy_and_the_next_push_follows_on(self):
        path = self.store(offline_first=True)
        self._unsent(path)
        srv = FakeServer()
        cycle = worker.SyncCycle(path)
        try:
            with srv.patch():
                cycle.push()
                self._check_cleared(path)
                self.assertIn(srv.pushed[0][1], (None, 0))
                # a bill made after the wipe goes up with the new generation, numbered 41
                till = sqlite3.connect(path, factory=ids.OfConnection)
                till.execute("INSERT INTO customers (name) VALUES ('after the wipe')")
                till.commit()
                till.close()
                cycle.push()
        finally:
            cycle.close()
        self.assertEqual(srv.pushed[-1], (1, 1))
        conn = sqlite3.connect(path)
        self.assertEqual(meta_get(conn, "server_last_seq"), "41")
        conn.close()

    def test_the_same_wipe_twice_changes_nothing(self):
        path = self.store(offline_first=True)
        conn = sqlite3.connect(path)
        self.assertTrue(store_wipe.apply_server_wipe(conn, {"wipe_generation": 1, "wipe_revision": 900}))
        conn.commit()
        _fill(conn, "customers", 99)
        conn.commit()
        self.assertFalse(store_wipe.apply_server_wipe(conn, {"wipe_generation": 1, "wipe_revision": 900}))
        self.assertEqual(_count(conn, "customers"), 1)
        conn.close()

    def test_danger_wipe_on_offline_first_asks_the_server_then_clears_this_copy(self):
        path = self.store(offline_first=True)
        self._unsent(path)
        from core import desktop_settings_service as dss

        conn = sqlite3.connect(path)
        calls = []

        def fake_wipe(**kw):
            calls.append(kw)
            return {"wiped": True, "store_name": "Wipe Test", "total_rows": 12, "wipe_generation": 1,
                    "wipe_revision": 900, "export": {"path": "/x/20261025_store9_wipe.json.gz"}}

        base = {"action": "danger_wipe", "confirm": "DELETE ALL TABLES", "password": "satpuda core",
                "_heavy_sync": True}
        with mock.patch.object(dss, "_wipe_mode", return_value="offline_first"), \
                mock.patch.object(client, "store_wipe", side_effect=fake_wipe), \
                mock.patch.object(dss, "_vendor_admin_token", return_value="ADMIN-TOKEN"):
            first = dss.system_action(conn, dict(base))
            self.assertEqual(first.get("code"), "needs_second_confirm")
            self.assertEqual(calls, [])
            res = dss.system_action(conn, dict(base, all_devices_ok=True))
        self.assertTrue(res["ok"], res)
        self.assertEqual(calls[0]["admin_token"], "ADMIN-TOKEN")
        self.assertEqual(calls[0]["confirm"], "DELETE ALL TABLES")
        self.assertIn("every other pc and phone", res["message"].lower())
        conn.close()
        self._check_cleared(path)

    def test_without_the_administrator_sign_in_nothing_happens(self):
        path = self.store(offline_first=True)
        from core import desktop_settings_service as dss

        conn = sqlite3.connect(path)
        with mock.patch.object(dss, "_wipe_mode", return_value="offline_first"), \
                mock.patch.object(dss, "_vendor_admin_token",
                                  return_value={"ok": False, "code": "admin_credential_required", "error": "x"}), \
                mock.patch.object(client, "store_wipe", side_effect=AssertionError("must not be called")):
            res = dss.system_action(conn, {"action": "danger_wipe", "confirm": "DELETE ALL TABLES",
                                           "password": "satpuda core", "all_devices_ok": True,
                                           "_heavy_sync": True})
        self.assertEqual(res["code"], "admin_credential_required")
        self.assertEqual(_count(conn, "sales"), 1)
        conn.close()


class OnlinePc(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.p = mock.patch.object(store_wipe, "_seen_path", lambda: os.path.join(self.dir, "seen.json"))
        self.p.start()
        self.addCleanup(self.p.stop)

    def test_status_with_a_new_wipe_drops_the_caches_once(self):
        with mock.patch.object(store_wipe, "forget_online_caches") as forget:
            self.assertFalse(store_wipe.check_online_wipe({"wipe_generation": 0}))   # first look: recorded
            self.assertFalse(store_wipe.check_online_wipe({"wipe_generation": 0}))
            self.assertTrue(store_wipe.check_online_wipe({"wipe_generation": 1}))
            self.assertFalse(store_wipe.check_online_wipe({"wipe_generation": 1}))
            self.assertEqual(forget.call_count, 1)

    def test_a_store_wiped_before_this_pc_ever_looked_reloads_once(self):
        with mock.patch.object(store_wipe, "forget_online_caches") as forget:
            self.assertTrue(store_wipe.check_online_wipe({"wipe_generation": 2}))
            self.assertFalse(store_wipe.check_online_wipe({"wipe_generation": 2}))
            self.assertEqual(forget.call_count, 1)

    def test_forget_removes_the_catalogue_snapshot(self):
        from core import online_catalog

        snap = os.path.join(self.dir, "snap.json")
        with open(snap, "w") as fh:
            fh.write('{"medicines": [{"id": 1}]}')
        with mock.patch.object(online_catalog, "_snapshot_path", lambda: snap), \
                mock.patch.object(online_catalog, "invalidate") as inv:
            store_wipe.forget_online_caches()
        inv.assert_any_call()
        self.assertFalse(os.path.exists(snap))

    def test_danger_wipe_online_asks_the_server_and_drops_the_caches(self):
        from core import desktop_settings_service as dss

        conn = sqlite3.connect(":memory:")
        with mock.patch.object(dss, "_wipe_mode", return_value="online"), \
                mock.patch.object(dss, "_vendor_admin_token", return_value="T"), \
                mock.patch.object(client, "store_wipe", return_value={
                    "wipe_generation": 3, "wipe_revision": 70, "store_name": "S", "total_rows": 5, "export": {}}), \
                mock.patch.object(store_wipe, "forget_online_caches") as forget:
            res = dss.system_action(conn, {"action": "danger_wipe", "confirm": "DELETE ALL TABLES",
                                           "password": "satpuda core", "all_devices_ok": True,
                                           "_heavy_sync": True})
        self.assertTrue(res["ok"], res)
        forget.assert_called_once()
        self.assertEqual(store_wipe.seen_generation(), 3)


if __name__ == "__main__":
    unittest.main()
