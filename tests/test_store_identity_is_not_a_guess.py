"""The blackout: a shop opens to a clean, healthy, completely empty app.

Online mode keeps no local data -- the engine's connection is
``sqlite3.connect(":memory:")`` and every read goes to the server. So anything
that breaks the link to the server does not produce an error; it produces
zeroes. Home showed a day with no sales, the dropdowns showed a shop with no
medicines, and nothing anywhere said the server had not answered.

Worse, several of the buttons an owner presses when the numbers look wrong used
to CREATE the missing store on the server, repoint the PC at it and overwrite
the saved pairing key -- so pressing "Verify server sync" caused the thing it
was pressed to diagnose.

These tests pin the four halves that a test can hold:
  * Home says the read failed instead of rendering zero rupees
  * the catalog remembers why it is empty, and an empty read never overwrites
    the on-disk snapshot a restart falls back to
  * a store that has been paired before is never silently re-created
  * a rebuilt registry does not let the alphabet choose which shop opens
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import home_dashboard, online_catalog, server_live, store_manager  # noqa: E402


def _empty_conn():
    """The shape Online mode actually runs on: an empty in-memory database."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from core import db_setup

    db_setup.initialise(conn)
    return conn


class HomeSaysTheServerDidNotAnswer(unittest.TestCase):
    def test_online_failure_is_reported_not_rendered_as_zero(self):
        conn = _empty_conn()
        with mock.patch("core.sync_prefs.is_online_mode", return_value=True), \
             mock.patch(
                 "core.store_query_client.home_summary",
                 side_effect=RuntimeError("Connection refused"),
             ):
            stats = home_dashboard.query_dashboard_stats(conn)
        # The figures still come back -- Home must never fail to render.
        self.assertTrue(stats["values"])
        self.assertTrue(
            stats.get("server_error"),
            "Home fell through to the empty database and said nothing about it",
        )
        self.assertIn("server", stats["server_error"].lower())

    def test_offline_is_not_an_error(self):
        conn = _empty_conn()
        with mock.patch("core.sync_prefs.is_online_mode", return_value=False):
            stats = home_dashboard.query_dashboard_stats(conn)
        self.assertEqual(stats.get("server_error"), "")


class CatalogRemembersWhyItIsEmpty(unittest.TestCase):
    def setUp(self):
        online_catalog.invalidate()
        with online_catalog._lock:
            online_catalog._last_error.clear()
        # Point the snapshot somewhere empty: otherwise the failure path falls
        # back to whatever this machine's real catalog snapshot holds, and the
        # test reads a live shop's customers.
        self._tmp = tempfile.TemporaryDirectory()
        self._snap = mock.patch.object(
            online_catalog,
            "_snapshot_path",
            return_value=os.path.join(self._tmp.name, "snapshot.json"),
        )
        self._snap.start()

    def tearDown(self):
        self._snap.stop()
        self._tmp.cleanup()
        online_catalog.invalidate()
        with online_catalog._lock:
            online_catalog._last_error.clear()

    def test_failed_read_is_observable_and_clears_on_success(self):
        def _boom():
            raise RuntimeError("Cannot reach the server")

        rows = online_catalog._cached_list("customers", _boom, force=True)
        # Still a list: raising here would take out bill save and printing.
        self.assertEqual(rows, [])
        self.assertIn("Cannot reach the server", online_catalog.last_error())
        self.assertIn("Cannot reach the server", online_catalog.last_error("customers"))

        online_catalog._cached_list("customers", lambda: [{"id": 1}], force=True)
        self.assertEqual(online_catalog.last_error(), "")

    def test_a_genuinely_empty_shelf_is_not_an_error(self):
        online_catalog._cached_list("customers", lambda: [], force=True)
        self.assertEqual(online_catalog.last_error(), "")


class AnEmptyReadDoesNotOverwriteTheSnapshot(unittest.TestCase):
    """The line that made the blackout survive a restart.

    ``if not snap: return`` -- and a dict of empty lists is truthy. So a
    successful zero-row read against the wrong store was written straight over
    the good snapshot, and the emptiness came back on every launch even after
    the link was repaired.
    """

    def test_all_empty_snapshot_is_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "snapshot.json")
            good = {"customers": [{"id": 1, "name": "Real Customer"}]}
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(good, fh)

            online_catalog.invalidate()
            with online_catalog._lock:
                for key in online_catalog._SNAP_KEYS:
                    online_catalog._cache[key] = (1.0, [])
            with mock.patch.object(online_catalog, "_snapshot_path", return_value=path):
                online_catalog._persist_snapshot()

            with open(path, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh), good)
            online_catalog.invalidate()

    def test_a_real_snapshot_still_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "snapshot.json")
            online_catalog.invalidate()
            with online_catalog._lock:
                online_catalog._cache["customers"] = (1.0, [{"id": 7}])
            with mock.patch.object(online_catalog, "_snapshot_path", return_value=path):
                online_catalog._persist_snapshot()
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["customers"], [{"id": 7}])
            online_catalog.invalidate()


class APairedStoreIsNeverSilentlyRecreated(unittest.TestCase):
    """create_if_new: publish a new store, refuse to replace a paired one."""

    def _run(self, *, paired: bool, create_if_new: bool):
        with mock.patch.object(server_live, "_was_paired_before", return_value=paired), \
             mock.patch("core.store_manager.get_active_store_key", return_value="Store_Roshan"), \
             mock.patch("core.store_manager.get_active_display_name", return_value="Roshan Medical"), \
             mock.patch("core.server_api.list_remote_stores", return_value=[]), \
             mock.patch("core.server_api.create_store") as created, \
             mock.patch("core.server_api.ensure_store_session", return_value={"token": "s"}), \
             mock.patch("core.store_link.get_local_android_key", return_value="SC-1"), \
             mock.patch("core.store_link._save_local"), \
             mock.patch("core.server_api._pc_device_id", return_value="pc"):
            created.return_value = {"store_id": "roshan", "android_key": "SC-1"}
            try:
                # An operator's publish, so it carries a typed administrator
                # token. Without one the call takes the SC- key path and cannot
                # create anything at all.
                server_live.ensure_active_store_on_server(
                    create_if_new=create_if_new, admin_token="tok"
                )
                return created.called, None
            except server_live.StoreNotLinkedOnServer as exc:
                return created.called, exc

    def test_a_store_this_pc_has_used_before_is_refused(self):
        called, exc = self._run(paired=True, create_if_new=True)
        self.assertFalse(called, "recreated a store the PC was already paired to")
        self.assertIsNotNone(exc)

    def test_a_brand_new_store_still_publishes(self):
        called, exc = self._run(paired=False, create_if_new=True)
        self.assertIsNone(exc)
        self.assertTrue(called, "first publish of a new store was refused")

    def test_the_default_still_refuses_everything(self):
        with mock.patch.object(server_live, "_was_paired_before", return_value=False), \
             mock.patch("core.store_manager.get_active_store_key", return_value="Store_Roshan"), \
             mock.patch("core.store_manager.get_active_display_name", return_value="Roshan"), \
             mock.patch("core.server_api.list_remote_stores", return_value=[]), \
             mock.patch("core.store_link.get_local_android_key", return_value=""), \
             mock.patch("core.server_api.create_store") as created:
            with self.assertRaises(server_live.StoreNotLinkedOnServer):
                server_live.ensure_active_store_on_server(admin_token="tok")
            self.assertFalse(created.called)

    def test_the_unattended_default_never_reaches_the_administrator(self):
        """No token at all: the SC- key decides, and nothing else is read.

        This is the launch path, and it used to sign the shop's PC in as the
        vendor administrator before doing anything else.
        """
        def _boom(*a, **k):
            raise AssertionError("a shop PC signed itself in as the administrator")

        with mock.patch("core.store_manager.get_active_store_key", return_value="Store_Roshan"), \
             mock.patch("core.store_manager.get_active_display_name", return_value="Roshan"), \
             mock.patch("core.server_api.admin_login", _boom), \
             mock.patch("core.server_api.list_remote_stores", _boom), \
             mock.patch("core.server_api.create_store", _boom), \
             mock.patch("core.store_link.get_local_android_key", return_value="SC-1"), \
             mock.patch("core.server_api._pc_device_id", return_value="pc"), \
             mock.patch("core.server_api.ensure_store_session",
                        return_value={"token": "s", "store_id": "roshan",
                                      "store_name": "Roshan"}) as paired:
            res = server_live.ensure_active_store_on_server()
        self.assertEqual(res["store_name"], "Roshan")
        self.assertEqual(paired.call_args.kwargs["android_key"], "SC-1")


class TheAlphabetDoesNotChooseTheShop(unittest.TestCase):
    """sorted(os.listdir(root))[0] picked Store_Default over the real store."""

    def _root(self, tmp):
        root = os.path.join(tmp, "stores")
        os.makedirs(os.path.join(root, "Store_Default"))
        os.makedirs(os.path.join(root, "Store_ZZ_Test_Pharmacy"))
        # The leftover folder carries no database; the real one does.
        open(os.path.join(root, "Store_Default", "backup_slots.dat"), "wb").close()
        with open(os.path.join(root, "Store_ZZ_Test_Pharmacy", "veterinary.db"), "wb") as fh:
            fh.write(b"x" * 4096)
        return root

    def test_the_folder_with_the_data_wins(self):
        keys = ["Store_Default", "Store_ZZ_Test_Pharmacy"]
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            with mock.patch.object(server_live, "_load_adoptions", return_value={}):
                picked = store_manager._preferred_active_store(root, keys)
        self.assertEqual(picked, "Store_ZZ_Test_Pharmacy")

    def test_the_link_ledger_outranks_the_disk(self):
        keys = ["Store_Default", "Store_ZZ_Test_Pharmacy"]
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            with mock.patch.object(
                server_live,
                "_load_adoptions",
                return_value={"Store_Default": {"store_id": "sd"}},
            ):
                picked = store_manager._preferred_active_store(root, keys)
        self.assertEqual(picked, "Store_Default")


class TheBackupDialogDoesNotRenameTheStore(unittest.TestCase):
    """Trigger (1): pressing Save on Backup Settings renamed the active store.

    It moved stores/<key>/ and left server_session_<key>.json, store_key_<key>.txt,
    the catalog snapshot, the bootstrap marker and the watermark bucket behind
    under the old key -- and the server was never told.
    """

    def test_neither_build_calls_update_active_store_display_name(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for rel in (
            "ui/settings/settings_tabs/database_tab.py",
            "core/desktop_settings_service.py",
        ):
            with open(os.path.join(root, rel), encoding="utf-8") as fh:
                body = "\n".join(
                    ln for ln in fh.read().splitlines() if not ln.lstrip().startswith("#")
                )
            self.assertNotIn(
                "update_active_store_display_name(", body,
                f"{rel} still renames the store from a backup-settings save",
            )


class ConfirmingTheStoreClearsTheBanner(unittest.TestCase):
    def test_switching_store_clears_the_auto_selected_flag(self):
        reg = {"stores": [{"store_key": "Store_A"}], "active_auto_selected": True}
        saved = {}

        def _save(data):
            saved.update(data)

        with mock.patch.object(store_manager, "load_registry", return_value=reg), \
             mock.patch.object(store_manager, "save_registry", _save), \
             mock.patch.object(
                 store_manager, "_find_store_by_key",
                 return_value={"store_key": "Store_A", "display_name": "A"},
             ), \
             mock.patch.object(store_manager, "_sync_backup_config_for_store"):
            store_manager.set_active_store("Store_A")
        self.assertNotIn("active_auto_selected", saved)


if __name__ == "__main__":
    unittest.main()
