"""Offline-first: the bill logo, the signature and the home banner travel with the store.

Online the three pictures go up as settings rows (core/store_images.py) and every device
pulls them. Offline-first was treated as plain Offline: a picture saved on an offline-first PC
stayed on that PC (marked "pending", never sent), and a fresh offline-first install -- whose
store download had already brought the rows -- printed no logo and no signature because
nothing read them back from the copy (25 Oct 2026).

Now, offline-first, a saved picture is written into the settings row of this PC's copy, the
sync triggers note it for the worker, and a row the download or a pull wrote is read back
from the copy. Temp store, temp AppData, no server.
"""
from __future__ import annotations

import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from unittest import mock

from core import db_setup, store_images as si, sync_prefs
from core.offline_first import pull, schema
from core.offline_first.schema import meta_set

DEVICE = 3
BASE = DEVICE * 1_000_000_000


def _png(w=120, h=40) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (w, h), (10, 20, 30, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _make_store() -> str:
    fd, path = tempfile.mkstemp(suffix=".db", prefix="of_pic_")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    db_setup.initialise(conn)
    conn.commit()
    schema.ensure_schema(conn)
    meta_set(conn, "device_no", DEVICE)
    meta_set(conn, "id_base", BASE)
    meta_set(conn, "id_max", BASE + 999_999_999)
    meta_set(conn, "install_id", "pc-test-install")
    conn.execute("DELETE FROM of_dirty")
    conn.commit()
    conn.close()
    return path


class PicturesOnOfflineFirst(unittest.TestCase):
    def setUp(self):
        self.path = _make_store()
        self.appdata = tempfile.mkdtemp(prefix="of_pic_appdata_")
        stack = ExitStack()
        self.addCleanup(stack.close)
        for p in (
            mock.patch.object(sync_prefs, "get_sync_mode", return_value=sync_prefs.MODE_OFFLINE_FIRST),
            mock.patch("core.store_manager.get_active_db_path", return_value=self.path),
            mock.patch.object(si, "_appdata_dir", return_value=self.appdata),
            mock.patch.object(si, "_store_key", return_value="Pic_Test_Store"),
            mock.patch("core.offline_first.worker.kick"),
        ):
            stack.enter_context(p)
        si._reset_server_cache()
        self.addCleanup(si._reset_server_cache)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    def row(self, name):
        conn = sqlite3.connect(self.path)
        try:
            r = conn.execute("SELECT id, value FROM settings WHERE name=?", (name,)).fetchone()
            dirty = {(c, i, o) for c, i, o in conn.execute("SELECT collection, local_id, op FROM of_dirty")}
            return r, dirty
        finally:
            conn.close()

    def test_a_saved_signature_goes_into_the_store_copy_and_is_noted_for_the_worker(self):
        res = si.save_image(si.BILL_SIGNATURE, ".png", _png())
        self.assertTrue(res["shared"], res)
        self.assertEqual(res["note"], "")
        self.assertTrue(os.path.isfile(res["path"]))
        r, dirty = self.row("bill_signature_image")
        self.assertIsNotNone(r)
        self.assertTrue(str(r[1]).startswith("data:image/png;base64,"), str(r[1])[:40])
        self.assertIn(("settings", int(r[0]), "upsert"), dirty)   # the worker will send it
        self.assertEqual(si._load_pending(), {})                   # nothing left "pending"

    def test_a_logo_the_download_brought_is_printed_on_a_fresh_pc(self):
        # The row is in the copy (the store download / a pull wrote it); this PC has no file.
        uri = si.to_data_uri(".png", _png(300, 100))
        conn = sqlite3.connect(self.path)
        conn.execute("INSERT INTO settings (name, value) VALUES ('bill_logo_image', ?)", (uri,))
        conn.commit()
        conn.close()
        self.assertEqual(si.local_path(si.BILL_LOGO), "")
        self.assertEqual(si.resolve_data_uri(si.BILL_LOGO), uri)
        path = si.resolve_path(si.BILL_LOGO)                      # ...and now it has one
        self.assertTrue(path and os.path.isfile(path), path)
        self.assertTrue(path.startswith(os.path.join(self.appdata, "store_images")))

    def test_clearing_the_picture_clears_the_row_too(self):
        si.save_image(si.BILL_LOGO, ".png", _png())
        si.forget(si.BILL_LOGO)
        r, dirty = self.row("bill_logo_image")
        self.assertEqual(r[1], "")
        self.assertIn(("settings", int(r[0]), "upsert"), dirty)
        self.assertEqual(si.local_path(si.BILL_LOGO), "")
        self.assertEqual(si.resolve_data_uri(si.BILL_LOGO), "")

    def test_a_picture_another_device_saved_is_applied_by_the_pull(self):
        uri = si.to_data_uri(".png", _png())
        conn = sqlite3.connect(self.path)
        try:
            with mock.patch("core.offline_first.pull._locally_pending", return_value=set()):
                n = pull._apply_settings_bundle(
                    conn, [{"name": "bill_signature_image", "value": uri},
                           {"name": "fy_serial_migrated_v1", "value": "2"}], set())
            self.assertEqual(n, 1)                                 # the shop's row, not the PC's own marker
            got = conn.execute("SELECT value FROM settings WHERE name='bill_signature_image'").fetchone()
            self.assertEqual(got[0], uri)
        finally:
            conn.close()
        self.assertEqual(si.resolve_data_uri(si.BILL_SIGNATURE), uri)

    def test_plain_offline_still_keeps_the_picture_on_this_pc_only(self):
        with mock.patch.object(sync_prefs, "get_sync_mode", return_value=sync_prefs.MODE_OFFLINE):
            res = si.save_image(si.BILL_LOGO, ".png", _png())
            self.assertFalse(res["shared"])
            self.assertEqual(si._load_pending(), {si.BILL_LOGO: True})
            r, _dirty = self.row("bill_logo_image")
            self.assertIsNone(r)


if __name__ == "__main__":
    unittest.main()
