"""
core/app_setup.py
─────────────────
Window creation, font configuration, theme loading/saving, icon setup.
Called once from VeterinaryManagementSystem.__init__.
No UI pages, no DB code here.
"""
import os
import sys

from core.app_prefs import (
    AVAILABLE_THEMES,
    load_app_mode,
    load_theme,
    save_app_mode,
    save_theme,
)

try:
    import ttkbootstrap as tttk_bootstrap_check  # noqa: F401
    TTKBOOTSTRAP_AVAILABLE = True
except ImportError:
    TTKBOOTSTRAP_AVAILABLE = False

from core.font_config import *


def _tk():
    import tkinter as tk
    return tk


def _ttk():
    if TTKBOOTSTRAP_AVAILABLE:
        import ttkbootstrap as ttk
        return ttk
    from tkinter import ttk
    return ttk


# ── Window creation ───────────────────────────────────────────────────────────

def _patch_ttkbootstrap_return_binding(root) -> None:
    """
    ttkbootstrap invokes the default button on Enter globally. If a dialog was
    closed, the target widget may already be destroyed → KeyError / TclError.
    """
    tk = _tk()
    ttk = _ttk()

    def _safe_return(event):
        try:
            w = root.nametowidget(event.widget)
            if w.winfo_exists():
                w.invoke()
        except (KeyError, tk.TclError):
            pass

    try:
        root.unbind_class('TButton', '<Key-Return>')
        root.unbind_class('TButton', '<KP_Enter>')
    except Exception:
        pass
    root.bind_class('TButton', '<Key-Return>', _safe_return, add='+')
    root.bind_class('TButton', '<KP_Enter>', _safe_return, add='+')


def create_window(theme: str):
    """Create and return the root window with fonts applied."""
    tk = _tk()
    ttk = _ttk()
    try:
        if TTKBOOTSTRAP_AVAILABLE:
            import warnings
            warnings.filterwarnings("ignore", category=UserWarning)
            # Register custom themes before creating the window
            from core.custom_themes import register_custom_themes
            register_custom_themes()
            root = ttk.Window(themename=theme)
            _patch_ttkbootstrap_return_binding(root)
            _setup_fonts(root)
            from core.alert_colors import apply_alert_colors_to_theme
            apply_alert_colors_to_theme()
            _apply_native_widget_theme(root)
            root.after(100, lambda: _refresh_fonts(root))
        else:
            root = tk.Tk()
            _setup_fallback_fonts(root)
    except Exception:
        root = tk.Tk()
        _setup_fallback_fonts(root)
    return root


def _apply_native_widget_theme(root):
    """Apply ttkbootstrap theme colors to native tk widgets (Menu, Listbox, Canvas)."""
    ttk = _ttk()
    try:
        style = ttk.Style()
        colors = style.colors
        bg     = colors.bg
        fg     = colors.fg
        selbg  = colors.selectbg
        selfg  = colors.selectfg
        inputbg = getattr(colors, 'inputbg', bg)
        inputfg = getattr(colors, 'inputfg', fg)
        border  = getattr(colors, 'border',  selbg)

        # tk.Menu
        root.option_add('*Menu.background',       bg)
        root.option_add('*Menu.foreground',       fg)
        root.option_add('*Menu.activeBackground', selbg)
        root.option_add('*Menu.activeForeground', selfg)
        root.option_add('*Menu.relief',           'flat')
        root.option_add('*Menu.borderWidth',      '1')

        # tk.Listbox
        root.option_add('*Listbox.background',       inputbg)
        root.option_add('*Listbox.foreground',       inputfg)
        root.option_add('*Listbox.selectBackground', selbg)
        root.option_add('*Listbox.selectForeground', selfg)
        root.option_add('*Listbox.relief',           'flat')
        root.option_add('*Listbox.borderWidth',      '1')
        root.option_add('*Listbox.highlightThickness','1')
        root.option_add('*Listbox.highlightColor',   border)

        # tk.Canvas (used by scrollable frames)
        root.option_add('*Canvas.background', bg)
        root.option_add('*Canvas.highlightThickness', '0')

        # tk.Text
        root.option_add('*Text.background',       inputbg)
        root.option_add('*Text.foreground',       inputfg)
        root.option_add('*Text.insertBackground', fg)
        root.option_add('*Text.selectBackground', selbg)
        root.option_add('*Text.selectForeground', selfg)
        root.option_add('*Text.relief',           'flat')
        root.option_add('*Text.borderWidth',      '1')
        root.option_add('*Text.highlightThickness','1')
        root.option_add('*Text.highlightColor',   border)
    except Exception:
        pass


def _setup_fonts(root):
    if not TTKBOOTSTRAP_AVAILABLE:
        return
    ttk = _ttk()
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
        from core.window_icon import apply_main_window_icon
        apply_main_window_icon(root)
    except Exception:
        pass


# ── Restart helper ────────────────────────────────────────────────────────────

def restart_app(root=None):
    """Restart the app. Frozen EXE must exit immediately so the child can extract."""
    if getattr(sys, 'frozen', False):
        from core.frozen_bootstrap import relaunch_executable
        relaunch_executable(root)
        return

    import subprocess
    args = [sys.executable, os.path.abspath(sys.argv[0])] + sys.argv[1:]
    if root is not None:
        try:
            root._style = type('_S', (), {'instance': None})()
        except Exception:
            pass
        try:
            root.quit()
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass
    try:
        subprocess.Popen(args)
    except Exception:
        pass
