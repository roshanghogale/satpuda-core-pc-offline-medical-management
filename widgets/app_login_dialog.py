"""Modal app login gate shown on startup when App Login is enabled."""
from __future__ import annotations

import tkinter as tk

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.font_config import FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT, FONT_SIZE_DEFAULT
from core.scroll_manager import open_dialog
from core.themed_messagebox import showerror


def show_app_login_dialog(parent) -> bool:
    """Ask for username/password. Returns True on success, False if cancelled."""
    from core.login_prefs import load_login_prefs, verify_login

    prefs = load_login_prefs()
    dlg = open_dialog(parent, "Login", width=400, height=260, resizable=False)
    body = dlg.content

    ttk.Label(
        body,
        text="Enter your username and password to open Satpuda Core.",
        font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        wraplength=360,
        justify=tk.LEFT,
    ).pack(anchor=tk.W, padx=16, pady=(16, 10))

    ttk.Label(body, text="Username", font=(FONT_FAMILY, FONT_SIZE_DEFAULT)).pack(
        anchor=tk.W, padx=16,
    )
    user_var = tk.StringVar(value=prefs.get('username') or '')
    user_e = ttk.Entry(body, textvariable=user_var, width=36)
    user_e.pack(padx=16, pady=(2, 8), fill=tk.X)

    ttk.Label(body, text="Password", font=(FONT_FAMILY, FONT_SIZE_DEFAULT)).pack(
        anchor=tk.W, padx=16,
    )
    pass_var = tk.StringVar()
    pass_e = ttk.Entry(body, textvariable=pass_var, show='*', width=36)
    pass_e.pack(padx=16, pady=(2, 8), fill=tk.X)

    result = {'ok': False}

    def _submit():
        u = user_var.get().strip()
        p = pass_var.get()
        if not u or not p:
            showerror("Login", "Username and password are required.", parent=dlg)
            return
        if not verify_login(u, p):
            showerror("Login", "Invalid username or password.", parent=dlg)
            pass_var.set('')
            pass_e.focus_set()
            return
        result['ok'] = True
        dlg.destroy()

    def _cancel():
        result['ok'] = False
        dlg.destroy()

    user_e.bind('<Return>', lambda e: pass_e.focus_set())
    pass_e.bind('<Return>', lambda e: _submit())

    ttk.Button(dlg.footer, text="Login", command=_submit).pack(side=tk.LEFT, padx=6)
    ttk.Button(dlg.footer, text="Exit", command=_cancel).pack(side=tk.LEFT, padx=6)

    try:
        if user_var.get().strip():
            pass_e.focus_set()
        else:
            user_e.focus_set()
    except Exception:
        pass

    dlg.wait_window()
    return bool(result['ok'])
