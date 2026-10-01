"""Regular medicines: a customer's standing list, kept apart from the sales history (1 Oct 2026).

The owner: a customer buys many things once; the regulars (BP, sugar ...) are a list of their
own. Picked on the bill, the list pops up: give all, some, change a quantity, or skip. A bill
made from it is an ordinary sale -- nothing in the sales history changes.

    python -m pytest tests/test_regular_medicines.py
"""
import json
import sqlite3
import unittest
from unittest import mock

from core import db_setup
from core import regular_medicines as rm


class Offline(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        db_setup.initialise(self.conn)
        self.addCleanup(self.conn.close)
        p = mock.patch("core.regular_medicines._online", return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def test_a_list_is_kept_and_read_back(self):
        rm.save_regulars(self.conn, 12, "ramesh patil", "9822000000",
                         [{"name": "telmikind am", "qty": 1, "unit": "strip"},
                          {"name": "GLYCOMET 500", "qty": 2, "unit": "strip"},
                          {"name": "telmikind am", "qty": 5}])           # the same medicine once
        got = rm.get_regulars(self.conn, 12)
        self.assertEqual(got["customer"], "RAMESH PATIL")
        self.assertEqual([(i["name"], i["qty"], i["unit"]) for i in got["items"]],
                         [("TELMIKIND AM", 1, "strip"), ("GLYCOMET 500", 2, "strip")])

    def test_a_customer_without_a_list_has_none(self):
        self.assertEqual(rm.get_regulars(self.conn, 99)["items"], [])

    def test_an_empty_list_removes_it_and_lists_show_only_real_ones(self):
        rm.save_regulars(self.conn, 1, "A", "", [{"name": "DOLO 650", "qty": 1}])
        rm.save_regulars(self.conn, 2, "B", "", [{"name": "PAN 40", "qty": 1}])
        rm.save_regulars(self.conn, 2, "B", "", [])
        self.assertEqual([r["customer"] for r in rm.list_regulars(self.conn)], ["A"])

    def test_no_customer_no_list(self):
        with self.assertRaises(ValueError):
            rm.save_regulars(self.conn, None, "", "", [{"name": "DOLO 650", "qty": 1}])

    def test_the_sales_history_is_not_touched(self):
        before = self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        rm.save_regulars(self.conn, 1, "A", "", [{"name": "DOLO 650", "qty": 1}])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], before)


class Online(unittest.TestCase):
    """Online every PC of the store shares the list through the server's store_settings."""

    def setUp(self):
        rm._cache.update(at=0.0, rows={})
        self.store: dict = {}
        p1 = mock.patch("core.regular_medicines._online", return_value=True)
        p2 = mock.patch("core.server_live.push_settings_kv",
                        side_effect=lambda name, value: self.store.__setitem__(name, value))
        p3 = mock.patch("core.server_live._token", return_value="t")
        p4 = mock.patch("core.server_api._request",
                        side_effect=lambda *a, **k: {"ok": True, "data": [
                            {"id": 0, "local_id": 0, "settings": [{"name": n, "value": v} for n, v in self.store.items()]}]})
        for p in (p1, p2, p3, p4):
            p.start()
            self.addCleanup(p.stop)

    def test_saved_on_the_server_and_read_back_by_another_pc(self):
        rm.save_regulars(None, 7, "Sunita", "", [{"name": "THYRONORM 50", "qty": 1, "unit": "strip"}])
        self.assertIn("regular_meds:7", self.store)
        self.assertEqual(json.loads(self.store["regular_meds:7"])["items"][0]["name"], "THYRONORM 50")
        rm._cache.update(at=0.0, rows={})                        # another PC: nothing cached
        self.assertEqual(rm.get_regulars(None, 7)["items"][0]["qty"], 1)
        self.assertEqual([r["customer"] for r in rm.list_regulars(None)], ["SUNITA"])


if __name__ == "__main__":
    unittest.main()
