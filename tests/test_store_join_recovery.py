"""The way back: connecting a PC to the store it actually belongs to.

StoreNotLinkedOnServer has always ended with "Open Settings and connect it to
the existing store." There was no such screen. `adopt_by_name=True` was passed
by no production code at all, and the 409 the engine answers the offline->online
switch with -- carrying the name and id of the store already holding the name --
was thrown away in the browser as a bare Error.

So every refusal was a dead end, which is why the refusals had to be soft: the
buttons created a replacement store instead. This is the screen that lets them
be hard.

The dangerous direction is the point of most of these: a join is the one action
that can deliberately point this PC at a different shop, so it refuses before it
writes, it never hands out a pairing key, and it never carries this shop's rows
into the other shop's books.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class JoinAnExistingStore(unittest.TestCase):
    REMOTE = [
        {"store_key": "Store_Shivkrupa", "store_id": "shivkrupa",
         "store_name": "Shivkrupa Medical", "android_key": "SC-AAAA"},
        {"store_key": "Store_Roshan", "store_id": "roshan",
         "store_name": "Roshan Medical", "android_key": "SC-BBBB"},
    ]

    def setUp(self):
        import core.server_api as api
        import core.server_live as live
        import core.store_link as sl
        import core.store_manager as smgr

        self.live, self.api, self.sl, self.smgr = live, api, sl, smgr
        self.tmp = tempfile.mkdtemp()
        self.saved_keys = []
        self.sessions = []
        self.regenerated = []
        self._saved = (
            live._adoption_path, api.admin_login, api.list_remote_stores,
            api.ensure_store_session, api.regenerate_android_key, api._pc_device_id,
            sl.get_local_android_key, sl._save_local,
            smgr.get_active_store_key, smgr.get_active_display_name,
            smgr.list_stores,
        )
        live._adoption_path = lambda: os.path.join(self.tmp, "adopt.json")
        live._clear_link_error()
        api.admin_login = lambda: "tok"
        api.list_remote_stores = lambda a: [dict(s) for s in self.REMOTE]
        api.ensure_store_session = lambda **kw: (
            self.sessions.append(kw) or {"ok": True, "token": "T", **kw}
        )
        api.regenerate_android_key = lambda a, sid: (
            self.regenerated.append(sid) or {"store_id": sid, "android_key": "SC-FRESH"}
        )
        api._pc_device_id = lambda: "pc-1"
        sl.get_local_android_key = lambda *a, **k: "SC-OLD"
        sl._save_local = lambda k, *a, **kw: self.saved_keys.append(k)
        smgr.get_active_store_key = lambda: "Store_Local"
        smgr.get_active_display_name = lambda: "Local Shop"
        smgr.list_stores = lambda: [{"store_key": "Store_Local"}]

    def tearDown(self):
        (self.live._adoption_path, self.api.admin_login,
         self.api.list_remote_stores, self.api.ensure_store_session,
         self.api.regenerate_android_key, self.api._pc_device_id,
         self.sl.get_local_android_key, self.sl._save_local,
         self.smgr.get_active_store_key, self.smgr.get_active_display_name,
         self.smgr.list_stores) = self._saved
        self.live._clear_link_error()

    def _join(self, **kw):
        args = {
            "store_key": "Store_Local",
            "store_id": "shivkrupa",
            "confirm_name": "Shivkrupa Medical",
            # Picking a shop out of the whole account's list is the VENDOR
            # administrator's act. The token used to come from a password
            # compiled into the build; it is typed on the screen now.
            "admin_token": "tok",
        }
        args.update(kw)
        return self.live.join_server_store(**args)

    # --- the list ----------------------------------------------------------

    def test_the_list_never_carries_a_pairing_key(self):
        """The engine listens on 127.0.0.1 and answers any origin.

        A list that included android_key would let any page open in a browser on
        the counter PC read the pairing key of every shop on the account.
        """
        rows = self.live.list_server_stores(admin_token="tok")
        blob = json.dumps(rows)
        self.assertNotIn("android_key", blob)
        self.assertNotIn("SC-AAAA", blob)
        self.assertEqual(
            {r["store_name"] for r in rows},
            {"Shivkrupa Medical", "Roshan Medical"},
        )

    def test_a_store_another_local_store_already_uses_is_marked(self):
        with open(os.path.join(self.tmp, "adopt.json"), "w", encoding="utf-8") as fh:
            json.dump({"Store_Other": {"store_id": "roshan"}}, fh)
        rows = {r["store_id"]: r
                for r in self.live.list_server_stores(admin_token="tok")}
        self.assertEqual(rows["roshan"]["used_by_local_store"], "Store_Other")
        self.assertEqual(rows["shivkrupa"]["used_by_local_store"], "")

    # --- the join ----------------------------------------------------------

    def test_a_join_pairs_and_is_remembered(self):
        res = self._join()
        self.assertTrue(res["ok"])
        self.assertEqual(res["store_name"], "Shivkrupa Medical")
        self.assertEqual(self.saved_keys, ["SC-AAAA"])
        with open(os.path.join(self.tmp, "adopt.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["Store_Local"]["store_id"], "shivkrupa")

    def test_the_typed_name_has_to_match(self):
        with self.assertRaises(RuntimeError):
            self._join(confirm_name="Shivkrupa")
        self.assertEqual(self.saved_keys, [], "the pairing key was overwritten")
        self.assertEqual(self.sessions, [], "the PC was paired anyway")

    def test_an_unknown_store_id_is_refused(self):
        with self.assertRaises(RuntimeError):
            self._join(store_id="ghost")
        self.assertEqual(self.sessions, [])

    def test_a_store_switched_underneath_is_refused(self):
        """The list is a snapshot; another tab may have switched store since."""
        self.smgr.get_active_store_key = lambda: "Store_Somewhere_Else"
        with self.assertRaises(RuntimeError):
            self._join()
        self.assertEqual(self.sessions, [])

    def test_two_local_stores_may_not_share_one_server_store(self):
        with open(os.path.join(self.tmp, "adopt.json"), "w", encoding="utf-8") as fh:
            json.dump({"Store_Other": {"store_id": "shivkrupa"}}, fh)
        with self.assertRaises(RuntimeError):
            self._join()
        self.assertEqual(self.sessions, [])

    def test_an_existing_pairing_key_is_not_rotated(self):
        """Rotating it would unpair every phone and second PC on that store."""
        self._join()
        self.assertEqual(self.regenerated, [])

    def test_the_stale_cursors_are_cleared_so_history_is_not_skipped(self):
        with mock.patch("core.sync_watermarks.clear_all") as wm, \
             mock.patch("core.sync_revision.set_head_revision") as rev:
            res = self._join()
        self.assertTrue(wm.called, "the old store's high-water mark was kept")
        self.assertTrue(rev.called, "the old store's head revision was kept")
        self.assertIn("watermarks", res["cleared"])


class AnAdoptionIsAnIdentity(unittest.TestCase):
    """Once a person says which store this is, a folder name stops deciding."""

    REMOTE = [
        {"store_key": "Store_Shivkrupa", "store_id": "shivkrupa",
         "store_name": "Shivkrupa Medical", "android_key": "SC-AAAA"},
        {"store_key": "Store_Renamed", "store_id": "renamed",
         "store_name": "Local Shop", "android_key": "SC-CCCC"},
    ]

    def setUp(self):
        import core.server_api as api
        import core.server_live as live
        import core.store_link as sl
        import core.store_manager as smgr

        self.live, self.api, self.sl, self.smgr = live, api, sl, smgr
        self.tmp = tempfile.mkdtemp()
        self.created = []
        self._saved = (
            live._adoption_path, api.admin_login, api.list_remote_stores,
            api.create_store, api.ensure_store_session, api._pc_device_id,
            sl.get_local_android_key, sl._save_local,
            smgr.get_active_store_key, smgr.get_active_display_name,
        )
        live._adoption_path = lambda: os.path.join(self.tmp, "adopt.json")
        live._clear_link_error()
        api.admin_login = lambda: "tok"
        api.list_remote_stores = lambda a: [dict(s) for s in self.REMOTE]
        api.create_store = lambda a, **kw: (
            self.created.append(kw) or dict(kw, android_key="SC-NEW")
        )
        api.ensure_store_session = lambda **kw: {"ok": True, "token": "T", **kw}
        api._pc_device_id = lambda: "pc-1"
        sl.get_local_android_key = lambda *a, **k: ""
        sl._save_local = lambda *a, **k: None
        smgr.get_active_store_key = lambda: "Store_Local"
        smgr.get_active_display_name = lambda: "Local Shop"

    def tearDown(self):
        (self.live._adoption_path, self.api.admin_login,
         self.api.list_remote_stores, self.api.create_store,
         self.api.ensure_store_session, self.api._pc_device_id,
         self.sl.get_local_android_key, self.sl._save_local,
         self.smgr.get_active_store_key,
         self.smgr.get_active_display_name) = self._saved
        self.live._clear_link_error()

    def _adopt(self, entry):
        with open(os.path.join(self.tmp, "adopt.json"), "w", encoding="utf-8") as fh:
            json.dump({"Store_Local": entry}, fh)

    def test_the_recorded_store_wins_over_a_name_that_matches_another(self):
        """Store_Renamed answers to "Local Shop" -- the name this PC shows.

        Without the adoption, the name heuristic would hand this PC that store.
        """
        self._adopt({"store_id": "shivkrupa", "store_name": "Shivkrupa Medical"})
        res = self.live.ensure_active_store_on_server(admin_token="tok")
        self.assertEqual(res["store_name"], "Shivkrupa Medical")

    def test_an_adopted_store_that_is_gone_is_never_replaced_with_a_new_one(self):
        self._adopt({"store_id": "deleted_shop", "store_name": "Deleted Shop"})
        with self.assertRaises(self.live.StoreNotLinkedOnServer):
            self.live.ensure_active_store_on_server(create_if_missing=True, admin_token="tok")
        self.assertEqual(self.created, [], "an empty replacement store was created")
        self.assertIn("Deleted Shop", self.live.last_link_error())

    def test_with_no_adoption_nothing_changes(self):
        """Every installed PC today has no ledger. This path must be untouched.

        Store_Renamed already answers to "Local Shop" on the server, and a
        shared NAME is not an identity -- so with no adoption recorded this is
        still refused as a name clash, exactly as it was before the pass was
        added. The new branch must be invisible until someone joins.
        """
        with self.assertRaises(self.live.StoreNameTakenOnServer):
            self.live.ensure_active_store_on_server(create_if_missing=True, admin_token="tok")
        self.assertEqual(self.created, [])

    def test_a_matching_store_key_still_pairs_with_no_adoption(self):
        self.smgr.get_active_store_key = lambda: "Store_Renamed"
        res = self.live.ensure_active_store_on_server(admin_token="tok")
        self.assertEqual(res["store_name"], "Local Shop")
        self.assertEqual(self.created, [])

    def test_the_refusal_sentence_is_kept_for_a_screen_to_show(self):
        self.smgr.get_active_store_key = lambda: "Store_Nowhere"
        self.smgr.get_active_display_name = lambda: "Nowhere Chemist"
        with self.assertRaises(self.live.StoreNotLinkedOnServer):
            self.live.ensure_active_store_on_server(admin_token="tok")
        self.assertIn("Nowhere Chemist", self.live.last_link_error())
        # And a healthy pair clears it, so the banner does not stick.
        self.smgr.get_active_store_key = lambda: "Store_Renamed"
        self.smgr.get_active_display_name = lambda: "Local Shop"
        self.live.ensure_active_store_on_server(admin_token="tok")
        self.assertEqual(self.live.last_link_error(), "")


class JoiningDoesNotCarryThisShopsRowsIntoTheOther(unittest.TestCase):
    """The same disaster, running the other way.

    A join points this PC at a shop whose ledger is already on the server, while
    the local database still holds whatever this PC was billing. Every path that
    uploads the local database is therefore a way to merge two shops' books.

    The dangerous one is not the offline->online flip, which a person is at
    least watching: it is OnlineMigrateDialog, which opens BY ITSELF on any
    Online launch that finds local rows, days later, in front of whoever opened
    the shop that morning.
    """

    def setUp(self):
        import core.server_live as live

        self.live = live
        self.tmp = tempfile.mkdtemp()
        self._saved = live._adoption_path
        live._adoption_path = lambda: os.path.join(self.tmp, "adopt.json")

    def tearDown(self):
        self.live._adoption_path = self._saved

    def _record(self, entry):
        with open(os.path.join(self.tmp, "adopt.json"), "w", encoding="utf-8") as fh:
            json.dump({"Store_Local": entry}, fh)

    def test_the_self_opening_migrate_dialog_refuses_to_merge_a_joined_store(self):
        from core import online_migrate

        self._record({"store_id": "shivkrupa", "joined_existing": True})
        with mock.patch("core.store_manager.get_active_store_key", return_value="Store_Local"), \
             mock.patch("core.server_sync.push_active_store_to_server") as push:
            res = online_migrate.push_local_then_wipe()
        self.assertFalse(res["ok"])
        self.assertIn("two shops", res["error"])
        self.assertFalse(push.called, "the local ledger was uploaded into the joined store")

    def test_a_store_this_pc_published_itself_still_migrates(self):
        """The guard must not block the ordinary offline->online move."""
        from core import online_migrate

        self._record({"store_id": "shivkrupa"})
        with mock.patch("core.store_manager.get_active_store_key", return_value="Store_Local"), \
             mock.patch.object(online_migrate, "store_db_path", return_value=""), \
             mock.patch.object(online_migrate, "wipe_local_store", return_value={"ok": True}):
            res = online_migrate.push_local_then_wipe()
        self.assertTrue(res["ok"])

    def test_the_join_marker_survives_the_next_pair(self):
        """_record_store_adoption runs on every pair and rebuilt the entry."""
        self._record({"store_id": "shivkrupa", "store_name": "Shivkrupa Medical",
                      "joined_existing": True})
        self.live._record_store_adoption(
            "Store_Local", {"store_id": "shivkrupa", "store_name": "Shivkrupa Medical"}
        )
        with open(os.path.join(self.tmp, "adopt.json"), encoding="utf-8") as fh:
            self.assertTrue(json.load(fh)["Store_Local"]["joined_existing"])

    def test_the_marker_does_not_follow_the_pc_to_a_different_store(self):
        self._record({"store_id": "shivkrupa", "joined_existing": True})
        self.live._record_store_adoption(
            "Store_Local", {"store_id": "roshan", "store_name": "Roshan Medical"}
        )
        with open(os.path.join(self.tmp, "adopt.json"), encoding="utf-8") as fh:
            self.assertNotIn("joined_existing", json.load(fh)["Store_Local"])

    def test_a_wrong_join_can_be_undone(self):
        self._record({"store_id": "shivkrupa", "joined_existing": True})
        with mock.patch("core.store_manager.get_active_store_key", return_value="Store_Local"):
            res = self.live.forget_store_adoption("Store_Local")
        self.assertTrue(res["ok"])
        self.assertEqual(self.live._load_adoptions(), {})


class TheLedgerDoesNotChooseTheShopByTheAlphabet(unittest.TestCase):
    """The record is an identity, but it is stored sort_keys=True.

    Taking the first recorded key would be the alphabet picking again under a
    better name -- and on this machine the alphabet picks the empty leftover
    folder over the one holding the shop's database.
    """

    def test_a_recorded_store_with_no_database_loses_to_one_with_a_database(self):
        import core.server_live as live
        import core.store_manager as smgr

        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "stores")
            os.makedirs(os.path.join(root, "Store_Default"))
            os.makedirs(os.path.join(root, "Store_ZZ_Real"))
            open(os.path.join(root, "Store_Default", "backup_slots.dat"), "wb").close()
            with open(os.path.join(root, "Store_ZZ_Real", "veterinary.db"), "wb") as fh:
                fh.write(b"x" * 4096)
            adoptions = {
                "Store_Default": {"store_id": "a_default"},
                "Store_ZZ_Real": {"store_id": "z_real"},
            }
            with mock.patch.object(live, "_load_adoptions", return_value=adoptions):
                picked = smgr._preferred_active_store(
                    root, ["Store_Default", "Store_ZZ_Real"]
                )
        self.assertEqual(picked, "Store_ZZ_Real")


class AnIdentityErrorNeverFabricatesAPairingKey(unittest.TestCase):
    """core.store_link.regenerate_android_key ends in a bare except that

    answers any failure by inventing a pairing key and writing it over this
    PC's real one. The errors that exist to stop a PC pairing to the wrong
    store would, on their way past, have destroyed its link to the right one.
    """

    def test_a_refusal_propagates_instead_of_overwriting_the_key(self):
        import core.server_live as live
        import core.store_link as sl

        saved = []
        with mock.patch.object(
            live, "ensure_active_store_on_server",
            side_effect=live.StoreNotLinkedOnServer("Store_X", "X Medical"),
        ), mock.patch.object(sl, "_save_local", lambda k, *a, **kw: saved.append(k)):
            with self.assertRaises(live.StoreNotLinkedOnServer):
                sl.regenerate_android_key("X Medical", admin_token="tok")
        self.assertEqual(saved, [], "a made-up key was written over the real one")

    def test_a_network_failure_still_falls_back_to_a_local_key(self):
        """The fallback exists for a reason; only identity errors bypass it."""
        import core.server_live as live
        import core.store_link as sl

        saved = []
        with mock.patch.object(
            live, "ensure_active_store_on_server", side_effect=OSError("no route to host"),
        ), mock.patch.object(sl, "_save_local", lambda k, *a, **kw: saved.append(k)):
            key = sl.regenerate_android_key("X Medical", admin_token="tok")
        self.assertTrue(key.startswith("SC-"))
        self.assertEqual(saved, [key])


if __name__ == "__main__":
    unittest.main()
