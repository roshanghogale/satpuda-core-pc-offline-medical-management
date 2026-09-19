from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
pm = ROOT / "core" / "printer_manager.py"
text = pm.read_text(encoding="utf-8")
if "def get_printer_driver_name" not in text:
    insert = '''
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
        markers = (
            "class driver",
            "9pin v4",
            "esc/p 9pin",
            "generic / text",
            "text only",
        )
        return any(m in driver for m in markers)

    @classmethod
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
            import win32print
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

        left = 150
        top = 150
        line_height = 220
        for copy_idx in range(copies):
            dc = win32ui.CreateDC()
            try:
                dc.CreatePrinterDC(printer)
                dc.SetMapMode(win32con.MM_TWIPS)
                dc.StartDoc(f"Satpuda Bill {copy_idx + 1}")
                dc.StartPage()
                font_normal = win32ui.CreateFont({
                    "name": "Courier New",
                    "height": 180,
                    "weight": win32con.FW_NORMAL,
                })
                font_bold = win32ui.CreateFont({
                    "name": "Courier New",
                    "height": 180,
                    "weight": win32con.FW_BOLD,
                })
                bold = False
                y = top
                for line in text.splitlines():
                    if line == "---BOLD---":
                        bold = True
                        continue
                    if line == "---NOBOLD---":
                        bold = False
                        continue
                    dc.SelectObject(font_bold if bold else font_normal)
                    dc.TextOut(left, y, line[:80])
                    y += line_height
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
    text = text.replace(
        "    @classmethod\n    def get_printer_for_slot(cls, slot: int) -> str:",
        insert + "\n    @classmethod\n    def get_printer_for_slot(cls, slot: int) -> str:",
        1,
    )
    pm.write_text(text, encoding="utf-8", newline="\n")
    print("patched printer_manager")

# patch dot_matrix_print.py
p = ROOT / "core" / "dot_matrix_print.py"
raw = p.read_bytes()
text = raw.decode("utf-8-sig") if raw.count(b"\x00") < len(raw)//4 else raw.decode("utf-16-le")
old = '''    from core.printer_manager import PrinterManager

    PrinterManager.print_raw_escp(payload, printer_name, copies=copies)'''
new = '''    from core.printer_manager import PrinterManager

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
if old in text:
    text = text.replace(old, new)
else:
    print("WARN: dot_matrix block not found")
p.write_text(text, encoding="utf-8", newline="\n")
print("patched dot_matrix_print nulls", p.read_bytes().count(b"\x00"))

# default setting key
pm2 = pm.read_text(encoding="utf-8")
if "dot_matrix_print_method" not in pm2:
    pm2 = pm2.replace(
        "'printer_type': PRINTER_TYPE_STANDARD,\n}",
        "'printer_type': PRINTER_TYPE_STANDARD,\n    'dot_matrix_print_method': 'auto',\n}",
        1,
    )
    pm.write_text(pm2, encoding="utf-8", newline="\n")
    print("added dot_matrix_print_method default")