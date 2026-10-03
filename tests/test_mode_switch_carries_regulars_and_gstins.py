"""Switching Online <-> Offline keeps the regular-medicine lists and customer GSTINs (3 Oct 2026).

They live in store settings, which the switch did not move: going Offline they stayed on the
server, going Online a list made Offline was never sent. core/store_kv_carry moves them.
"""
import os
import sqlite3
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import store_kv_carry as carry  # noqa: E402

SERVER_ROWS = [
    {"name": "regular_meds:7", "value": '{"customer": "RAMESH PATIL", "items": [{"name": "DOLO 650", "qty": 1}]}'},
    {"name": "customer_gst:7", "value": '{"gstin": "27AAPFU0939F1ZV", "since": "2026-09-01"}'},
    {"name": "low_stock_tablet", "value": "10"},                        # not ours to carry
    {"name": "regular_meds:9", "value": ""},                            # a removed list
]


def _conn():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT)")
    return c


class GoingOffline(unittest.TestCase):
    def test_the_lists_and_gstins_come_down(self):
        c = _conn()
        with mock.patch("core.server_api._request", return_value={"ok": True, "data": SERVER_ROWS}), \
                mock.patch("core.server_live._token", return_value="t"):
            res = carry.try_pull(c)
        self.assertEqual({"ok": True, "rows": 2}, res)
        names = {n for (n,) in c.execute("SELECT name FROM settings")}
        self.assertEqual({"regular_meds:7", "customer_gst:7"}, names)

    def test_no_internet_is_reported_not_raised(self):
        c = _conn()
        with mock.patch("core.server_api._request", side_effect=OSError("offline")), \
                mock.patch("core.server_live._token", return_value="t"):
            res = carry.try_pull(c)
        self.assertFalse(res["ok"])


class GoingOnline(unittest.TestCase):
    def test_what_was_made_offline_goes_up(self):
        c = _conn()
        c.executemany("INSERT INTO settings VALUES (?,?)", [
            ("regular_meds:3", '{"items": [{"name": "TELMA 40", "qty": 1}]}'),
            ("customer_gst:3", '{"gstin": "29AAGCB7383J1Z4"}'),
            ("bill_print", "{}"), ("regular_meds:4", ""),
        ])
        sent = []
        with mock.patch("core.server_live.push_settings_kv", side_effect=lambda k, v: sent.append(k) or True):
            res = carry.try_push(c)
        self.assertEqual({"ok": True, "rows": 2}, res)
        self.assertEqual({"regular_meds:3", "customer_gst:3"}, set(sent))

    def test_the_local_store_is_kept_when_they_cannot_go_up(self):
        import core.online_migrate as om

        path = os.path.join(os.path.dirname(__file__), "_carry_tmp.db")
        sqlite3.connect(path).close()
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        with mock.patch("core.server_live.active_store_was_joined", return_value=False), \
                mock.patch("core.db_utils.open_store_db", side_effect=lambda *a, **k: _conn()), \
                mock.patch("core.server_sync.push_active_store_to_server_detailed",
                           return_value={"upserted": 5, "store_token": "t"}), \
                mock.patch.object(om, "verify_push_before_wipe", return_value=(True, "all there")), \
                mock.patch("core.store_kv_carry.try_push", return_value={"ok": False, "error": "timeout"}), \
                mock.patch.object(om, "delete_store_db_files") as delete:
            res = om.push_local_then_wipe(db_path=path)
        self.assertFalse(res["ok"])
        self.assertIn("local database kept", res["error"])
        delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
