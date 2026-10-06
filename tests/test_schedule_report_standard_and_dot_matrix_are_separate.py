"""Schedule Report: a real A4 report for a laser printer, the compact one for the dot matrix.

On a laser printer the Schedule Report came out as the dot matrix layout -- Courier 8 pt, a
narrow table in the middle of an empty page -- and its fixed column widths added up to more
than the page, so Bill No was squeezed until it printed one character per line.

Print / PDF now ask for the style first. Standard / Laser is its own template
(core.schedule_report_standard) and never touches the dot matrix: no Courier, no reshape, no
RAW printing. Dot Matrix keeps the compact template and the RAW print exactly as before.
"""
from __future__ import annotations

import re
import unittest
from unittest import mock

from core import desktop_export_service as svc
from core import schedule_report_standard as std
from core.document_output import build_schedule_report_html

COLS = ["Date", "Bill No", "Customer", "Doctor", "Medicine", "Batch", "Content/Drug",
        "Schedule", "Expiry", "Qty", "Rate", "Amount"]
ROW = ["2026-09-28", "SCB2961", "POOJA MORE", "DR.AJIT PATIL", "NIFEN 200 PLUS TABLET",
       "NN2601021", "Ofloxacin (200mg) + Cefixime (200mg)", "H1", "01/12/27", 4, "13.78", "55.12"]


def data(rows=None, cols=None):
    return {
        "columns": cols or COLS,
        "rows": rows or [ROW, ROW],
        "filename": "schedule_report_H1",
        "title": "H1 Schedule Report",
        "meta": {"schedule_label": "H1", "date_range": "2026-09-01 → 2026-10-06",
                 "page_layout": "landscape", "dm_style": "classic", "dm_borders": True},
    }


class StandardLayout(unittest.TestCase):
    def html(self, rows=None, cols=None):
        d = data(rows, cols)
        return std.build_html(headers=d["columns"], rows=d["rows"], title="H1 Schedule Report",
                              schedule_label="H1", date_range="2026-09-01 to 2026-10-06",
                              store={"name": "TEST MEDICAL", "dl_number": "20-MH-1"})

    def widths(self, html):
        return [float(w) for w in re.findall(r'<col style="width:([0-9.]+)%">', html)]

    def test_every_column_has_room_and_the_table_fills_the_page(self):
        html, _ = self.html()
        w = self.widths(html)
        self.assertEqual(len(w), len(COLS) - 2 + 2)        # - Rate, Amount; + Sr, Sign
        self.assertAlmostEqual(sum(w), 100.0, delta=0.1)
        # Bill No (and every short column) at least as wide as "SCB2961" at the font used.
        cols = std.plan_columns(COLS, [[str(v) for v in ROW]])
        orient, pt, mm = std.fit(cols, [[str(v) for v in ROW]])
        bill = COLS.index("Bill No") + 1
        self.assertGreaterEqual(mm[bill], std._mm(std.em_width("SCB2961"), pt))
        self.assertIn(orient, ("portrait", "landscape"))

    def test_a_readable_professional_page(self):
        html, _ = self.html()
        self.assertNotIn("Courier", html)
        self.assertIn("Arial", html)
        size = float(re.search(r"body \{ font-family:[^}]*font-size: ([0-9.]+)pt", html).group(1))
        self.assertGreaterEqual(size, 8.5)
        self.assertIn("display: table-header-group", html)     # header on every page
        self.assertIn("break-inside: avoid", html)              # a row is never split
        self.assertIn("margin: 0 auto", html)                   # centred
        self.assertIn("TEST MEDICAL", html)
        self.assertIn("H1 Schedule Report", html)
        self.assertIn("28/09/2026", html)                       # a date a person reads
        self.assertNotIn("<tfoot", html)                        # totals once, at the end

    def test_few_columns_print_portrait_and_many_landscape(self):
        _, few = self.html(rows=[["SCB1", "RAM", "DOLO 650", 2]],
                           cols=["Bill No", "Customer", "Medicine", "Qty"])
        _, many = self.html(rows=[ROW] * 30)
        self.assertEqual(few, "portrait")
        self.assertEqual(many, "landscape")

    def test_totals_add_up(self):
        html, _ = self.html()
        self.assertRegex(html, r'class="total".*>8</td>')
        self.assertNotIn(">110.24<", html)                  # no money on the register

    def test_rate_and_amount_give_way_to_the_pharmacist_sign(self):
        html, _ = self.html(rows=[ROW] * 40)
        head = re.search(r"<thead><tr>(.*?)</tr></thead>", html).group(1)
        names = re.findall(r">([^<]+)</th>", head)
        self.assertNotIn("Rate", names)
        self.assertNotIn("Amount", names)
        self.assertEqual(names[-1], "Pharmacist Sign")     # far right, in the repeated header
        self.assertNotIn("13.78", html)
        self.assertNotIn("55.12", html)
        # Blank on every row, for the pen.
        signs = re.findall(r'<td class="sign">([^<]*)</td>', html)
        self.assertEqual(len(signs), 40)
        self.assertTrue(all(s == "&nbsp;" for s in signs))

    def test_the_sign_column_is_wide_enough_to_sign_in(self):
        rows = [[str(v) for v in ROW[:10]] + [""]]
        heads = [c for c in COLS if c not in ("Rate", "Amount")] + ["Pharmacist Sign"]
        cols = std.plan_columns(heads, rows)
        orient, pt, mm = std.fit(cols, rows)
        self.assertGreaterEqual(mm[-1], 45.0)                # about 5 cm to sign in
        self.assertGreaterEqual(min(mm[-1], 999), std.SIGN_MIN_MM)


class TheStyleDecidesThePath(unittest.TestCase):
    def test_standard_never_touches_the_dot_matrix(self):
        with mock.patch.object(std, "save_pdf", return_value=(__file__, __file__)), \
             mock.patch("core.desktop_export_service.print_pdf_on_printer", return_value="") as laser, \
             mock.patch("core.dot_matrix_print.print_schedule_report_dot_matrix",
                        side_effect=AssertionError("dot matrix print")), \
             mock.patch("core.dot_matrix_print._reshape_schedule_for_dm_style",
                        side_effect=AssertionError("dot matrix reshape")), \
             mock.patch("core.document_output.build_schedule_report_html",
                        side_effect=AssertionError("compact template")), \
             mock.patch("core.printer_manager.PrinterManager.is_dot_matrix_mode", return_value=True):
            out = svc._schedule_styled_pdf_bytes(data(), do_print=True, print_style="standard")
        self.assertTrue(out["printed"], out)
        self.assertEqual(out["print_style"], "standard")
        laser.assert_called_once()

    def test_dot_matrix_keeps_the_compact_raw_print(self):
        with mock.patch("core.document_output.save_schedule_report_document",
                        return_value=(__file__, __file__)), \
             mock.patch("core.dot_matrix_print.print_schedule_report_dot_matrix") as raw, \
             mock.patch("core.desktop_export_service.print_pdf_on_printer",
                        side_effect=AssertionError("laser print")), \
             mock.patch.object(std, "build_html", side_effect=AssertionError("A4 template")):
            out = svc._schedule_styled_pdf_bytes(
                data(), do_print=True, print_to="dot_matrix", print_style="dot_matrix")
        self.assertTrue(out["printed"], out)
        raw.assert_called_once()

    def test_no_choice_follows_the_printer_in_settings(self):
        with mock.patch("core.printer_manager.PrinterManager.is_dot_matrix_mode", return_value=False):
            self.assertEqual(svc._schedule_print_style("", ""), "standard")
        with mock.patch("core.printer_manager.PrinterManager.is_dot_matrix_mode", return_value=True):
            self.assertEqual(svc._schedule_print_style("", ""), "dot_matrix")
        self.assertEqual(svc._schedule_print_style("standard", "dot_matrix"), "standard")


class TheCompactTemplateGivesEveryColumnAWidth(unittest.TestCase):
    def test_bill_no_and_date_are_never_squeezed_to_nothing(self):
        html = build_schedule_report_html(
            sch_label="H1", date_range="x", headers=COLS, table_rows=[ROW],
            page_layout="landscape",
        )
        head = re.search(r"<thead><tr>(.*?)</tr></thead>", html).group(1)
        for th in re.findall(r"<th[^>]*>", head):
            self.assertIn("class=", th, th)
        css = re.findall(r"\.(?:sr|col-[a-z]+)\{width:(\d+)%\}", html.split("{landscape_widths}")[0])
        self.assertTrue(css)
        # The landscape set (applied last) adds up to the page.
        land = re.findall(r"\.(sr|col-[a-z]+)\{width:(\d+)%\}", html)
        last = {}
        for name, w in land:
            last[name] = int(w)
        used = {"sr", "col-date", "col-bill", "col-cust", "col-doc", "col-med", "col-content",
                "col-batch", "col-sch", "col-exp", "col-qty", "col-amt"}
        total = sum(last[n] for n in used) + last["col-amt"]   # Rate and Amount both col-amt
        self.assertLessEqual(total, 100)


if __name__ == "__main__":
    unittest.main()
