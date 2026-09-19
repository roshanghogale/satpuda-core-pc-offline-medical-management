from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
pm = ROOT / "core" / "printer_manager.py"
text = pm.read_text(encoding="utf-8")

resolve_fn = '''
    @classmethod
    def find_escp_lx310_printer(cls) -> str:
        """Best LX-310 entry for dot matrix (prefer native ESC/P driver name)."""
        try:
            installed = cls.get_installed_printers()
        except Exception:
            return ""
        lx = [p for p in installed if "lx-310" in p.lower() or "lx310" in p.lower()]
        if not lx:
            return ""
        for p in lx:
            if "esc/p" in p.lower() or "escp" in p.lower():
                return p
        return lx[0]

    @classmethod
    def resolve_dot_matrix_printer(cls, printer_name: str | None = None) -> str:
        """Map generic LX-310 selection to native ESC/P queue when installed."""
        try:
            base = cls._resolve_printer(printer_name) if (printer_name or "").strip() else ""
        except Exception:
            base = (printer_name or "").strip()
        if not base:
            base = cls.find_escp_lx310_printer()
        if not base:
            return cls.get_default_printer()
        lower = base.lower()
        if "lx-310" in lower or "lx310" in lower:
            escp = cls.find_escp_lx310_printer()
            if escp and escp.lower() != lower:
                try:
                    from core.print_log import print_log
                    print_log(
                        f'dot_matrix resolved printer "{base}" -> "{escp}"',
                    )
                except Exception:
                    pass
                return escp
        return base

'''

if "def find_escp_lx310_printer" not in text:
    text = text.replace(
        "    @classmethod\n    def get_printer_for_slot(cls, slot: int) -> str:",
        resolve_fn + "\n    @classmethod\n    def get_printer_for_slot(cls, slot: int) -> str:",
        1,
    )

# patch get_printer_for_slot body
old_slot = '''        if name:
            return name
        selected = (cfg.get('selected_printer') or '').strip()
        if selected:
            return selected
        return cls.get_default_printer()'''
new_slot = '''        if not name:
            selected = (cfg.get('selected_printer') or '').strip()
            name = selected or cls.get_default_printer()
        if cls.is_dot_matrix_mode():
            return cls.resolve_dot_matrix_printer(name)
        return name'''
if old_slot in text and "resolve_dot_matrix_printer(name)" not in text:
    text = text.replace(old_slot, new_slot, 1)

# improve should_use_dot_matrix_gdi - native ESC/P driver uses RAW
old_gdi = '''        driver = cls.get_printer_driver_name(printer_name).lower()
        markers = (
            "class driver",
            "9pin v4",
            "esc/p 9pin",
            "generic / text",
            "text only",
        )
        return any(m in driver for m in markers)'''
new_gdi = '''        driver = cls.get_printer_driver_name(printer_name).lower()
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
        return any(m in driver for m in markers)'''
text = text.replace(old_gdi, new_gdi, 1)

# replace print_text_gdi with MM_TEXT version
import re
start = text.find("    def print_text_gdi(")
end = text.find("\n    @classmethod\n    def get_printer_for_slot", start)
if start > 0 and end > start:
    new_gdi_fn = r'''    @classmethod
    def print_text_gdi(
        cls,
        text: str,
        printer_name: str | None = None,
        *,
        copies: int = 1,
    ) -> None:
        """Print fixed-width plain text via GDI monospace (Courier New)."""
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
                f'GDI text start printer="{printer}" lines={len(text.splitlines())} copies={copies}'
            )

        x_margin = 24
        y_margin = 24
        font_h = 18
        line_h = font_h + 4
        for copy_idx in range(copies):
            dc = win32ui.CreateDC()
            try:
                dc.CreatePrinterDC(printer)
                dc.SetMapMode(win32con.MM_TEXT)
                page_h = dc.GetDeviceCaps(win32con.VERTRES)
                dc.StartDoc(f"Satpuda Bill {copy_idx + 1}")
                dc.StartPage()
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
                for line in text.splitlines():
                    if line == "---BOLD---":
                        bold = True
                        continue
                    if line == "---NOBOLD---":
                        bold = False
                        continue
                    if y + line_h > page_h - y_margin:
                        dc.EndPage()
                        dc.StartPage()
                        y = y_margin
                    dc.SelectObject(font_bold if bold else font_normal)
                    dc.TextOut(x_margin, y, line[:80])
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

'''
    text = text[:start] + new_gdi_fn + text[end:]

pm.write_text(text, encoding="utf-8", newline="\n")
print("patched printer_manager")

# patch dot_matrix print_dot_matrix_bill with fallback chain
p = ROOT / "core" / "dot_matrix_print.py"
dt = p.read_text(encoding="utf-8")
old_block = '''    from core.printer_manager import PrinterManager

    if PrinterManager.should_use_dot_matrix_gdi(printer_name):
        try:
            from core.print_log import print_log
            driver = PrinterManager.get_printer_driver_name(printer_name)
            print_log(
                f'dot_matrix using GDI (driver="{driver}") printer="{printer_name}"',
            )
        except Exception:
            pass
        PrinterManager.print_text_gdi(text, printer_name, copies=copies)
    else:
        PrinterManager.print_raw_escp(payload, printer_name, copies=copies)'''
new_block = '''    from core.printer_manager import PrinterError, PrinterManager

    printer = PrinterManager.resolve_dot_matrix_printer(printer_name)
    driver = PrinterManager.get_printer_driver_name(printer)
    errors: list[str] = []

    def _log(msg: str, level: str = "INFO") -> None:
        try:
            from core.print_log import print_log
            print_log(msg, level=level)
        except Exception:
            pass

    _log(f'dot_matrix target="{printer}" driver="{driver}"')

    if not PrinterManager.should_use_dot_matrix_gdi(printer):
        try:
            _log(f'dot_matrix trying RAW ESC/P on "{printer}"')
            PrinterManager.print_raw_escp(payload, printer, copies=copies)
            return
        except PrinterError as exc:
            errors.append(f"RAW ESC/P: {exc}")
            _log(f"RAW failed: {exc}", level="WARN")

    try:
        _log(f'dot_matrix trying GDI text on "{printer}"')
        PrinterManager.print_text_gdi(text, printer, copies=copies)
        return
    except PrinterError as exc:
        errors.append(f"GDI text: {exc}")
        _log(f"GDI failed: {exc}", level="WARN")

    if PrinterManager.should_use_dot_matrix_gdi(printer):
        try:
            _log(f'dot_matrix trying RAW fallback on "{printer}"')
            PrinterManager.print_raw_escp(payload, printer, copies=copies)
            return
        except PrinterError as exc:
            errors.append(f"RAW fallback: {exc}")

    hint = (
        "Use printer queue \"EPSON LX-310 ESC/P\" (not the Class Driver queue). "
        "Settings -> Printer Setup -> Print Sales 1 = EPSON LX-310 ESC/P."
    )
    raise DotMatrixPrintError(
        "Dot matrix print failed on all methods:\\n- " + "\\n- ".join(errors) + f"\\n\\n{hint}"
    )'''
if old_block in dt:
    dt = dt.replace(old_block, new_block)
    p.write_text(dt, encoding="utf-8", newline="\n")
    print("patched dot_matrix_print")
else:
    print("WARN dot_matrix block missing")

# update dev printer settings
import json
cfg_path = ROOT / "config" / "printer_settings.json"
cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
cfg["printer_type"] = "dot_matrix"
cfg["print_slot_1_printer"] = "EPSON LX-310 ESC/P"
cfg["print_slot_2_printer"] = "EPSON LX-310 ESC/P"
cfg["dot_matrix_print_method"] = "auto"
cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
print("updated printer_settings.json")