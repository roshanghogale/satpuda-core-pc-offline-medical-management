"""A bottle is not a strip.

An imported bill carried a pack of "200ML" as tablets_per_stripe=200, so one
bottle of gel went onto the shelf as two hundred. Every measured type on the
RUSHABH 10573 bill came in inflated -- 1 gel became 200, 4 powders became 800,
6 gels became 1200 -- while the one Tablet line was correct. The pack size of a
measured medicine must never be read as a count of tablets.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.layout_config import (  # noqa: E402
    is_strip_count_type,
    parse_tablets_per_stripe,
)

# What the bill actually said, and what the shelf should have gained.
BILL = [
    # type,        pack,     qty, free, expected stock added
    ("Ointment",   "15GM",     3, 0,    3),
    ("Gel",        "200ML",    1, 0,    1),
    ("Powder",     "60GM",     3, 1,    4),
    ("Liquid",     "400",      2, 0,    2),
    ("Gel",        "200ML",    5, 1,    6),
    ("Liquid",     "30ML",     5, 1,    6),
    ("Capsule",    "10",      10, 2,    120),
    ("Liquid",     "30 ML",    2, 0,    2),
    ("Gel",        "30GM",     5, 1,    6),
    ("Tablet",     "10'S",    10, 2,    120),
]


def stock_added(med_type: str, pack: str, qty: int, free: int) -> int:
    """Mirrors _add_line_figures: strips multiply, everything else does not."""
    if is_strip_count_type(med_type, pack):
        tps = max(1, parse_tablets_per_stripe(pack))
    else:
        tps = 1
    return (qty + free) * tps


class TestMeasuredPacksAreNotStrips(unittest.TestCase):
    def test_every_line_of_the_bill(self):
        for med_type, pack, qty, free, want in BILL:
            with self.subTest(type=med_type, pack=pack):
                self.assertEqual(stock_added(med_type, pack, qty, free), want)

    def test_a_millilitre_pack_never_becomes_a_strip_count(self):
        for pack in ("200ML", "30ML", "1ml", "100 ML"):
            self.assertEqual(parse_tablets_per_stripe(pack), 1, pack)

    def test_a_weight_pack_never_becomes_a_strip_count(self):
        for pack in ("15GM", "60GM", "30GM", "200 g"):
            self.assertEqual(parse_tablets_per_stripe(pack), 1, pack)

    def test_a_real_strip_still_multiplies(self):
        self.assertEqual(stock_added("Tablet", "10", 10, 2), 120)
        self.assertEqual(stock_added("Capsule", "15", 2, 0), 30)

    def test_the_gel_that_became_two_hundred(self):
        # The exact line from the bill that started this.
        self.assertEqual(stock_added("Gel", "200ML", 1, 0), 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
