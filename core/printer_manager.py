"""
Windows silent PDF printing for pharmacy billing (Win7–Win11).

Uses pywin32 to enumerate printers and SumatraPDF for dialog-free printing.
Printer driver preferences (orientation, paper, tray, quality, etc.) are taken
from the Windows printer defaults — this module does not override them, except
colour: while 'print_black_only' is on, Sumatra asks the driver for grayscale.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from typing import Any

# ── Config path (same layout as bill_print_settings.json) ─────────────────────

PRINTER_TYPE_STANDARD = 'standard'
PRINTER_TYPE_DOT_MATRIX = 'dot_matrix'
_PRINTER_TYPES = (PRINTER_TYPE_STANDARD, PRINTER_TYPE_DOT_MATRIX)

DEFAULT_PRINTER_SETTINGS: dict[str, Any] = {
    'selected_printer': '',
    'print_slot_1_printer': '',
    'print_slot_2_printer': '',
    'sumatra_path': '',
    'silent_print_enabled': True,
    'printer_type': PRINTER_TYPE_STANDARD,
    # Colour inkjets print black text as a mix of colour inks unless the job
    # asks for grayscale -- the shop's colour cartridges drained on plain bills.
    # Default on, and load_settings fills it in for installs saved before it.
    'print_black_only': True,
    # Dot matrix bills: 'raw' sends ESC/P straight to the printer (exact
    # placement), 'gdi' draws through the Windows driver, 'auto' picks RAW for
    # an ESC/P driver and the Windows driver for class / generic drivers.
    'dot_matrix_print_method': 'auto',
}


def gdi_placement_box(
    page_w: int, page_h: int, dpi_x: int, dpi_y: int, placement: dict,
) -> tuple[int, int, int, int]:
    """Top-left box for a dot matrix bill drawn through a Windows driver.

    Returns (margin_x, margin_y, avail_w, avail_h) in device pixels: the bill
    starts ``left_cm`` from the first printable column and ``top_cm`` from the
    top, and is fitted inside ``width_cm`` x ``height_cm`` (never beyond the
    printable page). Nothing is centred.
    """
    def cm(key: str) -> float:
        try:
            return max(0.0, float(placement.get(key) or 0))
        except (TypeError, ValueError):
            return 0.0

    dpi_x = max(1, int(dpi_x)); dpi_y = max(1, int(dpi_y))
    margin_x = int(round(cm("left_cm") / 2.54 * dpi_x))
    margin_y = int(round(cm("top_cm") / 2.54 * dpi_y))
    avail_w = page_w - margin_x
    avail_h = page_h - margin_y
    if cm("width_cm") > 0:
        avail_w = min(avail_w, int(cm("width_cm") / 2.54 * dpi_x))
    if cm("height_cm") > 0:
        avail_h = min(avail_h, int(cm("height_cm") / 2.54 * dpi_y))
    return margin_x, margin_y, max(100, avail_w), max(100, avail_h)


def merge_print_settings(current: str, *extra: str) -> str:
    """Join SumatraPDF -print-settings values (comma-separated), no duplicates."""
    tokens: list[str] = []
    for part in [*(current or '').split(','), *extra]:
        token = (part or '').strip()
        if token and token.lower() not in (t.lower() for t in tokens):
            tokens.append(token)
    return ','.join(tokens)


def _config_path() -> str:
    if getattr(sys, 'frozen', False):
        base = os.path.join(
            os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
            'VeterinaryApp',
        )
    else:
        base = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'config',
        )
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, 'printer_settings.json')


# ── Errors ───────────────────────────────────────────────────────────────────

class PrinterError(Exception):
    """Base printer error."""


class PrinterNotFoundError(PrinterError):
    pass


class PrinterOfflineError(PrinterError):
    pass


class SumatraNotFoundError(PrinterError):
    pass


class PdfNotFoundError(PrinterError):
    pass


class PrintAccessDeniedError(PrinterError):
    pass


class PrintFailedError(PrinterError):
    pass


# ── PrinterManager ─────────────────────────────────────────────────────────────

class PrinterManager:
    """Production helper for silent invoice printing on Windows."""

    PRINTER_STATUS_OFFLINE = 0x00000080
    PRINTER_STATUS_ERROR = 0x00000002
    PRINTER_ATTRIBUTE_WORK_OFFLINE = 0x00000400
    # winspool PRINTER_STATUS_* that stop a job, with what to tell the shop.
    PRINTER_STATUS_BLOCKERS = (
        (0x00000001, 'is paused. Open its print queue and choose Resume.'),
        (0x00000010, 'is out of paper. Load paper and try again.'),
        (0x00000008, 'has a paper jam. Clear it and try again.'),
        (0x00000040, 'reports a paper problem. Check the paper and try again.'),
        (0x00400000, 'has its cover open. Close it and try again.'),
        (0x00001000, 'is not available. Check the cable and power.'),
        (0x00100000, 'needs attention (check the printer for a blinking light).'),
    )
    # winspool JOB_STATUS_* that mean a job is stuck rather than just spooling.
    JOB_STATUS_STUCK = (
        (0x00000002, 'error'),
        (0x00000020, 'offline'),
        (0x00000040, 'out of paper'),
        (0x00000200, 'blocked'),
        (0x00000400, 'needs attention'),
    )

    @classmethod
    def _stuck_job_note(cls, handle) -> str:
        """Describe the first stuck job in an open printer's queue, '' when none."""
        try:
            import win32print
            jobs = win32print.EnumJobs(handle, 0, 32, 1) or []
        except Exception:
            return ''
        for job in jobs:
            status = int(job.get('Status') or 0)
            for flag, why in cls.JOB_STATUS_STUCK:
                if status & flag:
                    return f'"{job.get("pDocument") or "job"}": {why}'
        return ''

    @staticmethod
    def is_windows() -> bool:
        return sys.platform == 'win32'

    @classmethod
    def is_spooler_running(cls) -> bool:
        """True when Windows Print Spooler service is running."""
        if not cls.is_windows():
            return False
        try:
            import win32service
            import win32serviceutil
            status = win32serviceutil.QueryServiceStatus('Spooler')[1]
            return status == win32service.SERVICE_RUNNING
        except Exception:
            pass
        try:
            result = subprocess.run(
                ['sc', 'query', 'Spooler'],
                capture_output=True,
                text=True,
                timeout=8,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            return 'RUNNING' in (result.stdout or '').upper()
        except Exception:
            return False

    @classmethod
    def _printers_from_registry(cls) -> list[str]:
        """Fallback list from HKCU Devices (works even if spooler RPC is down)."""
        try:
            import winreg
        except ImportError:
            return []
        names: list[str] = []
        path = r'Software\Microsoft\Windows NT\CurrentVersion\Devices'
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            return []
        try:
            i = 0
            while True:
                try:
                    name, _val, _typ = winreg.EnumValue(key, i)
                except OSError:
                    break
                text = (name or '').strip()
                if text and text not in names:
                    names.append(text)
                i += 1
        finally:
            try:
                winreg.CloseKey(key)
            except Exception:
                pass
        return names

    @classmethod
    def _printers_from_enum(cls) -> list[str]:
        import win32print

        flag_sets = [
            win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS,
            win32print.PRINTER_ENUM_LOCAL,
            win32print.PRINTER_ENUM_CONNECTIONS,
        ]
        printers: list[str] = []
        last_err: Exception | None = None
        for flags in flag_sets:
            try:
                for row in win32print.EnumPrinters(flags):
                    # Level-1 rows: (flags, description, name, comment)
                    name = row[2] if len(row) > 2 else ''
                    text = (name or '').strip()
                    if text and text not in printers:
                        printers.append(text)
                if printers:
                    return printers
            except Exception as exc:
                last_err = exc
                continue
        if last_err and not printers:
            raise last_err
        return printers

    @classmethod
    def get_installed_printers(cls) -> list[str]:
        """Return installed printers (EnumPrinters, then registry fallback)."""
        if not cls.is_windows():
            return []
        try:
            import win32print  # noqa: F401
        except ImportError as exc:
            raise PrinterError(
                'pywin32 is required for printer support. Install: pip install pywin32'
            ) from exc

        printers: list[str] = []
        enum_error: Exception | None = None
        try:
            printers = cls._printers_from_enum()
        except Exception as exc:
            enum_error = exc

        if not printers:
            for name in cls._printers_from_registry():
                if name not in printers:
                    printers.append(name)

        default = cls.get_default_printer()
        if default and default not in printers:
            printers.append(default)

        if printers:
            return sorted(printers, key=str.lower)

        # Nothing found — explain the usual cause on this PC (spooler stopped).
        if not cls.is_spooler_running():
            raise PrinterError(
                'Could not list printers because the Windows Print Spooler is stopped.\n\n'
                'Fix: Start menu → Services → Print Spooler → Start\n'
                '(or run: net start spooler  as Administrator).\n\n'
                f'Technical detail: {enum_error or "no printers found"}'
            )
        raise PrinterError(
            f'Could not list printers: {enum_error or "no printers found"}'
        )

    @classmethod
    def get_default_printer(cls) -> str:
        """Windows default printer name, or empty string."""
        if not cls.is_windows():
            return ''
        try:
            import win32print
            return (win32print.GetDefaultPrinter() or '').strip()
        except Exception:
            pass
        # Registry fallback when spooler RPC is down
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows NT\CurrentVersion\Windows',
            )
            try:
                raw, _ = winreg.QueryValueEx(key, 'Device')
            finally:
                winreg.CloseKey(key)
            # Format: "Printer Name,winspool,Ne01:"
            name = (str(raw).split(',', 1)[0] or '').strip()
            return name
        except Exception:
            return ''

    @classmethod
    def load_settings(cls) -> dict[str, Any]:
        path = _config_path()
        if not os.path.isfile(path):
            return dict(DEFAULT_PRINTER_SETTINGS)
        try:
            with open(path, encoding='utf-8') as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                return dict(DEFAULT_PRINTER_SETTINGS)
            merged = dict(DEFAULT_PRINTER_SETTINGS)
            merged.update(data)
            return merged
        except Exception:
            return dict(DEFAULT_PRINTER_SETTINGS)

    @classmethod
    def save_settings(cls, settings: dict[str, Any]) -> None:
        merged = dict(DEFAULT_PRINTER_SETTINGS)
        merged.update(settings or {})
        path = _config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(merged, fh, indent=2)

    @classmethod
    def load_selected_printer(cls) -> str:
        return (cls.load_settings().get('selected_printer') or '').strip()

    @classmethod
    def save_selected_printer(cls, printer_name: str, **extra) -> None:
        cfg = cls.load_settings()
        cfg['selected_printer'] = (printer_name or '').strip()
        for key, value in extra.items():
            cfg[key] = value
        cls.save_settings(cfg)

    @classmethod
    def get_printer_type(cls) -> str:
        raw = (cls.load_settings().get('printer_type') or PRINTER_TYPE_STANDARD).strip().lower()
        return raw if raw in _PRINTER_TYPES else PRINTER_TYPE_STANDARD

    @classmethod
    def is_dot_matrix_mode(cls) -> bool:
        return cls.get_printer_type() == PRINTER_TYPE_DOT_MATRIX

    @classmethod
    def is_black_only_print(cls, cfg: dict[str, Any] | None = None) -> bool:
        if cfg is None:
            cfg = cls.load_settings()
        return bool(cfg.get('print_black_only', True))

    @classmethod
    def sumatra_print_settings(cls, cfg: dict[str, Any] | None = None, print_settings: str = '') -> str:
        """-print-settings value for Sumatra; adds 'monochrome' while black-only is on.

        'monochrome' sets the job's DEVMODE colour to grayscale, so the driver
        prints with the black cartridge instead of mixing colour inks. Whether
        grey logo shading still draws on colour ink is the driver's choice.
        """
        value = (print_settings or '').strip()
        if not cls.is_black_only_print(cfg):
            return value
        kept = ','.join(t for t in value.split(',') if t.strip().lower() != 'color')
        return merge_print_settings(kept, 'monochrome')


    @classmethod
    def get_printer_driver_name(cls, printer_name: str | None = None) -> str:
        printer = cls._resolve_printer(printer_name)
        import win32print
        handle = win32print.OpenPrinter(printer)
        try:
            info = win32print.GetPrinter(handle, 2)
            return (info.get("pDriverName") or "").strip()
        finally:
            try:
                win32print.ClosePrinter(handle)
            except Exception:
                pass

    @classmethod
    def get_dot_matrix_print_method(cls) -> str:
        raw = (cls.load_settings().get("dot_matrix_print_method") or "auto").strip().lower()
        return raw if raw in ("auto", "raw", "gdi") else "auto"

    @classmethod
    def should_use_dot_matrix_gdi(cls, printer_name: str | None = None) -> bool:
        method = cls.get_dot_matrix_print_method()
        if method == "gdi":
            return True
        if method == "raw":
            return False
        driver = cls.get_printer_driver_name(printer_name).lower()
        if "lx-310 esc/p" in driver or driver == "epson lx-310 esc/p":
            return False
        if "esc/p" in driver and "lx-310" in driver:
            return False
        markers = (
            "class driver",
            "9pin v4",
            "generic / text",
            "text only",
        )
        return any(m in driver for m in markers)

    @classmethod
    def _gdi_dot_matrix_metrics(
        cls,
        dc,
        num_lines: int,
        *,
        num_cols: int = 80,
        paper_size: str = "A6",
        scale_pct: float = 92.0,
        placement: dict | None = None,
    ) -> tuple[int, int, int, int]:
        """Pick font/line size so the bill fits the printable area (A6 landscape).

        Without ``placement`` the bill is centred on the driver's page (the old
        behaviour). With it - dot matrix bills - the bill starts at the top-left
        of the printable area, moved right by ``left_cm``, and must fit inside
        ``width_cm`` x ``height_cm``: a class driver that thinks the paper is
        10 inches wide no longer pushes a slip loaded on the left into the middle.
        """
        import win32ui
        import win32con

        page_w = dc.GetDeviceCaps(win32con.HORZRES)
        page_h = dc.GetDeviceCaps(win32con.VERTRES)
        paper = (paper_size or "A6").upper()
        fit = max(0.75, min(1.0, float(scale_pct or 92.0) / 100.0))
        if placement is not None:
            dpi_x = max(1, dc.GetDeviceCaps(win32con.LOGPIXELSX))
            dpi_y = max(1, dc.GetDeviceCaps(win32con.LOGPIXELSY))
            margin_x, margin_y, avail_w, avail_h = gdi_placement_box(
                page_w, page_h, dpi_x, dpi_y, placement,
            )
        else:
            if paper == "A6":
                fit *= 0.82
            elif paper == "A5":
                fit *= 0.92
            avail_w = max(100, int(page_w * fit))
            avail_h = max(100, int(page_h * fit))
            margin_x = max(2, (page_w - avail_w) // 2)
            margin_y = max(2, (page_h - avail_h) // 2)

        for font_h in range(14, 6, -1):
            line_h = max(7, int(font_h * 1.06))
            if num_lines * line_h > avail_h:
                continue
            probe = win32ui.CreateFont({
                "name": "Courier New",
                "height": font_h,
                "weight": win32con.FW_NORMAL,
                "pitchandfamily": win32con.FF_MODERN | win32con.FIXED_PITCH,
            })
            dc.SelectObject(probe)
            cw = max(1, dc.GetTextExtent("0")[0])
            if cw * num_cols > avail_w:
                continue
            return font_h, line_h, margin_x, margin_y

        font_h = 7
        return font_h, 8, margin_x, margin_y

    @classmethod
    def print_text_gdi(
        cls,
        text: str,
        printer_name: str | None = None,
        *,
        copies: int = 1,
        paper_size: str | None = None,
        scale_pct: float = 92.0,
        placement: dict | None = None,
    ) -> None:
        """Print fixed-width plain text via GDI monospace (Courier New).

        With ``placement`` (dot matrix bills) a form feed in ``text`` starts a
        new page, each page starts at the top-left instead of being centred, and
        a ``slip_cm`` asks the driver for a page exactly one slip long, so every
        page ejects exactly one slip instead of a whole default-size sheet.
        """
        if not cls.is_windows():
            raise PrinterError("GDI text printing is only supported on Windows.")
        printer = cls._resolve_printer(printer_name)
        cls._assert_printer_ready(printer)
        try:
            import win32ui
            import win32con
        except ImportError as exc:
            raise PrinterError("pywin32 is required for GDI printing.") from exc

        copies = max(1, min(int(copies or 1), 10))
        try:
            from core.print_log import print_log
        except Exception:
            print_log = None  # type: ignore

        if print_log:
            print_log(
                f'GDI text start printer="{printer}" lines={len(text.splitlines())} '
                f'paper={paper_size or "A6"} scale_pct={scale_pct} copies={copies}'
            )

        if placement is not None:
            pages = [pg.strip("\n") for pg in text.split("\f") if pg.strip()] or [text]
        else:
            pages = [text]
        all_lines = [ln for pg in pages for ln in pg.splitlines()]
        num_lines = max(1, max(len(pg.splitlines()) for pg in pages))
        max_cols = max(1, min(120, max((len(line.replace("---BOLD---", "").replace("---NOBOLD---", "")) for line in all_lines), default=110)))
        paper = (paper_size or "A6").upper()
        slip_mm = 0.0
        if placement is not None:
            try:
                slip_mm = max(0.0, float(placement.get("slip_cm") or 0) * 10.0)
            except (TypeError, ValueError):
                slip_mm = 0.0
        for copy_idx in range(copies):
            dc = cls._create_printer_dc(printer, paper_length_mm=slip_mm, log=print_log)
            try:
                dc.SetMapMode(win32con.MM_TEXT)
                page_h = dc.GetDeviceCaps(win32con.VERTRES)
                dc.StartDoc(f"Satpuda Bill {copy_idx + 1}")
                font_h, line_h, x_margin, y_margin = cls._gdi_dot_matrix_metrics(
                    dc,
                    num_lines,
                    num_cols=max_cols,
                    paper_size=paper,
                    scale_pct=scale_pct,
                    placement=placement,
                )
                if print_log:
                    print_log(
                        f"GDI fit paper={paper} font_h={font_h} line_h={line_h} "
                        f"margin=({x_margin},{y_margin}) cols={max_cols} lines={num_lines}"
                    )
                font_normal = win32ui.CreateFont({
                    "name": "Courier New",
                    "height": font_h,
                    "weight": win32con.FW_NORMAL,
                    "pitchandfamily": win32con.FF_MODERN | win32con.FIXED_PITCH,
                })
                font_bold = win32ui.CreateFont({
                    "name": "Courier New",
                    "height": font_h,
                    "weight": win32con.FW_BOLD,
                    "pitchandfamily": win32con.FF_MODERN | win32con.FIXED_PITCH,
                })
                bold = False
                y = y_margin
                bold_on = "---BOLD---"
                bold_off = "---NOBOLD---"

                def _char_w() -> int:
                    dc.SelectObject(font_normal)
                    ext = dc.GetTextExtent("0")
                    return max(1, ext[0])

                def _print_mixed_line(line: str, y_pos: int) -> None:
                    nonlocal bold
                    x_pos = x_margin
                    chunk = line[:120]
                    cw = _char_w()
                    while chunk:
                        start = chunk.find(bold_on)
                        if start < 0:
                            dc.SelectObject(font_bold if bold else font_normal)
                            dc.TextOut(x_pos, y_pos, chunk[:max_cols])
                            return
                        if start > 0:
                            dc.SelectObject(font_bold if bold else font_normal)
                            dc.TextOut(x_pos, y_pos, chunk[:start])
                            x_pos += cw * start
                        chunk = chunk[start + len(bold_on):]
                        bold = True
                        end = chunk.find(bold_off)
                        if end < 0:
                            dc.SelectObject(font_bold)
                            dc.TextOut(x_pos, y_pos, chunk[:max_cols])
                            return
                        dc.SelectObject(font_bold)
                        dc.TextOut(x_pos, y_pos, chunk[:end])
                        x_pos += cw * end
                        chunk = chunk[end + len(bold_off):]
                        bold = False

                for page_text in pages:
                    dc.StartPage()
                    bold = False
                    y = y_margin
                    for line in page_text.splitlines():
                        if line == bold_on:
                            bold = True
                            continue
                        if line == bold_off:
                            bold = False
                            continue
                        # Dot-matrix bills are sized to one page — never spill to extra pages.
                        _print_mixed_line(line, y)
                        y += line_h
                    dc.EndPage()
                dc.EndDoc()
            finally:
                try:
                    dc.DeleteDC()
                except Exception:
                    pass
            if print_log:
                print_log(f'GDI text copy {copy_idx + 1}/{copies} sent')
            if copy_idx < copies - 1:
                time.sleep(0.25)
        if print_log:
            print_log(f'GDI text done printer="{printer}" copies={copies}')

    @classmethod
    def _create_printer_dc(cls, printer: str, *, paper_length_mm: float = 0.0, log=None):
        """Printer DC; with ``paper_length_mm`` the page is that long (custom size).

        A dot matrix driver's default page is often 11 or 12 inches, so every
        bill fed a whole sheet. Asking for a user-defined paper length equal to
        the slip makes EndPage feed exactly one slip. A driver that refuses a
        custom size keeps its own page; that is logged, not fatal.
        """
        import win32con
        import win32ui

        if paper_length_mm and paper_length_mm > 0:
            try:
                import win32gui
                import win32print

                probe = win32ui.CreateDC()
                probe.CreatePrinterDC(printer)
                try:
                    phys_w = probe.GetDeviceCaps(win32con.PHYSICALWIDTH)
                    dpi_x = max(1, probe.GetDeviceCaps(win32con.LOGPIXELSX))
                finally:
                    probe.DeleteDC()
                handle = win32print.OpenPrinter(printer)
                try:
                    devmode = win32print.GetPrinter(handle, 2)["pDevMode"]
                    if devmode is None:
                        raise RuntimeError("driver gave no DEVMODE")
                    devmode.PaperSize = 256                       # DMPAPER_USER
                    devmode.PaperLength = int(round(paper_length_mm * 10))
                    devmode.PaperWidth = int(round(phys_w / dpi_x * 254))
                    devmode.Fields |= 0x2 | 0x4 | 0x8             # DM_PAPERSIZE | DM_PAPERLENGTH | DM_PAPERWIDTH
                    win32print.DocumentProperties(0, handle, printer, devmode, devmode, 2 | 8)
                finally:
                    win32print.ClosePrinter(handle)
                hdc = win32gui.CreateDC("WINSPOOL", printer, devmode)
                dc = win32ui.CreateDCFromHandle(hdc)
                got_mm = (dc.GetDeviceCaps(win32con.PHYSICALHEIGHT)
                          / max(1, dc.GetDeviceCaps(win32con.LOGPIXELSY)) * 25.4)
                if log:
                    level = "INFO" if abs(got_mm - paper_length_mm) <= 3 else "WARN"
                    log(f"GDI page length asked {paper_length_mm:.1f} mm, driver gave {got_mm:.1f} mm",
                        level=level)
                return dc
            except Exception as exc:
                if log:
                    log(f"GDI custom page length refused ({exc}); using the driver page", level="WARN")
        dc = win32ui.CreateDC()
        dc.CreatePrinterDC(printer)
        return dc

    @classmethod
    def _printer_work_offline(cls, printer_name: str) -> bool:
        """True when Windows marked the queue Work Offline (stale USB port)."""
        try:
            import win32print
            handle = win32print.OpenPrinter(printer_name)
            try:
                info = win32print.GetPrinter(handle, 2)
                attrs = int(info.get('Attributes') or 0)
                return bool(attrs & cls.PRINTER_ATTRIBUTE_WORK_OFFLINE)
            finally:
                win32print.ClosePrinter(handle)
        except Exception:
            return False

    @classmethod
    def _live_lx310_usb_ports(cls) -> list[str]:
        """USB ports (e.g. USB009) where Windows PnP reports EPSON LX-310 as connected."""
        ports: list[str] = []
        seen: set[str] = set()
        try:
            import win32com.client
            wmi = win32com.client.GetObject('winmgmts:')
            for dev in wmi.InstancesOf('Win32_PnPEntity'):
                name = str(getattr(dev, 'Name', '') or '')
                lower = name.lower()
                if 'lx-310' not in lower and 'lx310' not in lower:
                    continue
                status = str(getattr(dev, 'Status', '') or '').lower()
                if status and status != 'ok':
                    continue
                pid = str(getattr(dev, 'PNPDeviceID', '') or '')
                match = re.search(r'&(USB\d+)\s*$', pid, re.I)
                if not match:
                    match = re.search(r'(USB\d+)', pid, re.I)
                if not match:
                    continue
                port = match.group(1).upper()
                if port not in seen:
                    seen.add(port)
                    ports.append(port)
        except Exception:
            pass
        return ports

    @classmethod
    def _lx310_queue_on_port(cls, port: str) -> str:
        """ESC/P queue bound to a USB port, if any."""
        port_u = (port or '').strip().upper()
        if not port_u:
            return ''
        try:
            installed = cls.get_installed_printers()
        except Exception:
            return ''
        for name in installed:
            lower = name.lower()
            if 'lx-310' not in lower and 'lx310' not in lower:
                continue
            if 'esc/p' not in lower and 'escp' not in lower:
                continue
            try:
                if cls._printer_port_name(name).upper() == port_u:
                    return name
            except Exception:
                continue
        return ''

    @classmethod
    def _raw_port_candidates(cls, printer_name: str) -> list[str]:
        """Ports to try for direct RAW write — live USB first."""
        ports: list[str] = []
        seen: set[str] = set()

        def _add(port: str) -> None:
            p = (port or '').strip().upper()
            if p and p not in seen:
                seen.add(p)
                ports.append(p)

        for live in cls._live_lx310_usb_ports():
            _add(live)
        try:
            _add(cls._printer_port_name(printer_name))
        except Exception:
            pass
        return ports

    @classmethod
    def find_escp_lx310_printer(cls) -> str:
        """Best LX-310 queue — live USB port first, then any online ESC/P queue."""
        try:
            installed = cls.get_installed_printers()
        except Exception:
            return ''
        lx = [p for p in installed if 'lx-310' in p.lower() or 'lx310' in p.lower()]
        if not lx:
            return ''
        escp = [p for p in lx if 'esc/p' in p.lower() or 'escp' in p.lower()]

        # Prefer queue on the USB port where PnP says the printer is connected.
        for port in cls._live_lx310_usb_ports():
            match = cls._lx310_queue_on_port(port)
            if match and not cls._printer_work_offline(match):
                return match

        online = [p for p in escp if not cls._printer_work_offline(p)]
        if online:
            return online[0]
        if escp:
            return escp[0]
        online_lx = [p for p in lx if not cls._printer_work_offline(p)]
        if online_lx:
            return online_lx[0]
        return lx[0]

    @classmethod
    def resolve_dot_matrix_printer(cls, printer_name: str | None = None) -> str:
        """Pick the live LX-310 ESC/P queue (auto-detect USB port)."""
        base = (printer_name or '').strip()
        if base:
            try:
                base = cls._resolve_printer(base)
            except Exception:
                pass
        if not base:
            base = cls.find_escp_lx310_printer() or cls.get_default_printer()

        lower = (base or '').lower()
        if 'lx-310' in lower or 'lx310' in lower:
            live = cls.find_escp_lx310_printer()
            if live and live.lower() != lower:
                try:
                    from core.print_log import print_log
                    live_port = cls._printer_port_name(live)
                    pnp_ports = ','.join(cls._live_lx310_usb_ports()) or 'unknown'
                    print_log(
                        f'dot_matrix auto-detected "{live}" port={live_port} '
                        f'(PnP USB: {pnp_ports}) — was "{base}"',
                        level='WARN' if cls._printer_work_offline(base) else 'INFO',
                    )
                except Exception:
                    pass
                return live
        return base

    @classmethod
    def get_printer_for_slot(cls, slot: int) -> str:
        """Printer for Print Sales 1/2; falls back to selected_printer."""
        cfg = cls.load_settings()
        slot_key = f'print_slot_{slot}_printer'
        name = (cfg.get(slot_key) or '').strip()
        if not name:
            selected = (cfg.get('selected_printer') or '').strip()
            name = selected or cls.get_default_printer()
        if cls.is_dot_matrix_mode():
            return cls.resolve_dot_matrix_printer(name)
        return name

    @classmethod
    def _app_root(cls) -> str:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    @classmethod
    def _exe_dir(cls) -> str:
        if getattr(sys, 'frozen', False):
            return os.path.dirname(os.path.abspath(sys.executable))
        return cls._app_root()

    @classmethod
    def _prefer_64bit_sumatra(cls) -> bool:
        return sys.maxsize > 2 ** 32

    @classmethod
    def _bundled_sumatra_filenames(cls) -> tuple[str, ...]:
        if cls._prefer_64bit_sumatra():
            return ('SumatraPDF64.exe', 'SumatraPDF32.exe', 'SumatraPDF.exe')
        return ('SumatraPDF32.exe', 'SumatraPDF64.exe', 'SumatraPDF.exe')

    @classmethod
    def _sumatra_search_dirs(cls) -> list[str]:
        """Folders checked for bundled SumatraPDF32/64.exe (dev + EXE)."""
        dirs: list[str] = []
        seen: set[str] = set()
        candidates = [
            os.path.join(cls._exe_dir(), 'tools'),
            cls._exe_dir(),
            os.path.join(cls._app_root(), 'tools'),
        ]
        if getattr(sys, 'frozen', False):
            parent = os.path.dirname(cls._exe_dir())
            candidates.insert(0, os.path.join(parent, 'tools'))
        for folder in candidates:
            norm = os.path.normcase(os.path.abspath(folder))
            if norm in seen:
                continue
            seen.add(norm)
            dirs.append(folder)
        return dirs

    @classmethod
    def find_bundled_sumatra_path(cls) -> str:
        """Auto-detect Sumatra from project/EXE tools folder (no saved config)."""
        for folder in cls._sumatra_search_dirs():
            for fname in cls._bundled_sumatra_filenames():
                path = os.path.join(folder, fname)
                if path and os.path.isfile(path):
                    return os.path.abspath(path)
        return ''

    @classmethod
    def _installed_sumatra_candidates(cls) -> list[str]:
        """Full installs — more reliable for -print-to than bundled tools copies."""
        out: list[str] = []
        local = os.environ.get('LOCALAPPDATA', '')
        if local:
            out.append(os.path.join(local, 'SumatraPDF', 'SumatraPDF.exe'))
        for root in (
            os.environ.get('PROGRAMFILES', r'C:\Program Files'),
            os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'),
        ):
            if not root:
                continue
            out.extend([
                os.path.join(root, 'SumatraPDF', 'SumatraPDF.exe'),
                os.path.join(root, 'SumatraPDF.exe'),
            ])
        return out

    @classmethod
    def find_sumatra_path(cls, configured: str = '') -> str:
        """Locate Sumatra — saved override, installed copy, bundled tools, then PATH."""
        candidates: list[str] = []
        if configured and configured.strip():
            candidates.append(configured.strip())
        env_path = os.environ.get('SUMATRA_PDF', '').strip()
        if env_path:
            candidates.append(env_path)

        # Prefer a full install; bundled SumatraPDF64.exe often exits 0 without spooling.
        candidates.extend(cls._installed_sumatra_candidates())
        candidates.extend(
            os.path.join(folder, fname)
            for folder in cls._sumatra_search_dirs()
            for fname in cls._bundled_sumatra_filenames()
        )

        seen: set[str] = set()
        for path in candidates:
            norm = os.path.normcase(os.path.abspath(path))
            if norm in seen:
                continue
            seen.add(norm)
            if path and os.path.isfile(path):
                return os.path.abspath(path)
        return ''

    @classmethod
    def _resolve_printer(cls, printer_name: str | None) -> str:
        name = (printer_name or '').strip()
        if not name:
            name = cls.load_selected_printer()
        if not name:
            name = cls.get_default_printer()
        if not name:
            raise PrinterNotFoundError(
                'No printer selected. Open Settings → Pharmacy → Printer Setup.'
            )

        installed = {p.lower(): p for p in cls.get_installed_printers()}
        key = name.lower()
        if key not in installed:
            raise PrinterNotFoundError(
                f'Printer "{name}" is not installed or not available on this PC.'
            )
        return installed[key]

    @classmethod
    def _assert_printer_ready(cls, printer_name: str) -> None:
        try:
            import win32print
            import pywintypes
        except ImportError as exc:
            raise PrinterError('pywin32 is required for printing.') from exc

        try:
            handle = win32print.OpenPrinter(printer_name)
        except pywintypes.error as exc:
            if exc.winerror in (5,):  # ACCESS_DENIED
                raise PrintAccessDeniedError(
                    f'Access denied opening printer "{printer_name}".'
                ) from exc
            raise PrinterNotFoundError(
                f'Printer "{printer_name}" could not be opened: {exc}'
            ) from exc

        try:
            info = win32print.GetPrinter(handle, 2)
            status = int(info.get('Status') or 0)
            attrs = int(info.get('Attributes') or 0)
            if attrs & cls.PRINTER_ATTRIBUTE_WORK_OFFLINE:
                alt = ''
                if 'lx-310' in (printer_name or '').lower() or 'lx310' in (printer_name or '').lower():
                    alt = cls.find_escp_lx310_printer()
                hint = ''
                if alt and alt.lower() != (printer_name or '').lower():
                    hint = f'\nUse the online queue "{alt}" in Printer Setup instead.'
                raise PrinterOfflineError(
                    f'Printer "{printer_name}" is set Work Offline (stale USB port).'
                    f'{hint}'
                )
            if status & cls.PRINTER_STATUS_OFFLINE:
                raise PrinterOfflineError(
                    f'Printer "{printer_name}" is offline or not connected.'
                )
            # Each of these leaves a bill waiting in the queue (or printing at
            # the wrong place once cleared), so say which one before sending it.
            for flag, why in cls.PRINTER_STATUS_BLOCKERS:
                if status & flag:
                    raise PrinterOfflineError(f'Printer "{printer_name}" {why}')
            if status & cls.PRINTER_STATUS_ERROR:
                raise PrinterOfflineError(
                    f'Printer "{printer_name}" reported a hardware error.'
                )
            stuck = cls._stuck_job_note(handle)
            if stuck:
                raise PrinterOfflineError(
                    f'Printer "{printer_name}" has an earlier job stuck in its queue ({stuck}).\n'
                    'Open Settings > Printers, open this printer and cancel the stuck '
                    'job, then print again.'
                )
        finally:
            try:
                win32print.ClosePrinter(handle)
            except Exception:
                pass

    @classmethod
    def _is_virtual_pdf_printer(cls, printer_name: str) -> bool:
        """Drivers that always prompt for a save path (Sumatra cannot silence them)."""
        lower = (printer_name or '').lower()
        markers = (
            'microsoft print to pdf',
            'print to pdf',
            'adobe pdf',
            'foxit reader pdf',
            'cutepdf',
            'bullzip',
            'pdfcreator',
            'novapdf',
            'save as pdf',
            'send to onenote',
        )
        return any(m in lower for m in markers)


    @classmethod
    def _resolve_cups_destination(cls, printer_name: str | None) -> str | None:
        """Map a configured printer name onto a real CUPS queue.

        The saved name comes from Windows ("HP Smart Tank 520_540 series PCL-3
        (V4)") and never matches the CUPS queue ("HP_Smart_Tank"), so match
        loosely on the alphanumerics before falling back to the default queue.
        """
        import subprocess

        try:
            out = subprocess.run(
                ["lpstat", "-a"], capture_output=True, text=True, timeout=10
            ).stdout
        except Exception:
            return (printer_name or "").strip() or None
        queues = [ln.split()[0] for ln in out.splitlines() if ln.strip()]
        if not queues:
            return (printer_name or "").strip() or None

        want = "".join(ch for ch in (printer_name or "").lower() if ch.isalnum())
        if want:
            for q in queues:
                if "".join(ch for ch in q.lower() if ch.isalnum()) == want:
                    return q
            for q in queues:
                qn = "".join(ch for ch in q.lower() if ch.isalnum())
                if qn and (qn in want or want.startswith(qn)):
                    return q
        try:
            d = subprocess.run(
                ["lpstat", "-d"], capture_output=True, text=True, timeout=10
            ).stdout
            if ":" in d:
                return d.split(":", 1)[1].strip() or queues[0]
        except Exception:
            pass
        return queues[0]

    @classmethod
    def _print_pdf_via_cups(
        cls, pdf_path: str, printer_name: str | None = None, *, copies: int = 1
    ) -> None:
        import subprocess

        pdf_path = os.path.abspath(pdf_path or "")
        if not os.path.isfile(pdf_path):
            raise PrinterError(f"Bill PDF not found: {pdf_path}")
        dest = cls._resolve_cups_destination(printer_name)
        if not dest:
            raise PrinterError(
                "No printer is set up on this Mac.\n\n"
                "System Settings -> Printers & Scanners -> Add Printer."
            )
        cmd = ["lp", "-d", dest, "-n", str(max(1, int(copies or 1))), pdf_path]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except Exception as exc:
            raise PrinterError(f"Could not start printing: {exc}") from exc
        if res.returncode != 0:
            raise PrinterError(
                f"Printer '{dest}' rejected the job: "
                f"{(res.stderr or res.stdout or '').strip()[:200]}"
            )

    @classmethod
    def print_pdf_silently(
        cls,
        pdf_path: str,
        printer_name: str | None = None,
        *,
        copies: int = 1,
        print_settings: str = '',
    ) -> None:
        """
        Print PDF to the given printer without any dialog.

        Uses SumatraPDF: SumatraPDF.exe -print-to "<printer>" -silent "<pdf>"
        Windows printer preferences (paper, orientation, etc.) are preserved.
        With 'print_black_only' on, -print-settings carries 'monochrome' merged
        into print_settings; with it off, the command is left exactly as given.

        Virtual PDF printers (e.g. Microsoft Print to PDF) always show a save
        dialog via Sumatra — the bill PDF is already saved as Bill_<no>.pdf in
        Downloads, so those printers are skipped to avoid prompting.
        """
        if not cls.is_windows():
            # macOS/Linux: CUPS can print silently too. Without this the Mac
            # build made the bill PDF and then threw, so nothing ever reached
            # the printer -- the shop saw "printed" with an empty output tray.
            return cls._print_pdf_via_cups(pdf_path, printer_name, copies=copies)

        if not cls.is_spooler_running():
            raise PrinterError(
                'Windows Print Spooler is stopped — printing cannot start.\n\n'
                'Start menu → Services → Print Spooler → Start\n'
                '(or run as Administrator: net start spooler).'
            )

        pdf_path = os.path.abspath(pdf_path or '')
        if not pdf_path or not os.path.isfile(pdf_path):
            raise PdfNotFoundError(f'PDF not found: {pdf_path}')
        if os.path.getsize(pdf_path) < 100:
            raise PdfNotFoundError(f'PDF file is empty or invalid: {pdf_path}')

        printer = cls._resolve_printer(printer_name)

        if cls._is_virtual_pdf_printer(printer):
            try:
                from core.print_log import print_log
                print_log(
                    f'print_pdf_silently SKIPPED virtual printer="{printer}" pdf="{pdf_path}"',
                    level='WARN',
                )
            except Exception:
                pass
            # Bill PDF already written with bill number before print is called.
            return

        cls._assert_printer_ready(printer)
        cfg = cls.load_settings()
        if cls.pdf_print_method(cfg, printer) == 'gdi':
            try:
                cls.print_pdf_gdi(
                    pdf_path, printer, copies=copies,
                    black_only=cls.is_black_only_print(cfg),
                    landscape='landscape' in (print_settings or '').lower(),
                )
                return
            except Exception as exc:
                # Sumatra is still there to try; a GDI failure is logged, not shown.
                try:
                    from core.print_log import print_log
                    print_log(f'print_pdf_gdi failed on "{printer}": {exc} -- trying SumatraPDF', level='WARN')
                except Exception:
                    pass
        settings_arg = cls.sumatra_print_settings(cfg, print_settings)
        try:
            from core.print_log import print_log
            print_log(
                f'print_pdf_silently start printer="{printer}" pdf="{pdf_path}" copies={copies} '
                f'print_settings="{settings_arg}"'
            )
        except Exception:
            pass

        sumatra = cls.find_sumatra_path(cfg.get('sumatra_path') or '')
        if not sumatra:
            raise SumatraNotFoundError(
                'SumatraPDF was not found.\n\n'
                'Place SumatraPDF64.exe and/or SumatraPDF32.exe in the tools folder:\n'
                f'  {os.path.join(cls._exe_dir(), "tools")}\n\n'
                'Or install SumatraPDF from https://www.sumatrapdfreader.org/\n'
                'or set a custom path in Settings → Printer Setup.'
            )

        copies = max(1, min(int(copies or 1), 10))
        last_error = ''
        default_printer = cls.get_default_printer()
        use_print_to_default = (
            default_printer
            and default_printer.lower() == printer.lower()
        )

        for copy_idx in range(copies):
            if use_print_to_default:
                cmd = [sumatra, '-print-to-default']
            else:
                cmd = [sumatra, '-print-to', printer]
            if settings_arg:
                cmd += ['-print-settings', settings_arg]
            cmd += ['-silent', '-exit-when-done', pdf_path]
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
            except subprocess.TimeoutExpired as exc:
                raise PrintFailedError(
                    f'Print timed out on copy {copy_idx + 1} of {copies}.'
                ) from exc
            except OSError as exc:
                if getattr(exc, 'winerror', None) == 5:
                    raise PrintAccessDeniedError(
                        'Access denied while starting SumatraPDF.'
                    ) from exc
                raise PrintFailedError(f'Failed to run SumatraPDF: {exc}') from exc

            if result.returncode != 0:
                err = (result.stderr or result.stdout or '').strip()
                last_error = err or f'exit code {result.returncode}'
                if copy_idx == 0:
                    raise PrintFailedError(
                        f'SumatraPDF could not print to "{printer}":\n{last_error}'
                    )
            if copy_idx < copies - 1:
                time.sleep(0.35)

    @classmethod
    def _raw_debug_path(cls) -> str:
        path = os.path.join(
            os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
            'VeterinaryApp',
        )
        os.makedirs(path, exist_ok=True)
        return os.path.join(path, 'last_dot_matrix_bill.prn')

    @classmethod
    def _is_xps_driver(cls, printer_name: str) -> bool:
        """A v4 (XPS-pipeline) driver -- the HP Smart Tank's "PCL-3 (V4)", for one."""
        try:
            import win32print

            h = win32print.OpenPrinter(printer_name)
            try:
                info = win32print.GetPrinter(h, 2)
            finally:
                win32print.ClosePrinter(h)
        except Exception:
            return False
        proc = str(info.get('pPrintProcessor') or '').upper()
        driver = str(info.get('pDriverName') or '').upper()
        return proc == 'MS_XPS_PROC' or '(V4)' in driver

    @classmethod
    def pdf_print_method(cls, cfg: dict[str, Any] | None, printer_name: str) -> str:
        """'gdi' or 'sumatra' for a PDF on this printer.

        SumatraPDF 3.6 sends nothing to a v4 (XPS) driver: on the shop's HP Smart
        Tank it changed the printer's settings, exited with 0, and no job ever
        reached the spooler -- every bill "printed" and the tray stayed empty
        (2 Oct 2026). Windows' own GDI printing reaches the same printer at once.
        Settings → printer_settings.json "pdf_print_method": "gdi" / "sumatra"
        forces one; "auto" (the default) picks GDI for v4 drivers only, so a
        printer that already works through Sumatra is left as it was.
        """
        method = str((cfg or {}).get('pdf_print_method') or 'auto').strip().lower()
        if method in ('gdi', 'sumatra'):
            return method
        return 'gdi' if cls.is_windows() and cls._is_xps_driver(printer_name) else 'sumatra'

    @classmethod
    def print_pdf_gdi(
        cls,
        pdf_path: str,
        printer_name: str,
        *,
        copies: int = 1,
        black_only: bool = False,
        landscape: bool = False,
        dpi_cap: int = 300,
    ) -> None:
        """Print a PDF through Windows GDI: each page drawn as a picture on the printer.

        Sizing follows Sumatra's default ("shrink"): a page larger than the
        printable area is fitted, a smaller one prints at its real size, centred
        across and from the top. Pages are rendered at no more than 300 dpi and
        stretched by the driver, so an A4 page stays near 25 MB instead of 100.
        """
        import pypdfium2 as pdfium
        import win32con
        import win32ui
        from PIL import ImageWin

        copies = max(1, min(int(copies or 1), 10))
        dc = win32ui.CreateDC()
        dc.CreatePrinterDC(printer_name)
        try:
            dpi_x = dc.GetDeviceCaps(win32con.LOGPIXELSX) or 300
            dpi_y = dc.GetDeviceCaps(win32con.LOGPIXELSY) or 300
            area_w = dc.GetDeviceCaps(win32con.HORZRES)
            area_h = dc.GetDeviceCaps(win32con.VERTRES)
            doc = pdfium.PdfDocument(pdf_path)
            try:
                pages = []
                for i in range(len(doc)):
                    page = doc[i]
                    w_pt, h_pt = page.get_size()
                    render_dpi = min(dpi_cap, dpi_x)
                    img = page.render(scale=render_dpi / 72.0).to_pil()
                    img = img.convert('L' if black_only else 'RGB')
                    turn = (landscape or w_pt > h_pt) and area_h > area_w
                    if turn:
                        img = img.rotate(90, expand=True)
                        w_pt, h_pt = h_pt, w_pt
                    # device size of the page at its real size, then shrink to fit
                    dev_w = w_pt / 72.0 * dpi_x
                    dev_h = h_pt / 72.0 * dpi_y
                    shrink = min(1.0, area_w / dev_w, area_h / dev_h)
                    dev_w, dev_h = int(dev_w * shrink), int(dev_h * shrink)
                    left = max(0, (area_w - dev_w) // 2)
                    pages.append((img, (left, 0, left + dev_w, dev_h)))
            finally:
                doc.close()
            dc.StartDoc(os.path.basename(pdf_path))
            try:
                for _ in range(copies):
                    for img, rect in pages:
                        dc.StartPage()
                        ImageWin.Dib(img).draw(dc.GetHandleOutput(), rect)
                        dc.EndPage()
            except Exception:
                dc.AbortDoc()
                raise
            dc.EndDoc()
        finally:
            dc.DeleteDC()
        try:
            from core.print_log import print_log
            print_log(f'print_pdf_gdi OK printer="{printer_name}" pdf="{pdf_path}" pages={len(pages)} copies={copies}')
        except Exception:
            pass

    @classmethod
    def _save_raw_debug_copy(cls, data: bytes, printer_name: str) -> str:
        """Keep last RAW payload so user can test/copy manually if needed."""
        path = cls._raw_debug_path()
        try:
            with open(path, 'wb') as fh:
                fh.write(data)
            sidecar = path + '.txt'
            with open(sidecar, 'w', encoding='utf-8') as fh:
                fh.write(f'printer={printer_name}\nbytes={len(data)}\n')
        except Exception:
            return ''
        return path

    @classmethod
    def _printer_port_name(cls, printer_name: str) -> str:
        import win32print
        handle = win32print.OpenPrinter(printer_name)
        try:
            info = win32print.GetPrinter(handle, 2)
            return (info.get('pPortName') or '').strip()
        finally:
            try:
                win32print.ClosePrinter(handle)
            except Exception:
                pass

    @classmethod
    def _write_raw_to_port(cls, port_name: str, data: bytes) -> None:
        port = (port_name or '').strip()
        if not port:
            raise PrintFailedError('Printer port name is empty.')
        if port.upper().startswith('PORTPROMPT'):
            raise PrintFailedError('Printer uses PORTPROMPT — choose the real USB/LPT port.')
        device = port if port.startswith('\\\\.\\') else f'\\\\.\\{port.rstrip(":")}'
        if port.endswith(':') and not port.upper().startswith('COM'):
            device = f'\\\\.\\{port}'
        try:
            with open(device, 'wb', buffering=0) as fh:
                written = fh.write(data)
                fh.flush()
        except OSError as exc:
            raise PrintFailedError(f'Could not write to printer port "{port}": {exc}') from exc
        if written != len(data):
            raise PrintFailedError(
                f'Port write incomplete on "{port}": sent {written} of {len(data)} bytes.'
            )

    @classmethod
    def _send_raw_via_spooler(cls, printer_name: str, data: bytes, datatype: str) -> None:
        import win32print
        import pywintypes

        handle = None
        try:
            handle = win32print.OpenPrinter(printer_name)
        except pywintypes.error as exc:
            if exc.winerror in (5,):
                raise PrintAccessDeniedError(
                    f'Access denied opening printer "{printer_name}".'
                ) from exc
            raise PrinterNotFoundError(
                f'Printer "{printer_name}" could not be opened: {exc}'
            ) from exc

        try:
            win32print.StartDocPrinter(handle, 1, ('Satpuda Bill', None, datatype))
            try:
                # RAW dot-matrix: skip StartPage/EndPage — they trigger extra form feeds.
                if str(datatype or '').upper() == 'RAW':
                    written = win32print.WritePrinter(handle, data)
                else:
                    win32print.StartPagePrinter(handle)
                    written = win32print.WritePrinter(handle, data)
                    win32print.EndPagePrinter(handle)
            finally:
                win32print.EndDocPrinter(handle)
        except pywintypes.error as exc:
            if exc.winerror in (5,):
                raise PrintAccessDeniedError(
                    f'Access denied sending data to printer "{printer_name}".'
                ) from exc
            raise PrintFailedError(
                f'RAW print via spooler ({datatype}) failed: {exc}'
            ) from exc
        finally:
            try:
                win32print.ClosePrinter(handle)
            except Exception:
                pass

        if written != len(data):
            raise PrintFailedError(
                f'Printer spooler accepted only {written} of {len(data)} bytes.'
            )

    @classmethod
    def print_raw_escp(
        cls,
        data: bytes,
        printer_name: str | None = None,
        *,
        copies: int = 1,
    ) -> None:
        """Send RAW ESC/P bytes directly to the printer (bypasses GDI/fonts)."""
        if not cls.is_windows():
            raise PrinterError('RAW ESC/P printing is only supported on Windows.')
        if not cls.is_spooler_running():
            raise PrinterError(
                'Windows Print Spooler is stopped — printing cannot start.\n\n'
                'Start menu → Services → Print Spooler → Start\n'
                '(or run as Administrator: net start spooler).'
            )
        if not data:
            raise PrintFailedError('No print data to send.')

        if cls.is_dot_matrix_mode():
            printer = cls.resolve_dot_matrix_printer(printer_name)
            if cls._printer_work_offline(printer):
                live = cls.find_escp_lx310_printer()
                if live and live.lower() != (printer or '').lower():
                    try:
                        from core.print_log import print_log
                        print_log(
                            f'RAW ESC/P switched offline queue "{printer}" -> "{live}"',
                            level='WARN',
                        )
                    except Exception:
                        pass
                    printer = live
        else:
            printer = cls._resolve_printer(printer_name)
        if not cls._printer_work_offline(printer):
            cls._assert_printer_ready(printer)

        try:
            from core.print_log import print_log
        except Exception:
            print_log = None  # type: ignore

        copies = max(1, min(int(copies or 1), 10))
        debug_path = cls._save_raw_debug_copy(data, printer)
        port_name = ''
        try:
            port_name = cls._printer_port_name(printer)
        except Exception:
            pass
        if print_log:
            print_log(
                f'RAW ESC/P start printer="{printer}" port="{port_name}" '
                f'bytes={len(data)} copies={copies} debug="{debug_path}"'
            )

        try:
            import win32print
            import pywintypes
        except ImportError as exc:
            if print_log:
                print_log(f'pywin32 missing: {exc}', level='ERROR')
            raise PrinterError('pywin32 is required for RAW printing.') from exc

        last_error = ''

        for copy_idx in range(copies):
            sent = False
            method = ''
            queue_offline = cls._printer_work_offline(printer)
            if not queue_offline:
                for datatype in ('RAW', 'TEXT'):
                    try:
                        cls._send_raw_via_spooler(printer, data, datatype)
                        sent = True
                        method = f'spooler/{datatype}'
                        if print_log:
                            print_log(f'copy {copy_idx + 1}/{copies} sent via {method}')
                        break
                    except PrintFailedError as exc:
                        last_error = str(exc)
                        if print_log:
                            print_log(f'spooler/{datatype} failed: {exc}', level='WARN')
                        continue
            elif print_log:
                print_log(
                    f'queue "{printer}" is Work Offline — skipping spooler, using USB port',
                    level='WARN',
                )

            if not sent:
                for port in cls._raw_port_candidates(printer):
                    try:
                        cls._write_raw_to_port(port, data)
                        sent = True
                        method = f'port/{port}'
                        if print_log:
                            print_log(f'copy {copy_idx + 1}/{copies} sent via {method}')
                        break
                    except PrintFailedError as exc:
                        last_error = str(exc)
                        if print_log:
                            print_log(f'port/{port} failed: {exc}', level='WARN')

            if not sent:
                hint = ''
                if debug_path:
                    hint = f'\n\nLast RAW bill saved to:\n  {debug_path}'
                if print_log:
                    print_log(f'RAW ESC/P FAILED printer="{printer}" error={last_error}', level='ERROR')
                raise PrintFailedError(
                    f'Could not send bill to "{printer}".\n{last_error or "Unknown error."}'
                    f'{hint}'
                )

            if copy_idx < copies - 1:
                time.sleep(0.35)

        if print_log:
            print_log(f'RAW ESC/P done printer="{printer}" copies={copies} saved="{debug_path}"')

    @classmethod
    def test_print(cls, printer_name: str | None = None) -> None:
        """Print a one-page test page to verify printer setup."""
        if not cls.is_windows():
            raise PrinterError('Test print is only available on Windows.')

        printer = cls._resolve_printer(printer_name)
        if cls.is_dot_matrix_mode():
            from core.dot_matrix_print import format_test_page, render_escp_document

            text = format_test_page(printer)
            if cls.should_use_dot_matrix_gdi(printer):
                cls.print_text_gdi(text, printer, copies=1)
            else:
                payload = render_escp_document(text)
                cls.print_raw_escp(payload, printer, copies=1)
            return
        ink = (
            'Black only is ON — the printer should use only the black cartridge.'
            if cls.is_black_only_print()
            else 'Black only is OFF — the printer driver decides colour or black.'
        )
        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{{font-family:Arial,sans-serif;padding:24px;color:#000}}</style></head>
<body>
<h2>Satpuda — Printer Test</h2>
<p>Printer: <b>{printer}</b></p>
<p>If you can read this page, silent printing is configured correctly.</p>
<p style="font-size:11px">{ink}</p>
</body></html>"""

        tmp_dir = tempfile.gettempdir()
        html_path = os.path.join(tmp_dir, 'satpuda_printer_test.html')
        pdf_path = os.path.join(tmp_dir, 'satpuda_printer_test.pdf')
        with open(html_path, 'w', encoding='utf-8') as fh:
            fh.write(html)

        # Same Edge/Chrome HTML -> PDF step the bills use (the old built-in
        # HTML-to-PDF module has no source any more, so Test Print failed here).
        from core.bill_output import _try_pdf_via_browser
        if not _try_pdf_via_browser(html_path, pdf_path):
            raise PrintFailedError(
                'Could not create the test page PDF. Microsoft Edge or Google Chrome is needed.'
            )
        cls.print_pdf_silently(pdf_path, printer, copies=1)

        for path in (html_path, pdf_path):
            try:
                os.remove(path)
            except Exception:
                pass


# Convenience module-level wrappers (match user API spec)
def get_installed_printers() -> list[str]:
    return PrinterManager.get_installed_printers()


def get_default_printer() -> str:
    return PrinterManager.get_default_printer()


def save_selected_printer(printer_name: str, **extra) -> None:
    PrinterManager.save_selected_printer(printer_name, **extra)


def load_selected_printer() -> str:
    return PrinterManager.load_selected_printer()


def print_pdf_silently(pdf_path: str, printer_name: str | None = None, copies: int = 1) -> None:
    PrinterManager.print_pdf_silently(pdf_path, printer_name, copies=copies)


def test_print(printer_name: str | None = None) -> None:
    PrinterManager.test_print(printer_name)
