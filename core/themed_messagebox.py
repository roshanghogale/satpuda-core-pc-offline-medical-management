"""
core/themed_messagebox.py
─────────────────────────
Drop-in themed replacements for tkinter.messagebox dialogs.
Uses ttkbootstrap styling so dialogs match the active theme.
Falls back to standard tkinter.messagebox if ttkbootstrap is unavailable.
"""
import tkinter as tk
from tkinter import messagebox as _mb

try:
    import ttkbootstrap as ttk
    _BOOT = True
except ImportError:
    from tkinter import ttk as ttk
    _BOOT = False

from core.font_config import FONT_FAMILY, FONT_SIZE_DEFAULT, FONT_SIZE_LABELS


def _get_theme_colors():
    try:
        style = ttk.Style()
        c = style.colors
        return {
            'bg':     c.bg,
            'fg':     c.fg,
            'selbg':  c.selectbg,
            'selfg':  c.selectfg,
            'border': getattr(c, 'border', c.selectbg),
        }
    except Exception:
        return {'bg': None, 'fg': None, 'selbg': None, 'selfg': None, 'border': None}


def _icon_char(kind):
    return {'info': 'ℹ', 'warning': '⚠', 'error': '✖', 'question': '?'}.get(kind, 'ℹ')


def _bootstyle(kind):
    return {'info': 'primary', 'warning': 'warning', 'error': 'danger', 'question': 'primary'}.get(kind, 'primary')


def _show(parent, title, message, kind, buttons):
    """
    Core dialog builder.
    buttons: list of (label, return_value, bootstyle)
    Returns the return_value of the clicked button.
    """
    if not _BOOT:
        # Fallback to native
        if kind == 'info':     return _mb.showinfo(title, message, parent=parent)
        if kind == 'warning':  return _mb.showwarning(title, message, parent=parent)
        if kind == 'error':    return _mb.showerror(title, message, parent=parent)
        if kind == 'question': return _mb.askyesno(title, message, parent=parent)

    result = [None]

    # Find a valid parent window
    try:
        root = parent.winfo_toplevel() if parent else tk._default_root
    except Exception:
        root = tk._default_root

    dlg = tk.Toplevel(root)
    dlg.title(title)
    dlg.resizable(False, False)
    dlg.grab_set()

    # Apply theme background
    colors = _get_theme_colors()
    if colors['bg']:
        dlg.configure(bg=colors['bg'])

    # Apply icon
    try:
        from core.scroll_manager import _apply_icon
        _apply_icon(dlg)
    except Exception:
        pass

    # Icon + message
    top = ttk.Frame(dlg, padding=(20, 16, 20, 8))
    top.pack(fill=tk.X)

    icon_lbl = ttk.Label(top, text=_icon_char(kind),
                         font=(FONT_FAMILY, 22),
                         bootstyle=_bootstyle(kind) if _BOOT else None)
    icon_lbl.pack(side=tk.LEFT, padx=(0, 14), anchor='n')

    msg_frame = ttk.Frame(top)
    msg_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    ttk.Label(msg_frame, text=title,
              font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).pack(anchor='w')
    ttk.Label(msg_frame, text=message,
              font=(FONT_FAMILY, FONT_SIZE_DEFAULT),
              wraplength=380, justify='left').pack(anchor='w', pady=(4, 0))

    ttk.Separator(dlg, orient='horizontal').pack(fill=tk.X, padx=0, pady=(8, 0))

    # Buttons
    bf = ttk.Frame(dlg, padding=(12, 8))
    bf.pack(fill=tk.X)

    def _click(val):
        result[0] = val
        dlg.destroy()

    for i, (label, val, bstyle) in enumerate(buttons):
        try:
            b = ttk.Button(bf, text=label, width=10,
                           bootstyle=bstyle,
                           command=lambda v=val: _click(v))
        except Exception:
            b = ttk.Button(bf, text=label, width=10,
                           command=lambda v=val: _click(v))
        b.pack(side=tk.RIGHT, padx=4)
        if i == 0:
            b.focus_set()
            b.bind('<Return>', lambda e, v=val: _click(v))

    dlg.bind('<Escape>', lambda e: _click(buttons[-1][1]))

    # Centre on parent
    dlg.update_idletasks()
    sw = dlg.winfo_screenwidth()
    sh = dlg.winfo_screenheight()
    w  = dlg.winfo_reqwidth()
    h  = dlg.winfo_reqheight()
    try:
        px = root.winfo_rootx() + root.winfo_width()  // 2 - w // 2
        py = root.winfo_rooty() + root.winfo_height() // 2 - h // 2
    except Exception:
        px, py = (sw - w) // 2, (sh - h) // 2
    dlg.geometry(f"+{max(0, px)}+{max(0, py)}")

    dlg.wait_window()
    return result[0]


# ── Public API (mirrors tkinter.messagebox) ───────────────────────────────────

def showinfo(title, message, parent=None):
    _show(parent, title, message, 'info',
          [('OK', True, 'primary')])

def showwarning(title, message, parent=None):
    _show(parent, title, message, 'warning',
          [('OK', True, 'warning')])

def showerror(title, message, parent=None):
    _show(parent, title, message, 'error',
          [('OK', True, 'danger')])

def askyesno(title, message, parent=None):
    return _show(parent, title, message, 'question',
                 [('Yes', True, 'primary'), ('No', False, 'secondary')])

def askokcancel(title, message, parent=None):
    return _show(parent, title, message, 'question',
                 [('OK', True, 'primary'), ('Cancel', False, 'secondary')])
