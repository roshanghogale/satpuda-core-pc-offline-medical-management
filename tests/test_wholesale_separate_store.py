"""The Retail | Wholesale switch opens ANOTHER STORE (owner, 18 Oct 2026).

Wholesale and retail are two different stores -- different medicines, rates, purchases, sales
and stock (D:\\projects\\WHOLESALE_DESIGN.md, "Wholesale is a SEPARATE store"). The open retail
store's licence names its wholesale companion {store_id, store_key, store_name, android_key};
POST /api/wholesale/mode puts that store on this PC once (linked to its own SC- key), gives it
its own offline-first copy when the PC is offline-first, and only then opens it. A failed copy
leaves the PC on the retail store. Nothing is copied from one store into the other.

A real store registry in a temporary app-data folder; the server, the copy and the engine's
reopen are stood in for. Nothing reaches a server.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.license_manager as lm  # noqa: E402
import core.store_manager as smgr  # noqa: E402
from core import wholesale_api  # noqa: E402
from core import wholesale_mode as wm  # noqa: E402
from core import wholesale_store_switch as sw  # noqa: E402
from core.offline_first import api as of_api  # noqa: E402

RETAIL_KEY = "Store_Test_Medical"
COMPANION = {"store_id": "test_medical_wholesale", "store_key": "Test_Medical_Wholesale",
             "store_name": "Test Medical Wholesale", "android_key": "SC-W1W2W3W4"}


def _retail_answer(**over):
    return {"wholesale_enabled": True, "store_kind": "retail", "wholesale_store": COMPANION, **over}


class _PC(unittest.TestCase):
    """One retail store on this PC, its licence naming the wholesale companion."""

    offline_first = False

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sessions: list[dict] = []
        self.reopened: list[str] = []

        def ensure_store_session(**kw):
            self.sessions.append(kw)
            return {"token": "t-" + kw["store_key"], "store_id": "x"}

        def reopen():
            self.reopened.append(smgr.get_active_store_key())
            return {"ok": True}

        for p in (
            mock.patch.object(lm, "_appdata_dir", lambda: self.tmp),
            mock.patch("core.sync_prefs.get_sync_mode",
                       lambda: "offline_first" if self.offline_first else "offline"),
            mock.patch("core.offline_first.runtime.is_active", lambda: self.offline_first),
            mock.patch("core.server_api.ensure_store_session", side_effect=ensure_store_session),
            mock.patch("core.desktop_api.reopen_active_store", side_effect=reopen),
            mock.patch.object(smgr, "_sync_backup_config_for_store", lambda *_a, **_k: None),
            mock.patch.dict(os.environ, {wm.OVERRIDE_ENV: ""}),
        ):
            p.start()
            self.addCleanup(p.stop)
        lm._WHOLESALE_LIVE.clear()
        self.addCleanup(lm._WHOLESALE_LIVE.clear)
        of_api._job.update({"running": False, "steps": [], "result": None, "error": None, "kind": ""})
        smgr.save_registry({
            "device_role": "admin",
            "active_store_key": RETAIL_KEY,
            "stores": [{"store_key": RETAIL_KEY, "display_name": "Test Medical"}],
        })
        retail_db = sqlite3.connect(smgr.get_store_db_path(RETAIL_KEY))
        retail_db.execute("CREATE TABLE medicines (id INTEGER PRIMARY KEY, name TEXT)")
        retail_db.execute("INSERT INTO medicines (name) VALUES ('RETAIL ONLY TABLET')")
        retail_db.commit()
        retail_db.close()
        with open(os.path.join(self.tmp, f"store_key_{RETAIL_KEY}.txt"), "w") as fh:
            fh.write("SC-R1R2R3R4")
        lm.remember_server_wholesale(_retail_answer(), store_key=RETAIL_KEY)

    def keys(self) -> list[str]:
        return [s["store_key"] for s in smgr.list_stores()]

    def companion_key(self) -> str:
        return next(k for k in self.keys() if k != RETAIL_KEY)

    def wait_for_job(self) -> dict:
        for _ in range(200):
            with of_api._job_lock:
                if not of_api._job["running"]:
                    return dict(of_api._job)
            time.sleep(0.02)
        self.fail("the copy job did not finish")


class TheSwitchOpensTheCompanion(_PC):
    def test_it_adds_the_wholesale_store_once_links_its_key_and_opens_it(self):
        from core.server_live import _load_adoptions
        from core.store_link import get_local_android_key

        res = sw.switch_to("wholesale")
        self.assertEqual((res["ok"], res["reload"], res["store_name"], res["mode"]),
                         (True, True, "Test Medical Wholesale", "wholesale"))
        self.assertEqual(len(self.keys()), 2)
        new = self.companion_key()
        self.assertEqual(smgr.get_active_store_key(), new)
        self.assertEqual(get_local_android_key(new), "SC-W1W2W3W4")
        self.assertEqual(_load_adoptions()[new]["store_id"], "test_medical_wholesale")
        self.assertTrue(_load_adoptions()[new].get("joined_existing"))
        # paired with the companion's OWN key, before anything changed on this PC
        self.assertEqual(self.sessions[0]["android_key"], "SC-W1W2W3W4")
        self.assertTrue(self.sessions[0]["force_pair"])
        self.assertEqual(self.reopened, [new])
        # the store just added knows what it is and where it links back to
        self.assertEqual(wm.current_mode(), "wholesale")
        st = wm.state(None)
        self.assertEqual((st["mode"], st["allowed"], st["companion_name"]), ("wholesale", True, "Test Medical"))
        self.assertEqual(wm.store_info()["retail_store"]["android_key"], "SC-R1R2R3R4")

        # back to retail: the retail store is found by its key, nothing is added
        res = sw.switch_to("retail")
        self.assertEqual((res["ok"], res["store_name"]), (True, "Test Medical"))
        self.assertEqual(smgr.get_active_store_key(), RETAIL_KEY)
        self.assertEqual(wm.current_mode(), "retail")

        # and to wholesale again: the same store, not a second copy
        sw.switch_to("wholesale")
        self.assertEqual(len(self.keys()), 2)
        self.assertEqual(smgr.get_active_store_key(), new)
        self.assertEqual(self.reopened, [new, RETAIL_KEY, new])
        self.assertFalse(self.sessions[-1].get("force_pair"))

    def test_the_two_stores_are_two_files_and_nothing_is_copied(self):
        sw.switch_to("wholesale")
        new = self.companion_key()
        retail_path = smgr.get_store_db_path(RETAIL_KEY)
        ws_path = smgr.get_store_db_path(new)
        self.assertNotEqual(os.path.normcase(os.path.abspath(retail_path)),
                            os.path.normcase(os.path.abspath(ws_path)))
        self.assertEqual(smgr.get_active_db_path(), ws_path)
        c = sqlite3.connect(ws_path)
        try:
            have = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            rows = c.execute("SELECT name FROM medicines").fetchall() if "medicines" in have else []
        finally:
            c.close()
        self.assertEqual(rows, [])
        c = sqlite3.connect(retail_path)
        try:
            self.assertEqual(c.execute("SELECT name FROM medicines").fetchall(), [("RETAIL ONLY TABLET",)])
        finally:
            c.close()

    def test_the_same_mode_changes_nothing(self):
        res = sw.switch_to("retail")
        self.assertEqual((res["ok"], res["reload"], res.get("unchanged")), (True, False, True))
        self.assertEqual(self.keys(), [RETAIL_KEY])
        self.assertEqual(self.sessions, [])

    def test_the_api_route(self):
        code, body = wholesale_api.handle_post("/api/wholesale/mode", {"mode": "wholesale"}, None)
        self.assertEqual((code, body["ok"], body["reload"], body["store_name"]),
                         (200, True, True, "Test Medical Wholesale"))


class NoCompanionIsRefused(_PC):
    def test_a_retail_store_without_a_wholesale_store(self):
        lm.remember_server_wholesale({"wholesale_enabled": False}, store_key=RETAIL_KEY)
        with self.assertRaises(sw.NoCompanion) as ctx:
            sw.switch_to("wholesale")
        self.assertIn("no wholesale store", str(ctx.exception))
        code, body = wholesale_api.handle_post("/api/wholesale/mode", {"mode": "wholesale"}, None)
        self.assertEqual((code, body["code"]), (403, "no_companion"))
        self.assertEqual(self.keys(), [RETAIL_KEY])
        self.assertEqual((self.sessions, self.reopened), ([], []))
        self.assertEqual(smgr.get_active_store_key(), RETAIL_KEY)

    def test_a_server_that_refuses_the_pairing_changes_nothing(self):
        with mock.patch("core.server_api.ensure_store_session", side_effect=RuntimeError("401")):
            with self.assertRaises(RuntimeError) as ctx:
                sw.switch_to("wholesale")
        self.assertIn("Test Medical Wholesale", str(ctx.exception))
        self.assertEqual(self.keys(), [RETAIL_KEY])
        self.assertEqual(smgr.get_active_store_key(), RETAIL_KEY)

    def test_a_store_of_the_same_name_that_is_not_linked(self):
        smgr.create_store("Test Medical Wholesale", activate=False)
        with self.assertRaises(RuntimeError) as ctx:
            sw.switch_to("wholesale")
        self.assertIn("not linked", str(ctx.exception))
        self.assertEqual(self.sessions, [])
        self.assertEqual(smgr.get_active_store_key(), RETAIL_KEY)

    def test_a_single_store_pc(self):
        reg = smgr.load_registry()
        reg["device_role"] = "satellite"
        smgr.save_registry(reg)
        with self.assertRaises(RuntimeError):
            sw.switch_to("wholesale")
        self.assertEqual(self.keys(), [RETAIL_KEY])


class OfflineFirstGivesItsOwnCopy(_PC):
    offline_first = True

    def test_the_copy_runs_for_the_new_store_then_it_opens(self):
        seen: dict = {}

        def activate(progress_cb=None, app_version=""):
            from core.online_migrate import store_db_path
            from core.store_link import get_local_android_key

            seen["key"] = smgr.get_active_store_key()
            seen["path"] = store_db_path()
            seen["android_key"] = get_local_android_key()
            other: dict = {}
            t = threading.Thread(target=lambda: other.setdefault("key", smgr.get_active_store_key()))
            t.start()
            t.join()
            seen["other_thread"] = other["key"]
            progress_cb("Copying the store from the server…")
            return {"ok": True, "device_no": 3}

        with mock.patch("core.offline_first.runtime.activate_from_online", side_effect=activate):
            res = sw.switch_to("wholesale")
            self.assertEqual((res["running"], res["progress"]),
                             (True, "/api/offline-first/activate/progress"))
            job = self.wait_for_job()
        new = self.companion_key()
        self.assertIsNone(job["error"])
        self.assertEqual(job["kind"], "wholesale_switch")
        self.assertIn("Copying the store from the server…", job["steps"])
        # the copy worked for the NEW store, into its own file, with its own key ...
        self.assertEqual(seen["key"], new)
        self.assertEqual(seen["path"], smgr.get_store_db_path(new))
        self.assertEqual(seen["android_key"], "SC-W1W2W3W4")
        # ... while the rest of the app stayed on the retail store
        self.assertEqual(seen["other_thread"], RETAIL_KEY)
        self.assertEqual(smgr.get_active_store_key(), new)
        self.assertEqual(self.reopened, [new])
        _, prog = of_api.handle_get("/api/offline-first/activate/progress", None)
        self.assertFalse(prog["running"])

    def test_a_failed_copy_keeps_the_retail_store_open(self):
        with mock.patch("core.offline_first.runtime.activate_from_online",
                        side_effect=RuntimeError("The store could not be copied from the server.")):
            res = sw.switch_to("wholesale")
            self.assertTrue(res["running"])
            job = self.wait_for_job()
        self.assertIn("could not be copied", job["error"])
        self.assertEqual(smgr.get_active_store_key(), RETAIL_KEY)
        self.assertEqual(self.reopened, [])
        self.assertEqual(len(self.keys()), 2)        # added once; the next press tries the copy again
        with mock.patch("core.offline_first.runtime.activate_from_online", return_value={"ok": True}):
            sw.switch_to("wholesale")
            self.wait_for_job()
        self.assertEqual(len(self.keys()), 2)
        self.assertEqual(smgr.get_active_store_key(), self.companion_key())

    def test_a_store_that_has_its_copy_opens_at_once(self):
        with mock.patch("core.offline_first.runtime.activate_from_online") as act:
            with mock.patch.object(sw, "has_offline_first_copy", return_value=True):
                res = sw.switch_to("wholesale")
        act.assert_not_called()
        self.assertFalse(res.get("running"))
        self.assertEqual(smgr.get_active_store_key(), self.companion_key())

    def test_a_second_press_while_copying_is_refused(self):
        gate = threading.Event()

        def slow(progress_cb=None, app_version=""):
            gate.wait(5)
            return {"ok": True}

        with mock.patch("core.offline_first.runtime.activate_from_online", side_effect=slow):
            sw.switch_to("wholesale")
            code, body = wholesale_api.handle_post("/api/wholesale/mode", {"mode": "wholesale"}, None)
            self.assertEqual((code, body["code"]), (409, "busy"))
            gate.set()
            self.wait_for_job()

    def test_has_offline_first_copy_reads_the_store_file(self):
        self.assertFalse(sw.has_offline_first_copy(RETAIL_KEY))
        from core.offline_first.schema import meta_set

        c = sqlite3.connect(smgr.get_store_db_path(RETAIL_KEY))
        try:
            c.execute("CREATE TABLE IF NOT EXISTS of_meta (k TEXT PRIMARY KEY, v TEXT)")
            meta_set(c, "device_no", 4)
            c.commit()
        finally:
            c.close()
        self.assertTrue(sw.has_offline_first_copy(RETAIL_KEY))


class TheRealCopyIsTheTargetStoresWhole(_PC):
    """The real copy (activate_from_online -> download_store_for_offline) against two stand-in
    stores, while this PC is ALREADY offline-first -- as a shop is once it has switched once.

    Shree Gajanan, 23 Oct 2026 (stores 127 / 153): the switch back to retail stopped at "Only 0
    of the server's 1,792 medicines arrived". Every call used the retail store's own token;
    every record was thrown away because the copy applied server records only in Online mode
    ("skipped_offline"). The way to wholesale had done the same, silently: a store of a few
    medicines is under the short-copy tolerance."""

    offline_first = True

    def setUp(self):
        super().setUp()
        # the retail store's file is the server's copy too (the stand-in one-column table goes)
        os.remove(smgr.get_store_db_path(RETAIL_KEY))
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import fake_sync_server
        from test_fast_store_copy import store_data

        self.retail_srv = fake_sync_server.FakeServer(store_data())
        ws = store_data()
        ws["medicines"] = [dict(d, name="WS " + d["name"]) for d in ws["medicines"][:6]]
        for col in ("sales", "purchases", "sales_returns", "purchase_returns",
                    "customer_payments", "supplier_payments"):
            ws[col] = []
        ws["customers"] = [dict(d, name="WS " + d["name"]) for d in ws["customers"][:2]]
        self.ws_srv = fake_sync_server.FakeServer(ws)
        self.tokens: list[str] = []

        def server_for(token):
            self.tokens.append(token or "")
            return self.retail_srv if token == "t-" + RETAIL_KEY else self.ws_srv

        def request(method, path, *, token=None, **kw):
            srv = server_for(token)
            if path.split("?")[0] == "/api/sync/status":
                return {"ok": True, "data": {"head_revision": 0}}
            return srv.request(method, path, token=token, **kw)

        def pull(token, collection, **kw):
            return server_for(token).pull_collection(token, collection, **kw)

        def register(conn, **kw):
            from core.offline_first import client

            self.tokens.append(client._token())
            return {"device_no": 1, "id_base": 0}

        for p in (
            mock.patch("core.server_api._request", side_effect=request),
            mock.patch("core.server_api.pull_collection", side_effect=pull),
            mock.patch("core.offline_first.runtime.register_and_prepare", side_effect=register),
            mock.patch("core.server_live.start_poller", lambda *a, **k: None),
            mock.patch("core.server_live._PULL_PAGE", 7),
            mock.patch.object(wm, "_maybe_refresh", lambda *a, **k: None),
            mock.patch("core.sync_prefs.set_sync_mode", lambda *_a, **_k: None),
        ):
            p.start()
            self.addCleanup(p.stop)

    def names(self, store_key: str) -> list[str]:
        c = sqlite3.connect(smgr.get_store_db_path(store_key))
        try:
            return [r[0] for r in c.execute(
                "SELECT name FROM medicines WHERE COALESCE(deleted, 0) = 0 ORDER BY id")]
        finally:
            c.close()

    def switch(self, mode: str) -> dict:
        self.tokens.clear()
        res = sw.switch_to(mode)
        self.assertTrue(res.get("running"), res)
        return self.wait_for_job()

    def test_retail_to_wholesale_and_back_each_copy_whole_with_its_own_token(self):
        job = self.switch("wholesale")
        self.assertIsNone(job["error"])
        ws_key = self.companion_key()
        self.assertEqual(smgr.get_active_store_key(), ws_key)
        self.assertEqual(set(self.tokens), {"t-" + ws_key})
        names = self.names(ws_key)
        self.assertEqual(len(names), 6, names)
        self.assertTrue(all(n.startswith("WS ") for n in names), names)

        job = self.switch("retail")
        self.assertIsNone(job["error"])
        self.assertEqual(smgr.get_active_store_key(), RETAIL_KEY)
        self.assertEqual(set(self.tokens), {"t-" + RETAIL_KEY})
        names = self.names(RETAIL_KEY)
        # 39 live + the stubs two bills name (98, 99) (tests/test_fast_store_copy.py)
        self.assertEqual(len(names), 41, names)
        self.assertFalse([n for n in names if n.startswith("WS ")])
        c = sqlite3.connect(smgr.get_store_db_path(RETAIL_KEY))
        try:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 14)
        finally:
            c.close()
        # the wholesale copy was left alone
        self.assertEqual(len(self.names(ws_key)), 6)

    def test_a_real_short_copy_is_still_refused_and_the_open_store_stays(self):
        self.assertIsNone(self.switch("wholesale")["error"])
        ws_key = self.companion_key()
        real = self.retail_srv.request

        def more_on_the_server(method, path, **kw):
            res = real(method, path, **kw)
            if path.startswith("/api/store/inventory/counts"):
                res["data"]["all"] = 500
            return res

        self.retail_srv.request = more_on_the_server
        job = self.switch("retail")
        self.assertIn("of the server's 500 medicines arrived", job["error"] or "")
        self.assertEqual(smgr.get_active_store_key(), ws_key)

    def test_a_small_store_that_arrives_empty_is_refused(self):
        """Six medicines on the server, none written: under the tolerance, still refused."""
        with mock.patch("core.server_entity_sync.sync_down_doc", return_value="skipped_offline"):
            job = self.switch("wholesale")
        self.assertIn("Only 0 of the server's 6 medicines arrived", job["error"] or "")
        self.assertEqual(smgr.get_active_store_key(), RETAIL_KEY)


class ActingForAnotherStore(unittest.TestCase):
    def test_only_this_thread_and_only_a_store_on_this_pc(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.object(lm, "_appdata_dir", lambda: tmp):
            smgr.save_registry({"device_role": "admin", "active_store_key": "Store_A",
                                "stores": [{"store_key": "Store_A", "display_name": "A"},
                                           {"store_key": "Store_B", "display_name": "B"}]})
            with smgr.acting_for_store("Store_B"):
                self.assertEqual(smgr.get_active_store_key(), "Store_B")
                self.assertEqual(smgr.get_active_db_path(), smgr.get_store_db_path("Store_B"))
            self.assertEqual(smgr.get_active_store_key(), "Store_A")
            with self.assertRaises(ValueError):
                with smgr.acting_for_store("Store_Missing"):
                    pass


if __name__ == "__main__":
    unittest.main()
