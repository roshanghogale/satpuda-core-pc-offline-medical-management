"""A plain black bill must not empty the colour cartridges.

The owner asked: when we print from Tauri, can the printer use every colour
except black? It can. Every silent bill print goes

    bill HTML -> headless Edge/Chrome --print-to-pdf -> SumatraPDF -print-to

and Sumatra never said anything about colour, so the Windows driver default
applied -- on a colour inkjet that is "Color", and in colour mode the driver is
free to lay pure black (and every anti-aliased grey edge, and the logo) down as
a mix of cyan, magenta and yellow. The bill's own HTML was already black on
white; the colour came from the job, not the page.

The fix asks Sumatra for grayscale ('-print-settings monochrome') on every
silent print while "Print in black only" is on, which it is by default and for
installs saved before the setting existed. Off, the command is exactly what it
was. A failed (or switched-off) silent print falls back to the Windows print
dialog; that dialog now opens on a grayscale DEVMODE and draws grayscale pages.
Nothing here prints: subprocess, the printer checks and the Win32 DLLs are
mocked, and the printer config lives in a temp folder.
"""
import ctypes
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import printer_manager as pm  # noqa: E402
from core.printer_manager import DEFAULT_PRINTER_SETTINGS, PrinterManager, merge_print_settings  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUMATRA = r"C:\Satpuda\tools\SumatraPDF64.exe"
PRINTER = "HP Smart Tank 520_540 series"


def src(*parts) -> str:
    with open(os.path.join(_REPO, *parts), encoding="utf-8-sig") as fh:
        return fh.read()


class _SumatraRun(unittest.TestCase):
    """Drive print_pdf_silently as on a Windows PC, capturing each Sumatra command."""

    def setUp(self):
        fd, self.pdf = tempfile.mkstemp(suffix=".pdf", prefix="black_only_")
        with os.fdopen(fd, "wb") as fh:
            fh.write(b"%PDF-1.4\n" + b"0" * 400)
        self.addCleanup(os.remove, self.pdf)
        self.pdf = os.path.abspath(self.pdf)

    def run_print(self, cfg, *, default_printer="Some Other Printer", **kwargs):
        runs = []

        def fake_run(cmd, **_kw):
            runs.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch.object(PrinterManager, "is_windows", return_value=True), \
                mock.patch.object(PrinterManager, "is_spooler_running", return_value=True), \
                mock.patch.object(PrinterManager, "_resolve_printer", side_effect=lambda n: n or PRINTER), \
                mock.patch.object(PrinterManager, "_assert_printer_ready", return_value=None), \
                mock.patch.object(PrinterManager, "get_default_printer", return_value=default_printer), \
                mock.patch.object(PrinterManager, "find_sumatra_path", return_value=SUMATRA), \
                mock.patch.object(PrinterManager, "load_settings", return_value=dict(cfg)), \
                mock.patch("core.print_log.print_log"), \
                mock.patch.object(pm.time, "sleep"), \
                mock.patch.object(pm.subprocess, "run", side_effect=fake_run):
            PrinterManager.print_pdf_silently(self.pdf, PRINTER, **kwargs)
        return runs


class BlackOnlyIsTheDefault(unittest.TestCase):
    def test_the_shipped_default_is_on(self):
        self.assertIs(DEFAULT_PRINTER_SETTINGS["print_black_only"], True)

    def test_an_install_saved_before_the_setting_is_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "printer_settings.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"selected_printer": PRINTER, "silent_print_enabled": True}, fh)
            with mock.patch.object(pm, "_config_path", return_value=path):
                self.assertTrue(PrinterManager.load_settings()["print_black_only"])
                self.assertTrue(PrinterManager.is_black_only_print())

    def test_no_config_file_at_all_is_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "printer_settings.json")
            with mock.patch.object(pm, "_config_path", return_value=path):
                self.assertTrue(PrinterManager.is_black_only_print())

    def test_a_shop_that_turned_it_off_stays_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "printer_settings.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"print_black_only": False}, fh)
            with mock.patch.object(pm, "_config_path", return_value=path):
                self.assertFalse(PrinterManager.is_black_only_print())


class SumatraAsksForGrayscale(_SumatraRun):
    def test_named_printer_gets_monochrome(self):
        (cmd,) = self.run_print(DEFAULT_PRINTER_SETTINGS)
        self.assertEqual(
            cmd,
            [SUMATRA, "-print-to", PRINTER, "-print-settings", "monochrome",
             "-silent", "-exit-when-done", self.pdf],
        )

    def test_windows_default_printer_gets_monochrome(self):
        (cmd,) = self.run_print(DEFAULT_PRINTER_SETTINGS, default_printer=PRINTER)
        self.assertEqual(
            cmd,
            [SUMATRA, "-print-to-default", "-print-settings", "monochrome",
             "-silent", "-exit-when-done", self.pdf],
        )

    def test_every_copy_is_monochrome(self):
        runs = self.run_print(DEFAULT_PRINTER_SETTINGS, copies=2)
        self.assertEqual(len(runs), 2)
        for cmd in runs:
            i = cmd.index("-print-settings")
            self.assertEqual(cmd[i + 1], "monochrome")
            self.assertEqual(cmd[-1], self.pdf)

    def test_merged_with_paper_and_fit_not_replacing_them(self):
        runs = self.run_print(DEFAULT_PRINTER_SETTINGS, copies=2, print_settings="paper=A5,fit")
        self.assertEqual(len(runs), 2)
        for cmd in runs:
            self.assertEqual(cmd.count("-print-settings"), 1)
            i = cmd.index("-print-settings")
            self.assertEqual(cmd[i + 1], "paper=A5,fit,monochrome")

    def test_test_page_goes_the_same_way(self):
        # Printer Setup -> Test Print ends in print_pdf_silently, so the shop
        # sees on its test page exactly what its bills will get.
        body = src("core", "printer_manager.py")
        test_print = body[body.index("def test_print"):]
        self.assertIn("cls.print_pdf_silently(pdf_path, printer, copies=1)", test_print)


class OffIsExactlyToday(_SumatraRun):
    OFF = {**DEFAULT_PRINTER_SETTINGS, "print_black_only": False}

    def test_named_printer_command_unchanged(self):
        (cmd,) = self.run_print(self.OFF)
        self.assertEqual(cmd, [SUMATRA, "-print-to", PRINTER, "-silent", "-exit-when-done", self.pdf])

    def test_default_printer_command_unchanged(self):
        (cmd,) = self.run_print(self.OFF, default_printer=PRINTER)
        self.assertEqual(cmd, [SUMATRA, "-print-to-default", "-silent", "-exit-when-done", self.pdf])

    def test_given_settings_pass_through_without_monochrome(self):
        (cmd,) = self.run_print(self.OFF, print_settings="paper=A5,fit")
        i = cmd.index("-print-settings")
        self.assertEqual(cmd[i + 1], "paper=A5,fit")
        self.assertNotIn("monochrome", " ".join(cmd))


class MergingPrintSettings(unittest.TestCase):
    def test_comma_joined_trimmed_and_ordered(self):
        self.assertEqual(
            merge_print_settings(" 2x, paper=A5 ,fit", "monochrome"),
            "2x,paper=A5,fit,monochrome",
        )

    def test_no_duplicate_monochrome(self):
        self.assertEqual(merge_print_settings("fit,Monochrome", "monochrome"), "fit,Monochrome")

    def test_empty_current(self):
        self.assertEqual(merge_print_settings("", "monochrome"), "monochrome")

    def test_color_request_does_not_survive_black_only(self):
        on = dict(DEFAULT_PRINTER_SETTINGS)
        self.assertEqual(PrinterManager.sumatra_print_settings(on, "color,fit"), "fit,monochrome")

    def test_off_leaves_value_alone(self):
        off = {**DEFAULT_PRINTER_SETTINGS, "print_black_only": False}
        self.assertEqual(PrinterManager.sumatra_print_settings(off, ""), "")
        self.assertEqual(PrinterManager.sumatra_print_settings(off, "color,fit"), "color,fit")


class TheSwitchIsOnBothSettingsScreens(unittest.TestCase):
    def test_tauri_settings_round_trip(self):
        from core import db_setup
        from core.desktop_settings_service import get_pharmacy, save_pharmacy

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        db_setup.initialise(conn)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "printer_settings.json")
            with mock.patch.object(pm, "_config_path", return_value=path):
                self.assertIs(get_pharmacy(conn)["printer"]["print_black_only"], True)
                out = save_pharmacy(conn, {"printer": {"print_black_only": False}})
                self.assertIs(out["printer"]["print_black_only"], False)
                with open(path, encoding="utf-8") as fh:
                    self.assertIs(json.load(fh)["print_black_only"], False)

    def test_tauri_panel_shows_it_default_on(self):
        panel = src("desktop", "src", "pages", "settings", "PharmacyPanels.tsx")
        self.assertIn('label="Print in black only (saves colour ink)"', panel)
        self.assertIn("printer.print_black_only ?? true", panel)
        self.assertIn("print_black_only: v", panel)

    def test_classic_tk_loads_and_saves_it(self):
        tab = src("ui", "settings", "settings_tabs", "pharmacy_tab.py")
        self.assertIn('text="Print in black only (saves colour ink)"', tab)
        self.assertIn("self._black_only_var.set(PrinterManager.is_black_only_print(cfg))", tab)
        self.assertIn("'print_black_only': bool(self._black_only_var.get())", tab)


# ── The Windows print dialog (silent print off, or silent print failed) ──────

def _load_print_dialog():
    """Import the Windows-only core/windows_print_dialog.py with its Win32 DLLs mocked."""
    dlls = {n: mock.MagicMock(name=n) for n in ("comdlg32", "kernel32", "gdi32", "winspool")}
    windll = types.SimpleNamespace(**{n: dlls[n] for n in ("comdlg32", "kernel32", "gdi32")})
    spec = importlib.util.spec_from_file_location(
        "_windows_print_dialog_under_test", os.path.join(_REPO, "core", "windows_print_dialog.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    with mock.patch.object(sys, "platform", "win32"), \
            mock.patch.object(ctypes, "windll", windll, create=True), \
            mock.patch.object(ctypes, "WinDLL", lambda _name: dlls["winspool"], create=True):
        spec.loader.exec_module(mod)
    return mod, dlls


class DialogFallbackIsBlackOnlyToo(unittest.TestCase):
    HMEM = 0x5150
    OFF = {**DEFAULT_PRINTER_SETTINGS, "print_black_only": False}

    def setUp(self):
        self.mod, self.dll = _load_print_dialog()
        # The driver's own default DEVMODE: colour, orientation set, 1 copy.
        self.devmode = ctypes.create_string_buffer(220)
        self.head = self.mod._DEVMODEW_HEAD.from_buffer(self.devmode)
        self.head.dmSize, self.head.dmFields, self.head.dmColor, self.head.dmCopies = 220, 0x1, 2, 1
        kernel32, winspool, comdlg32 = self.dll["kernel32"], self.dll["winspool"], self.dll["comdlg32"]
        kernel32.GlobalAlloc.return_value = self.HMEM
        kernel32.GlobalLock.return_value = ctypes.addressof(self.devmode)
        winspool.OpenPrinterW.return_value = 1
        self.dp_calls = []

        def document_properties(_hwnd, _hprinter, _name, out, inp, mode):
            self.dp_calls.append((mode, out, inp))
            return len(self.devmode) if mode == 0 else 1  # IDOK

        winspool.DocumentPropertiesW.side_effect = document_properties
        self.opened = []  # (hDevMode, nCopies) each time the dialog opened

        def print_dlg(ref):
            self.opened.append((ref._obj.hDevMode, ref._obj.nCopies))
            return 0  # the shop pressed Cancel

        comdlg32.PrintDlgW.side_effect = print_dlg
        comdlg32.CommDlgExtendedError.return_value = 0
        patcher = mock.patch.object(self.mod, "_default_printer_name", return_value=PRINTER)
        patcher.start()
        self.addCleanup(patcher.stop)

    def dialog(self, cfg, copies=2):
        with mock.patch.object(PrinterManager, "load_settings", return_value=dict(cfg)):
            return self.mod.show_native_print_dialog(0, copies=copies)

    def test_devmode_layout_matches_wingdi(self):
        h = self.mod._DEVMODEW_HEAD
        self.assertEqual((h.dmFields.offset, h.dmCopies.offset, h.dmColor.offset), (72, 86, 92))

    def test_dialog_opens_on_grayscale_by_default(self):
        hdc, _pd = self.dialog(DEFAULT_PRINTER_SETTINGS)
        self.assertIsNone(hdc)
        self.assertEqual(self.opened, [(self.HMEM, 2)])  # Cancel does not reopen it
        self.assertEqual(self.head.dmColor, self.mod.DMCOLOR_MONOCHROME)
        self.assertTrue(self.head.dmFields & self.mod.DM_COLOR)
        # PrintDlg takes the starting copies from a preset DEVMODE, not nCopies.
        self.assertEqual(self.head.dmCopies, 2)
        self.assertTrue(self.head.dmFields & self.mod.DM_COPIES)
        self.assertTrue(self.head.dmFields & 0x1)
        addr = ctypes.addressof(self.devmode)
        self.assertEqual(self.dp_calls[-1], (self.mod.DM_IN_BUFFER | self.mod.DM_OUT_BUFFER, addr, addr))
        self.dll["winspool"].ClosePrinter.assert_called_once()
        self.assertEqual(self.dll["kernel32"].GlobalFree.call_args[0][0].value, self.HMEM)

    def test_off_opens_exactly_as_before(self):
        self.dialog(self.OFF)
        self.assertEqual(self.opened, [(None, 2)])
        self.dll["winspool"].OpenPrinterW.assert_not_called()
        self.dll["winspool"].DocumentPropertiesW.assert_not_called()

    def test_refused_preset_still_opens_the_dialog(self):
        self.dll["comdlg32"].CommDlgExtendedError.return_value = 0x100B  # PDERR_PRINTERNOTFOUND
        self.dialog(DEFAULT_PRINTER_SETTINGS)
        self.assertEqual(self.opened, [(self.HMEM, 2), (None, 2)])
        self.assertEqual(self.dll["kernel32"].GlobalFree.call_args_list[0][0][0].value, self.HMEM)

    def test_driver_without_a_devmode_leaves_the_dialog_as_before(self):
        self.dll["winspool"].DocumentPropertiesW.side_effect = (
            lambda *a: len(self.devmode) if a[5] == 0 else -1
        )
        self.dialog(DEFAULT_PRINTER_SETTINGS)
        self.assertEqual(self.opened, [(None, 2)])
        self.assertEqual(self.dll["kernel32"].GlobalFree.call_args[0][0].value, self.HMEM)
        self.dll["winspool"].ClosePrinter.assert_called_once()

    def pages_drawn(self, cfg):
        from PIL import Image

        page = mock.MagicMock()
        page.to_image.return_value.original = Image.new("RGB", (20, 30), (200, 0, 0))
        fake_pdfplumber = types.ModuleType("pdfplumber")
        fake_pdfplumber.open = mock.MagicMock()
        fake_pdfplumber.open.return_value.__enter__.return_value.pages = [page]
        gdi32 = self.dll["gdi32"]
        gdi32.GetDeviceCaps.return_value = 1000
        for fn in ("StartDocW", "StartPage", "EndPage", "EndDoc"):
            getattr(gdi32, fn).return_value = 1
        fd, pdf = tempfile.mkstemp(suffix=".pdf", prefix="black_only_dlg_")
        os.close(fd)
        self.addCleanup(os.remove, pdf)
        with mock.patch.dict(sys.modules, {"pdfplumber": fake_pdfplumber}), \
                mock.patch.object(PrinterManager, "load_settings", return_value=dict(cfg)), \
                mock.patch.object(self.mod, "show_native_print_dialog",
                                  return_value=(0x99, self.mod.PRINTDLG())) as dlg, \
                mock.patch("PIL.ImageWin.Dib") as dib:
            self.assertTrue(self.mod.print_pdf_with_native_dialog(pdf, copies=2))
        return dlg.call_args.kwargs["black_only"], [c.args[0].mode for c in dib.call_args_list]

    def test_pages_are_drawn_grayscale(self):
        self.assertEqual(self.pages_drawn(DEFAULT_PRINTER_SETTINGS), (True, ["L"]))

    def test_off_pages_are_drawn_as_before(self):
        self.assertEqual(self.pages_drawn(self.OFF), (False, ["RGB"]))


# ── The printed documents themselves ─────────────────────────────────────────

_BLACK = {"#000", "#000000", "black"}
_PAPER = {"#fff", "#ffffff", "white", "transparent", "none"}


def _strip_screen_only_css(html: str) -> str:
    """Drop the on-screen print toolbar and @media screen blocks (never printed)."""

    def drop_blocks(text: str, head: re.Pattern) -> str:
        out, pos = [], 0
        for m in head.finditer(text):
            if m.start() < pos:
                continue
            open_at = text.index("{", m.start())
            depth, i = 0, open_at
            while i < len(text):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            out.append(text[pos:m.start()])
            pos = i + 1
        out.append(text[pos:])
        return "".join(out)

    html = drop_blocks(html, re.compile(r"@media\s+screen\s*\{"))
    html = drop_blocks(html, re.compile(r"[^{}]*\.print-(?:toolbar|btn)[^{}]*\{"))
    return re.sub(r'<div class="print-toolbar">.*?</div>', "", html, flags=re.S)


def _colour_decls(html: str):
    pat = re.compile(
        r"(?<![-\w])(color|background(?:-color)?|border(?:-(?:top|right|bottom|left))?(?:-color)?)"
        r"\s*:\s*([^;}\"]+)",
        re.I,
    )
    for m in pat.finditer(html):
        yield m.group(1).lower(), m.group(2).strip().lower().replace("!important", "").strip()


def _sample_bills():
    from bill_templates.classic import render_classic_bill_html_multi
    from core.bill_config import (
        BillContext, BillItem, apply_print_bill_layout, load_bill_print_settings, render_bill_html,
    )
    from core.document_output import _purchase_return_html_from, build_schedule_report_html

    def ctx(n):
        return BillContext(
            store_name="TEST STORE", address="MAIN ROAD", phone="99", bill_no="S-1",
            bill_date="11/09/26", bill_date_landscape="11/09/2026", cust_name="RAM", doctor_name="DR X",
            items=[BillItem(name=f"MED {i}", batch="B1", expiry="2027-01-01", qty=2, rate=10,
                            mrp=10, amount=20, gst_percent=12) for i in range(n)],
            sub_total=20 * n, taxable_amount=20 * n, grand_total=20 * n, previous_due=5, total_due=5,
        )

    base = dict(load_bill_print_settings())
    for paper in ("A4", "A5", "A6"):
        for template in ("classic", "legacy"):
            for mode in ("normal", "dot_matrix"):
                s = {**base, "paper_size": paper, "template": template, "bill_size_mode": mode}
                yield f"{paper}/{template}/{mode}", render_bill_html(ctx(3), s)
                two = apply_print_bill_layout(dict(s), print_slot_copies=2)
                two["paper_size"] = paper
                yield f"{paper}/{template}/{mode}/2 copies", render_bill_html(ctx(14), two)
        batch = apply_print_bill_layout({**base, "paper_size": paper, "bill_copies": 1}, print_slot_copies=1)
        batch["paper_size"] = paper
        yield f"{paper}/print all", render_classic_bill_html_multi([ctx(3), ctx(2)], batch)
    yield "purchase return", _purchase_return_html_from(
        None,
        {"return_no": "PR1", "return_date": "2026-09-11", "refund": 18, "reason": "EXP",
         "supplier": "SUP", "phone": "1", "address": "A", "purchase_no": "P1", "bill_no": "B"},
        [{"name": "M", "batch": "B", "expiry": "2027-01-01", "qty": 1, "rate": 20, "amount": 20, "mrp": 20}],
    )
    yield "schedule report", build_schedule_report_html(
        sch_label="H1", date_range="01/09/26 - 11/09/26",
        headers=["Date", "Bill No", "Customer", "Medicine", "Qty"],
        table_rows=[["11/09/26", "S1", "RAM", "MED", "2"]], total_qty=2, qty_idx=4,
    )


class PrintedBillsAreBlackOnWhite(unittest.TestCase):
    def test_no_grey_or_coloured_text_rules_or_fills(self):
        seen = 0
        for name, html in _sample_bills():
            printed = _strip_screen_only_css(html)
            for prop, value in _colour_decls(printed):
                seen += 1
                if prop in ("color",):
                    self.assertIn(value, _BLACK, f"{name}: text {prop}: {value}")
                elif prop.startswith("background"):
                    self.assertIn(value, _PAPER, f"{name}: {prop}: {value}")
                else:
                    colours = re.findall(r"#[0-9a-f]{3,8}\b|rgba?\([^)]*\)|\b(?:gr[ae]y|silver|red|blue|green)\b",
                                         value)
                    for c in colours:
                        self.assertIn(c, _BLACK, f"{name}: rule {prop}: {value}")
            self.assertNotRegex(printed, r"rgba?\(", f"{name}: rgb() colour on the printed page")
        self.assertGreater(seen, 100)

    def test_the_toolbar_is_the_only_colour_and_never_prints(self):
        from core.bill_page_config import print_toolbar_css

        css = print_toolbar_css()
        self.assertRegex(css, r"@media print\s*\{\s*\.print-toolbar\s*\{\s*display:\s*none !important;")


if __name__ == "__main__":
    unittest.main(verbosity=1)
