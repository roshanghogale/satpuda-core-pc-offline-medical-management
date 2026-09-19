"""One shop must never be able to reach another shop's books.

These cover the two ways it happened: a locally created store adopting a
same-named store on the server, and a second store inheriting the first store's
pairing key from a single shared file.
"""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ServerStoreAdoptionTests(unittest.TestCase):
    """Pairing must match on identity, not on a name two shops can share."""

    REMOTE = [
        {"store_key": "Store_Shivkrupa", "store_id": "shivkrupa",
         "store_name": "Shivkrupa Medical", "android_key": "SC-AAAA"},
        {"store_key": "Store_Roshan", "store_id": "roshan",
         "store_name": "Roshan", "android_key": "SC-BBBB"},
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
        api.admin_login = lambda: "tok"
        api.list_remote_stores = lambda a: [dict(s) for s in self.REMOTE]
        api.create_store = lambda a, **kw: (
            self.created.append(kw) or dict(kw, android_key="SC-NEW")
        )
        api.ensure_store_session = lambda **kw: {"ok": True, **kw}
        api._pc_device_id = lambda: "pc-1"
        sl.get_local_android_key = lambda *a, **k: ""
        sl._save_local = lambda *a, **k: None

    def tearDown(self):
        (self.live._adoption_path, self.api.admin_login,
         self.api.list_remote_stores, self.api.create_store,
         self.api.ensure_store_session, self.api._pc_device_id,
         self.sl.get_local_android_key, self.sl._save_local,
         self.smgr.get_active_store_key,
         self.smgr.get_active_display_name) = self._saved

    def _pair(self, store_key, display, adopt=False, create=True):
        # server_live imports these from store_manager inside the call.
        #
        # admin_token is passed because everything in this class is about the
        # OPERATOR's path -- searching the account's store list, creating,
        # adopting by name. That path needs the vendor administrator, typed at
        # that moment; without a token ensure_active_store_on_server takes the
        # SC- key path instead and never reads the list at all (covered by
        # TheUnattendedPathCarriesNoCredential below).
        self.smgr.get_active_store_key = lambda: store_key
        self.smgr.get_active_display_name = lambda: display
        return self.live.ensure_active_store_on_server(
            adopt_by_name=adopt, create_if_missing=create, admin_token="tok",
        )

    def test_a_new_store_may_not_take_an_existing_name(self):
        with self.assertRaises(self.live.StoreNameTakenOnServer):
            self._pair("Store_New_Shop", "Shivkrupa Medical")

    def test_the_error_names_the_store_so_the_shop_can_act(self):
        try:
            self._pair("Store_New_Shop", "Shivkrupa Medical")
            self.fail("should have refused")
        except self.live.StoreNameTakenOnServer as exc:
            self.assertIn("Shivkrupa Medical", str(exc))
            self.assertEqual(exc.remote.get("store_id"), "shivkrupa")

    def test_our_own_store_still_pairs_by_key(self):
        res = self._pair("Store_Roshan", "Roshan")
        self.assertEqual(res["store_name"], "Roshan")

    def test_a_free_name_creates_a_new_server_store_when_asked(self):
        self._pair("Store_Brand_New", "Brand New Chemist", create=True)
        self.assertEqual(
            [c.get("store_name") for c in self.created], ["Brand New Chemist"]
        )

    def test_an_unattended_pair_never_invents_a_store(self):
        """Startup, the poller and the 401/403 retry must not create.

        This is how a shop lost sight of its own books: anything that made the
        local store_key stop matching -- a rename, a rebuilt registry -- had this
        function quietly build a NEW empty store on the server and repoint the PC
        at it. In Online mode the desktop holds no data of its own, so the shop
        opened to a clean, working, completely empty app while its real ledger
        sat on the server untouched.
        """
        with self.assertRaises(self.live.StoreNotLinkedOnServer):
            self._pair("Store_Brand_New", "Brand New Chemist", create=False)
        self.assertEqual(self.created, [], "no store may be created")

    def test_the_not_linked_error_names_the_store_so_the_shop_can_act(self):
        try:
            self._pair("Store_Brand_New", "Brand New Chemist", create=False)
            self.fail("should have refused")
        except self.live.StoreNotLinkedOnServer as exc:
            self.assertIn("Brand New Chemist", str(exc))
            self.assertEqual(exc.store_key, "Store_Brand_New")

    def test_a_store_that_does_match_still_pairs_unattended(self):
        """The guard must not lock out a healthy PC on every launch."""
        res = self._pair("Store_Roshan", "Roshan", create=False)
        self.assertEqual(res["store_name"], "Roshan")
        self.assertEqual(self.created, [])

    def test_a_deliberate_join_is_allowed_and_remembered(self):
        res = self._pair("Store_New_Shop", "Shivkrupa Medical", adopt=True)
        self.assertEqual(res["store_name"], "Shivkrupa Medical")
        # Asked once, not on every launch.
        again = self._pair("Store_New_Shop", "Shivkrupa Medical")
        self.assertEqual(again["store_name"], "Shivkrupa Medical")

    def test_a_join_to_one_store_does_not_unlock_another(self):
        self._pair("Store_New_Shop", "Shivkrupa Medical", adopt=True)
        with self.assertRaises(self.live.StoreNameTakenOnServer):
            self._pair("Store_Other", "Roshan")


class TheUnattendedPathCarriesNoCredential(unittest.TestCase):
    """What a shop PC does by itself, and what it must never do by itself.

    ``ensure_active_store_on_server`` runs on every launch, from the poller and
    again on every 401/403 re-pair. It used to open with ``api.admin_login()``,
    whose username and password were compiled into the build -- so the till
    signed itself in as the ADMINISTRATOR of every shop on the account several
    times a day, and the live access log shows exactly that. Anyone who opened
    the downloadable build had the password to every other shop.

    Without a token it now resolves the store from the store's own SC- key,
    which reaches one store and no others.
    """

    def setUp(self):
        import core.server_api as api
        import core.server_live as live
        import core.store_link as sl
        import core.store_manager as smgr

        self.live, self.api, self.sl, self.smgr = live, api, sl, smgr
        self.tmp = tempfile.mkdtemp()
        self.admin_calls = []
        self.listed = []
        self.paired = []
        self._saved = (
            live._adoption_path, api.admin_login, api.list_remote_stores,
            api.create_store, api.ensure_store_session, api._pc_device_id,
            sl.get_local_android_key, smgr.get_active_store_key,
            smgr.get_active_display_name,
        )
        live._adoption_path = lambda: os.path.join(self.tmp, "adopt.json")
        live._clear_link_error()

        def _no_admin(*a, **k):
            self.admin_calls.append((a, k))
            raise AssertionError("a shop PC signed itself in as the administrator")

        api.admin_login = _no_admin
        api.list_remote_stores = lambda a: self.listed.append(a) or []
        api.create_store = lambda a, **kw: self.fail("a store was created")
        api.ensure_store_session = lambda **kw: (
            self.paired.append(kw)
            or {"token": "T", "store_id": "roshan", "store_key": "Store_Roshan",
                "store_name": "Roshan Medical", "android_key": kw["android_key"]}
        )
        api._pc_device_id = lambda: "pc-1"
        smgr.get_active_store_key = lambda: "Store_Roshan"
        smgr.get_active_display_name = lambda: "Roshan Medical"

    def tearDown(self):
        (self.live._adoption_path, self.api.admin_login,
         self.api.list_remote_stores, self.api.create_store,
         self.api.ensure_store_session, self.api._pc_device_id,
         self.sl.get_local_android_key, self.smgr.get_active_store_key,
         self.smgr.get_active_display_name) = self._saved
        self.live._clear_link_error()

    def test_a_paired_pc_resolves_its_store_with_its_own_key(self):
        self.sl.get_local_android_key = lambda *a, **k: "SC-BBBB"
        res = self.live.ensure_active_store_on_server()
        self.assertEqual(res["store_name"], "Roshan Medical")
        self.assertEqual(self.paired[0]["android_key"], "SC-BBBB")
        self.assertEqual(self.admin_calls, [], "the administrator was used")
        self.assertEqual(self.listed, [], "the account's store list was read")

    def test_the_store_id_the_key_resolved_is_recorded(self):
        """So a later link never falls back to matching a display NAME."""
        self.sl.get_local_android_key = lambda *a, **k: "SC-BBBB"
        self.live.ensure_active_store_on_server()
        self.assertEqual(
            self.live._load_adoptions()["Store_Roshan"]["store_id"], "roshan"
        )

    def test_a_pc_with_no_key_is_refused_and_told_what_to_do(self):
        self.sl.get_local_android_key = lambda *a, **k: ""
        with self.assertRaises(self.live.StoreNotLinkedOnServer):
            self.live.ensure_active_store_on_server(create_if_missing=True)
        self.assertEqual(self.admin_calls, [])
        self.assertIn("SC- key", self.live.last_link_error())

    def test_creating_is_impossible_without_a_credential(self):
        """The flag that used to make an empty store appear on the server."""
        self.sl.get_local_android_key = lambda *a, **k: ""
        for flags in ({"create_if_missing": True}, {"create_if_new": True},
                      {"adopt_by_name": True}):
            with self.assertRaises(self.live.StoreNotLinkedOnServer):
                self.live.ensure_active_store_on_server(**flags)
        self.assertEqual(self.admin_calls, [])


class AdminGateTests(unittest.TestCase):
    """Store work behind a PIN, with switching grantable on its own."""

    def setUp(self):
        import core.admin_gate as ag
        import core.settings_mirror as sm

        self.ag = ag
        self._remember = sm.remember
        sm.remember = lambda *a, **k: None
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT)"
        )

    def tearDown(self):
        import core.settings_mirror as sm

        sm.remember = self._remember

    def _try(self, action, pin=None):
        data = {} if pin is None else {"admin_pin": pin}
        try:
            self.ag.check(self.conn, action, data)
            return True
        except self.ag.AdminPinRequired:
            return False

    def test_nothing_is_locked_until_a_pin_is_set(self):
        for a in ("create_store", "switch_store", "delete_store"):
            self.assertTrue(self._try(a), a)

    def test_creating_a_store_needs_the_pin(self):
        self.ag.set_pin(self.conn, "4821")
        self.assertFalse(self._try("create_store"))
        self.assertFalse(self._try("create_store", "0000"))
        self.assertTrue(self._try("create_store", "4821"))

    def test_switching_stays_open_by_default(self):
        self.ag.set_pin(self.conn, "4821")
        self.assertTrue(self._try("switch_store"))

    def test_switching_can_be_tightened(self):
        self.ag.set_pin(self.conn, "4821")
        self.ag.set_switch_needs_pin(self.conn, True)
        self.assertFalse(self._try("switch_store"))
        self.assertTrue(self._try("switch_store", "4821"))

    def test_ordinary_settings_are_never_gated(self):
        self.ag.set_pin(self.conn, "4821")
        self.assertTrue(self._try("export"))
        self.assertTrue(self._try("normalize_names"))

    def test_changing_the_pin_needs_the_old_one(self):
        self.ag.set_pin(self.conn, "4821")
        with self.assertRaises(self.ag.AdminPinRequired):
            self.ag.set_pin(self.conn, "9999", current_pin="0000")
        self.ag.set_pin(self.conn, "9999", current_pin="4821")
        self.assertFalse(self.ag.verify_pin(self.conn, "4821"))
        self.assertTrue(self.ag.verify_pin(self.conn, "9999"))

    def test_a_short_pin_is_refused(self):
        with self.assertRaises(ValueError):
            self.ag.set_pin(self.conn, "12")

    def test_the_pin_itself_is_not_stored(self):
        self.ag.set_pin(self.conn, "4821")
        values = [
            r[0] for r in self.conn.execute("SELECT value FROM settings").fetchall()
        ]
        self.assertNotIn("4821", values)


class PerStorePairingKeyTests(unittest.TestCase):
    """The pairing key belongs to one store, not to the whole machine."""

    def setUp(self):
        import core.store_link as sl

        self.sl = sl
        self.tmp = tempfile.mkdtemp()
        self._saved = (sl._appdata_dir, sl._active_store_key)
        sl._appdata_dir = lambda: self.tmp
        self.store = "Store_A"
        sl._active_store_key = lambda: self.store

    def tearDown(self):
        self.sl._appdata_dir, self.sl._active_store_key = self._saved

    def test_each_store_keeps_its_own_key(self):
        self.sl._save_local("SC-AAAA")
        self.store = "Store_B"
        self.sl._save_local("SC-BBBB")
        self.assertEqual(self.sl.get_local_android_key(), "SC-BBBB")
        self.store = "Store_A"
        self.assertEqual(self.sl.get_local_android_key(), "SC-AAAA")

    def test_a_second_store_does_not_inherit_the_first_key(self):
        self.sl._save_local("SC-AAAA")
        self.store = "Store_B"
        self.assertEqual(self.sl.get_local_android_key(), "")

    def test_an_upgraded_single_store_install_keeps_working(self):
        # Written by a build that had one key file for the whole machine.
        with open(os.path.join(self.tmp, "android_store_key.txt"), "w") as fh:
            fh.write("SC-LEGACY")
        self.assertEqual(self.sl.get_local_android_key(), "SC-LEGACY")


if __name__ == "__main__":
    unittest.main(verbosity=2)
