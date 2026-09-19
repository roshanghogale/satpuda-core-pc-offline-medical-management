"""The web purchase-entry catalog, on a shop that keeps its records on the server.

"Start Web Entry Server" writes catalog.json next to the page it serves: the
shop's suppliers and the medicine names already in stock. Both were read from
the engine's local connection -- which, Online, is sqlite3.connect(":memory:").
So the catalog was built from an empty database and then SAVED over the shop's
own file. The web page it feeds lost every supplier, and the loss survived
going back Offline, because the good file had already been overwritten.

Two things pinned here: Online reads the server's catalog, and an empty read
never overwrites a catalog that already has something in it.
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db_setup, web_purchase_server as web  # noqa: E402

SERVER_SUPPLIERS = [
    {"name": "ZZ SUPPLIER TWO", "address": "Road 2", "phone": "900", "gstin": "G2",
     "dl_numbers": "DL2"},
    {"name": "ZZ SUPPLIER ONE", "address": "Road 1", "phone": "901", "gstin": "G1",
     "dl_numbers": "DL1"},
    {"name": "   ", "address": "", "phone": "", "gstin": "", "dl_numbers": ""},
]

SERVER_MEDICINES = [
    {"id": 1, "name": "ZZ PARA 500", "batch_no": "A"},
    {"id": 2, "name": "ZZ PARA 500", "batch_no": "B"},   # same name, two batches
    {"id": 3, "name": "ZZ AMOX 250", "batch_no": "C"},
]


class OnlineTheCatalogComesFromTheServer(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")   # the empty shell Online really uses
        self.conn.row_factory = sqlite3.Row
        db_setup.initialise(self.conn)
        self._patches = [
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.online_catalog.suppliers", return_value=SERVER_SUPPLIERS),
            mock.patch("core.online_catalog.medicines", return_value=SERVER_MEDICINES),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.conn.close()

    def test_suppliers_are_not_empty(self):
        cat = web.build_runtime_catalog(self.conn)
        names = [s["name"] for s in cat["suppliers"]]
        self.assertEqual(names, ["ZZ SUPPLIER ONE", "ZZ SUPPLIER TWO"])

    def test_supplier_details_survive_the_trip(self):
        cat = web.build_runtime_catalog(self.conn)
        one = cat["suppliers"][0]
        self.assertEqual(one["gstin"], "G1")
        self.assertEqual(one["dl_numbers"], "DL1")

    def test_medicine_names_are_distinct_and_sorted(self):
        cat = web.build_runtime_catalog(self.conn)
        self.assertEqual(cat["inventory_medicine_names"], ["ZZ AMOX 250", "ZZ PARA 500"])

    def test_the_count_matches_what_was_written(self):
        cat = web.build_runtime_catalog(self.conn)
        self.assertEqual(cat["supplier_count"], 2)


class AnUnreachableServerIsNotAnEmptyShop(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db_setup.initialise(self.conn)
        self._patches = [
            mock.patch("core.sync_prefs.is_online_mode", return_value=True),
            mock.patch("core.online_catalog.suppliers",
                       side_effect=OSError("no route to host")),
            mock.patch("core.online_catalog.medicines",
                       side_effect=OSError("no route to host")),
        ]
        for p in self._patches:
            p.start()
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.conn.close()

    def test_the_failure_is_reported_not_hidden(self):
        cat = web.build_runtime_catalog(self.conn)
        self.assertFalse(cat["suppliers"])
        self.assertTrue(cat["supplier_error"], "an empty read looked like a clean answer")

    def test_a_good_catalog_on_disk_is_left_alone(self):
        path = os.path.join(self.dir, "catalog.json")
        good = {"ok": True, "suppliers": [{"name": "ZZ SUPPLIER ONE"}],
                "inventory_medicine_names": ["ZZ PARA 500"]}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(good, f)

        web.write_runtime_catalog(self.dir, self.conn)

        with open(path, encoding="utf-8") as f:
            after = json.load(f)
        self.assertEqual(
            after, good,
            "an empty read overwrote the shop's own catalog -- the web page lost "
            "every supplier it had",
        )

    def test_a_first_run_still_writes_the_file(self):
        # Nothing to protect yet: an empty catalog is better than no file at all.
        web.write_runtime_catalog(self.dir, self.conn)
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "catalog.json")))


if __name__ == "__main__":
    unittest.main()
