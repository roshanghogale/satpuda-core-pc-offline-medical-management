"""Alert & Monitoring exports and prints every list (1 Oct 2026).

It could only save the list on screen as CSV. Now: this list or all five, as Excel, PDF or
CSV, or on paper -- dot matrix A4 / A5 through the RAW report printer, or the normal Windows
printer through a PDF.

    python -m pytest tests/test_alerts_export_and_print.py
"""
import base64
import io
import os
import unittest
from unittest import mock

from core.desktop_export_service import export_alert_sections

LOW = {"title": "Low Stock", "columns": ["Medicine Name", "Current Stock", "Unit", "Supplier"],
       "rows": [["CIPCAL 500", 2, "10", "SHREE BALAJI PHARMA"], ["DOLO 650", 3, "15", ""]]}
EXP = {"title": "Expired — Mar 2026", "columns": ["Medicine Name", "Batch Number", "Expiry Date", "Quantity Expired"],
       "rows": [["ZANDU COUGH", "Z1", "01-03-2026", 4]]}


class AFile(unittest.TestCase):
    def test_excel_has_one_sheet_per_list(self):
        res = export_alert_sections({"sections": [LOW, EXP], "format": "xlsx"})
        self.assertTrue(res["ok"], res)
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(base64.b64decode(res["content_base64"])))
        self.assertEqual(len(wb.sheetnames), 2)
        self.assertEqual(wb.worksheets[0]["A2"].value, "CIPCAL 500")
        self.assertEqual(res["row_count"], 3)

    def test_csv_names_each_list(self):
        res = export_alert_sections({"sections": [LOW, EXP], "format": "csv"})
        text = base64.b64decode(res["content_base64"]).decode("utf-8-sig")
        self.assertIn("Low Stock", text)
        self.assertIn("ZANDU COUGH", text)

    def test_pdf(self):
        res = export_alert_sections({"sections": [LOW], "format": "pdf"})
        self.assertTrue(res["ok"], res)
        self.assertIn(res["format"], ("pdf", "html"))
        self.assertGreater(len(base64.b64decode(res["content_base64"])), 200)

    def test_nothing_to_export_says_so(self):
        res = export_alert_sections({"sections": [dict(LOW, rows=[])], "format": "csv"})
        self.assertFalse(res["ok"])


class OnPaper(unittest.TestCase):
    def test_dot_matrix_a4_gets_every_list(self):
        with mock.patch("core.dot_matrix_print.print_dot_matrix_reports_combined") as dm:
            res = export_alert_sections({"sections": [LOW, EXP], "print_to": "dot_matrix", "paper": "A4"})
        self.assertTrue(res["ok"], res)
        sections = dm.call_args[0][0]
        self.assertEqual([s[0] for s in sections], ["Low Stock", "Expired — Mar 2026"])
        self.assertEqual(dm.call_args[1]["paper"], "A4")

    def test_dot_matrix_a5(self):
        with mock.patch("core.dot_matrix_print.print_dot_matrix_reports_combined") as dm:
            export_alert_sections({"sections": [LOW], "print_to": "dot_matrix", "paper": "A5"})
        self.assertEqual(dm.call_args[1]["paper"], "A5")

    def test_the_dot_matrix_text_carries_the_rows(self):
        from core.dot_matrix_print import format_reports_combined
        text = format_reports_combined([(LOW["title"], LOW["columns"], LOW["rows"])], {}, paper="A4")
        self.assertIn("CIPCAL 500", text)

    @unittest.skipUnless(os.name == "nt", "Windows printing")
    def test_normal_printer_prints_the_pdf(self):
        with mock.patch("os.startfile") as start:
            res = export_alert_sections({"sections": [LOW], "print_to": "printer"})
        self.assertTrue(res["ok"], res)
        self.assertEqual(start.call_args[0][1], "print")

    def test_a_printer_error_is_told_not_hidden(self):
        with mock.patch("core.dot_matrix_print.print_dot_matrix_reports_combined",
                        side_effect=RuntimeError("printer offline")):
            res = export_alert_sections({"sections": [LOW], "print_to": "dot_matrix"})
        self.assertFalse(res["ok"])
        self.assertIn("printer offline", res["error"])


if __name__ == "__main__":
    unittest.main()
