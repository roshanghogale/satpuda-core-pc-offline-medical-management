"""Native Windows print dialog via comdlg32.PrintDlg (Win7–11, all pywin32 versions)."""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

if sys.platform != 'win32':
    raise ImportError('windows_print_dialog is Windows-only')

comdlg32 = ctypes.windll.comdlg32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32
winspool = ctypes.WinDLL('winspool.drv')

PD_RETURNDC = 0x00000100
PD_USEDEVMODECOPIESANDCOLLATE = 0x00040000
PD_NOSELECTION = 0x00000004
PD_NOPAGENUMS = 0x00000008

HORZRES = 8
VERTRES = 10

# DEVMODE fields (wingdi.h) and DocumentProperties modes (winspool.h).
DM_COPIES = 0x00000100
DM_COLOR = 0x00000800
DMCOLOR_MONOCHROME = 1
DM_OUT_BUFFER = 2
DM_IN_BUFFER = 8
GMEM_MOVEABLE = 0x0002
GMEM_ZEROINIT = 0x0040

_IS_64BIT = ctypes.sizeof(ctypes.c_void_p) == 8


class DOCINFOW(ctypes.Structure):
    _fields_ = [
        ('cbSize', ctypes.c_int),
        ('lpszDocName', wintypes.LPCWSTR),
        ('lpszOutput', wintypes.LPCWSTR),
        ('lpszDatatype', wintypes.LPCWSTR),
        ('fwType', wintypes.DWORD),
    ]


class _DEVMODEW_HEAD(ctypes.Structure):
    """DEVMODEW up to dmColor (printer union); the rest is left to the driver.

    Fixed-width types (WCHAR/WORD = 16 bit, DWORD = 32 bit) keep the Windows
    layout on every platform, so the tests can check the offsets.
    """
    _fields_ = [
        ('dmDeviceName', ctypes.c_uint16 * 32),  # WCHAR[32], never read
        ('dmSpecVersion', ctypes.c_uint16),
        ('dmDriverVersion', ctypes.c_uint16),
        ('dmSize', ctypes.c_uint16),
        ('dmDriverExtra', ctypes.c_uint16),
        ('dmFields', ctypes.c_uint32),
        ('dmOrientation', ctypes.c_short),
        ('dmPaperSize', ctypes.c_short),
        ('dmPaperLength', ctypes.c_short),
        ('dmPaperWidth', ctypes.c_short),
        ('dmScale', ctypes.c_short),
        ('dmCopies', ctypes.c_short),
        ('dmDefaultSource', ctypes.c_short),
        ('dmPrintQuality', ctypes.c_short),
        ('dmColor', ctypes.c_short),
    ]


# 64-bit safe ctypes signatures (default argtypes use c_int and overflow on Win64).
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
winspool.GetDefaultPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
winspool.GetDefaultPrinterW.restype = wintypes.BOOL
winspool.OpenPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.HANDLE), wintypes.LPVOID]
winspool.OpenPrinterW.restype = wintypes.BOOL
winspool.ClosePrinter.argtypes = [wintypes.HANDLE]
winspool.ClosePrinter.restype = wintypes.BOOL
winspool.DocumentPropertiesW.argtypes = [
    wintypes.HWND, wintypes.HANDLE, wintypes.LPWSTR, wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD,
]
winspool.DocumentPropertiesW.restype = wintypes.LONG
comdlg32.CommDlgExtendedError.argtypes = []
comdlg32.CommDlgExtendedError.restype = wintypes.DWORD
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.GetDeviceCaps.restype = ctypes.c_int
gdi32.StartDocW.argtypes = [wintypes.HDC, ctypes.POINTER(DOCINFOW)]
gdi32.StartDocW.restype = ctypes.c_int
gdi32.EndDoc.argtypes = [wintypes.HDC]
gdi32.EndDoc.restype = ctypes.c_int
gdi32.StartPage.argtypes = [wintypes.HDC]
gdi32.StartPage.restype = ctypes.c_int
gdi32.EndPage.argtypes = [wintypes.HDC]
gdi32.EndPage.restype = ctypes.c_int
kernel32.GetLastError.argtypes = []
kernel32.GetLastError.restype = wintypes.DWORD

if _IS_64BIT:
    class PRINTDLG(ctypes.Structure):
        _fields_ = [
            ('lStructSize', wintypes.DWORD),
            ('hwndOwner', wintypes.HWND),
            ('hDevMode', wintypes.HGLOBAL),
            ('hDevNames', wintypes.HGLOBAL),
            ('hDC', wintypes.HDC),
            ('Flags', wintypes.DWORD),
            ('nFromPage', wintypes.WORD),
            ('nToPage', wintypes.WORD),
            ('nMinPage', wintypes.WORD),
            ('nMaxPage', wintypes.WORD),
            ('nCopies', wintypes.WORD),
            ('_pad', wintypes.WORD),
            ('hInstance', wintypes.HINSTANCE),
            ('lCustData', wintypes.LPARAM),
            ('lpfnPrintHook', wintypes.LPVOID),
            ('lpfnSetupHook', wintypes.LPVOID),
            ('lpPrintTemplateName', wintypes.LPCWSTR),
            ('lpSetupTemplateName', wintypes.LPCWSTR),
            ('hPrintTemplate', wintypes.HGLOBAL),
            ('hSetupTemplate', wintypes.HGLOBAL),
        ]
else:
    class PRINTDLG(ctypes.Structure):
        _fields_ = [
            ('lStructSize', wintypes.DWORD),
            ('hwndOwner', wintypes.HWND),
            ('hDevMode', wintypes.HGLOBAL),
            ('hDevNames', wintypes.HGLOBAL),
            ('hDC', wintypes.HDC),
            ('Flags', wintypes.DWORD),
            ('nFromPage', wintypes.WORD),
            ('nToPage', wintypes.WORD),
            ('nMinPage', wintypes.WORD),
            ('nMaxPage', wintypes.WORD),
            ('nCopies', wintypes.WORD),
            ('hInstance', wintypes.HINSTANCE),
            ('lCustData', wintypes.LPARAM),
            ('lpfnPrintHook', wintypes.LPVOID),
            ('lpfnSetupHook', wintypes.LPVOID),
            ('lpPrintTemplateName', wintypes.LPCWSTR),
            ('lpSetupTemplateName', wintypes.LPCWSTR),
            ('hPrintTemplate', wintypes.HGLOBAL),
            ('hSetupTemplate', wintypes.HGLOBAL),
        ]


# Fix PrintDlgW argtype to the real struct pointer.
comdlg32.PrintDlgW.argtypes = [ctypes.POINTER(PRINTDLG)]


def _as_handle(value) -> int:
    """Return a Windows handle as a Python int safe for PIL/pywin32."""
    if value is None:
        return 0
    if isinstance(value, ctypes.c_void_p):
        return value.value or 0
    if isinstance(value, int):
        return value
    return int(value)


def _as_hdc(value) -> wintypes.HDC:
    handle = _as_handle(value)
    return wintypes.HDC(handle)


def _as_hglobal(value) -> wintypes.HGLOBAL:
    handle = _as_handle(value)
    return wintypes.HGLOBAL(handle)


def _release_printdlg(pd: PRINTDLG) -> None:
    if pd.hDevMode:
        kernel32.GlobalFree(_as_hglobal(pd.hDevMode))
        pd.hDevMode = None
    if pd.hDevNames:
        kernel32.GlobalFree(_as_hglobal(pd.hDevNames))
        pd.hDevNames = None


def _black_only_print() -> bool:
    try:
        from core.printer_manager import PrinterManager
        return PrinterManager.is_black_only_print()
    except Exception:
        return True


def _default_printer_name() -> str:
    size = wintypes.DWORD(0)
    winspool.GetDefaultPrinterW(None, ctypes.byref(size))
    if not size.value:
        return ''
    buf = ctypes.create_unicode_buffer(size.value)
    if not winspool.GetDefaultPrinterW(buf, ctypes.byref(size)):
        return ''
    return buf.value


def _monochrome_devmode(hwnd_owner: int, copies: int) -> int:
    """
    GlobalAlloc'd DEVMODE of the default printer with colour set to monochrome,
    for PRINTDLG.hDevMode (the same DEVMODE change Sumatra's 'monochrome' makes).
    A preset DEVMODE also carries the starting copies -- PrintDlg ignores
    nCopies then. Returns 0 if it cannot be built; the dialog opens as before.
    """
    name = _default_printer_name()
    if not name:
        return 0
    hprinter = wintypes.HANDLE()
    if not winspool.OpenPrinterW(name, ctypes.byref(hprinter), None):
        return 0
    hmem = 0
    try:
        size = winspool.DocumentPropertiesW(hwnd_owner, hprinter, name, None, None, 0)
        if size < ctypes.sizeof(_DEVMODEW_HEAD):
            return 0
        hmem = _as_handle(kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, size))
        ptr = kernel32.GlobalLock(_as_hglobal(hmem)) if hmem else None
        if not ptr:
            raise OSError('GlobalAlloc failed')
        try:
            if winspool.DocumentPropertiesW(hwnd_owner, hprinter, name, ptr, None, DM_OUT_BUFFER) < 0:
                raise OSError('DocumentProperties failed')
            dm = _DEVMODEW_HEAD.from_address(ptr)
            dm.dmColor = DMCOLOR_MONOCHROME
            dm.dmCopies = copies
            dm.dmFields |= DM_COLOR | DM_COPIES
            # Let the driver fold the change into its private settings too.
            if winspool.DocumentPropertiesW(
                hwnd_owner, hprinter, name, ptr, ptr, DM_IN_BUFFER | DM_OUT_BUFFER,
            ) < 0:
                raise OSError('DocumentProperties failed')
        finally:
            kernel32.GlobalUnlock(_as_hglobal(hmem))
        return hmem
    except Exception:
        if hmem:
            kernel32.GlobalFree(_as_hglobal(hmem))
        return 0
    finally:
        winspool.ClosePrinter(hprinter)


def _new_printdlg(hwnd_owner: int, copies: int, hdevmode: int = 0) -> PRINTDLG:
    pd = PRINTDLG()
    pd.lStructSize = ctypes.sizeof(PRINTDLG)
    pd.hwndOwner = _as_handle(hwnd_owner)
    if hdevmode:
        pd.hDevMode = hdevmode
    pd.Flags = (
        PD_RETURNDC
        | PD_USEDEVMODECOPIESANDCOLLATE
        | PD_NOSELECTION
        | PD_NOPAGENUMS
    )
    pd.nCopies = copies
    return pd


def show_native_print_dialog(
    hwnd_owner: int = 0, copies: int = 1, *, black_only: bool | None = None,
) -> tuple[int, PRINTDLG] | tuple[None, PRINTDLG]:
    """
    Show the standard Windows printer dialog.
    Returns (printer HDC as int, PRINTDLG struct) or (None, struct) if cancelled.

    With 'print_black_only' on (black_only=None reads it), the dialog opens on
    a grayscale DEVMODE for the default printer, so a failed silent print that
    lands here still asks for the black cartridge.
    """
    try:
        copies = max(1, min(99, int(copies or 1)))
    except Exception:
        copies = 1
    if black_only is None:
        black_only = _black_only_print()
    hdevmode = _monochrome_devmode(hwnd_owner, copies) if black_only else 0

    pd = _new_printdlg(hwnd_owner, copies, hdevmode)
    ok = comdlg32.PrintDlgW(ctypes.byref(pd))
    if not ok and hdevmode and comdlg32.CommDlgExtendedError():
        # The dialog refused the preset (e.g. a printer name longer than
        # DEVMODE's 31 characters): open it as before rather than not at all.
        _release_printdlg(pd)
        pd = _new_printdlg(hwnd_owner, copies)
        ok = comdlg32.PrintDlgW(ctypes.byref(pd))
    if not ok:
        _release_printdlg(pd)
        return None, pd

    hdc = _as_handle(pd.hDC)
    return hdc if hdc else None, pd


def release_print_dialog(pd: PRINTDLG) -> None:
    _release_printdlg(pd)


def _gdi_start_doc(hdc: int, doc_name: str) -> tuple[DOCINFOW, ctypes.Array]:
    """Start a GDI print job. Caller must keep the returned name buffer alive."""
    info = DOCINFOW()
    info.cbSize = ctypes.sizeof(DOCINFOW)
    name_buf = ctypes.create_unicode_buffer(doc_name or 'Bill')
    info.lpszDocName = ctypes.cast(name_buf, wintypes.LPCWSTR)
    info.lpszOutput = None
    info.lpszDatatype = None
    info.fwType = 0
    if gdi32.StartDocW(_as_hdc(hdc), ctypes.byref(info)) <= 0:
        err = kernel32.GetLastError()
        raise OSError(f'StartDoc failed (Win32 error {err})')
    return info, name_buf


def print_pdf_with_native_dialog(
    pdf_path: str,
    hwnd_owner: int = 0,
    *,
    copies: int = 1,
    paper_hint: str = "",
) -> bool:
    """
    Open the Windows print dialog and send a PDF to the chosen printer.
    Returns True when finished or cancelled; False only on hard failure.
    """
    import pdfplumber
    from PIL import ImageWin

    path = os.path.abspath(pdf_path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    black_only = _black_only_print()
    hdc, pd = show_native_print_dialog(hwnd_owner, copies=copies, black_only=black_only)
    if not hdc:
        return True

    hdc_t = _as_hdc(hdc)
    doc_info = None
    doc_name_buf = None
    try:
        printable_w = gdi32.GetDeviceCaps(hdc_t, HORZRES)
        printable_h = gdi32.GetDeviceCaps(hdc_t, VERTRES)
        if printable_w <= 0 or printable_h <= 0:
            raise OSError('Printer returned invalid page size')
        doc_info, doc_name_buf = _gdi_start_doc(hdc, os.path.basename(path))
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                if gdi32.StartPage(hdc_t) <= 0:
                    err = kernel32.GetLastError()
                    raise OSError(f'StartPage failed (Win32 error {err})')
                # Grayscale page data too, so the logo carries no colour even if
                # another printer is picked in the dialog.
                pil = page.to_image(resolution=200).original.convert('L' if black_only else 'RGB')
                iw, ih = pil.size
                scale = min(printable_w / iw, printable_h / ih)
                dw, dh = int(iw * scale), int(ih * scale)
                x = (printable_w - dw) // 2
                y = (printable_h - dh) // 2
                ImageWin.Dib(pil).draw(
                    int(hdc),
                    (x, y, x + dw, y + dh),
                )
                if gdi32.EndPage(hdc_t) <= 0:
                    err = kernel32.GetLastError()
                    raise OSError(f'EndPage failed (Win32 error {err})')
        if gdi32.EndDoc(hdc_t) <= 0:
            err = kernel32.GetLastError()
            raise OSError(f'EndDoc failed (Win32 error {err})')
    except Exception:
        try:
            gdi32.AbortDoc(hdc_t)
        except Exception:
            pass
        raise
    finally:
        gdi32.DeleteDC(hdc_t)
        release_print_dialog(pd)
    return True
