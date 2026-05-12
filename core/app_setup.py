"""
core/app_setup.py
─────────────────
Window creation, font configuration, theme loading/saving, icon setup.
Called once from VeterinaryManagementSystem.__init__.
No UI pages, no DB code here.
"""
import os
import sys

try:
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import *
    TTKBOOTSTRAP_AVAILABLE = True
except ImportError:
    import tkinter.ttk as ttk
    TTKBOOTSTRAP_AVAILABLE = False

import tkinter as tk
from core.font_config import *


# ── Theme ─────────────────────────────────────────────────────────────────────

AVAILABLE_THEMES = {
    # ── Dark themes ───────────────────────────────────────────────────────
    'superhero':  'Dark Blue',
    'darkly':     'Dark Gray',
    'cyborg':     'Dark Cyan',
    'solar':      'Dark Orange',
    'vapor':      'Dark Purple',
    'slate':      'Dark Slate',
    # ── Light themes ──────────────────────────────────────────────────────
    'cosmo':      'Light Blue',
    'minty':      'Light Green',
    'flatly':     'Light Flat',
    'journal':    'Light Classic',
    'sandstone':  'Light Warm',
    'litera':     'Light Clean',
    'lumen':      'Light Lumen',
    'pulse':      'Light Purple',
    'united':     'Light United',
    'yeti':       'Light Yeti',
}


def _theme_config_path() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.join(
            os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
            'VeterinaryApp', 'theme_config.txt')
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        '..', 'config', 'theme_config.txt')


def load_theme() -> str:
    try:
        path = _theme_config_path()
        if os.path.exists(path):
            t = open(path).read().strip()
            if t in AVAILABLE_THEMES:
                return t
    except Exception:
        pass
    return 'superhero'


def save_theme(theme: str):
    try:
        with open(_theme_config_path(), 'w') as f:
            f.write(theme)
    except Exception:
        pass


# ── Window creation ───────────────────────────────────────────────────────────

def create_window(theme: str):
    """Create and return the root window with fonts applied."""
    try:
        if TTKBOOTSTRAP_AVAILABLE:
            import warnings
            warnings.filterwarnings("ignore", category=UserWarning)
            root = ttk.Window(themename=theme)
            _setup_fonts(root)
            from core.alert_colors import apply_alert_colors_to_theme
            apply_alert_colors_to_theme()
            root.after(100, lambda: _refresh_fonts(root))
        else:
            root = tk.Tk()
            _setup_fallback_fonts(root)
    except Exception:
        root = tk.Tk()
        _setup_fallback_fonts(root)
    return root


def _setup_fonts(root):
    if not TTKBOOTSTRAP_AVAILABLE:
        return
    try:
        root.option_add('*TCombobox*Listbox.Font', (FONT_FAMILY, FONT_SIZE_DROPDOWNS))
        root.option_add('*Font', (FONT_FAMILY, FONT_SIZE_DEFAULT))
        style = ttk.Style()
        style.configure('Large.TButton',  font=(FONT_FAMILY, FONT_SIZE_BUTTONS, 'bold'), padding=(8, 6))
        style.configure('Nav.TButton',    font=(FONT_FAMILY, FONT_SIZE_NAV_BUTTONS, 'bold'), padding=(10, 8))
        style.configure('TButton',        font=(FONT_FAMILY, FONT_SIZE_BUTTONS))
        style.configure('Large.TLabel',   font=(FONT_FAMILY, FONT_SIZE_LABELS))
        style.configure('Heading.TLabel', font=(FONT_FAMILY, FONT_SIZE_SECTION_TITLE, 'bold'))
        style.configure('Large.TLabelframe',       font=(FONT_FAMILY, FONT_SIZE_SECTION_TITLE, 'bold'))
        style.configure('Large.TLabelframe.Label', font=(FONT_FAMILY, FONT_SIZE_SECTION_TITLE, 'bold'))
        style.configure('TLabelframe.Label',       font=(FONT_FAMILY, FONT_SIZE_SECTION_TITLE, 'bold'))
        style.configure('Large.TEntry',   font=(FONT_FAMILY, FONT_SIZE_ENTRIES), padding=(6, 4))
        style.configure('TEntry',         font=(FONT_FAMILY, FONT_SIZE_ENTRIES))
        style.configure('Large.TCombobox',font=(FONT_FAMILY, FONT_SIZE_DROPDOWNS), padding=(6, 4))
        style.configure('TCombobox',      font=(FONT_FAMILY, FONT_SIZE_DROPDOWNS))
        screen_h = root.winfo_screenheight()
        row_h = min(max(FONT_SIZE_TABLES + 16, 28), min(screen_h // 25, 50))
        style.configure('Large.Treeview',         font=(FONT_FAMILY, FONT_SIZE_TABLES), rowheight=row_h)
        style.configure('Large.Treeview.Heading', font=(FONT_FAMILY, FONT_SIZE_TABLE_HEADERS, 'bold'))
        style.configure('Treeview',               font=(FONT_FAMILY, FONT_SIZE_TABLES), rowheight=row_h)
        style.configure('Treeview.Heading',       font=(FONT_FAMILY, FONT_SIZE_TABLE_HEADERS, 'bold'))
        style.configure('TNotebook.Tab', font=(FONT_FAMILY, FONT_SIZE_BUTTONS))
        style.configure('TSpinbox',      font=(FONT_FAMILY, FONT_SIZE_ENTRIES))
        style.configure('Large.TFrame',  padding=(8, 8))
        root.update_idletasks()
    except Exception as e:
        print(f"Font setup warning (non-fatal): {e}")
        _setup_fallback_fonts(root)


def _setup_fallback_fonts(root):
    root.option_add('*Font',        (FONT_FAMILY, FONT_SIZE_DEFAULT))
    root.option_add('*Button.Font', (FONT_FAMILY, FONT_SIZE_BUTTONS, 'bold'))
    root.option_add('*Label.Font',  (FONT_FAMILY, FONT_SIZE_LABELS))
    root.option_add('*Entry.Font',  (FONT_FAMILY, FONT_SIZE_ENTRIES))
    root.option_add('*Listbox.Font',(FONT_FAMILY, FONT_SIZE_TABLES))


def _refresh_fonts(root):
    try:
        from core.font_updater import update_all_fonts
        update_all_fonts(root)
    except Exception:
        pass


# ── Icon ──────────────────────────────────────────────────────────────────────

def set_window_icon(root):
    try:
        base = sys._MEIPASS if getattr(sys, 'frozen', False) \
               else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ico = os.path.join(base, 'assets', 'satpuda_logo.ico')
        png = os.path.join(base, 'assets', 'satpuda_logo.png')
        if os.path.exists(ico):
            root.iconbitmap(default=ico)
        if os.path.exists(png):
            root._taskbar_icon = tk.PhotoImage(file=png)
            root.wm_iconphoto(True, root._taskbar_icon)
    except Exception:
        pass


# ── Restart helper ────────────────────────────────────────────────────────────

def restart_app(root=None):
    import subprocess
    if getattr(sys, 'frozen', False):
        args = [sys.executable]
    else:
        args = [sys.executable, os.path.abspath(sys.argv[0])] + sys.argv[1:]
    if root is not None:
        try: root.quit()
        except Exception: pass
        try: root.destroy()
        except Exception: pass
    try:
        subprocess.Popen(args)
    except Exception:
        pass
