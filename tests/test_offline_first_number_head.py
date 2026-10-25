"""Number blocks overtaken on the server (live store 154, Oct 2026): a data import made
SCB1..SCB4607 and purchases 1..1176 while the PC held blocks 1-200 / 1-50, so its next bill took
a number already in use and the server quarantined it.

While online the worker asks the server (GET /api/sync/v2/number-head) whether the numbers left
in its blocks are still free, drops a block that is not, and reserves a fresh one after every
number issued: the next bill is 501, nothing is quarantined. Offline the blocks are used as they
are. A fake server in memory (tests/fake_sync_server.py, numbering=True); no socket, never 8765.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

from core import db_setup
from core.fy_serial import encode_purchase_no, encode_sales_bill_no
from core.offline_first import ids, numbers, schema, worker
from core.offline_first.runtime import current_fy
from core.offline_first.schema import meta_set
from tests.fake_sync_server import FakeV2Server

FY = current_fy()
INSTALL = "pc-number-head"


def make_store(device: int = 3) -> str:
    fd, path = tempfile.mkstemp(suffix=".db", prefix="of_numhead_")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    db_setup.initialise(conn)
    conn.commit()
    schema.ensure_schema(conn)
    base = device * 1_000_000_000
    meta_set(conn, "device_no", device)
    meta_set(conn, "id_base", base)
    meta_set(conn, "id_max", base + 999_999_999)
    meta_set(conn, "install_id", INSTALL)
    conn.commit()
    conn.close()
    return path


class NumberHead(unittest.TestCase):
    def setUp(self):
        self.server = FakeV2Server(numbering=True)
        self.path = make_store()
        self.till = sqlite3.connect(self.path, check_same_thread=False, timeout=30, factory=ids.OfConnection)
        self.cycle = worker.SyncCycle(self.path)
        self.addCleanup(self._cleanup)
        # Registration: the PC reserves its blocks, sales 1-200 and purchases 1-50.
        with self.server.bind(INSTALL):
            for kind, size in numbers.BLOCK_SIZE.items():
                blk = self.server.number_block(INSTALL, kind, FY, size)
                numbers.add_block(self.till, kind, FY, blk["from_serial"], blk["to_serial"])
        self.till.commit()

    def _cleanup(self):
        self.cycle.close()
        self.till.close()
        for s in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + s)
            except OSError:
                pass

    def sync(self):
        with self.server.bind(INSTALL):
            return self.cycle.run_once()

    def sell(self) -> str:
        serial = numbers.take_serial(self.till, "sales", FY)
        bill_no = encode_sales_bill_no(serial, FY)
        self.till.execute(
            "INSERT INTO sales (bill_no, bill_date, total_amount, amount_paid, cash_paid, fy_start_year, fy_serial) "
            "VALUES (?,?,?,?,?,?,?)", (bill_no, f"{FY}-10-07", 100, 100, 100, FY, serial))
        self.till.commit()
        return bill_no

    def buy(self) -> str:
        serial = numbers.take_serial(self.till, "purchases", FY)
        no = encode_purchase_no(serial, FY)
        self.till.execute(
            "INSERT INTO purchases (purchase_no, purchase_date, total_amount, fy_start_year, fy_serial) "
            "VALUES (?,?,?,?,?)", (no, f"{FY}-10-07", 500, FY, serial))
        self.till.commit()
        return no

    def server_numbers(self, kind: str) -> list[str]:
        col = "bill_no" if kind == "sales" else "purchase_no"
        return [d[col] for (c, i), d in self.server.docs.items() if c == kind and not 8_000_000 <= i < 9_000_000]

    def test_bills_imported_into_the_block_move_the_next_bill_past_them(self):
        self.assertEqual(self.sell(), encode_sales_bill_no(1, FY))
        self.sync()
        # The import: bills 1..500 and purchases 1..1176 now exist on the server.
        self.server.import_bills("sales", FY, 2, 500)
        self.server.import_bills("purchases", FY, 1, 1176)
        self.cycle.last_number_check = 0.0       # the next check is due
        self.sync()
        self.assertEqual(self.sell(), encode_sales_bill_no(501, FY))
        self.assertEqual(self.buy(), encode_purchase_no(1177, FY))
        self.sync()
        self.assertEqual(self.server.quarantined, [])
        self.assertIn(encode_sales_bill_no(501, FY), self.server_numbers("sales"))
        self.assertIn(encode_purchase_no(1177, FY), self.server_numbers("purchases"))
        # The overtaken blocks are used up for good: registration cannot hand them back.
        numbers.add_block(self.till, "sales", FY, 1, 200, 2)
        self.assertEqual(numbers.ranges_left(self.till, FY)["sales"][0][0], 502)

    def test_without_the_check_the_same_bill_is_quarantined(self):
        """What happened on live: the PC keeps its block and prints a number the import holds."""
        self.server.import_bills("sales", FY, 1, 500)
        self.cycle.last_number_check = 10 ** 12  # no check this time
        self.sync()
        self.assertEqual(self.sell(), encode_sales_bill_no(1, FY))
        self.sync()
        self.assertEqual(len(self.server.quarantined), 1)

    def test_a_block_whose_numbers_are_free_is_kept(self):
        # Bills elsewhere in the year (a web bill after every block) do not cost this block anything.
        self.server.import_bills("sales", FY, 251, 260)
        self.sync()
        self.assertEqual(self.sell(), encode_sales_bill_no(1, FY))
        self.sync()
        self.assertEqual(self.server.quarantined, [])
        self.assertEqual(numbers.remaining(self.till, "sales", FY), 199)

    def test_only_a_number_at_or_after_the_next_one_drops_the_block(self):
        for _ in range(3):
            self.sell()                          # 1, 2, 3 printed here
        dropped = numbers.drop_taken(self.till, FY, {"sales": {"ranges": [{"from": 1, "to": 200, "taken": 3}]}})
        self.assertEqual(dropped, [])            # 3 is this PC's own; 4..200 are free
        dropped = numbers.drop_taken(self.till, FY, {"sales": {"ranges": [{"from": 4, "to": 200, "taken": 150}]}})
        self.assertEqual([(d["kind"], d["next_serial"], d["taken"]) for d in dropped], [("sales", 4, 150)])
        self.assertEqual(numbers.remaining(self.till, "sales", FY), 0)

    def test_offline_the_blocks_are_used_as_they_are(self):
        self.server.import_bills("sales", FY, 1, 500)

        def offline(*_a, **_k):
            raise ConnectionError("no internet")

        with self.server.bind(INSTALL):
            from core.offline_first import client

            client.number_head = offline
            with self.assertRaises(ConnectionError):
                self.cycle.check_numbers()
        self.assertEqual(numbers.remaining(self.till, "sales", FY), 200)
        self.assertEqual(self.sell(), encode_sales_bill_no(1, FY))   # the server will quarantine it
        # Asked again a minute later, not at once and not five minutes later.
        import time

        self.assertAlmostEqual(self.cycle.last_number_check + worker.NUMBER_CHECK_EVERY - time.time(), 60, delta=5)

    def test_the_question_sends_only_the_numbers_left(self):
        for _ in range(50):
            self.sell()
        asked = []
        with self.server.bind(INSTALL):
            from core.offline_first import client

            real = client.number_head
            client.number_head = lambda fy, ranges: (asked.append((fy, ranges)), real(fy, ranges))[1]
            self.cycle.check_numbers(force=True)
        self.assertEqual(asked, [(FY, {"sales": [(51, 200)], "purchases": [(1, 50)]})])


class TheRequest(unittest.TestCase):
    def test_number_head_builds_the_read_only_get(self):
        from core.offline_first import client

        seen = {}
        real = client._call
        client._call = lambda method, path, body=None, timeout=60.0: seen.update(m=method, p=path) or {}
        try:
            client.number_head(2026, {"sales": [(51, 200), (401, 600)], "purchases": [(11, 50)]})
            client.number_head(2026, {"sales": [], "purchases": [(1, 50)]})
        finally:
            client._call = real
        self.assertEqual(seen["m"], "GET")
        self.assertEqual(seen["p"], "/api/sync/v2/number-head?fy=2026&purchases=1-50")


if __name__ == "__main__":
    unittest.main()
