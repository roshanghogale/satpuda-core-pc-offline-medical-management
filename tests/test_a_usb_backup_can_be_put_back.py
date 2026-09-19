"""The copy on the pendrive was write-only.

Backup has written SatpudaCore_Backup\\Store_<name>\\*.db.gz to a USB stick
since the beginning -- the copy that survives a dead internet connection, a
disabled OAuth client, or a PC that was never given a Drive folder. Restore
could only ever read from Google Drive, so that copy could not be put back
without the developer.

The USB restore goes through the same _apply_restored_db as the Drive one, so
the care around it -- live connections closed, WAL sidecars cleared, Online
watermarks reset, row counts verified -- is not a second implementation that
can drift.
"""
import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

STORE = "Test Shop"


def _make_db(path: str, sales: int) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, bill_date TEXT)")
    conn.executemany(
        "INSERT INTO sales (bill_date) VALUES (?)",
        [(f"2026-09-{(i % 28) + 1:02d}",) for i in range(sales)],
    )
    # Big enough that the "tiny empty shell" guard does not reject it.
    conn.execute("CREATE TABLE filler (blob TEXT)")
    conn.executemany("INSERT INTO filler (blob) VALUES (?)", [("x" * 900,)] * 100)
    conn.commit()
    conn.close()


def _write_usb_backup(usb_root: str, filename: str, sales: int) -> str:
    from core import backup_manager as bm

    folder = os.path.join(
        usb_root, "SatpudaCore_Backup", bm._drive_subfolder_name(STORE)
    )
    os.makedirs(folder, exist_ok=True)
    raw = os.path.join(usb_root, "raw.db")
    _make_db(raw, sales)
    gz = os.path.join(folder, filename)
    with open(raw, "rb") as f_in, gzip.open(gz, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    os.remove(raw)
    return gz


class TheStickIsListed(unittest.TestCase):
    def test_backups_on_a_usb_drive_are_found_newest_first(self):
        from core import backup_manager as bm

        with tempfile.TemporaryDirectory() as usb:
            _write_usb_backup(usb, "SatpudaCore_2026-09-10_09-00.db.gz", 5)
            _write_usb_backup(usb, "SatpudaCore_2026-09-16_18-30.db.gz", 9)
            ok, items = bm.list_local_backups(STORE, roots=[usb])

            self.assertTrue(ok, items)
            self.assertEqual(
                [i["name"] for i in items],
                ["SatpudaCore_2026-09-16_18-30.db.gz",
                 "SatpudaCore_2026-09-10_09-00.db.gz"],
            )
            self.assertTrue(all(os.path.isfile(i["path"]) for i in items))

    def test_no_stick_connected_says_so(self):
        from core import backup_manager as bm

        ok, err = bm.list_local_backups(STORE, roots=[])
        self.assertFalse(ok)
        self.assertIn("USB", err)

    def test_a_stick_with_no_folder_for_this_store_says_which_folder(self):
        from core import backup_manager as bm

        with tempfile.TemporaryDirectory() as usb:
            ok, err = bm.list_local_backups(STORE, roots=[usb])
        self.assertFalse(ok)
        self.assertIn(bm._drive_subfolder_name(STORE), err)


class TheStickCanBePutBack(unittest.TestCase):
    def test_restoring_from_usb_replaces_the_store_database(self):
        from core import backup_manager as bm

        with tempfile.TemporaryDirectory() as tmp:
            usb = os.path.join(tmp, "usb")
            os.makedirs(usb)
            _write_usb_backup(usb, "SatpudaCore_2026-09-16_18-30.db.gz", 9)

            store_dir = os.path.join(tmp, "stores", "Store_Test_Shop")
            os.makedirs(store_dir)
            dest = os.path.join(store_dir, "veterinary.db")
            _make_db(dest, 2)          # the PC's current (wrong) data

            with mock.patch.object(bm, "_upgrade_restored_db"), \
                 mock.patch.object(bm, "_close_all_db_users"), \
                 mock.patch.object(bm, "_after_restore_sync_policy"), \
                 mock.patch("core.store_manager.get_store_db_path",
                            return_value=dest), \
                 mock.patch("core.store_manager.get_store_dir",
                            return_value=store_dir):
                ok, result = bm.restore_local_backup_to_store(
                    STORE, "Store_Test_Shop", roots=[usb],
                )

            self.assertTrue(ok, result)
            self.assertEqual(result["sale_count"], 9)
            conn = sqlite3.connect(dest)
            try:
                live = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
            finally:
                conn.close()
        self.assertEqual(live, 9, "the restored database was not put in place")

    def test_a_named_file_is_restored_rather_than_the_newest(self):
        from core import backup_manager as bm

        with tempfile.TemporaryDirectory() as tmp:
            usb = os.path.join(tmp, "usb")
            os.makedirs(usb)
            older = _write_usb_backup(usb, "SatpudaCore_2026-09-10_09-00.db.gz", 4)
            _write_usb_backup(usb, "SatpudaCore_2026-09-16_18-30.db.gz", 9)

            store_dir = os.path.join(tmp, "stores", "Store_Test_Shop")
            os.makedirs(store_dir)
            dest = os.path.join(store_dir, "veterinary.db")
            _make_db(dest, 2)

            with mock.patch.object(bm, "_upgrade_restored_db"), \
                 mock.patch.object(bm, "_close_all_db_users"), \
                 mock.patch.object(bm, "_after_restore_sync_policy"), \
                 mock.patch("core.store_manager.get_store_db_path",
                            return_value=dest), \
                 mock.patch("core.store_manager.get_store_dir",
                            return_value=store_dir):
                ok, result = bm.restore_local_backup_to_store(
                    STORE, "Store_Test_Shop", path=older, roots=[usb],
                )
        self.assertTrue(ok, result)
        self.assertEqual(result["sale_count"], 4)

    def test_a_file_that_is_not_a_database_is_refused_not_installed(self):
        from core import backup_manager as bm

        with tempfile.TemporaryDirectory() as tmp:
            usb = os.path.join(tmp, "usb")
            folder = os.path.join(
                usb, "SatpudaCore_Backup", bm._drive_subfolder_name(STORE)
            )
            os.makedirs(folder)
            junk = os.path.join(folder, "SatpudaCore_2026-09-16_18-30.db.gz")
            with gzip.open(junk, "wb") as fh:
                fh.write(b"this is not a sqlite database" * 10)

            store_dir = os.path.join(tmp, "stores", "Store_Test_Shop")
            os.makedirs(store_dir)
            dest = os.path.join(store_dir, "veterinary.db")
            _make_db(dest, 2)

            with mock.patch.object(bm, "_upgrade_restored_db"), \
                 mock.patch.object(bm, "_close_all_db_users"), \
                 mock.patch.object(bm, "_after_restore_sync_policy"), \
                 mock.patch("core.store_manager.get_store_db_path",
                            return_value=dest), \
                 mock.patch("core.store_manager.get_store_dir",
                            return_value=store_dir):
                ok, err = bm.restore_local_backup_to_store(
                    STORE, "Store_Test_Shop", roots=[usb],
                )
            self.assertFalse(ok)
            conn = sqlite3.connect(dest)
            try:
                live = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
            finally:
                conn.close()
        self.assertEqual(live, 2, "a corrupt USB file overwrote the live database")


class TheEngineExposesTheUsbRestore(unittest.TestCase):
    def test_the_settings_service_answers_both_usb_actions(self):
        service = os.path.join(ROOT, "core", "desktop_settings_service.py")
        with open(service, encoding="utf-8") as fh:
            body = fh.read()
        for action in ('if action == "list_usb_backups"',
                       'if action == "restore_usb_backup"'):
            self.assertIn(action, body)

    def test_the_api_reopens_the_database_after_a_usb_restore(self):
        """Without this the shop sees the old data until it restarts."""
        api = os.path.join(ROOT, "core", "desktop_api.py")
        with open(api, encoding="utf-8") as fh:
            body = fh.read()
        start = body.index('"restore_drive_backup",')
        self.assertIn(
            '"restore_usb_backup",', body[start:start + 200],
            "a USB restore does not reopen the store database",
        )


if __name__ == "__main__":
    unittest.main()
