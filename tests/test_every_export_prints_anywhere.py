"""Every export prints on the dot matrix or a normal printer, A4 vertical or horizontal (1 Oct 2026).

Only the Schedule register could print, and only on the printer type set in Settings. Now
every report (Inventory, Sales, Purchase reports and Alert & Monitoring) prints, on the printer
the shop picks: the dot matrix (A4 vertical 80 columns, horizontal 110 with the sheet fed
sideways) or the normal Windows printer (a PDF on A4 portrait / landscape). The Schedule
register keeps its own page layout and Classic / Sign style, and now prints where the shop says.

    python -m pytest tests/test_every_export_prints_anywhere.py
"""
import os
import unittest
from unittest import mock

import core.dot_matrix_print as dmp
from core import desktop_export_service as ex

COLS = ["Medicine Name", "Batch", "Expiry", "Qty", "Amount"]
ROWS = [["DOLO 650", "B12", "03/27", 10, 300.0], ["SHELCAL 500", "S9", "05/27", 4, 240.0]]


class TheDotMatrixPage(unittest.TestCase):
    def width(self, landscape):
        text = dmp.format_report_text("Stock Statement", COLS, ROWS, {}, paper="A4", landscape=landscape)
        return max(len(line) for line in dmp.printed_text(text).splitlines())

    def test_vertical_is_80_columns_and_horizontal_110(self):
        self.assertLessEqual(self.width(False), 80)
        self.assertGreater(self.width(True), 80)
        self.assertLessEqual(self.width(True), 110)


class AReportPrints(unittest.TestCase):
    def test_on_the_dot_matrix_horizontal(self):
        with mock.patch("core.dot_matrix_print.print_dot_matrix_report") as dm:
            res = ex.print_report("Stock Statement", COLS, ROWS, "dot_matrix", "landscape")
        self.assertTrue(res["ok"], res)
        self.assertTrue(dm.call_args[1]["landscape"])

    @unittest.skipUnless(os.name == "nt", "Windows printing")
    def test_on_a_normal_printer_vertical(self):
        with mock.patch("os.startfile") as start, \
                mock.patch("core.export_manager._save_pdf_to_path", return_value="x.pdf") as pdf:
            res = ex.print_report("Stock Statement", COLS, ROWS, "printer", "portrait")
        self.assertTrue(res["ok"], res)
        self.assertEqual(pdf.call_args[1]["orientation"], "portrait")
        self.assertEqual(start.call_args[0], ("x.pdf", "print"))

    def test_export_to_file_prints_any_report(self):
        data = {"columns": COLS, "rows": ROWS, "title": "Near Expiry Report", "filename": "near"}
        with mock.patch.object(ex, "run_export", return_value=data), \
                mock.patch("core.dot_matrix_print.print_dot_matrix_report") as dm:
            res = ex.export_to_file(None, "inventory", "near_expiry", "pdf", do_print=True,
                                    print_to="dot_matrix", page_layout="landscape")
        self.assertTrue(res["ok"], res)
        self.assertEqual(dm.call_args[0][0], "Near Expiry Report")

    def test_a_printer_error_is_said(self):
        with mock.patch("core.dot_matrix_print.print_dot_matrix_report", side_effect=RuntimeError("offline")):
            res = ex.print_report("Stock", COLS, ROWS, "dot_matrix")
        self.assertFalse(res["ok"])
        self.assertIn("offline", res["error"])


class TheScheduleRegisterGoesWhereTheShopSays(unittest.TestCase):
    def data(self):
        return {"columns": ["Date", "Bill No", "Patient", "Medicine", "Batch", "Expiry", "Qty"],
                "rows": [["01/09/26", "SCB2", "AMOL", "ALPRAX", "A1", "03/27", 1]],
                "filename": "schedule", "meta": {"page_layout": "portrait", "dm_style": "sign",
                                                 "schedule_label": "H1", "date_range": "Sep 2026"}}

    def test_on_the_dot_matrix_even_when_settings_say_a_normal_printer(self):
        with mock.patch("core.printer_manager.PrinterManager.is_dot_matrix_mode", return_value=False), \
                mock.patch("core.dot_matrix_print.print_schedule_report_dot_matrix") as dm, \
                mock.patch("core.document_output.save_schedule_report_document",
                           return_value=(None, __file__)):
            res = ex._schedule_styled_pdf_bytes(self.data(), do_print=True, print_to="dot_matrix")
        self.assertTrue(res["printed"], res)
        self.assertEqual(dm.call_args[1]["dm_style"], "sign")

    @unittest.skipUnless(os.name == "nt", "Windows printing")
    def test_on_a_normal_printer_even_when_settings_say_dot_matrix(self):
        with mock.patch("core.printer_manager.PrinterManager.is_dot_matrix_mode", return_value=True), \
                mock.patch("core.dot_matrix_print.print_schedule_report_dot_matrix") as dm, \
                mock.patch("core.document_output.save_schedule_report_document",
                           return_value=(__file__, __file__)), \
                mock.patch("os.startfile") as start:
            res = ex._schedule_styled_pdf_bytes(self.data(), do_print=True, print_to="printer")
        self.assertTrue(res["printed"], res)
        dm.assert_not_called()
        self.assertEqual(start.call_args[0][1], "print")


class AlertsPage(unittest.TestCase):
    def test_alerts_print_horizontal_on_the_dot_matrix(self):
        with mock.patch("core.dot_matrix_print.print_dot_matrix_reports_combined") as dm:
            res = ex.export_alert_sections({"sections": [{"title": "Low", "columns": COLS, "rows": ROWS}],
                                            "print_to": "dot_matrix", "page_layout": "landscape"})
        self.assertTrue(res["ok"], res)
        self.assertTrue(dm.call_args[1]["landscape"])


if __name__ == "__main__":
    unittest.main()
