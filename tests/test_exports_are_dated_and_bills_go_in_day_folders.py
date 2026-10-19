"""Files the app saves: dated export names, a folder for each kind, no HTML left behind
(owner, 19 Oct 2026).

1. "Exports should have the date in the file name so an old file can't be replaced":
   Sales_Register.pdf is saved as Sales_Register_2026-10-19_14-32.pdf.
2. "For bills and exports there should be separate folders inside the selected folder; in the
   bills folder a date-wise folder, so today's bills are in today's folder":
   <save folder>\\Bills\\<bill date>, Purchase Bills, Returns, Exports. The BILL's date: a
   reprint of an old bill goes in that bill's day folder, not today's.
3. "After printing the HTML files are not needed, only PDF": the HTML a PDF is made from is
   deleted once the PDF exists, in the shop's folder and in %TEMP%; it stays only when no PDF
   could be made. Nothing here starts a browser or prints.
"""
from __future__ import annotations

import base64
import datetime as dt
import os
import shutil
import tempfile
import unittest
from unittest import mock

from tests._bill_fixtures import discounted_bill_context, isolated_bill_settings

WHEN = dt.datetime(2026, 10, 19, 14, 32)


def fake_pdf(html_path, pdf_path, **_kw):
    """The browser step: a PDF next to the HTML, which must exist when it is called."""
    assert os.path.isfile(html_path), "the PDF is made from the HTML file"
    with open(pdf_path, "wb") as fh:
        fh.write(b"%PDF-1.4 " + b"x" * 600)
    return True


def no_pdf(_html_path, _pdf_path, **_kw):
    return False


def files_under(root: str, ext: str) -> list[str]:
    out = []
    for folder, _dirs, names in os.walk(root):
        out += [os.path.join(folder, n) for n in names if n.lower().endswith(ext)]
    return out


class DatedNames(unittest.TestCase):
    def test_the_save_time_goes_before_the_extension(self):
        from core.saved_files import dated_name

        self.assertEqual(dated_name("Sales_Register.pdf", WHEN), "Sales_Register_2026-10-19_14-32.pdf")
        self.assertEqual(dated_name("stock statement.xlsx", WHEN), "stock statement_2026-10-19_14-32.xlsx")
        self.assertEqual(dated_name("report", WHEN), "report_2026-10-19_14-32")

    def test_a_date_range_is_kept_and_the_save_time_still_added(self):
        from core.saved_files import dated_name

        self.assertEqual(
            dated_name("GST_27AB_2026-10-01_2026-10-31.xlsx", WHEN),
            "GST_27AB_2026-10-01_2026-10-31_2026-10-19_14-32.xlsx",
        )

    def test_a_name_is_dated_once(self):
        from core.saved_files import dated_name

        once = dated_name("x.csv", WHEN)
        self.assertEqual(dated_name(once, dt.datetime(2026, 10, 20, 9, 0)), once)
        self.assertEqual(dated_name("x_2026-10-19_14-32 (2).csv", WHEN), "x_2026-10-19_14-32 (2).csv")

    def test_a_clash_in_the_same_minute_gets_a_number(self):
        from core.saved_files import dated_path

        with tempfile.TemporaryDirectory() as tmp:
            first = dated_path(os.path.join(tmp, "Bills_A5_3.pdf"), WHEN)
            self.assertEqual(os.path.basename(first), "Bills_A5_3_2026-10-19_14-32.pdf")
            open(first, "wb").close()
            second = dated_path(os.path.join(tmp, "Bills_A5_3.pdf"), WHEN)
            self.assertEqual(os.path.basename(second), "Bills_A5_3_2026-10-19_14-32 (2).pdf")

    def test_every_export_saved_by_the_engine_is_dated(self):
        import core.saved_files as sf

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(sf, "datetime") as clock:
            clock.now.return_value = WHEN
            res = sf.save_base64("sales_register.csv", base64.b64encode(b"a,b").decode())
            self.assertTrue(res["ok"], res)
            self.assertEqual(res["file_name"], "sales_register_2026-10-19_14-32.csv")
            path = sf.save_bytes("doctor margin.pdf", b"%PDF", folder=tmp)
            self.assertEqual(os.path.basename(path), "doctor margin_2026-10-19_14-32.pdf")
            os.remove(res["path"])

    def test_the_voice_and_home_exports_are_dated_too(self):
        from core.export_manager import _voice_export_path

        with tempfile.TemporaryDirectory() as tmp:
            path = _voice_export_path("sales_export", "xlsx", tmp)
        self.assertRegex(os.path.basename(path), r"^sales_export_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}\.xlsx$")


class BillDayFolderName(unittest.TestCase):
    def test_every_way_a_bill_date_is_written(self):
        from core.bill_save_prefs import bill_day_folder_name as day

        self.assertEqual(day("2026-09-13"), "2026-09-13")
        self.assertEqual(day("2026-09-13 18:40:02"), "2026-09-13")
        self.assertEqual(day("2026-09-13T18:40:02Z"), "2026-09-13")
        self.assertEqual(day("13/09/26"), "2026-09-13")       # as the printed bill shows it
        self.assertEqual(day("13-09-2026"), "2026-09-13")
        self.assertEqual(day(dt.date(2026, 9, 13)), "2026-09-13")
        self.assertEqual(day(dt.datetime(2026, 9, 13, 8, 0)), "2026-09-13")

    def test_no_date_or_nonsense_is_today(self):
        from core.bill_save_prefs import bill_day_folder_name as day

        today = dt.date.today().isoformat()
        for raw in (None, "", "PENDING", "2026-13-45", "31/02/26"):
            self.assertEqual(day(raw), today, raw)


class _SaveFolder(unittest.TestCase):
    """The shop's chosen save folder is a temporary one; the engine's own temp dir too."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.shop = os.path.join(self.root, "Shop Files")
        os.makedirs(self.shop)
        self.temp = os.path.join(self.root, "temp")
        os.makedirs(self.temp)
        for p in (
            mock.patch("core.bill_save_prefs.resolve_sales_bill_save_dir", return_value=self.shop),
            mock.patch("tempfile.tempdir", self.temp),
            mock.patch("core.bill_output._is_dot_matrix_printer_mode", return_value=False),
        ):
            p.start()
            self.addCleanup(p.stop)

    def at(self, *parts) -> str:
        return os.path.join(self.shop, *parts)


class TheFolderLayout(_SaveFolder):
    def test_each_kind_has_its_folder_inside_the_chosen_one(self):
        from core import bill_save_prefs as bsp
        from core.saved_files import reports_dir

        self.assertEqual(bsp.resolve_bill_day_save_dir("2026-09-13"), self.at("Bills", "2026-09-13"))
        self.assertEqual(bsp.resolve_returns_save_dir(), self.at("Returns"))
        self.assertEqual(bsp.resolve_purchase_bills_save_dir(), self.at("Purchase Bills"))
        self.assertEqual(bsp.resolve_exports_save_dir(), self.at("Exports"))
        self.assertEqual(bsp.resolve_sales_report_save_dir(), self.at("Exports"))
        self.assertEqual(reports_dir(), self.at("Exports"))
        for sub in (("Bills", "2026-09-13"), ("Returns",), ("Purchase Bills",), ("Exports",)):
            self.assertTrue(os.path.isdir(self.at(*sub)), sub)

    def test_open_and_show_accept_files_in_the_new_folders_and_the_old_ones(self):
        import core.saved_files as sf

        for parts in (("Bills", "2026-09-13", "Bill_1.pdf"), ("Exports", "r_2026-10-19_14-32.csv"),
                      ("Returns", "SR1.pdf"), ("Purchase Bills", "PO1.pdf"),
                      ("reports", "old.pdf"), ("supplier_docs", "PR1.pdf"), ("Bill_9.pdf",)):
            path = self.at(*parts)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(b"%PDF")
            self.assertTrue(sf._allowed(path), parts)
        outside = os.path.join(self.root, "elsewhere.pdf")
        with open(outside, "wb") as fh:
            fh.write(b"%PDF")
        self.assertFalse(sf._allowed(outside))


class BillPdfs(_SaveFolder):
    def _slot_settings(self):
        from core.bill_config import get_print_slot_settings, load_bill_print_settings

        return get_print_slot_settings(load_bill_print_settings(), 1)

    def test_a_bill_goes_in_its_own_days_folder_and_no_html_stays(self):
        from core import bill_output

        ctx = discounted_bill_context()          # a bill of 13 Sep 2026, saved "today"
        self.assertEqual(ctx.bill_date_raw, "2026-09-13")
        with isolated_bill_settings(), \
             mock.patch.object(bill_output, "_load_sale_data_for_pdf",
                               return_value=("SCB1", ctx, self._slot_settings())), \
             mock.patch.object(bill_output, "_try_pdf_via_browser", side_effect=fake_pdf):
            pages = bill_output.save_bill_pdf_pages(None, 1)
        self.assertEqual(len(pages), 1)
        html_path, pdf_path = pages[0]
        self.assertEqual(pdf_path, self.at("Bills", "2026-09-13", "Bill_SCB1.pdf"))
        self.assertTrue(os.path.isfile(pdf_path))
        self.assertEqual(html_path, "")
        self.assertEqual(files_under(self.root, ".html"), [])

    def test_save_pdf_a6_uses_the_bill_date_folder_too(self):
        from core import bill_output

        ctx = discounted_bill_context()
        with isolated_bill_settings(), \
             mock.patch.object(bill_output, "_load_sale_data_for_pdf",
                               return_value=("SCB1", ctx, self._slot_settings())), \
             mock.patch.object(bill_output, "_try_pdf_via_browser", side_effect=fake_pdf):
            html_path, pdf_path = bill_output.save_bill_pdf_a6(None, 1)
        self.assertEqual(os.path.dirname(pdf_path), self.at("Bills", "2026-09-13"))
        self.assertEqual(html_path, "")
        self.assertEqual(files_under(self.root, ".html"), [])

    def test_when_no_pdf_can_be_made_the_html_is_kept(self):
        from core import bill_output

        ctx = discounted_bill_context()
        with isolated_bill_settings(), \
             mock.patch.object(bill_output, "_load_sale_data_for_pdf",
                               return_value=("SCB1", ctx, self._slot_settings())), \
             mock.patch.object(bill_output, "_try_pdf_via_browser", side_effect=no_pdf):
            (html_path, pdf_path), = bill_output.save_bill_pdf_pages(None, 1)
        self.assertIsNone(pdf_path)
        self.assertEqual(html_path, self.at("Bills", "2026-09-13", "Bill_SCB1.html"))
        self.assertTrue(os.path.isfile(html_path), "the only copy of the bill there is")

    def test_printing_a_bill_leaves_only_the_pdf(self):
        from core import bill_output

        ctx = discounted_bill_context()
        with isolated_bill_settings(), \
             mock.patch.object(bill_output, "_load_sale_data_for_pdf",
                               return_value=("SCB1", ctx, self._slot_settings())), \
             mock.patch.object(bill_output, "_try_pdf_via_browser", side_effect=fake_pdf), \
             mock.patch("core.printer_manager.PrinterManager.print_pdf_silently") as printed, \
             mock.patch("core.printer_manager.PrinterManager.get_printer_for_slot", return_value="P"):
            html_path, pdf_path = bill_output.print_bill_silent_with_slot(None, 1, 1)
        printed.assert_called_once()
        self.assertEqual(printed.call_args[0][0], pdf_path)
        self.assertEqual(os.path.dirname(pdf_path), self.at("Bills", "2026-09-13"))
        self.assertEqual(html_path, "")
        self.assertEqual(files_under(self.root, ".html"), [])

    def test_a_wholesale_invoice_goes_in_its_bill_date_folder(self):
        from core import bill_output

        inv = {"bill_no": "W7", "bill_date": "2026-08-05"}
        with mock.patch.object(bill_output, "_wholesale_invoice_ctx", return_value=inv), \
             mock.patch("bill_templates.wholesale_invoice.render_wholesale_invoice_html",
                        return_value="<html>invoice</html>"), \
             mock.patch.object(bill_output, "_try_pdf_via_browser", side_effect=fake_pdf):
            html_path, pdf_path = bill_output.wholesale_invoice_files(None, 7, {})
        self.assertEqual(pdf_path, self.at("Bills", "2026-08-05", "Bill_W7.pdf"))
        self.assertEqual(html_path, "")
        self.assertEqual(files_under(self.root, ".html"), [])

    def test_the_saved_bill_answer_names_the_bill_day_folder(self):
        import inspect

        import core.desktop_sales_service as svc

        self.assertIn("resolve_bill_day_save_dir(bill_date)", inspect.getsource(svc))


class PrintAllFiles(_SaveFolder):
    def test_save_pdf_goes_in_exports_dated_and_leaves_no_temp_files(self):
        import pypdfium2 as pdfium

        from core import desktop_sales_service as svc

        ctx = discounted_bill_context()

        def real_pdf(html_path, pdf_path, **kw):
            self.assertTrue(os.path.isfile(html_path))
            doc = pdfium.PdfDocument.new()
            doc.new_page(420, 595)
            doc.save(pdf_path)
            doc.close()
            return True

        with isolated_bill_settings(), \
             mock.patch("core.bill_output._load_sale_data", return_value=("SCB1", ctx, {})), \
             mock.patch("core.bill_output._try_pdf_via_browser", side_effect=real_pdf), \
             mock.patch("core.printer_manager.PrinterManager.print_pdf_silently") as printed:
            res = svc.print_all_sales(None, {"sale_ids": [1, 2, 3], "paper": "A5", "save_pdf": True})
        printed.assert_not_called()
        self.assertTrue(res["ok"], res)
        self.assertEqual(os.path.dirname(res["path"]), self.at("Exports"))
        self.assertRegex(res["file_name"], r"^Bills_A5_3_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}\.pdf$")
        self.assertEqual(os.listdir(self.temp), [], "no temporary HTML or PDF stays")
        self.assertEqual(files_under(self.root, ".html"), [])

    def test_a_batch_print_removes_its_html_at_once_and_its_pdf_later(self):
        from core import history_print

        ctx = discounted_bill_context()
        later = []
        with isolated_bill_settings(), \
             mock.patch.object(history_print, "_dot_matrix_mode", return_value=False), \
             mock.patch("core.bill_output._load_sale_data", return_value=("SCB1", ctx, {})), \
             mock.patch("core.bill_output._try_pdf_via_browser", side_effect=fake_pdf), \
             mock.patch.object(history_print, "_remove_later", side_effect=later.extend), \
             mock.patch.object(history_print.time, "sleep"), \
             mock.patch("core.printer_manager.PrinterManager.print_pdf_silently") as printed, \
             mock.patch("core.printer_manager.PrinterManager.get_printer_for_slot", return_value="P"):
            pages, failures = history_print.print_bills_batch(None, [1, 2], paper="A5")
        self.assertEqual((pages, failures), (1, []))
        printed.assert_called_once()
        self.assertEqual(files_under(self.temp, ".html"), [])
        pdfs = files_under(self.temp, ".pdf")
        self.assertEqual(len(pdfs), 1)
        self.assertIn(pdfs[0], later, "the printed PDF is handed over to be removed")


class ReportsAndSlips(_SaveFolder):
    def test_save_html_as_pdf_keeps_only_the_pdf(self):
        from core.document_output import save_html_as_pdf

        with mock.patch("core.bill_output._try_pdf_via_browser", side_effect=fake_pdf):
            pdf_path, html_path = save_html_as_pdf("<html>x</html>", os.path.join(self.root, "r.pdf"))
        self.assertTrue(os.path.isfile(pdf_path))
        self.assertEqual(html_path, "")
        self.assertFalse(os.path.exists(os.path.join(self.root, "r.html")))

        with mock.patch("core.bill_output._try_pdf_via_browser", side_effect=no_pdf):
            pdf_path, html_path = save_html_as_pdf("<html>x</html>", os.path.join(self.root, "s.pdf"))
        self.assertIsNone(pdf_path)
        self.assertTrue(os.path.isfile(html_path), "no PDF: the HTML is the only copy")

    def test_a_schedule_report_is_dated_in_exports(self):
        from core.document_output import save_schedule_report_document

        with mock.patch("core.bill_output._try_pdf_via_browser", side_effect=fake_pdf):
            a, _ = save_schedule_report_document("<html>x</html>", "schedule_report_H1_P")
            b, _ = save_schedule_report_document("<html>x</html>", "schedule_report_H1_P")
        self.assertEqual(os.path.dirname(a), self.at("Exports"))
        self.assertRegex(os.path.basename(a), r"^schedule_report_H1_P_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}( \(2\))?\.pdf$")
        self.assertNotEqual(a, b, "the second save does not replace the first")
        self.assertEqual(files_under(self.root, ".html"), [])

    def test_return_slips_go_in_returns_and_supplier_documents_in_purchase_bills(self):
        from core.document_output import save_customer_document, save_supplier_document

        with mock.patch("core.bill_output._try_pdf_via_browser", side_effect=fake_pdf):
            sr, sr_html = save_customer_document("<html>sr</html>", "SR12")
            pr, pr_html = save_supplier_document("<html>pr</html>", "PR4")
            po, po_html = save_supplier_document("<html>po</html>", "Reorder 3", kind="reorder")
        self.assertEqual(sr, self.at("Returns", "SR12.pdf"))
        self.assertEqual(pr, self.at("Returns", "PR4.pdf"))
        self.assertEqual(po, self.at("Purchase Bills", "Reorder_3.pdf"))
        self.assertEqual((sr_html, pr_html, po_html), ("", "", ""))
        self.assertEqual(files_under(self.root, ".html"), [])

    def test_a_pdf_export_leaves_nothing_in_temp(self):
        from core.desktop_export_service import export_alert_sections

        with mock.patch("core.bill_output._try_pdf_via_browser", side_effect=fake_pdf):
            res = export_alert_sections({
                "title": "Alerts", "format": "pdf",
                "sections": [{"title": "Low stock", "columns": ["Name"], "rows": [["ORS"]]}],
            })
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(os.listdir(self.temp), [])


if __name__ == "__main__":
    unittest.main()
