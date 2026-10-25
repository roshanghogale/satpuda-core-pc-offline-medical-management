"""A backup taken on an offline-first PC, restored on a PC that is not on offline-first.

The restore is refused on an offline-first PC (backup_manager._offline_first_restore_refusal).
Anywhere else the file arrived with the other PC's capture triggers, unsent notes, number
blocks and device registration: the Offline till went on noting every sale for a worker that
never runs, and turning offline-first on later was refused for good over "unsent" work that was
never this PC's. The records themselves must come back exactly as backed up.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import unittest

import core.backup_manager as bm
from core import db_setup
from core.offline_first import schema
from core.offline_first.schema import meta_get, meta_set


def _offline_first_backup(path: str) -> None:
    c = sqlite3.connect(path)
    db_setup.initialise(c)
    c.commit()
    schema.ensure_schema(c)
    meta_set(c, "device_no", 7)
    meta_set(c, "id_base", 7_000_000_000)
    meta_set(c, "install_id", "pc-the-other-one")
    c.execute("INSERT INTO of_number_blocks VALUES ('sales', 2026, 101, 200, 150)")
    # Work the backed-up PC had not sent when the backup was taken.
    c.execute("INSERT INTO medicines (name, type, stock_qty, unit, batch_no) VALUES ('DOLO', 'Tablet', 40, '10', 'B1')")
    c.execute("INSERT INTO customers (name) VALUES ('RAMESH')")
    c.commit()
    c.close()


class ARestoredOfflineFirstBackup(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.path = os.path.join(self.d, "restored.db")
        _offline_first_backup(self.path)

    def _conn(self):
        c = sqlite3.connect(self.path)
        self.addCleanup(c.close)
        return c

    def test_the_backup_really_carries_unsent_work(self):
        c = self._conn()
        self.assertGreater(schema.unsent_work(c), 0)

    def test_the_restore_leaves_no_trigger_note_or_registration(self):
        bm._upgrade_restored_db(self.path)
        c = self._conn()
        triggers = c.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'of\\_%' ESCAPE '\\'"
        ).fetchall()
        self.assertEqual([], triggers)
        self.assertEqual(0, schema.unsent_work(c))
        self.assertIsNone(meta_get(c, "device_no"))
        self.assertEqual(0, c.execute("SELECT COUNT(*) FROM of_number_blocks").fetchone()[0])
        # the Offline till's next sale is not noted for anyone
        c.execute("UPDATE medicines SET stock_qty = stock_qty - 5")
        c.commit()
        self.assertEqual(0, schema.unsent_work(c))

    def test_the_records_come_back_as_backed_up(self):
        bm._upgrade_restored_db(self.path)
        c = self._conn()
        self.assertEqual([("DOLO", 40)], c.execute("SELECT name, stock_qty FROM medicines").fetchall())
        self.assertEqual([("RAMESH",)], c.execute("SELECT name FROM customers").fetchall())

    def test_turning_offline_first_on_later_starts_clean(self):
        bm._upgrade_restored_db(self.path)
        c = self._conn()
        schema.clear_bookkeeping(c)      # what activate_from_online does; it refused before

    def test_a_file_never_on_offline_first_is_left_alone(self):
        plain = os.path.join(self.d, "plain.db")
        c = sqlite3.connect(plain)
        db_setup.initialise(c)
        c.commit()
        self.assertEqual(0, schema.forget_bookkeeping(c))
        self.assertFalse(c.execute("SELECT 1 FROM sqlite_master WHERE name='of_meta'").fetchone())
        c.close()


if __name__ == "__main__":
    unittest.main()
