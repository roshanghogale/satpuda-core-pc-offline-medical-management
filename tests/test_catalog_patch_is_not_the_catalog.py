"""A patch describes what a save touched. It must never become the whole list.

Reported from the counter: after saving a sale bill, the medicine dropdown
showed only the medicines that were on that bill, and the app had to be closed
and reopened to see the rest.

`patch_docs` merges the rows a save just flushed into the in-memory catalog. On
a cache MISS it started from an empty list, appended the bill's medicines, and
stored that under the inventory key with a fresh timestamp -- so those few rows
became the shop's entire medicine list, and `_apply_list_indexes` rebuilt the id
index from them too. Because every save re-stamped the timestamp, the 120s TTL
never elapsed while billing continued, which is why only a restart cleared it.
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import online_catalog as cat  # noqa: E402

KEY = "medicines_inventory"


def med(mid, name, stock=10):
    return {"id": mid, "local_id": mid, "name": name, "stock_qty": stock}


class PatchDocsNeverShrinksTheCatalog(unittest.TestCase):

    def setUp(self):
        cat._cache.clear()

    def tearDown(self):
        cat._cache.clear()

    def _cached(self):
        hit = cat._cache.get(KEY)
        return list(hit[1]) if hit else None

    def test_a_patch_with_no_cached_list_does_not_invent_one(self):
        """The bug: three medicines from a bill became the whole shop."""
        cat.patch_docs("medicines", [med(1, "AMOXY"), med(2, "DOLO"), med(3, "PANTOP")])
        self.assertIsNone(
            self._cached(),
            "a patch must not create the catalog; the next read loads the real one",
        )

    def test_a_patch_merges_into_a_real_list_without_dropping_anything(self):
        shelf = [med(i, f"MED {i}") for i in range(1, 21)]
        cat._cache[KEY] = (time.time(), shelf)
        cat.patch_docs("medicines", [med(3, "MED 3", stock=99)])
        rows = self._cached()
        self.assertEqual(len(rows), 20, "merging one row must not shrink the shelf")
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id[3]["stock_qty"], 99, "the patched row is updated in place")
        self.assertEqual(by_id[7]["name"], "MED 7", "untouched rows are left alone")

    def test_a_genuinely_new_medicine_is_added(self):
        cat._cache[KEY] = (time.time(), [med(1, "AMOXY")])
        cat.patch_docs("medicines", [med(2, "NEW MED")])
        rows = self._cached()
        self.assertEqual({r["id"] for r in rows}, {1, 2})

    def test_a_patch_does_not_extend_the_life_of_a_stale_list(self):
        """Every save patched, every patch re-stamped, so the TTL never elapsed."""
        old = time.time() - 100.0
        cat._cache[KEY] = (old, [med(1, "AMOXY")])
        cat.patch_docs("medicines", [med(1, "AMOXY", stock=5)])
        self.assertAlmostEqual(
            cat._cache[KEY][0], old, places=3,
            msg="the original timestamp must survive so the TTL still expires",
        )

    def test_many_saves_in_a_row_still_let_the_list_expire(self):
        old = time.time() - 100.0
        cat._cache[KEY] = (old, [med(1, "AMOXY")])
        for i in range(10):
            cat.patch_docs("medicines", [med(1, "AMOXY", stock=i)])
        age = time.time() - cat._cache[KEY][0]
        self.assertGreater(age, 99.0, "a burst of billing cannot hold a stale catalog alive")

    def test_an_empty_patch_is_a_no_op(self):
        cat._cache[KEY] = (time.time(), [med(1, "AMOXY")])
        cat.patch_docs("medicines", [])
        cat.patch_docs("medicines", None)
        self.assertEqual(len(self._cached()), 1)

    def test_the_same_rule_holds_for_the_other_collections(self):
        for collection in ("customers", "suppliers", "purchases", "sales"):
            cat._cache.clear()
            cat.patch_docs(collection, [{"id": 1, "name": "X"}])
            self.assertIsNone(
                cat._cache.get(collection),
                f"{collection}: a patch must not create the list either",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
