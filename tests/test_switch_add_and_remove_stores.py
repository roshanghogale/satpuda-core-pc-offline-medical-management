"""Switching, adding and removing stores on one PC (2 Oct 2026).

The owner typed the Satpuda administrator username and password and still could
not switch to another shop, and could not take a store off the PC at all:

  * the server list's only button, Connect this PC, re-points the store that is
    OPEN NOW -- the chosen shop never reached the Switch list;
  * a PC set up from a Drive restore is locked to one store, and no password
    unlocked it;
  * there was no remove.

These run against a real registry in a temporary app-data folder; only the
server is stood in for.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.license_manager as lm  # noqa: E402
import core.store_manager as smgr  # noqa: E402


class _TempAppData(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        p = mock.patch.object(lm, "_appdata_dir", lambda: self.tmp)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        smgr.save_registry({
            "device_role": "admin",
            "active_store_key": "Store_Ramesh_Medical",
            "stores": [
                {"store_key": "Store_Ramesh_Medical", "display_name": "Ramesh Medical"},
                {"store_key": "Store_Old_Shop", "display_name": "Old Shop"},
            ],
        })
        for key in ("Store_Ramesh_Medical", "Store_Old_Shop"):
            with open(smgr.get_store_db_path(key), "wb") as fh:
                fh.write(b"db of " + key.encode())
        with open(os.path.join(self.tmp, "store_key_Store_Old_Shop.txt"), "w") as fh:
            fh.write("SC-OLD1")


class RemoveAStoreFromThisPC(_TempAppData):
    def test_it_leaves_the_list_and_its_files_are_kept(self):
        gone = smgr.remove_store("Store_Old_Shop", confirm_name="old shop")
        self.assertEqual(["Store_Ramesh_Medical"], [s["store_key"] for s in smgr.list_stores()])
        kept = os.path.join(gone["kept_in"], "Store_Old_Shop", "veterinary.db")
        self.assertTrue(os.path.isfile(kept))
        self.assertTrue(os.path.isfile(os.path.join(gone["kept_in"], "store_key_Store_Old_Shop.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "store_key_Store_Old_Shop.txt")))

    def test_the_next_start_does_not_bring_it_back(self):
        smgr.remove_store("Store_Old_Shop", confirm_name="Old Shop")
        smgr.ensure_registry_on_startup()           # adds back any Store_* folder in stores\
        self.assertNotIn("Store_Old_Shop", [s["store_key"] for s in smgr.list_stores()])

    def test_the_open_store_the_last_store_and_a_wrong_name_are_refused(self):
        with self.assertRaises(ValueError):
            smgr.remove_store("Store_Ramesh_Medical", confirm_name="Ramesh Medical")
        with self.assertRaises(ValueError):
            smgr.remove_store("Store_Old_Shop", confirm_name="Old")
        smgr.remove_store("Store_Old_Shop", confirm_name="Old Shop")
        with self.assertRaises(ValueError):
            smgr.remove_store("Store_Ramesh_Medical", confirm_name="Ramesh Medical")
        self.assertEqual(1, len(smgr.list_stores()))


class TheSettingsActions(_TempAppData):
    def conn(self):
        import sqlite3

        c = sqlite3.connect(":memory:")
        self.addCleanup(c.close)
        return c

    def run_action(self, **data):
        from core.desktop_settings_service import system_action

        return system_action(self.conn(), data)

    def test_remove_answers_with_the_new_list(self):
        res = self.run_action(action="remove_store", store_key="Store_Old_Shop", confirm_name="Old Shop")
        self.assertTrue(res["ok"], res)
        self.assertEqual(["Store_Ramesh_Medical"], [s["store_key"] for s in res["stores"]])
        self.assertIn("nothing on the server was deleted", res["message"])

    def test_remove_says_why_when_refused(self):
        res = self.run_action(action="remove_store", store_key="Store_Ramesh_Medical",
                              confirm_name="Ramesh Medical")
        self.assertFalse(res["ok"])
        self.assertIn("Switch to another store first", res["error"])

    def test_a_single_store_pc_is_unlocked_by_the_administrator_sign_in(self):
        reg = smgr.load_registry()
        reg["device_role"] = "satellite"
        smgr.save_registry(reg)
        refused = self.run_action(action="switch_store", store_key="Store_Old_Shop")
        self.assertFalse(refused["ok"])

        with mock.patch("core.admin_session.sign_in", return_value="admin-tok") as signed:
            res = self.run_action(action="unlock_store_switching",
                                  admin_username="admin", admin_password="typed")
        signed.assert_called_once()
        self.assertTrue(res["ok"], res)
        self.assertFalse(smgr.is_satellite_device())
        self.assertTrue(self.run_action(action="switch_store", store_key="Store_Old_Shop")["ok"])

    def test_unlock_without_the_sign_in_asks_for_it(self):
        reg = smgr.load_registry()
        reg["device_role"] = "satellite"
        smgr.save_registry(reg)
        from core.server_api import AdminCredentialRequired

        with mock.patch("core.admin_session.token", side_effect=AdminCredentialRequired()):
            res = self.run_action(action="unlock_store_switching")
        self.assertEqual("admin_credential_required", res.get("code"))
        self.assertTrue(smgr.is_satellite_device())


class AddAServerStoreBesideThisOne(_TempAppData):
    REMOTE = [
        {"store_id": "sunil-medical", "store_key": "Store_Sunil_Medical",
         "store_name": "Sunil Medical", "android_key": "SC-SUNIL"},
        {"store_id": "ramesh-medical", "store_key": "Store_Ramesh_Medical",
         "store_name": "Ramesh Medical", "android_key": "SC-RAMESH"},
    ]

    def setUp(self):
        super().setUp()
        import core.server_api as api

        self.sessions = []
        for name, value in (
            ("list_remote_stores", lambda tok: [dict(s) for s in self.REMOTE]),
            ("ensure_store_session", lambda **kw: self.sessions.append(kw) or {"token": "T"}),
            ("_pc_device_id", lambda: "pc-1"),
        ):
            p = mock.patch.object(api, name, value)
            p.start()
            self.addCleanup(p.stop)

    def add(self, **kw):
        import core.server_live as live

        return live.add_server_store(admin_token="admin-tok", **kw)

    def test_it_reaches_the_switch_list_without_touching_the_open_store(self):
        res = self.add(store_id="sunil-medical", confirm_name="sunil medical")
        self.assertTrue(res["ok"], res)
        keys = [s["store_key"] for s in smgr.list_stores()]
        self.assertIn(res["created_store_key"], keys)
        self.assertEqual("Store_Ramesh_Medical", smgr.get_active_store_key())
        from core.store_link import get_local_android_key

        self.assertEqual("SC-SUNIL", get_local_android_key(res["created_store_key"]))
        self.assertEqual(res["created_store_key"], self.sessions[0]["store_key"])
        self.assertTrue(self.sessions[0]["force_pair"])
        adopt = json.load(open(os.path.join(self.tmp, "store_adoptions.json"), encoding="utf-8"))
        self.assertEqual("sunil-medical", adopt[res["created_store_key"]]["store_id"])

    def test_the_name_must_be_typed(self):
        with self.assertRaises(RuntimeError):
            self.add(store_id="sunil-medical", confirm_name="Sunil")
        self.assertEqual(2, len(smgr.list_stores()))
        self.assertEqual([], self.sessions)

    def test_a_store_already_here_is_offered_as_switch(self):
        with open(os.path.join(self.tmp, "store_key_Store_Ramesh_Medical.txt"), "w") as fh:
            fh.write("SC-RAMESH")                       # paired with its own SC- key
        res = self.add(store_id="ramesh-medical", confirm_name="Ramesh Medical")
        self.assertFalse(res["ok"])
        self.assertEqual("already_on_pc", res["code"])
        self.assertEqual("Store_Ramesh_Medical", res["store_key"])
        self.assertEqual([], self.sessions)

    def test_a_same_named_store_not_linked_to_it_is_not_taken_over(self):
        res = self.add(store_id="ramesh-medical", confirm_name="Ramesh Medical")
        self.assertEqual("name_on_pc", res["code"])
        self.assertEqual([], self.sessions)

    def test_the_server_refusing_leaves_this_pc_as_it_was(self):
        import core.server_api as api

        with mock.patch.object(api, "ensure_store_session", return_value={}):
            with self.assertRaises(RuntimeError):
                self.add(store_id="sunil-medical", confirm_name="Sunil Medical")
        self.assertEqual(2, len(smgr.list_stores()))


class TheOldSharedKeyBelongsToOneStore(_TempAppData):
    """Switch used to pair the store switched TO with the first shop's key.

    A PC from before per-store keys has one android_store_key.txt and, often, no
    record of whose it is. Every store without its own key read it -- so after
    Switch the "other" store opened the first shop's books, and the server's name
    was written over its label.
    """

    def setUp(self):
        super().setUp()
        os.remove(os.path.join(self.tmp, "store_key_Store_Old_Shop.txt"))
        with open(os.path.join(self.tmp, "android_store_key.txt"), "w") as fh:
            fh.write("SC-FIRST")

    def key(self, store):
        from core.store_link import get_local_android_key

        return get_local_android_key(store)

    def test_once_a_store_has_paired_with_it_no_other_store_gets_it(self):
        from core.store_link import claim_legacy_key

        self.assertEqual("SC-FIRST", self.key("Store_Ramesh_Medical"))
        claim_legacy_key("Store_Ramesh_Medical", "SC-FIRST")
        self.assertEqual("SC-FIRST", self.key("Store_Ramesh_Medical"))
        self.assertEqual("", self.key("Store_Old_Shop"))

    def test_the_store_the_registry_names_gets_it(self):
        reg = smgr.load_registry()
        for s in reg["stores"]:
            if s["store_key"] == "Store_Old_Shop":
                s["android_key"] = "SC-FIRST"
        smgr.save_registry(reg)
        self.assertEqual("SC-FIRST", self.key("Store_Old_Shop"))
        self.assertEqual("", self.key("Store_Ramesh_Medical"))

    def test_a_store_with_its_own_key_keeps_it(self):
        with open(os.path.join(self.tmp, "store_key_Store_Old_Shop.txt"), "w") as fh:
            fh.write("SC-OWN")
        self.assertEqual("SC-OWN", self.key("Store_Old_Shop"))

    def test_pairing_claims_it(self):
        import core.server_api as api
        import core.server_live as live

        with mock.patch.object(api, "ensure_store_session",
                               return_value={"token": "T", "store_id": "ramesh"}),              mock.patch.object(api, "_pc_device_id", return_value="pc"):
            live._link_by_pairing_key(store_key="Store_Ramesh_Medical", name="Ramesh Medical")
        self.assertEqual("", self.key("Store_Old_Shop"))
        with self.assertRaises(live.StoreNotLinkedOnServer):
            live._link_by_pairing_key(store_key="Store_Old_Shop", name="Old Shop")


if __name__ == "__main__":
    unittest.main()
