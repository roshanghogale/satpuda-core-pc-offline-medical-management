"""The alert thresholds and reorder default travel between devices -- against a REHEARSAL server.

Runs only when OF_TEST_BASE is set (an ssh tunnel to the rehearsal instance; never live):
    OF_TEST_BASE=http://127.0.0.1:3999 OF_TEST_KEY=SC-V2TEST01 python -m pytest -q tests/test_alert_settings_shop_wide_server.py

Offline-first PC A saves the thresholds; PC B (offline-first) and an Online PC read the same.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

from core import server_api as api
from core import settings_mirror, shop_alert_settings, sync_prefs
from tests.test_offline_first_pc_server import BASE, PC


@unittest.skipUnless(BASE, "set OF_TEST_BASE to a rehearsal server")
class AcrossDevices(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="shop_alerts_srv_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.mode = sync_prefs.MODE_OFFLINE_FIRST
        for p in (
            mock.patch.object(settings_mirror, "_mirror_path", lambda: os.path.join(self.dir, "mirror.json")),
            mock.patch.object(sync_prefs, "get_sync_mode", lambda: self.mode),
            mock.patch.object(sync_prefs, "is_online_mode", lambda: self.mode == sync_prefs.MODE_ONLINE),
            mock.patch("core.layout_config.get_med_types", lambda: ["TAB", "SYRUP"]),
        ):
            p.start()
            self.addCleanup(p.stop)
        shop_alert_settings.forget()
        self.addCleanup(shop_alert_settings.forget)

    def test_a_save_on_one_pc_reaches_the_others(self):
        from core.desktop_settings_service import get_thresholds, save_thresholds

        stamp = str(int(time.time()) % 900 + 100)          # a value no earlier run left
        a = PC("alerts_a", f"pc-alerts-a-{stamp}")
        b = PC("alerts_b", f"pc-alerts-b-{stamp}")
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        a.prepare()
        b.prepare()

        save_thresholds(a.till, {"low_stock": {"tab": stamp}, "near_expiry": {"syrup": "5"},
                                 "customer_due_min_amount": stamp, "customer_due_min_days": "21",
                                 "reorder_default_qty": stamp})
        a.sync()
        b.sync()
        got = dict(b.q("SELECT name, value FROM settings WHERE name IN "
                       "('low_stock_tab','near_expiry_syrup','customer_due_min_amount',"
                       "'customer_due_min_days','reorder_default_qty')"))
        self.assertEqual(got, {"low_stock_tab": stamp, "near_expiry_syrup": "5",
                               "customer_due_min_amount": stamp, "customer_due_min_days": "21",
                               "reorder_default_qty": stamp})

        # An Online PC of the same shop: the server's rows, not its own.
        self.mode = sync_prefs.MODE_ONLINE
        shell = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(shell.close)
        shell.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, value TEXT)")
        shell.execute("INSERT INTO settings (name, value) VALUES ('low_stock_tab', '1')")
        with mock.patch.object(api, "api_base", lambda: BASE), \
                mock.patch("core.server_live._token", lambda: a.token):
            t = get_thresholds(shell)
            self.assertEqual(t["low_stock"]["tab"], stamp)
            self.assertEqual(t["reorder_default_qty"], stamp)
            # ... and its own save goes to the server, where offline-first PC B picks it up.
            save_thresholds(shell, {"reorder_default_qty": "13"})
        self.mode = sync_prefs.MODE_OFFLINE_FIRST
        b.sync()
        self.assertEqual(b.q("SELECT value FROM settings WHERE name='reorder_default_qty'"), [("13",)])


if __name__ == "__main__":
    unittest.main()
