"""Where bills are saved, and which browser turns them into PDFs.

Bills belong in Documents, where a shop looks for its own papers - not in
Downloads among everything the browser ever fetched. And the browser that made
the last PDF is tried first, so the counter waits for one browser start instead
of one that is slow or blocked holding up every bill.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import bill_config, bill_save_prefs, printer_manager  # noqa: E402
import core.bill_output as bill_output  # noqa: E402


class TheDefaultBillFolder(unittest.TestCase):

    def setUp(self):
        self.tmp = os.path.join(tempfile.mkdtemp(), "bill_print_settings.json")
        with open(self.tmp, "w", encoding="utf-8") as fh:
            json.dump({"sales_bill_save_dir": ""}, fh)
        self._old_path, self._old_const = bill_config._bill_settings_path, bill_config.SETTINGS_PATH
        self._old_migrate = bill_save_prefs._migrate_legacy_files
        bill_config._bill_settings_path = lambda: self.tmp
        bill_config.SETTINGS_PATH = self.tmp
        bill_save_prefs._migrate_legacy_files = lambda: None

    def tearDown(self):
        bill_config._bill_settings_path, bill_config.SETTINGS_PATH = self._old_path, self._old_const
        bill_save_prefs._migrate_legacy_files = self._old_migrate

    def test_it_is_documents_when_nothing_is_configured(self):
        where = bill_save_prefs.resolve_sales_bill_save_dir()
        self.assertEqual(os.path.basename(where.rstrip(os.sep)), "Documents")
        self.assertTrue(os.path.isdir(where))

    def test_a_shop_that_chose_a_folder_keeps_it(self):
        chosen = tempfile.mkdtemp()
        with open(self.tmp, "w", encoding="utf-8") as fh:
            json.dump({"sales_bill_save_dir": chosen}, fh)
        self.assertEqual(bill_save_prefs.resolve_sales_bill_save_dir(), chosen)


class ThePdfBrowser(unittest.TestCase):

    def setUp(self):
        self.tmp = os.path.join(tempfile.mkdtemp(), "printer_settings.json")
        self._old = printer_manager._config_path
        printer_manager._config_path = lambda: self.tmp

    def tearDown(self):
        printer_manager._config_path = self._old

    def test_a_fresh_pc_tries_every_browser_with_the_full_timeout(self):
        attempts = bill_output._pdf_browser_attempts()
        self.assertTrue(attempts, "no chromium browser found to test with")
        self.assertTrue(all(t == 60 for _exe, _flag, t in attempts))

    def test_the_browser_that_worked_is_tried_first_and_fast(self):
        exe = bill_output._chromium_browser_paths()[0]
        bill_output._remember_pdf_browser(exe, "--headless", 1.0)
        first = bill_output._pdf_browser_attempts()[0]
        self.assertEqual(first[0], exe)
        self.assertEqual(first[1], "--headless")
        self.assertLess(first[2], 60)

    def test_a_remembered_browser_that_is_gone_is_ignored(self):
        cfg = printer_manager.PrinterManager.load_settings()
        cfg["pdf_browser_path"] = os.path.join(tempfile.gettempdir(), "no_such_browser.exe")
        cfg["pdf_browser_flag"] = "--headless"
        printer_manager.PrinterManager.save_settings(cfg)
        for exe, _flag, timeout_s in bill_output._pdf_browser_attempts():
            self.assertTrue(os.path.isfile(exe))
            self.assertEqual(timeout_s, 60)


if __name__ == "__main__":
    unittest.main()
