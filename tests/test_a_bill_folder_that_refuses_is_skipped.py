"""A bill folder that exists but refuses writes no longer stops every print (2 Oct 2026).

The saved folder was another Windows account's Documents. It existed, so the old check
said yes, and printing a bill on a normal printer failed with "Permission denied" while
writing Bill_<no>.html. Now the folder is tried once and this PC's Documents is used.
"""
import builtins
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.bill_save_prefs as prefs  # noqa: E402


class AFolderThatRefuses(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.refusing = os.path.join(self.tmp, "other_user_documents")
        os.makedirs(self.refusing)
        self.home_docs = os.path.join(self.tmp, "Documents")
        os.makedirs(self.home_docs)
        real_open = builtins.open

        def guarded_open(path, *a, **k):
            if str(path).startswith(self.refusing):
                raise PermissionError(13, "Permission denied", str(path))
            return real_open(path, *a, **k)

        for p in (
            mock.patch("builtins.open", guarded_open),
            mock.patch.object(prefs, "load_sales_bill_save_dir", return_value=self.refusing),
            mock.patch("core.bill_config.load_bill_print_settings",
                       return_value={"sales_bill_save_dir": self.refusing}),
            mock.patch.dict(os.environ, {"USERPROFILE": self.tmp, "OneDrive": ""}),
            mock.patch("os.path.expanduser", return_value=self.tmp),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_this_pcs_documents_is_used(self):
        self.assertEqual(self.home_docs, prefs.resolve_sales_bill_save_dir())

    def test_a_writable_folder_is_kept(self):
        mine = os.path.join(self.tmp, "my_bills")
        with mock.patch.object(prefs, "load_sales_bill_save_dir", return_value=mine):
            self.assertEqual(mine, prefs.resolve_sales_bill_save_dir())
        self.assertEqual([], os.listdir(mine))          # the probe file is gone


if __name__ == "__main__":
    unittest.main()
