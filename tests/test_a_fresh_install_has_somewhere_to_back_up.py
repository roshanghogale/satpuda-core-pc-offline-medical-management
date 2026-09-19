"""A build must carry the ONE Drive folder every shop backs into.

The clean-release rule of 2026-09-11 was right about the store name and wrong
about the folder. config/backup_config.dat carries both, so the whole file went
into SHOP_IDENTITY and clean_release() stripped it from all seven specs -- and
scripts/audit_release_folder.py refuses any clean build that contains a file by
that name. From that day every build shipped the Drive CREDENTIALS and no
DESTINATION, so core/backup_manager.py returned before it wrote anything: no
Drive upload, no pendrive copy, no restore. Shops that had ever run an older
build kept working on the %LOCALAPPDATA% copy left behind, which is why it was
invisible for five days and only showed up on the owner's fresh install.

The destination now travels in config/drive_backup_folder.dat: the folder id and
nothing else, so there is no shop's identity in it to leak. These tests pin all
three halves of that -- the file exists, both build guards treat it as the
vendor's, and an audit of a build without it REFUSES instead of saying "clean".
"""
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class TheDestinationIsPartOfTheProduct(unittest.TestCase):
    def test_the_repo_carries_a_readable_vendor_folder_file(self):
        from core import backup_manager as bm

        path = bm._project_vendor_folder_path()
        self.assertTrue(
            os.path.isfile(path),
            "config/drive_backup_folder.dat is missing — every build made from "
            "this tree will ship with no backup destination. "
            "Run: python scripts/make_vendor_drive_folder.py",
        )
        self.assertTrue(
            bm.read_vendor_drive_folder(),
            "the vendor Drive folder file does not decrypt to a folder id",
        )

    def test_it_carries_no_store_name(self):
        """The store name is the half that must never ship. Keep it out."""
        from core import backup_manager as bm

        with open(bm._project_vendor_folder_path(), "rb") as fh:
            payload = bm._decrypt_dict(fh.read())
        self.assertEqual(
            sorted(payload.keys()), ["folder_id"],
            "the vendor destination file grew a second field; if one of them is "
            "a store name this file becomes shop identity and must not ship",
        )

    def test_a_clean_build_keeps_it(self):
        import build_release_filter as brf

        self.assertNotIn("config/drive_backup_folder.dat", brf.SHOP_IDENTITY)
        self.assertIn("config/drive_backup_folder.dat", brf.VENDOR_CREDENTIALS)
        kept = [src for src, _ in brf.release_datas([
            ("config/backup_creds.dat", "config"),
            ("config/drive_backup_folder.dat", "config"),
            ("config/backup_config.dat", "config"),
        ])]
        self.assertIn(
            "config/drive_backup_folder.dat", kept,
            "a clean build still leaves out the Drive destination",
        )
        # And the store-name file is still excluded: this fix does not reopen
        # the leak the 2026-09-11 rule closed.
        self.assertNotIn("config/backup_config.dat", kept)

    def test_every_spec_ships_it(self):
        import glob

        for path in sorted(glob.glob(os.path.join(ROOT, "*.spec"))):
            with open(path, encoding="utf-8") as fh:
                spec = fh.read()
            with self.subTest(spec=os.path.basename(path)):
                self.assertIn(
                    "config/drive_backup_folder.dat", spec,
                    f"{os.path.basename(path)} builds with no backup destination",
                )

    def test_a_missing_required_file_is_shouted_about_not_skipped(self):
        """existing() drops an absent source silently. That is how it was lost."""
        import io
        import contextlib

        import build_release_filter as brf

        self.assertIn("config/drive_backup_folder.dat", brf.REQUIRED_IN_EVERY_BUILD)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            kept = brf.existing([("config/drive_backup_folder.dat", "config")])
        # The repo has the file, so force the absent case explicitly.
        with contextlib.redirect_stdout(buf):
            brf.existing([("config/does_not_exist.dat", "config")])
        out = buf.getvalue()
        self.assertEqual(len(kept), 1)
        with mock.patch.object(brf.os.path, "exists", return_value=False):
            buf2 = io.StringIO()
            with contextlib.redirect_stdout(buf2):
                brf.existing([("config/drive_backup_folder.dat", "config")])
            self.assertIn("MISSING REQUIRED FILE", buf2.getvalue())
        self.assertIn("skipping absent optional data file", out)


class TheAuditRefusesABuildWithNowhereToBackUp(unittest.TestCase):
    """The guard that called the broken build "clean" for five days."""

    def _build_folder(self, tmp, names):
        cfg = os.path.join(tmp, "engine", "_internal", "config")
        os.makedirs(cfg)
        for name in names:
            with open(os.path.join(cfg, name), "wb") as fh:
                fh.write(b"x" * 64)
        return tmp

    def _run(self, folder):
        env = dict(os.environ)
        env.pop("SATPUDA_BUILD", None)
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts/audit_release_folder.py"), folder],
            capture_output=True, text=True, env=env,
        )
        return r.returncode, r.stdout

    def test_a_build_without_the_destination_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._build_folder(tmp, ["backup_creds.dat", "gemini_api_key.txt"])
            code, out = self._run(tmp)
        self.assertEqual(
            code, 1,
            "the audit passed a build that cannot back up anywhere:\n" + out,
        )
        self.assertIn("drive_backup_folder.dat", out)
        self.assertIn("REFUSED", out)

    def test_a_build_with_the_destination_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._build_folder(
                tmp, ["backup_creds.dat", "drive_backup_folder.dat", "gemini_api_key.txt"]
            )
            code, out = self._run(tmp)
        self.assertEqual(code, 0, out)
        self.assertIn("clean", out)

    def test_a_build_without_the_drive_credentials_is_refused_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._build_folder(tmp, ["drive_backup_folder.dat"])
            code, out = self._run(tmp)
        self.assertEqual(code, 1, out)
        self.assertIn("backup_creds.dat", out)

    def test_a_folder_that_is_not_a_build_is_left_alone(self):
        """The audit is also used as a plain leak scanner. Do not fail that."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as fh:
                fh.write("<html>nothing personal here</html>")
            code, out = self._run(tmp)
        self.assertEqual(code, 0, out)


class AFreshInstallFindsTheDestination(unittest.TestCase):
    def setUp(self):
        from core import backup_manager as bm

        self.bm = bm

    def test_no_config_file_anywhere_still_yields_the_vendor_folder(self):
        bm = self.bm
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "nothing.dat")
            with mock.patch.object(bm, "_config_path", return_value=missing), \
                 mock.patch.object(bm, "_bundled_config_path", return_value=""):
                cfg = bm._read_backup_config()
        self.assertTrue(
            cfg.get("folder_id"),
            "a fresh install still has nowhere to back up — this is the bug",
        )
        self.assertEqual(cfg.get("source"), "vendor")
        self.assertEqual(cfg["folder_id"], bm.read_vendor_drive_folder())

    def test_a_shop_that_chose_its_own_folder_keeps_it(self):
        """The vendor value is a FALLBACK. It must never win."""
        bm = self.bm
        with tempfile.TemporaryDirectory() as tmp:
            own = os.path.join(tmp, "backup_config.dat")
            bm._write_config_file(own, "FOLDER-THIS-SHOP-CHOSE", "This Shop")
            with mock.patch.object(bm, "_config_path", return_value=own), \
                 mock.patch.object(bm, "_bundled_config_path", return_value=""):
                cfg = bm._read_backup_config()
        self.assertEqual(cfg["folder_id"], "FOLDER-THIS-SHOP-CHOSE")
        self.assertEqual(cfg.get("source"), "appdata")

    def test_the_vendor_default_is_never_written_into_the_shops_own_file(self):
        """Writing it would pin today's parent folder forever.

        _should_reseed_backup_file deliberately never moves a folder a PC
        already holds, so a destination written here could not be corrected by
        any later build.
        """
        bm = self.bm
        vendor = {"folder_id": "VENDOR", "store_name": "", "source": "vendor"}
        with mock.patch.object(bm, "_read_backup_config", return_value=vendor), \
             mock.patch.object(bm, "write_backup_config") as write, \
             mock.patch("core.store_manager.has_registry", return_value=True), \
             mock.patch("core.store_manager.get_active_display_name",
                        return_value="Some Shop"):
            bm.sync_backup_config_to_active_store()
        write.assert_not_called()

        # Creating or activating a store goes through store_manager, which
        # writes backup_config.dat too. Same rule there.
        from core import store_manager

        with mock.patch.object(bm, "_read_backup_config", return_value=vendor), \
             mock.patch.object(bm, "write_backup_config") as write2:
            store_manager._sync_backup_config_for_store("Some Shop")
        write2.assert_not_called()

        # A shop that DID choose its own folder is still kept in step.
        own = {"folder_id": "FOLDER-THIS-SHOP-CHOSE", "store_name": "Old Name",
               "source": "appdata"}
        with mock.patch.object(bm, "_read_backup_config", return_value=own), \
             mock.patch.object(bm, "write_backup_config") as write3:
            store_manager._sync_backup_config_for_store("Some Shop")
        write3.assert_called_once_with("FOLDER-THIS-SHOP-CHOSE", "Some Shop")

    def test_status_says_which_half_is_missing(self):
        """The screen used to blame backup_creds.dat for everything."""
        bm = self.bm
        with mock.patch.object(bm, "_read_backup_config", return_value={}), \
             mock.patch.object(bm, "_read_oauth_token",
                               return_value={"refresh_token": "t", "client_id": "c",
                                             "client_secret": "s"}), \
             mock.patch.object(bm, "_detect_pendrives", return_value=[]):
            status = bm.get_backup_config_status()
        self.assertFalse(status["configured"])
        self.assertFalse(status["folder_ok"])
        self.assertTrue(status["creds_ok"])


class TwoFoldersWithOneNameDoNotSplitAShopsBackups(unittest.TestCase):
    """The vendor's Drive already holds two Store_Bramhandnayak_Medical folders.

    list-then-create is not atomic. Whichever one is picked, backup and restore
    must pick the SAME one or half a shop's copies disappear from the list.
    """

    def test_the_oldest_folder_wins_for_both_paths(self):
        from core import backup_manager as bm

        files = [
            {"id": "newer", "createdTime": "2026-08-12T04:40:57Z"},
            {"id": "older", "createdTime": "2026-08-12T04:40:56Z"},
        ]
        self.assertEqual(bm._pick_store_subfolder(files), "older")
        self.assertEqual(bm._pick_store_subfolder(list(reversed(files))), "older")
        self.assertEqual(bm._pick_store_subfolder([]), "")


if __name__ == "__main__":
    unittest.main()
