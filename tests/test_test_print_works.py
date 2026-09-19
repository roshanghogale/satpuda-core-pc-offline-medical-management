"""Test Print (Standard printer) must reach SumatraPDF.

It used to import core.html_to_pdf, a module with no source left (only a stale
.pyc), so the button failed before printing anything — and Test Print is how
the owner checks that bills now print with black ink only.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import bill_output  # noqa: E402
from core.printer_manager import PrinterManager  # noqa: E402


class TestPrintReachesSumatra(unittest.TestCase):
    def _run(self, black_only: bool):
        seen = {}

        def fake_pdf(html_path, pdf_path, **_kw):
            seen['html'] = Path(html_path).read_text(encoding='utf-8')
            Path(pdf_path).write_bytes(b'%PDF-1.4 test')
            return True

        with mock.patch.object(PrinterManager, 'is_windows', return_value=True), \
                mock.patch.object(PrinterManager, '_resolve_printer', return_value='HP Smart Tank 520_540 series'), \
                mock.patch.object(PrinterManager, 'is_dot_matrix_mode', return_value=False), \
                mock.patch.object(PrinterManager, 'is_black_only_print', return_value=black_only), \
                mock.patch.object(bill_output, '_try_pdf_via_browser', side_effect=fake_pdf) as pdf, \
                mock.patch.object(PrinterManager, 'print_pdf_silently') as silent:
            PrinterManager.test_print('HP Smart Tank 520_540 series')
        return seen, pdf, silent

    def test_standard_test_print_sends_the_page_to_the_printer(self):
        seen, pdf, silent = self._run(black_only=True)
        pdf.assert_called_once()
        silent.assert_called_once()
        args, kwargs = silent.call_args
        self.assertTrue(args[0].endswith('satpuda_printer_test.pdf'))
        self.assertEqual(args[1], 'HP Smart Tank 520_540 series')
        self.assertEqual(kwargs.get('copies'), 1)
        self.assertIn('HP Smart Tank 520_540 series', seen['html'])
        self.assertIn('Black only is ON', seen['html'])
        self.assertFalse(os.path.exists(args[0]), 'temporary test PDF is cleaned up')

    def test_page_says_when_black_only_is_off(self):
        seen, _pdf, _silent = self._run(black_only=False)
        self.assertIn('Black only is OFF', seen['html'])

    def test_no_code_imports_the_missing_module(self):
        for path in list((ROOT / 'core').rglob('*.py')) + list((ROOT / 'ui').rglob('*.py')):
            if path.name.startswith('._'):
                continue
            src = path.read_text(encoding='utf-8-sig', errors='ignore')
            self.assertNotIn('core.html_to_pdf', src, f'{path} imports a module that has no source')

    def test_no_pdf_means_a_clear_error_not_a_silent_pass(self):
        from core.printer_manager import PrintFailedError
        with mock.patch.object(PrinterManager, 'is_windows', return_value=True), \
                mock.patch.object(PrinterManager, '_resolve_printer', return_value='P'), \
                mock.patch.object(PrinterManager, 'is_dot_matrix_mode', return_value=False), \
                mock.patch.object(PrinterManager, 'is_black_only_print', return_value=True), \
                mock.patch.object(bill_output, '_try_pdf_via_browser', return_value=False), \
                mock.patch.object(PrinterManager, 'print_pdf_silently') as silent:
            with self.assertRaises(PrintFailedError):
                PrinterManager.test_print('P')
        silent.assert_not_called()


if __name__ == '__main__':
    unittest.main()
