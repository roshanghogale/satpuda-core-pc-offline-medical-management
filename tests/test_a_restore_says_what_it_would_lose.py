"""One device at a time, the whole file passed over Drive: a restore that would throw away
this device's newer records stops and names them (3 Oct 2026).

PC and phone take turns: work, Backup Now, the other device restores. Forgetting the backup
before taking the other device's file used to lose the bills silently.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.backup_manager as bm  # noqa: E402


def _store(path, bills, payments=()):
    from core.db_setup import initialise

    c = sqlite3.connect(path)
    initialise(c)
    for no in bills:
        c.execute("INSERT INTO sales (bill_no, bill_date, total_amount) VALUES (?, '2026-10-03', 100)",
                  (f"{no}/FY2026-27",))
    for cid, amt, when in payments:
        c.execute("INSERT INTO customer_payments (customer_id, amount, payment_date, created_at)"
                  " VALUES (?,?,?,?)", (cid, amt, "2026-10-03", when))
    c.commit()
    c.close()


class WhatARestoreWouldLose(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.local = os.path.join(self.d, "local.db")
        self.incoming = os.path.join(self.d, "incoming.db")

    def test_bills_made_here_after_the_other_devices_file_are_named(self):
        _store(self.local, ["SCB1", "SCB2", "SCB3"], [(1, 50, "2026-10-03T10:00:00")])
        _store(self.incoming, ["SCB1", "SCB2"])                     # the phone's older file
        lost = bm.would_lose(self.local, self.incoming)
        self.assertEqual(["SCB3/FY2026-27"], lost["bills"])
        self.assertEqual(1, len(lost["customer payments"]))

    def test_the_other_devices_newer_file_loses_nothing(self):
        _store(self.local, ["SCB1", "SCB2"])
        _store(self.incoming, ["SCB1", "SCB2", "SCB3", "SCB4"])      # phone billed on, as it should
        self.assertEqual({}, bm.would_lose(self.local, self.incoming))

    def test_the_restore_stops_unless_the_shop_says_go_ahead(self):
        _store(self.local, ["SCB1", "SCB2", "SCB3"])
        _store(self.incoming, ["SCB1"])
        dest_dir = os.path.join(self.d, "stores", "Store_Test")
        os.makedirs(dest_dir)
        dest = os.path.join(dest_dir, "veterinary.db")
        shutil.copy2(self.local, dest)
        tmp = tempfile.mkdtemp(dir=self.d)
        incoming_copy = os.path.join(tmp, "in.db")
        shutil.copy2(self.incoming, incoming_copy)
        with mock.patch("core.store_manager.get_store_db_path", return_value=dest), \
                mock.patch("core.store_manager.get_store_dir", return_value=dest_dir), \
                mock.patch.object(bm, "_close_all_db_users"), \
                mock.patch.object(bm, "_after_restore_sync_policy"):
            ok, msg = bm._apply_restored_db({"db_path": incoming_copy, "tmp_dir": ""}, "Test", "Store_Test")
            self.assertFalse(ok)
            self.assertTrue(msg.startswith(bm.WOULD_LOSE))
            self.assertIn("SCB2", msg)
            self.assertIn("SCB3", msg)
            c = sqlite3.connect(dest)
            self.assertEqual(3, c.execute("SELECT COUNT(*) FROM sales").fetchone()[0])   # untouched
            c.close()
            ok, _ = bm._apply_restored_db({"db_path": incoming_copy, "tmp_dir": ""}, "Test", "Store_Test",
                                          allow_loss=True)
            self.assertTrue(ok)
        c = sqlite3.connect(dest)
        self.assertEqual(1, c.execute("SELECT COUNT(*) FROM sales").fetchone()[0])
        c.close()

    def test_the_screen_gets_a_code_it_can_ask_with(self):
        from core.desktop_settings_service import _restore_refusal

        res = _restore_refusal(bm.WOULD_LOSE + " Ya PC var ase 1 bills (SCB3) aahet")
        self.assertEqual("would_lose", res["code"])
        self.assertNotIn(bm.WOULD_LOSE, res["error"])
        self.assertNotIn("code", _restore_refusal("network down"))


if __name__ == "__main__":
    unittest.main()
