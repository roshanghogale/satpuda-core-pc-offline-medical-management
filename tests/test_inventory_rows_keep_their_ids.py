"""Whatever the sort, rows[i], row_ids[i] and row_styles[i] are one medicine.

The contract one level up from the sorter. list_inventory builds its three
parallel lists in a single loop, so they agree by construction -- as long as
nothing reorders one of them afterwards.

That "afterwards" is the whole bug. The browser used to reorder the rows and
leave the ids and the styles behind. The fix orders the SOURCE list instead, and
in the Online branch it has to happen before src_ids is derived from src_rows:
sorting one line later would rebuild exactly the same bug inside the engine,
where no browser sort is visible to blame. The Z-A case below is what would fail
if someone ever moved it.
"""
import os
import sqlite3
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db_setup, desktop_pages_service as pages, sync_prefs  # noqa: E402
from core.list_sort import INVENTORY_SORT_OPTIONS  # noqa: E402

TODAY = date.today()


def iso(days):
    return (TODAY + timedelta(days=days)).strftime("%Y-%m-%d")


# Alphabetical, stock and expiry orders are all different from each other and
# from insertion order, and the statuses make row_styles non-trivial: one
# expired, one out of stock, one low, two healthy.
SHELF = [
    # name,         type,      stock, expiry
    ("MECOVET",     "Tablet",     45, iso(400)),
    ("AMOXY 500",   "Capsule",     0, iso(300)),
    ("BECOSULES",   "Tablet",      2, iso(-30)),
    ("ZINCOVIT",    "Syrup",      18, iso(20)),
    ("CALPOL",      "Tablet",    120, iso(900)),
]


class RowsIdsAndStylesStayInStep(unittest.TestCase):

    def setUp(self):
        # This machine may be in Online mode, where list_inventory talks to the
        # store server and never looks at the connection it was handed.
        self._online = sync_prefs.is_online_mode
        sync_prefs.is_online_mode = lambda *a, **k: False
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db_setup.initialise(self.conn)
        # initialise() seeds sample medicines; the shelf under test is ours.
        self.conn.execute("DELETE FROM medicines")
        for name, typ, stock, expiry in SHELF:
            self.conn.execute(
                "INSERT INTO medicines "
                "(name, type, stock_qty, unit, mrp, rate, batch_no, expiry_date) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (name, typ, stock, "10", 100.0, 80.0, "B1", expiry),
            )
        self.conn.commit()
        self.names_by_id = {
            r[0]: r[1] for r in self.conn.execute("SELECT id, name FROM medicines")
        }

    def tearDown(self):
        self.conn.close()
        sync_prefs.is_online_mode = self._online

    def _payload(self, sort):
        return pages.list_inventory(self.conn, sort=sort)

    def test_every_sort_keeps_the_three_lists_the_same_length(self):
        for option in INVENTORY_SORT_OPTIONS:
            with self.subTest(option=option):
                p = self._payload(option)
                self.assertEqual(len(p["rows"]), len(SHELF))
                self.assertEqual(len(p["row_ids"]), len(p["rows"]))
                self.assertEqual(len(p["row_styles"]), len(p["rows"]))

    def test_the_name_on_the_row_belongs_to_the_id_beside_it(self):
        """Right-click Edit used to open a different medicine. This is why."""
        for option in INVENTORY_SORT_OPTIONS:
            with self.subTest(option=option):
                p = self._payload(option)
                name_col = p["columns"].index("Name")
                for row, mid in zip(p["rows"], p["row_ids"]):
                    self.assertEqual(
                        str(row[name_col]),
                        self.names_by_id[mid],
                        "the id under this row belongs to another medicine",
                    )

    def test_the_status_column_belongs_to_the_row_it_sits_on(self):
        """A full-stock item read "Expired" while the expired batch looked clean."""
        for option in INVENTORY_SORT_OPTIONS:
            with self.subTest(option=option):
                p = self._payload(option)
                cols = p["columns"]
                if "Status" not in cols:
                    continue
                sidx = cols.index("Status")
                for row, style in zip(p["rows"], p["row_styles"]):
                    if not style:
                        continue
                    badge = style.get("badge_text") or style.get("label") or ""
                    if badge:
                        self.assertEqual(
                            str(row[sidx]),
                            str(badge),
                            "the colour on this row describes another medicine",
                        )

    def test_z_a_puts_the_alphabetically_last_medicine_s_own_id_first(self):
        """The landmine test.

        In the Online branch the ids are derived positionally from the source
        list. Sort after that derivation and every row gets the wrong id --
        silently, because the list still looks correctly ordered on screen.
        """
        p = self._payload("Alphabetic (Z-A)")
        last_alphabetically = max(name for name, *_ in SHELF)
        self.assertEqual(self.names_by_id[p["row_ids"][0]], last_alphabetically)

    def test_the_shop_is_offered_the_engine_s_own_sort_list(self):
        p = self._payload("")
        self.assertEqual(list(p["sort_options"]), list(INVENTORY_SORT_OPTIONS))

    def test_the_resting_state_is_still_alphabetical(self):
        p = self._payload("")
        name_col = p["columns"].index("Name")
        got = [str(r[name_col]) for r in p["rows"]]
        self.assertEqual(got, sorted(got, key=str.lower))

    def test_the_shelf_actually_reorders(self):
        """Guards against a sort that quietly does nothing at all."""
        name_col = self._payload("")["columns"].index("Name")
        orders = {
            option: tuple(str(r[name_col]) for r in self._payload(option)["rows"])
            for option in INVENTORY_SORT_OPTIONS
        }
        self.assertNotEqual(orders["Alphabetic (A-Z)"], orders["Alphabetic (Z-A)"])
        self.assertNotEqual(orders["Alphabetic (A-Z)"], orders["Stock (low to high)"])
        self.assertNotEqual(
            orders["Stock (low to high)"], orders["Expiry (soonest first)"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
