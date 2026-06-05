"""
core/themed_messagebox.py
─────────────────────────
Drop-in themed replacements for tkinter.messagebox dialogs.
Content scrolls when long; buttons stay fixed and visible; size fits on screen.
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
    return {'info': 'i', 'warning': '!', 'error': 'X', 'question': '?'}.get(kind, 'i')


def _bootstyle(kind):
    return {'info': 'primary', 'warning': 'warning', 'error': 'danger', 'question': 'primary'}.get(kind, 'primary')


def _resolve_root(parent):
    try:
        if parent is not None:
            return parent.winfo_toplevel()
    except Exception:
        pass
    return tk._default_root


def _show(parent, title, message, kind, buttons):
    if not _BOOT:
        if kind == 'info':
            return _mb.showinfo(title, message, parent=parent)
        if kind == 'warning':
            return _mb.showwarning(title, message, parent=parent)
        if kind == 'error':
            return _mb.showerror(title, message, parent=parent)
        if kind == 'question':
            return _mb.askyesno(title, message, parent=parent)

    result = [None]
    root = _resolve_root(parent)

    dlg = tk.Toplevel(root)
    dlg.title(title)
    dlg.resizable(False, False)
    dlg.grid_rowconfigure(0, weight=1)
    dlg.grid_rowconfigure(1, weight=0)
    dlg.grid_columnconfigure(0, weight=1)

    colors = _get_theme_colors()
    if colors['bg']:
        dlg.configure(bg=colors['bg'])

    try:
        from core.window_icon import apply_window_icon
        apply_window_icon(dlg, master=root, is_root=False)
    except Exception:
        pass

    from core.window_icon import get_screen_work_area
    from core.scroll_manager import make_dialog_scrollable, finalize_dialog_geometry

    max_w, max_h, sw, sh = get_screen_work_area(dlg)
    wrap = min(420, max(280, int(sw * 0.45)))

    body_wrap = ttk.Frame(dlg, padding=0)
    body_wrap.grid(row=0, column=0, sticky='nsew')
    body_wrap.grid_rowconfigure(0, weight=1)
    body_wrap.grid_columnconfigure(0, weight=1)
    scroll_body = make_dialog_scrollable(body_wrap)

    top = ttk.Frame(scroll_body, padding=(20, 16, 20, 8))
    top.pack(fill=tk.BOTH, expand=True)

    ttk.Label(
        top, text=_icon_char(kind),
        font=(FONT_FAMILY, 22),
        bootstyle=_bootstyle(kind),
    ).pack(side=tk.LEFT, padx=(0, 14), anchor='n')

    msg_frame = ttk.Frame(top)
    msg_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    ttk.Label(
        msg_frame, text=title,
        font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold'),
    ).pack(anchor='w')
    ttk.Label(
        msg_frame, text=message,
        font=(FONT_FAMILY, FONT_SIZE_DEFAULT),
        wraplength=wrap, justify='left',
    ).pack(anchor='w', pady=(4, 0))

    footer_shell = ttk.Frame(dlg)
    footer_shell.grid(row=1, column=0, sticky='ew')
    ttk.Separator(footer_shell, orient='horizontal').pack(fill=tk.X)
    bf = ttk.Frame(footer_shell, padding=(12, 8))
    bf.pack(fill=tk.X)

    def _click(val):
        result[0] = val
        try:
            dlg.grab_release()
        except Exception:
            pass
        dlg.destroy()

    for i, (label, val, bstyle) in enumerate(buttons):
        try:
            b = ttk.Button(
                bf, text=label, width=10,
                bootstyle=bstyle,
                command=lambda v=val: _click(v),
            )
        except Exception:
            b = ttk.Button(bf, text=label, width=10, command=lambda v=val: _click(v))
        b.pack(side=tk.RIGHT, padx=4)
        if i == 0:
            b.focus_set()
            b.bind('<Return>', lambda e, v=val: _click(v))

    dlg.bind('<Escape>', lambda e: _click(buttons[-1][1]))

    def _present():
        finalize_dialog_geometry(dlg, width=min(400, max_w), height=None, resizable=False)
        try:
            from core.window_icon import show_modal_toplevel
            show_modal_toplevel(dlg, root)
        except Exception:
            try:
                dlg.transient(root)
                dlg.grab_set()
            except Exception:
                pass

    dlg.after(1, _present)
    dlg.wait_window()
    return result[0]


def showinfo(title, message, parent=None):
    return _show(parent, title, message, 'info', [('OK', True, 'primary')])


def showwarning(title, message, parent=None):
    return _show(parent, title, message, 'warning', [('OK', True, 'warning')])


def showerror(title, message, parent=None):
    return _show(parent, title, message, 'error', [('OK', True, 'danger')])


def askyesno(title, message, parent=None):
    return _show(
        parent, title, message, 'question',
        [('Yes', True, 'primary'), ('No', False, 'secondary')],
    )


def askokcancel(title, message, parent=None):
    return _show(
        parent, title, message, 'question',
        [('OK', True, 'primary'), ('Cancel', False, 'secondary')],
    )
