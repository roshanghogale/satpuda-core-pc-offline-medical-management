"""Print All's "Store Copy" tag turns with the bill (owner, 18 Oct 2026).

On the A5 page with one bill in the top half the bill is turned 180 degrees, but the tag
stayed upright: it printed above the turned bill's top edge, upside down to it.
"""
import re
import unittest

from bill_templates.classic import _bill_copy  # noqa: F401  (the template module loads)


class TheTagInTheTopHalf(unittest.TestCase):
    def test_the_tag_frame_turns_with_the_bill(self):
        import bill_templates.classic as classic

        src = open(classic.__file__, encoding="utf-8").read()
        bill = re.search(r"a5-pdf-one-top \.band-a5p-top-copy \.upright-invoice \{\{\s*transform: rotate\(180deg\)", src)
        tag = re.search(r"a5-pdf-one-top \.band-a5p-top-copy \.copy-tag-frame \{\{\s*transform: translate\(-50%, -50%\) rotate\(180deg\)", src)
        self.assertIsNotNone(bill)
        self.assertIsNotNone(tag)


if __name__ == "__main__":
    unittest.main()
