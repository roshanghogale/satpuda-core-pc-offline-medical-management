"""Closing the app Online does not keep the engine running for a backup (5 Oct 2026).

The closing backup of an Online store pulls the whole store down from the server first
(~40 s for a small shop). The window was gone but SatpudaEngine.exe stayed in Task Manager,
and the installer refused to run ("Satpuda Core is still open"). Online skips it; Offline,
a copy of a file on this disk, still takes it.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import desktop_api as dap  # noqa: E402


def _close(online: bool):
    handler = dap._DesktopApiHandler.__new__(dap._DesktopApiHandler)
    handler.path = "/api/backup/close"
    sent = []
    with mock.patch.object(dap, "_json_response", side_effect=lambda h, code, body: sent.append(body)), \
            mock.patch("core.sync_prefs.is_online_mode", return_value=online), \
            mock.patch("core.sync_coordinator.should_run_drive_backup", return_value=True), \
            mock.patch("core.backup_manager.is_auto_backup_enabled", return_value=True), \
            mock.patch("core.backup_manager.run_backup_now") as backup:
        handler.do_GET()
    return sent[-1], backup


class TheClosingBackup(unittest.TestCase):
    def setUp(self):
        old = dap._CLOSE_BACKUP_DONE
        self.addCleanup(setattr, dap, "_CLOSE_BACKUP_DONE", old)
        dap._CLOSE_BACKUP_DONE = False

    def test_online_answers_at_once_without_a_backup(self):
        body, backup = _close(online=True)
        backup.assert_not_called()
        self.assertEqual({"ok": True, "ran": False, "skipped": "online"}, body)

    def test_offline_still_takes_it(self):
        body, backup = _close(online=False)
        backup.assert_called_once()
        self.assertTrue(body["ran"])

    def test_it_runs_once(self):
        _close(online=False)
        body, backup = _close(online=False)
        backup.assert_not_called()
        self.assertEqual("already_run", body["skipped"])


if __name__ == "__main__":
    unittest.main()
