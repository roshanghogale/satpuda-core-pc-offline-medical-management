"""The PC's sync id is worked out once and kept (store 131, 26-30 Oct 2026).

Vaibhav's PC sent a new id when its hardware fingerprint changed; the server had never
registered it and refused four days of pushes. The id is now pinned, and an offline-first store
pins the id it registered under, which the pushes then carry. No network, no real app data.
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import sync_metadata_schema as sms  # noqa: E402


class KeptId(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        pin = os.path.join(self.dir, "sync_device_id_pinned.txt")
        p = mock.patch.object(sms, "_pin_path", lambda: pin)
        p.start()
        self.addCleanup(p.stop)
        sms._PIN_CACHE.clear()
        self.addCleanup(sms._PIN_CACHE.clear)
        self.hw = "a" * 64
        h = mock.patch("core.license_manager._get_hardware_hash", lambda *a, **k: self.hw)
        h.start()
        self.addCleanup(h.stop)

    def test_a_changed_fingerprint_keeps_the_first_id(self):
        first = sms.get_sync_device_id()
        self.assertEqual(first, "a" * 64)
        self.hw = "b" * 64                      # the fingerprint moves on the same machine
        sms._PIN_CACHE.clear()                  # a fresh start of the app
        self.assertEqual(sms.get_sync_device_id(), first)
        from core import server_api

        self.assertEqual(server_api._pc_device_id(), "pc-" + "a" * 32)

    def test_offline_first_keeps_the_registered_id(self):
        sms.get_sync_device_id()
        db = os.path.join(self.dir, "store.db")
        conn = sqlite3.connect(db)
        from core.offline_first import schema, worker

        schema.ensure_schema(conn)
        schema.meta_set(conn, "install_id", "pc-" + "c" * 32)
        conn.commit()
        worker._keep_registered_id(conn)
        conn.close()
        from core import server_api
        from core.offline_first import client

        self.assertEqual(server_api._pc_device_id(), "pc-" + "c" * 32)
        self.assertEqual(client.install_id(), "pc-" + "c" * 32)

    def test_a_push_goes_under_the_registered_id(self):
        from core.offline_first import client

        sent = {}
        with mock.patch.object(client, "_call", lambda m, p, body=None, **k: sent.update(body) or {}):
            client.push([{"seq": 1}], install="pc-" + "d" * 32)
        self.assertEqual(sent["install_id"], "pc-" + "d" * 32)


if __name__ == "__main__":
    unittest.main()
