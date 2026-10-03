"""The app's store connection waits for a lock instead of failing (3 Oct 2026).

It used Python's 5-second default, so a Drive backup or an upload holding the file at the
moment of a save or a mode switch answered "database is locked". It waits 30 s now.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ALockedFile(unittest.TestCase):
    def test_a_write_waits_for_another_writer(self):
        from core.desktop_api import _open_conn

        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "v.db")
        setup = sqlite3.connect(path)
        setup.execute("CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT)")
        setup.commit()
        setup.close()

        held = threading.Event()

        def other_writer():
            c = sqlite3.connect(path, timeout=1)
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT INTO settings VALUES ('backup', 'running')")
            held.set()
            time.sleep(6)                      # longer than the old 5 s default
            c.commit()
            c.close()

        t = threading.Thread(target=other_writer)
        t.start()
        held.wait(5)
        conn = _open_conn(path)
        try:
            start = time.time()
            conn.execute("INSERT INTO settings VALUES ('bill', 'saved')")
            conn.commit()
            waited = time.time() - start
        finally:
            conn.close()
            t.join()
        self.assertGreater(waited, 4.0)
        c = sqlite3.connect(path)
        self.assertEqual({"backup", "bill"}, {r[0] for r in c.execute("SELECT name FROM settings")})
        c.close()


if __name__ == "__main__":
    unittest.main()
