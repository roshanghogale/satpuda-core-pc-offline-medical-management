"""A report that stops at its last row makes the shop add the column by hand.

Stock Statement, Customer Due, GST Purchase -- every export report -- now ends with a
bold TOTAL line, on the dot matrix and in the saved PDF. Only columns whose name says
money or a count are added: a batch number or a phone number is never a total.

The A4 bill is in this file too, because it had the same kind of fault the other way
round -- its own frame was wider than the paper, so every bordered row wrapped.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.dot_matrix_print as dmp  # noqa: E402

HEADERS = ["Bill No", "Date", "Customer", "Phone", "Total Amount", "Amount Paid", "Due Amount"]
ROWS = [
    ["SCB1", "01/09/26", "HARSHAL", "9325485954", "567.84", "300.00", "267.84"],
    ["SCB2", "02/09/26", "NARENDRA", "9822112233", "120.00", "120.00", "0.00"],
]


class TheTotalLine(unittest.TestCase):

    def test_money_columns_are_added_up(self):
        row = dmp._report_totals_row(HEADERS, ROWS)
        self.assertEqual(row[HEADERS.index("Total Amount")], "687.84")
        self.assertEqual(row[HEADERS.index("Amount Paid")], "420.00")
        self.assertEqual(row[HEADERS.index("Due Amount")], "267.84")
        self.assertEqual(row[0], "TOTAL")

    def test_a_phone_number_is_not_a_total(self):
        row = dmp._report_totals_row(HEADERS, ROWS)
        self.assertEqual(row[HEADERS.index("Phone")], "")
        self.assertEqual(row[HEADERS.index("Date")], "")

    def test_counts_stay_whole(self):
        row = dmp._report_totals_row(["Name", "Stock", "Qty"], [["A", "120", "2"], ["B", "18", "3"]])
        self.assertEqual(row[1], "138")
        self.assertEqual(row[2], "5")

    def test_a_report_with_nothing_to_add_gets_no_total_line(self):
        self.assertEqual(dmp._report_totals_row(["Name", "Batch"], [["A", "B123"]]), [])
        text = dmp.printed_text(dmp.format_report_text("Batches", ["Name", "Batch"], [["A", "B123"]]))
        self.assertNotIn("TOTAL", text)

    def test_the_printed_report_ends_with_it(self):
        text = dmp.printed_text(dmp.format_report_text("Sales Register", HEADERS, ROWS))
        lines = [line for line in text.splitlines() if line.strip()]
        self.assertTrue(lines[-1].startswith("TOTAL"), lines[-3:])
        self.assertIn("687.84", lines[-1])
        self.assertLessEqual(max(len(line) for line in text.splitlines()), 80)

    def test_the_saved_report_ends_with_it_too(self):
        from core.export_manager import _build_html

        html = _build_html("Sales Register", HEADERS, ROWS)
        self.assertIn("<tfoot>", html)
        self.assertIn("687.84", html.split("<tfoot>", 1)[1])


class TheA4BillFitsTheA4Page(unittest.TestCase):

    def test_every_row_is_eighty_characters(self):
        from tests.test_dot_matrix_paper_geometry import _Ctx, shop_settings

        ctx = _Ctx(5)
        merged = dmp._settings_with_layout(
            shop_settings(paper_size="A4", dot_matrix_style="classic"))
        merged["paper_size"] = "A4"
        text = dmp.printed_text(dmp.format_bill_text(ctx, merged))
        self.assertEqual({len(line) for line in text.splitlines()}, {80})

    def test_the_frame_adds_up(self):
        dm = dmp._layout_for_paper("A4")
        self.assertEqual(sum(dm.col_widths) + len(dm.col_widths) + 1, dm.line_width)
        self.assertEqual(dm.hdr_left_w + dm.hdr_center_w + dm.hdr_right_w, dm.line_width - 2)
        self.assertEqual(dm.foot_left_w + dm.foot_right_w, dm.line_width - 3)


if __name__ == "__main__":
    unittest.main()
