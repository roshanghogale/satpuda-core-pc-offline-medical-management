"""Online -> Offline on a big shop (3 Oct 2026, Vaibhav: 4,237 bills).

The store's copy took minutes inside the switch with nothing moving on screen; the app was
closed half way, the next start said "Local data found", and pressing Push to Server closed
the engine's database for good ("Database not open" everywhere).
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.online_migrate as om  # noqa: E402


def _store(path, *, pending=0, synced=3):
    from core.db_setup import initialise

    c = sqlite3.connect(path)
    initialise(c)
    for i in range(synced):
        c.execute("INSERT INTO sales (bill_no, bill_date, total_amount, sync_status) VALUES (?, '2026-10-03', 10, 'synced')",
                  (f"SCB{i + 1}",))
    for i in range(pending):
        c.execute("INSERT INTO sales (bill_no, bill_date, total_amount, sync_status) VALUES (?, '2026-10-03', 10, 'pending')",
                  (f"SCB{100 + i}",))
    c.commit()
    return c


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.path = os.path.join(self.d, "veterinary.db")


class AHalfCopy(_Tmp):
    def test_a_copy_stopped_half_way_is_known_for_what_it_is(self):
        c = _store(self.path)
        om._mark_mirror_started(c)              # the download started, never finished
        c.close()
        self.assertTrue(om.is_clean_prepared_mirror(self.path))
        self.assertFalse(om.has_complete_mirror(self.path))
        with mock.patch("core.sync_prefs.is_online_mode", return_value=True):
            gate = om.ensure_online_server_only_ready(db_path=self.path, auto_wipe_empty=False)
        self.assertFalse(gate["needs_migrate"])  # no "Local data found"

    def test_a_finished_copy_is_complete(self):
        c = _store(self.path)
        om._mark_mirror_started(c)
        om._mark_prepared_mirror(c)
        c.commit()
        c.close()
        self.assertTrue(om.has_complete_mirror(self.path))

    def test_an_old_file_without_a_mark_still_asks(self):
        _store(self.path).close()
        self.assertFalse(om.is_clean_prepared_mirror(self.path))


class UnsentWork(_Tmp):
    def test_the_servers_copy_never_overwrites_bills_that_never_reached_it(self):
        _store(self.path, pending=2).close()
        self.assertEqual({"sales": 2}, om.pending_local_work(self.path))
        with mock.patch("core.server_live.sync_down_all") as pull:
            res = om.download_store_for_offline(db_path=self.path)
        self.assertFalse(res["ok"])
        self.assertEqual("pending_local_work", res["code"])
        pull.assert_not_called()
        c = sqlite3.connect(self.path)
        self.assertEqual(5, c.execute("SELECT COUNT(*) FROM sales").fetchone()[0])
        c.close()
        self.assertFalse(om.has_complete_mirror(self.path))


class AFailedPushLeavesTheEngineOpen(unittest.TestCase):
    def test_the_engine_connection_is_opened_again(self):
        from core import desktop_api as dap
        from core import desktop_settings_service as svc

        old = dap._db.get("conn")
        self.addCleanup(lambda: dap._db.__setitem__("conn", old))
        dap._db["conn"] = None
        with mock.patch.object(dap, "reopen_active_store",
                               side_effect=lambda: dap._db.__setitem__("conn", "reopened")) as reopen:
            svc._reopen_engine_conn()
        reopen.assert_called_once()
        self.assertEqual("reopened", dap._db["conn"])

    def test_an_open_engine_is_left_alone(self):
        from core import desktop_api as dap
        from core import desktop_settings_service as svc

        old = dap._db.get("conn")
        self.addCleanup(lambda: dap._db.__setitem__("conn", old))
        dap._db["conn"] = "open"
        with mock.patch.object(dap, "reopen_active_store") as reopen:
            svc._reopen_engine_conn()
        reopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
