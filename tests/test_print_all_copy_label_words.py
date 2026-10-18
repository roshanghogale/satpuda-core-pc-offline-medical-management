"""Print All's copy labels are the shop's own words (owner, 18 Oct 2026).

Bill Print Style -> Print All copy labels: what is typed is printed under each bill as it
is, e.g. "Store copy : For Record Purpose Only"; left empty, CUSTOMER COPY / STORE COPY.
"""
import unittest

from core.bill_config import DEFAULT_BILL_PRINT_SETTINGS, copy_label_text


class TheWords(unittest.TestCase):
    def test_the_defaults(self):
        self.assertEqual(copy_label_text("store", {}), "STORE COPY")
        self.assertEqual(copy_label_text("customer", {}), "CUSTOMER COPY")
        self.assertEqual(DEFAULT_BILL_PRINT_SETTINGS["copy_label_store"], "STORE COPY")

    def test_the_shops_own_words_as_typed(self):
        s = {"copy_label_store": "  Store copy :  For Record Purpose Only "}
        self.assertEqual(copy_label_text("STORE", s), "Store copy : For Record Purpose Only")

    def test_no_copy_no_label(self):
        self.assertEqual(copy_label_text("", {"copy_label_store": "X"}), "")
        self.assertEqual(copy_label_text("other", {}), "")

    def test_the_bill_prints_it_as_typed(self):
        import bill_templates.classic as classic

        src = open(classic.__file__, encoding="utf-8").read()
        self.assertIn('<div class="copy-tag">{esc(copy_tag)}</div>', src)


if __name__ == "__main__":
    unittest.main()
