"""Copying a shop from the server writes each list in one transaction (owner, 18 Oct 2026).

The pull writers committed after every document -- two disk syncs a record -- and copying a
shop onto a PC's hard disk took minutes for a few hundred medicines. _BatchConn makes their
commit() a no-op inside a full download and their rollback() undo only the document that
failed (a savepoint), never the ones before it.
"""
import sqlite3
import unittest

from core.server_live import _BatchConn


class OneTransactionPerList(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        self.conn.commit()

    def apply(self, b, i, fail=False):
        b.begin_doc()
        try:
            b.execute("INSERT INTO t (id, v) VALUES (?, ?)", (i, f"v{i}"))
            if fail:
                raise RuntimeError("bad document")
            b.commit()
        except RuntimeError:
            b.rollback()
        finally:
            b.end_doc()

    def test_commit_is_held_until_flush(self):
        b = _BatchConn(self.conn)
        b.begin()
        for i in range(1, 4):
            self.apply(b, i)
        self.assertTrue(self.conn.in_transaction)
        b.flush()
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM t").fetchone()[0], 3)

    def test_a_failing_document_takes_only_itself_back(self):
        b = _BatchConn(self.conn)
        b.begin()
        self.apply(b, 1)
        self.apply(b, 2, fail=True)
        self.apply(b, 3)
        b.flush()
        ids = [r[0] for r in self.conn.execute("SELECT id FROM t ORDER BY id")]
        self.assertEqual(ids, [1, 3])

    def test_everything_else_is_the_real_connection(self):
        b = _BatchConn(self.conn)
        self.assertEqual(b.execute("SELECT 1").fetchone()[0], 1)
        self.assertIs(b.cursor().connection, self.conn)


if __name__ == "__main__":
    unittest.main()
