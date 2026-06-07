"""
Window and Windows taskbar icon helpers (dev + PyInstaller EXE).
"""
from __future__ import annotations

import os
import shutil
import sys

_APP_USER_MODEL_ID = 'SatpudaMedical.SatpudaCore.1'
_icon_cache_path: str | None = None


def init_process_app_id() -> None:
    """Call before the first Tk()/Window(); safe to call multiple times."""
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_APP_USER_MODEL_ID)
    except Exception:
        pass


def _assets_dir() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, 'assets')
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets')


def _appdata_icon_path() -> str:
    base = os.path.join(
        os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
        'VeterinaryApp',
    )
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, 'satpuda_logo.ico')


def _ensure_cached_ico() -> str:
    """Copy bundled .ico to AppData so iconbitmap always has a stable path."""
    global _icon_cache_path
    if _icon_cache_path and os.path.isfile(_icon_cache_path):
        return _icon_cache_path

    dst = _appdata_icon_path()
    src = os.path.join(_assets_dir(), 'satpuda_logo.ico')
    try:
        if os.path.isfile(src):
            if not os.path.isfile(dst) or os.path.getmtime(src) > os.path.getmtime(dst):
                shutil.copy2(src, dst)
            _icon_cache_path = dst
            return dst
    except Exception:
        pass

    if os.path.isfile(src):
        _icon_cache_path = src
        return src
    if os.path.isfile(dst):
        _icon_cache_path = dst
        return dst
    return ''


def get_icon_paths() -> tuple[str, str]:
    base = _assets_dir()
    ico = _ensure_cached_ico() or os.path.join(base, 'satpuda_logo.ico')
    return (ico, os.path.join(base, 'satpuda_logo.png'))


def get_icon_path() -> str:
    return get_icon_paths()[0]


def _set_iconbitmap(window, ico_path: str, default: bool = False) -> None:
    if not ico_path or not os.path.isfile(ico_path):
        return
    if default:
        window.iconbitmap(default=ico_path)
    else:
        window.iconbitmap(ico_path)


def apply_window_icon(window, master=None, *, is_root: bool = False) -> None:
    """Apply Satpuda icon (title bar; root also drives taskbar on Windows)."""
    import tkinter as tk

    init_process_app_id()
    ico, png = get_icon_paths()
    holder = master if master is not None else window

    if is_root and sys.platform == 'win32' and getattr(sys, 'frozen', False):
        try:
            _set_iconbitmap(window, sys.executable, default=True)
        except Exception:
            pass

    try:
        _set_iconbitmap(window, ico, default=is_root)
    except Exception:
        try:
            _set_iconbitmap(window, ico, default=False)
        except Exception:
            pass

    try:
        if os.path.isfile(png):
            img = tk.PhotoImage(file=png)
            holder._satpuda_icon_img = img
            window.wm_iconphoto(True, img)
    except Exception:
        pass


def apply_main_window_icon(root) -> None:
    """Main application window — call after create and again after zoom/maximize."""
    apply_window_icon(root, master=root, is_root=True)
    try:
        root.after(250, lambda: apply_window_icon(root, master=root, is_root=True))
        root.after(1000, lambda: apply_window_icon(root, master=root, is_root=True))
    except Exception:
        pass


def prepare_modal_toplevel(window, parent=None) -> None:
    """
    Prepare a modal Toplevel — transient only (safe for EXE + maximized main window).

    Do NOT use WS_EX_TOOLWINDOW here; it makes windows invisible in some PyInstaller builds.
    """
    if parent is None:
        return
    try:
        window.transient(parent.winfo_toplevel())
    except Exception:
        try:
            window.transient(parent)
        except Exception:
            pass


def show_modal_toplevel(window, parent=None) -> None:
    """Make a built Toplevel visible, centered, and modal."""
    if not callable(getattr(window, '_dialog_escape_close', None)):
        try:
            from core.dialog_escape import bind_escape_to_close
            bind_escape_to_close(window)
        except Exception:
            pass
    prepare_modal_toplevel(window, parent)
    window.update_idletasks()
    try:
        window.deiconify()
    except Exception:
        pass
    try:
        window.lift()
    except Exception:
        pass
    try:
        window.attributes('-topmost', True)
        window.update_idletasks()
        window.attributes('-topmost', False)
    except Exception:
        pass
    try:
        window.focus_force()
    except Exception:
        pass
    try:
        window.grab_set()
    except Exception:
        pass


def get_screen_work_area(window=None) -> tuple[int, int, int, int]:
    """
    Return (max_width, max_height, screen_width, screen_height) for dialog sizing.
    Leaves margin so title bar and taskbar stay visible.
    """
    try:
        if window is not None:
            window.update_idletasks()
            sw = window.winfo_screenwidth()
            sh = window.winfo_screenheight()
        else:
            import tkinter as tk
            r = tk.Tk()
            r.withdraw()
            sw = r.winfo_screenwidth()
            sh = r.winfo_screenheight()
            r.destroy()
    except Exception:
        sw, sh = 1366, 768
    margin = 32
    max_w = max(300, int(sw * 0.92))
    max_h = max(180, int(sh * 0.88) - margin)
    return max_w, max_h, sw, sh


def center_window_on_screen(window, width=None, height=None) -> None:
    """Center on screen; never larger than the visible work area."""
    window.update_idletasks()
    max_w, max_h, sw, sh = get_screen_work_area(window)
    req_w = max(window.winfo_reqwidth(), 280)
    req_h = max(window.winfo_reqheight(), 120)
    w = min(width if width else req_w, max_w)
    h = min(height if height else req_h, max_h)
    w = max(w, min(280, max_w))
    h = max(h, min(120, max_h))
    x = max(0, (sw - w) // 2)
    y = max(0, min((sh - h) // 2, sh - h - 8))
    window.geometry(f"{w}x{h}+{x}+{y}")
