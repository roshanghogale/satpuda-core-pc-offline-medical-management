"""After saving a bill, the app never said where the PDF went.

Reported from the counter: the "Bill Saved" box says only "Bill NNN saved." The
old screen printed the path underneath it, and the shop uses that to find the
file.

The engine could not have told it either. The PDF is written on a daemon thread
whose return value is thrown away, so the save response carried a literal
pdf_path of None -- which means the one place the page DID try to show a path
(`res.pdf_path ? ...`) had been dead code from the day it was written.

The filename cannot honestly be predicted from here: a merged counter sale or
an edit changes the bill number the stem is built from, and a long bill is split
across several sheets. The FOLDER is exact, and it is the thing actually being
asked. It has to be the RESOLVED folder, not the Settings string: a path from
another machine, or one that cannot be created, silently falls back to
Downloads, and pointing at a folder the bill is not in is worse than silence.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import bill_save_prefs  # noqa: E402


class TheFolderOnTheAnswerIsTheFolderUsed(unittest.TestCase):

    def test_the_save_response_names_a_folder(self):
        import core.desktop_sales_service as svc

        src = open(svc.__file__, encoding="utf-8").read()
        self.assertIn('"pdf_dir": pdf_dir', src,
                      "the save answer must say where the bill is written")

    def test_it_is_the_resolved_folder_not_the_settings_string(self):
        import core.desktop_sales_service as svc

        src = open(svc.__file__, encoding="utf-8").read()
        self.assertIn("resolve_sales_bill_save_dir", src)
        self.assertNotIn(
            "pdf_dir = load_sales_bill_save_dir()", src,
            "the configured path can be rejected and fall back to Downloads",
        )

    def test_the_writer_resolves_the_same_way(self):
        # bill_output writes to resolve_sales_bill_save_dir(); if the answer
        # came from anywhere else the dialog would point at the wrong folder.
        import core.bill_output as bo

        src = open(bo.__file__, encoding="utf-8").read()
        self.assertIn("resolve_sales_bill_save_dir", src)

    def test_a_configured_folder_is_used_when_it_can_be_made(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            want = os.path.join(tmp, "bills")
            with mock.patch.object(
                bill_save_prefs, "load_sales_bill_save_dir", return_value=want
            ):
                got = bill_save_prefs.resolve_sales_bill_save_dir()
            self.assertEqual(os.path.normpath(got), os.path.normpath(want))
            self.assertTrue(os.path.isdir(got), "and it is created, not just named")

    def test_a_path_from_another_machine_is_not_reported(self):
        # A Windows path carried over to a Mac (or the reverse) cannot be
        # written to; the shop must not be sent looking for it.
        foreign = "C:\\Users\\shop\\Bills" if os.name != "nt" else "/home/shop/Bills"
        with mock.patch.object(
            bill_save_prefs, "load_sales_bill_save_dir", return_value=foreign
        ):
            got = bill_save_prefs.resolve_sales_bill_save_dir()
        self.assertNotEqual(os.path.normpath(got), os.path.normpath(foreign))
        self.assertTrue(got, "there is always somewhere; it just is not that")


if __name__ == "__main__":
    unittest.main(verbosity=2)
