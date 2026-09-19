"""The dropdown ordering the shop asked for: what starts with the letter typed
comes first, then words starting with it, then anything merely containing it.

The first version of this looked only at the FIRST place the query appeared, so
"AMOXY-M" was judged on the m in AMOXY and sank below plain contains matches --
even though the module's own docstring gave it as an example of a word match.
"""
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.name_search_rank import (  # noqa: E402
    CONTAINS,
    NO_MATCH,
    STARTS_WITH,
    WORD_STARTS_WITH,
    match_rank,
    order_by_sql,
    rank_names,
    rank_rows,
)

NAMES = [
    "AMOXY", "CALPOL", "MECOVET", "BECOSULES", "MELONEX",
    "AMOXY-M", "VITAMIN M", "CALCIMAX", "MOXICIP", "B/COMPLEX-M",
]


class TestRank(unittest.TestCase):
    def test_starts_with_wins(self):
        self.assertEqual(match_rank("MECOVET", "m"), STARTS_WITH)
        self.assertEqual(match_rank("MELONEX", "me"), STARTS_WITH)

    def test_word_start_found_past_an_earlier_hit(self):
        # The m of AMOXY comes first; the M that is its own word still counts.
        self.assertEqual(match_rank("AMOXY-M", "m"), WORD_STARTS_WITH)
        self.assertEqual(match_rank("VITAMIN M", "m"), WORD_STARTS_WITH)
        self.assertEqual(match_rank("B/COMPLEX-M", "m"), WORD_STARTS_WITH)

    def test_plain_contains(self):
        self.assertEqual(match_rank("AMOXY", "m"), CONTAINS)
        self.assertEqual(match_rank("CALCIMAX", "m"), CONTAINS)

    def test_absent(self):
        self.assertEqual(match_rank("CALPOL", "m"), NO_MATCH)

    def test_empty_query_matches_everything(self):
        self.assertEqual(match_rank("ANYTHING", ""), STARTS_WITH)

    def test_ordering_groups(self):
        out = rank_names(NAMES, "m")
        first_three = out[:3]
        self.assertEqual(first_three, ["MECOVET", "MELONEX", "MOXICIP"])
        self.assertNotIn("CALPOL", out)  # no match at all is dropped
        # every starts-with sits above every word-start, which sits above contains
        ranks = [match_rank(n, "m") for n in out]
        self.assertEqual(ranks, sorted(ranks))

    def test_alphabetical_inside_a_group(self):
        out = [n for n in rank_names(NAMES, "m") if match_rank(n, "m") == STARTS_WITH]
        self.assertEqual(out, sorted(out))


class TestOrderBySql(unittest.TestCase):
    def _order(self, query):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE m (name TEXT)")
        db.executemany("INSERT INTO m VALUES (?)", [(n,) for n in NAMES])
        frag, params = order_by_sql("name", query)
        return [r[0] for r in db.execute(f"SELECT name FROM m ORDER BY {frag}", params)]

    def test_sql_puts_starts_with_first(self):
        self.assertEqual(self._order("m")[:3], ["MECOVET", "MELONEX", "MOXICIP"])

    def test_sql_ranks_word_starts_above_contains(self):
        out = self._order("m")
        self.assertLess(out.index("AMOXY-M"), out.index("AMOXY"))
        self.assertLess(out.index("VITAMIN M"), out.index("CALCIMAX"))

    def test_sql_agrees_with_python_on_the_leading_group(self):
        for q in ("m", "ca", "a", "b"):
            sql_first = [n for n in self._order(q) if match_rank(n, q) == STARTS_WITH]
            py_first = [n for n in rank_names(NAMES, q) if match_rank(n, q) == STARTS_WITH]
            self.assertEqual(sorted(sql_first), sorted(py_first), q)

    def test_empty_query_is_plain_alphabetical(self):
        self.assertEqual(self._order(""), sorted(NAMES, key=str.lower))

    def test_wildcards_in_the_query_are_escaped(self):
        # A shop typing "%" must not match every medicine.
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE m (name TEXT)")
        db.executemany("INSERT INTO m VALUES (?)", [(n,) for n in NAMES])
        frag, params = order_by_sql("name", "%")
        rows = [r[0] for r in db.execute(f"SELECT name FROM m ORDER BY {frag}", params)]
        self.assertEqual(len(rows), len(NAMES))


class TestRankBeforeTruncating(unittest.TestCase):
    """A dropdown must rank the WHOLE match set, then cut -- never the reverse.

    online_catalog.search_medicines_flat used to stop collecting once it had
    `cap` rows and rank only those. The catalogue is held alphabetically, so
    typing "m" in a shop of a few hundred medicines filled the list with AMOXY
    and stopped before reaching M, MECOVET or MELONEX. The ranking that was
    meant to lift them to the top never saw them.
    """

    def _catalogue(self):
        names = [f"AMOXY {i:03d} TABLET" for i in range(400)]
        names += [f"BECOSULES {i:03d} MG" for i in range(400)]
        names += ["M", "MECOVET", "MELONEX", "MOXICIP"]
        names.sort(key=str.lower)
        return [{"name": n} for n in names]

    def test_starts_with_survives_a_small_cap(self):
        rows = self._catalogue()
        cap = 40
        matches = [r for r in rows if "m" in r["name"].lower()]
        self.assertGreater(len(matches), cap, "test needs more matches than the cap")
        out = rank_rows(matches, "m", limit=cap)
        self.assertEqual(len(out), cap)
        starts = [r["name"] for r in out if r["name"].lower().startswith("m")]
        self.assertEqual(starts[:4], ["M", "MECOVET", "MELONEX", "MOXICIP"])

    def test_truncating_first_loses_them(self):
        # Guards the shape of the bug itself, so a future "optimisation" that
        # reintroduces an early break is caught by the test above.
        rows = self._catalogue()
        cap = 40
        early = []
        for r in rows:
            if "m" in r["name"].lower():
                early.append(r)
                if len(early) >= cap:
                    break
        ranked_late = rank_rows(early, "m", limit=cap)
        self.assertEqual(
            [r["name"] for r in ranked_late if r["name"].lower().startswith("m")],
            [],
            "cutting before ranking is what dropped every starts-with match",
        )


if __name__ == "__main__":
    unittest.main(verbosity=1)
