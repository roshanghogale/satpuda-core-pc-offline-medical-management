"""A backup that did nothing must not read as a backup that worked.

Three things were wrong at once on a PC with no Drive folder:

  * _do_backup returned at the folder-id check -- BEFORE the snapshot and
    before the pendrive copy -- so a shop with a USB stick and no Google
    account got no local copy either, for a reason that has nothing to do with
    Google;
  * it returned None, so the only evidence was a line in backup_log.txt;
  * the desktop screen read that line and failed only on the substrings "fail"
    and "error". "Backup skipped - backup_config.dat missing or invalid."
    contains neither, so the shop was shown a green "Backup finished."

These pin all three.
"""
import gzip
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _make_store_db(path: str, sales: int = 3) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE sales (id INTEGER PRIMARY KEY, bill_date TEXT, total REAL)"
    )
    for i in range(sales):
        conn.execute(
            "INSERT INTO sales (bill_date, total) VALUES (?, ?)",
            (f"2026-09-1{i % 9}", 100.0 + i),
        )
    conn.commit()
    conn.close()


class _BackupHarness:
    """Run the real _do_backup with only the machine around it faked."""

    def __init__(self, case, *, folder_id="", usb=True, store="Test Shop"):
        self.case = case
        self.folder_id = folder_id
        self.usb = usb
        self.store = store

    def __enter__(self):
        from core import backup_manager as bm

        self.bm = bm
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.db = os.path.join(root, "veterinary.db")
        _make_store_db(self.db)
        self.usb_root = os.path.join(root, "usb")
        os.makedirs(self.usb_root)
        self.slots = os.path.join(root, "backup_slots.dat")

        cfg = {"folder_id": self.folder_id, "store_name": self.store,
               "source": "appdata"} if self.folder_id else {}
        self._saved_slots = dict(bm._slots)
        self._saved_last = bm._last_backup_time
        bm._last_backup_time = None

        self.patches = [
            mock.patch.object(bm, "sync_backup_config_to_active_store"),
            mock.patch.object(bm, "_read_backup_config", return_value=cfg),
            mock.patch.object(bm, "_db_path", return_value=self.db),
            mock.patch.object(bm, "_slots_path", return_value=self.slots),
            mock.patch.object(bm, "get_backup_store_name", return_value=self.store),
            mock.patch.object(bm, "_detect_pendrive",
                              return_value=self.usb_root if self.usb else ""),
            # Never let a test open a socket to 8.8.8.8: _is_internet_available
            # uses a raw socket, which the suite's guard does not intercept.
            mock.patch.object(bm, "_is_internet_available", return_value=False),
        ]
        for p in self.patches:
            p.start()
        return self

    def usb_files(self):
        folder = os.path.join(
            self.usb_root, "SatpudaCore_Backup",
            self.bm._drive_subfolder_name(self.store),
        )
        if not os.path.isdir(folder):
            return []
        return sorted(os.listdir(folder))

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()
        self.bm._slots.clear()
        self.bm._slots.update(self._saved_slots)
        self.bm._last_backup_time = self._saved_last
        self.tmp.cleanup()
        return False


class APcWithNoDriveFolderStillGetsItsUsbCopy(unittest.TestCase):
    def test_the_usb_copy_is_written_even_with_no_drive_folder(self):
        from core import backup_manager as bm

        with _BackupHarness(self, folder_id="", usb=True) as h:
            res = bm._do_backup(force=True, trigger="manual")
            files = h.usb_files()

        self.assertEqual(
            len(files), 1,
            "no USB copy was written — the missing Drive folder stopped the "
            "local backup too",
        )
        self.assertTrue(files[0].endswith(".db.gz"))
        self.assertEqual(res["pendrive"], "ok")
        self.assertEqual(res["drive"], "not_configured")
        self.assertTrue(res["ok"], res["message"])
        self.assertIn("Drive", res["message"])

    def test_the_gzip_on_the_stick_is_the_real_database(self):
        from core import backup_manager as bm

        with _BackupHarness(self, folder_id="", usb=True) as h:
            bm._do_backup(force=True, trigger="manual")
            name = h.usb_files()[0]
            src = os.path.join(
                h.usb_root, "SatpudaCore_Backup",
                bm._drive_subfolder_name(h.store), name,
            )
            out = os.path.join(h.tmp.name, "check.db")
            with gzip.open(src, "rb") as f_in, open(out, "wb") as f_out:
                f_out.write(f_in.read())
            conn = sqlite3.connect(out)
            try:
                count = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
            finally:
                conn.close()
        self.assertEqual(count, 3)

    def test_with_no_drive_folder_and_no_stick_it_says_so_and_is_not_ok(self):
        from core import backup_manager as bm

        with _BackupHarness(self, folder_id="", usb=False):
            res = bm._do_backup(force=True, trigger="manual")

        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "no_destination")
        low = res["message"].lower()
        # The point of the whole fix: the old screen decided success by looking
        # for these two words, and this message has neither.
        self.assertNotIn("fail", low)
        self.assertNotIn("error", low)
        self.assertIn("drive", low)
        self.assertIn("usb", low)


class BackupNowAnswersTheCaller(unittest.TestCase):
    def test_run_backup_now_returns_the_outcome(self):
        from core import backup_manager as bm

        with _BackupHarness(self, folder_id="", usb=True):
            res = bm.run_backup_now(manual=True)
        self.assertIsInstance(
            res, dict,
            "run_backup_now tells the caller nothing, so the screen has to "
            "guess from a log line",
        )
        self.assertEqual(res["pendrive"], "ok")

    def test_the_desktop_screen_no_longer_guesses_from_substrings(self):
        service = os.path.join(ROOT, "core", "desktop_settings_service.py")
        with open(service, encoding="utf-8") as fh:
            body = fh.read()
        start = body.index('if action == "backup_now"')
        block = body[start: body.index('if action == "set_auto_backup"', start)]
        self.assertNotIn(
            'if "fail" in low or "error" in low', block,
            "backup_now still decides success by searching the log line for "
            "'fail'/'error' — a skipped backup reads as a green success",
        )
        self.assertIn("run_backup_now(manual=True)", block)

    def test_a_drive_error_is_turned_into_something_a_shop_can_act_on(self):
        from core import backup_manager as bm

        self.assertIn("folder", bm._drive_error_hint("HttpError 404 not found").lower())
        self.assertIn("share", bm._drive_error_hint("HttpError 403").lower())
        self.assertIn("build", bm._drive_error_hint("disabled_client").lower())


class AutomaticBackupIsOnUnlessSomebodySaidOtherwise(unittest.TestCase):
    """Nothing ever wrote backup_auto_enabled.txt except the two checkboxes.

    So a PC where nobody thought to tick the box did no automatic backup at all
    and said nothing about it. Four of the seventeen stores in the vendor's
    Drive folder have not uploaded since June 2026 — that is what an unticked
    box looks like from the outside.
    """

    def test_a_pc_that_never_chose_backs_up(self):
        from core import backup_manager as bm

        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "backup_auto_enabled.txt")
            with mock.patch.object(bm, "_auto_backup_pref_path", return_value=missing):
                self.assertTrue(
                    bm.is_auto_backup_enabled(),
                    "a fresh PC still does no automatic backup until somebody "
                    "finds the checkbox",
                )

    def test_a_shop_that_turned_it_off_stays_off(self):
        from core import backup_manager as bm

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "backup_auto_enabled.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("0")
            with mock.patch.object(bm, "_auto_backup_pref_path", return_value=path):
                self.assertFalse(bm.is_auto_backup_enabled())
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("1")
            with mock.patch.object(bm, "_auto_backup_pref_path", return_value=path):
                self.assertTrue(bm.is_auto_backup_enabled())

    def test_an_empty_or_unreadable_file_reads_as_not_chosen(self):
        from core import backup_manager as bm

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "backup_auto_enabled.txt")
            open(path, "w", encoding="utf-8").close()
            with mock.patch.object(bm, "_auto_backup_pref_path", return_value=path):
                self.assertTrue(bm.is_auto_backup_enabled())


if __name__ == "__main__":
    unittest.main()
