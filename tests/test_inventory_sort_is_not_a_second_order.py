"""The browser held a second opinion about the order and told only the rows.

Reported from the counter: on Inventory, right-click -> Edit opened a different
medicine's card; Delete named a third medicine in the "Hide ... from inventory?"
box and then hid a fourth; a full-stock item showed as Expired while the expired
batch looked clean.

The engine sends three lists that describe the same medicines position by
position -- rows, row_ids and row_styles -- built together in one loop. The page
re-sorted the ROWS in the browser and left the ids and the styles in the
engine's order, then indexed all three by the sorted row's position.

It did not need the Sort dropdown to fire. The dropdown had no resting state, so
every render went through the browser sort, and in Online mode the rows arrive in
the store server's own order with nothing asking for another -- so the plain
default screen, with no search and no sort change, could already be crossed.

The fix moves the ordering into the engine, onto the SOURCE list, before the ids
are derived from it. This file is the sorter's contract.
"""
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.list_sort import INVENTORY_SORT_OPTIONS, order_inventory_rows  # noqa: E402
from core.name_search_rank import order_by_sql  # noqa: E402

# (id, name, stock, expiry) -- deliberately built so that alphabetical, stock
# and expiry orders are all different from each other and from this order.
SHELF = [
    (11, "MECOVET", 5, "2027-03-01"),
    (12, "AMOXY 500", 40, "2025-06-01"),
    (13, "BECOSULES", 1, ""),
    (14, "ZINCOVIT", 12, "2026-01-01"),
    (15, "CALPOL", 0, "2028-11-01"),
]

FIELDS = dict(name=lambda r: r[1], stock=lambda r: r[2], expiry=lambda r: r[3])


def names(rows):
    return [r[1] for r in rows]


def ids(rows):
    return [r[0] for r in rows]


class TheIdsTravelWithTheirOwnRows(unittest.TestCase):

    def test_the_whole_bug_in_one_assertion(self):
        """Sorting must move the medicine, not just its name."""
        by_id = {r[0]: r[1] for r in SHELF}
        for option in INVENTORY_SORT_OPTIONS:
            with self.subTest(option=option):
                out = order_inventory_rows(SHELF, option, **FIELDS)
                for row in out:
                    self.assertEqual(
                        by_id[row[0]],
                        row[1],
                        "a row and its id must still describe one medicine",
                    )

    def test_no_option_can_lose_or_duplicate_a_medicine(self):
        for option in list(INVENTORY_SORT_OPTIONS) + ["", "something odd"]:
            with self.subTest(option=option):
                out = order_inventory_rows(SHELF, option, **FIELDS)
                self.assertEqual(len(out), len(SHELF))
                self.assertEqual(sorted(ids(out)), sorted(ids(SHELF)))

    def test_the_caller_s_list_is_not_reordered_underneath_it(self):
        original = list(SHELF)
        order_inventory_rows(SHELF, "Alphabetic (Z-A)", **FIELDS)
        self.assertEqual(SHELF, original, "the source list must be left alone")


class EachOptionOrdersWhatItSays(unittest.TestCase):

    def test_alphabetic_both_ways(self):
        az = names(order_inventory_rows(SHELF, "Alphabetic (A-Z)", **FIELDS))
        za = names(order_inventory_rows(SHELF, "Alphabetic (Z-A)", **FIELDS))
        self.assertEqual(az, ["AMOXY 500", "BECOSULES", "CALPOL", "MECOVET", "ZINCOVIT"])
        self.assertEqual(za, list(reversed(az)))

    def test_alphabetic_ignores_case(self):
        rows = [(1, "zinc", 0, ""), (2, "AMOXY", 0, ""), (3, "Becosules", 0, "")]
        self.assertEqual(
            names(order_inventory_rows(rows, "Alphabetic (A-Z)", **FIELDS)),
            ["AMOXY", "Becosules", "zinc"],
        )

    def test_stock_both_ways(self):
        low = order_inventory_rows(SHELF, "Stock (low to high)", **FIELDS)
        high = order_inventory_rows(SHELF, "Stock (high to low)", **FIELDS)
        self.assertEqual([r[2] for r in low], [0, 1, 5, 12, 40])
        self.assertEqual([r[2] for r in high], [40, 12, 5, 1, 0])

    def test_equal_stock_breaks_by_name_so_the_list_does_not_shuffle(self):
        rows = [(1, "ZINCOVIT", 7, ""), (2, "AMOXY", 7, ""), (3, "BECOSULES", 7, "")]
        self.assertEqual(
            names(order_inventory_rows(rows, "Stock (low to high)", **FIELDS)),
            ["AMOXY", "BECOSULES", "ZINCOVIT"],
        )

    def test_the_name_tie_break_reads_the_same_way_in_both_directions(self):
        # Reversing a combined (value, name) key reverses the tie-break too, so
        # medicines on equal stock would list A-Z one way and Z-A the other --
        # the list would look shuffled for no reason the shop can see.
        rows = [(1, "ZINCOVIT", 7, "2026-01-01"),
                (2, "AMOXY", 7, "2026-01-01"),
                (3, "BECOSULES", 7, "2026-01-01")]
        for option in ("Stock (low to high)", "Stock (high to low)",
                       "Expiry (soonest first)", "Expiry (latest first)"):
            with self.subTest(option=option):
                self.assertEqual(
                    names(order_inventory_rows(rows, option, **FIELDS)),
                    ["AMOXY", "BECOSULES", "ZINCOVIT"],
                )

    def test_an_unreadable_stock_is_zero_not_a_crash(self):
        rows = [(1, "A", "12", ""), (2, "B", "", ""), (3, "C", None, ""), (4, "D", "abc", "")]
        out = order_inventory_rows(rows, "Stock (low to high)", **FIELDS)
        self.assertEqual(names(out)[-1], "A", "the only real quantity sorts highest")
        self.assertEqual(len(out), 4)

    def test_expiry_soonest_first(self):
        out = order_inventory_rows(SHELF, "Expiry (soonest first)", **FIELDS)
        self.assertEqual(names(out)[:4], ["AMOXY 500", "ZINCOVIT", "MECOVET", "CALPOL"])

    def test_a_blank_expiry_sits_last_whichever_way_the_shop_looks(self):
        # It is not "soonest" and it is not "latest" -- it is unknown.
        for option in ("Expiry (soonest first)", "Expiry (latest first)"):
            with self.subTest(option=option):
                out = order_inventory_rows(SHELF, option, **FIELDS)
                self.assertEqual(names(out)[-1], "BECOSULES")

    def test_expiry_understands_the_formats_the_shop_actually_types(self):
        # The browser compared these as raw strings, which is meaningless for
        # everything but ISO.
        rows = [
            (1, "LATER", 0, "01-06-2026"),
            (2, "SOONER", 0, "01-12-2025"),
            (3, "SOONEST", 0, "03/2025"),
        ]
        self.assertEqual(
            names(order_inventory_rows(rows, "Expiry (soonest first)", **FIELDS)),
            ["SOONEST", "SOONER", "LATER"],
        )


class BestMatchIsTheEnginesOrderNotASecondOne(unittest.TestCase):

    def test_an_empty_search_box_is_plain_alphabetical(self):
        best = names(order_inventory_rows(SHELF, "", **FIELDS))
        az = names(order_inventory_rows(SHELF, "Alphabetic (A-Z)", **FIELDS))
        self.assertEqual(best, az, "the resting state must not move the shelf")

    def test_typing_a_letter_keeps_the_ranking_instead_of_throwing_it_away(self):
        # Plain alphabetical put AMOXY and BECOSULES above MECOVET when the shop
        # typed "m", which is never what they meant.
        out = names(order_inventory_rows(SHELF, "", query="m", **FIELDS))
        self.assertEqual(out[0], "MECOVET")

    def test_an_unrecognised_option_falls_back_and_never_raises(self):
        out = order_inventory_rows(SHELF, "Whatever The Next Version Adds", **FIELDS)
        self.assertEqual(names(out), names(order_inventory_rows(SHELF, "", **FIELDS)))

    def test_nothing_to_sort_is_safe(self):
        for rows in ([], None, [SHELF[0]]):
            with self.subTest(rows=rows):
                order_inventory_rows(rows, "Stock (low to high)", **FIELDS)


class BothModesAgreeOnBestMatch(unittest.TestCase):
    """The equality that justifies 'Best match' existing at all.

    Offline gets its order from a SQL ORDER BY; Online has no query to lean on,
    so the same ordering is spelled out in Python. If these two ever disagree, a
    shop sees a different list depending on which mode it is in.
    """

    # The punctuation is the point: SQLite's COLLATE NOCASE and Python's lower()
    # are most likely to disagree exactly here.
    NAMES = ["AMOXY-M", "AMOXY M", "B/COMPLEX", "MECOVET", "amoxil", "ZINCOVIT"]

    def _sql_order(self, query):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE medicines (name TEXT)")
        conn.executemany(
            "INSERT INTO medicines (name) VALUES (?)", [(n,) for n in self.NAMES]
        )
        order_sql, params = order_by_sql("name", query)
        rows = conn.execute(
            f"SELECT name FROM medicines ORDER BY {order_sql}", params
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]

    def _python_order(self, query):
        rows = [(i, n, 0, "") for i, n in enumerate(self.NAMES)]
        return names(order_inventory_rows(rows, "", query=query, **FIELDS))

    def test_they_agree_with_an_empty_search_box(self):
        self.assertEqual(self._python_order(""), self._sql_order(""))

    def test_they_agree_while_the_shop_is_typing(self):
        self.assertEqual(self._python_order("m"), self._sql_order("m"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
