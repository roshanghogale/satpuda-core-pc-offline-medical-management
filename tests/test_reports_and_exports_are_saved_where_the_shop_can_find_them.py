"""Saving reports, exports and bill PDFs (owner, 17 Oct 2026: "exports and reports like the
Schedule Report only say print -- there is no option to save a PDF or a file; put it on
every page").

Downloads left as a blob link inside the webview, with no word of where they went; Print All
and the bill preview had no save at all; Home -> Export Data imported a class that does not
exist and said "Export complete". Now the engine writes the file into the shop's reports
folder and the app shows it with Open / Show in folder. Nothing here prints.
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def src(*parts) -> str:
    with open(os.path.join(_REPO, *parts), encoding="utf-8") as fh:
        return fh.read()


class _Reports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.reports = os.path.join(self.tmp, "reports")
        os.makedirs(self.reports)
        import core.saved_files as sf

        for p in (mock.patch.object(sf, "reports_dir", return_value=self.reports),
                  mock.patch.object(sf, "_save_roots", return_value=[os.path.normcase(self.reports)])):
            p.start()
            self.addCleanup(p.stop)
        self.sf = sf


class SavedFiles(_Reports):
    def test_save_writes_into_the_reports_folder_and_says_where(self):
        res = self.sf.save_base64("Sales Report.pdf", base64.b64encode(b"%PDF-1.4 x").decode())
        self.assertTrue(res["ok"], res)
        self.assertEqual(os.path.dirname(res["path"]), self.reports)
        self.assertEqual(res["file_name"], "Sales Report.pdf")
        with open(res["path"], "rb") as fh:
            self.assertEqual(fh.read(), b"%PDF-1.4 x")

    def test_a_second_save_does_not_overwrite_the_first(self):
        a = self.sf.save_base64("x.csv", base64.b64encode(b"1").decode())["path"]
        b = self.sf.save_base64("x.csv", base64.b64encode(b"2").decode())["path"]
        self.assertNotEqual(a, b)
        self.assertTrue(b.endswith("x (2).csv"))

    def test_a_name_cannot_climb_out_of_the_folder(self):
        res = self.sf.save_base64("..\\..\\evil.csv", base64.b64encode(b"1").decode())
        self.assertEqual(os.path.dirname(res["path"]), self.reports)
        res = self.sf.save_base64("../../x:y?.csv", base64.b64encode(b"1").decode())
        self.assertEqual(os.path.dirname(res["path"]), self.reports)

    def test_empty_is_refused(self):
        self.assertFalse(self.sf.save_base64("x.csv", "")["ok"])

    def test_open_and_show_only_files_it_may(self):
        saved = self.sf.save_base64("ok.pdf", base64.b64encode(b"%PDF").decode())["path"]
        with mock.patch("os.startfile", create=True) as start, \
             mock.patch("subprocess.Popen") as popen:
            self.assertTrue(self.sf.open_file(saved)["ok"])
            self.assertTrue(self.sf.show_in_folder(saved)["ok"])
            start.assert_called_once()
            popen.assert_called_once()
            outside = os.path.join(self.tmp, "elsewhere.pdf")
            with open(outside, "wb") as fh:
                fh.write(b"%PDF")
            self.assertFalse(self.sf.open_file(outside)["ok"])
            exe = os.path.join(self.reports, "run.exe")
            with open(exe, "wb") as fh:
                fh.write(b"MZ")
            self.assertFalse(self.sf.open_file(exe)["ok"])
            self.assertFalse(self.sf.open_file("relative.pdf")["ok"])
            self.assertEqual(start.call_count, 1)

    def test_a_remembered_file_elsewhere_may_be_opened(self):
        other = os.path.join(self.tmp, "bills", "Bill_1.pdf")
        os.makedirs(os.path.dirname(other))
        with open(other, "wb") as fh:
            fh.write(b"%PDF")
        self.assertFalse(self.sf._allowed(other))
        self.sf.remember(other)
        self.assertTrue(self.sf._allowed(other))


class HomeExport(_Reports):
    def test_every_kind_and_format_writes_a_file(self):
        from core.desktop_settings_service import system_action

        db = os.path.join(self.tmp, "store.db")
        shutil.copy(os.path.join(_REPO, "tests", "fixtures", "desktop_made_store.db"), db)
        conn = sqlite3.connect(db)
        self.addCleanup(conn.close)
        with mock.patch("core.sync_prefs.is_online_mode", return_value=False):
            for kind, fmt in (("sales", "csv"), ("purchases", "xlsx"), ("inventory", "csv"), ("all", "xlsx")):
                res = system_action(conn, {"action": "export", "kind": kind, "format": fmt, "_heavy_sync": True})
                self.assertTrue(res.get("ok"), (kind, fmt, res))
                self.assertTrue(os.path.isfile(res["path"]), res)
                self.assertEqual(os.path.dirname(res["path"]), self.reports)
        with open(system_action(conn, {"action": "export", "kind": "sales", "format": "csv",
                                       "_heavy_sync": True})["path"], encoding="utf-8-sig") as fh:
            head = fh.readline()
        self.assertIn("Bill No", head)

    def test_voice_csv_export_no_longer_says_failed(self):
        from core.export_manager import export_data_direct

        path, msg = export_data_direct(None, "T", ["a"], [[1]], fmt="csv", speak_path=False, folder=self.reports)
        self.assertTrue(path and os.path.isfile(path), msg)


class PrintAllSavePdf(_Reports):
    def test_save_pdf_is_one_file_and_nothing_printed(self):
        import pypdfium2 as pdfium

        from core import desktop_sales_service as svc
        from tests._bill_fixtures import discounted_bill_context, isolated_bill_settings

        ctx = discounted_bill_context()

        def fake_pdf(html_path, pdf_path, **kw):
            doc = pdfium.PdfDocument.new()
            doc.new_page(420, 595)
            doc.save(pdf_path)
            doc.close()
            return True

        with isolated_bill_settings(), \
             mock.patch("core.bill_output._load_sale_data", return_value=("SCB1", ctx, {})), \
             mock.patch("core.bill_output._try_pdf_via_browser", side_effect=fake_pdf), \
             mock.patch("core.bill_output._is_dot_matrix_printer_mode", return_value=False), \
             mock.patch("core.printer_manager.PrinterManager.print_pdf_silently") as printed:
            res = svc.print_all_sales(None, {"sale_ids": [1, 2, 3], "paper": "A5", "save_pdf": True})
        printed.assert_not_called()
        self.assertTrue(res["ok"], res)
        self.assertEqual(os.path.dirname(res["path"]), self.reports)
        doc = pdfium.PdfDocument(res["path"])
        self.assertEqual(len(doc), 2)    # three bills, two to an A5 sheet
        doc.close()


class ThePages(unittest.TestCase):
    def test_no_page_downloads_a_blob_any_more(self):
        for f in ("ExportDialog.tsx", "GstReportsDialog.tsx", "StartupAlertsDialog.tsx",
                  os.path.join("settings", "InteractivePanels.tsx")):
            text = src("desktop", "src", "pages", f)
            self.assertNotIn("a.download = filename", text, f)
            self.assertIn("savedFiles'", text, f)

    def test_the_saved_dialog_is_mounted_once(self):
        app = src("desktop", "src", "App.tsx")
        self.assertEqual(app.count("<SavedFileHost />"), 1)
        host = src("desktop", "src", "components", "SavedFileHost.tsx")
        for text in ("Show in folder", "Open", "openSavedFile", "showSavedFileInFolder"):
            self.assertIn(text, host)

    def test_print_all_and_the_bill_preview_offer_save_pdf(self):
        hist = src("desktop", "src", "pages", "SalesHistoryPage.tsx")
        self.assertIn("runPrintAll(true)", hist)
        self.assertIn("save_pdf: saveOnly", hist)
        self.assertIn("onSavePdf=", hist)
        self.assertIn("Save PDF", src("desktop", "src", "pages", "BillPreviewDialog.tsx"))

    def test_a_schedule_pdf_is_not_called_a_print(self):
        dlg = src("desktop", "src", "pages", "ExportDialog.tsx")
        self.assertIn("Select Report Style (PDF)", dlg)
        self.assertNotIn("Saved to:\\n${savedTo}", dlg)

    def test_the_engine_routes_exist(self):
        api = src("core", "desktop_api.py")
        for route in ("/api/files/save", "/api/files/open", "/api/files/reveal"):
            self.assertIn(route, api)
        self.assertTrue(re.search(r"API_REVISION = (\d+)", api) and
                        int(re.search(r"API_REVISION = (\d+)", api).group(1)) >= 67)


if __name__ == "__main__":
    unittest.main()
