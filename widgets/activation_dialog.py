"""
Activation dialog — shown only on first run on a new device.
3-factor login: Username + Password + Device Key
No hints or paths are shown to the user.
"""

import tkinter as tk
from tkinter import messagebox


def show_activation_dialog(on_success_callback):
    from core.license_manager import attempt_activation

    root = tk.Tk()
    root.title("Satpuda Core — Activation")
    root.state('zoomed')
    root.resizable(True, True)
    root.configure(bg='#0d1117')

    # Set window icon
    try:
        from core.license_manager import get_icon_path
        import os
        ico = get_icon_path()
        if os.path.exists(ico):
            root.iconbitmap(ico)
    except Exception:
        pass

    # ── Center card ────────────────────────────────────────────────────────────
    outer = tk.Frame(root, bg='#0d1117')
    outer.place(relx=0.5, rely=0.5, anchor='center')

    card = tk.Frame(outer, bg='#161b22', bd=0,
                    highlightbackground='#30363d', highlightthickness=1)
    card.pack()

    # ── Top accent bar ─────────────────────────────────────────────────────────
    tk.Frame(card, bg='#1f6feb', height=6).pack(fill=tk.X)

    tk.Label(card, text="\U0001f512", font=('Segoe UI', 36),
             bg='#161b22', fg='#58a6ff').pack(pady=(28, 4))

    tk.Label(card, text="Software Activation Required",
             font=('Segoe UI', 16, 'bold'),
             bg='#161b22', fg='#e6edf3').pack()

    tk.Label(card,
             text="This device is not activated.\nContact the developer to activate this software.",
             font=('Segoe UI', 10), bg='#161b22', fg='#8b949e',
             justify='center').pack(pady=(6, 4))

    # ── Contact info ───────────────────────────────────────────────────────────
    contact_frame = tk.Frame(card, bg='#1c2128',
                             highlightbackground='#30363d', highlightthickness=1)
    contact_frame.pack(padx=48, pady=(0, 16), fill=tk.X)

    tk.Label(contact_frame, text="Developer Contact",
             font=('Segoe UI', 9, 'bold'), bg='#1c2128', fg='#58a6ff').pack(pady=(8, 4))

    for icon, text in [
        ('📞', 'Roshan Bonde  —  +91 98765 43210'),
        ('✉', 'roshanb.dev@gmail.com'),
        ('💬', 'WhatsApp: +91 98765 43210'),
    ]:
        row = tk.Frame(contact_frame, bg='#1c2128')
        row.pack(anchor='center', pady=1)
        tk.Label(row, text=icon, font=('Segoe UI', 10),
                 bg='#1c2128', fg='#8b949e').pack(side=tk.LEFT, padx=(0, 6))
        tk.Label(row, text=text, font=('Segoe UI', 9),
                 bg='#1c2128', fg='#c9d1d9').pack(side=tk.LEFT)

    tk.Frame(contact_frame, bg='#1c2128', height=8).pack()

    # ── Form ───────────────────────────────────────────────────────────────────
    form = tk.Frame(card, bg='#161b22')
    form.pack(padx=48, pady=(0, 8))

    def make_field(label_text, show=None):
        tk.Label(form, text=label_text, font=('Segoe UI', 10, 'bold'),
                 bg='#161b22', fg='#8b949e', anchor='w').pack(fill=tk.X, pady=(10, 2))
        e = tk.Entry(form, font=('Segoe UI', 11), show=show,
                     bg='#21262d', fg='#e6edf3', insertbackground='#58a6ff',
                     relief='flat', bd=0,
                     highlightbackground='#30363d', highlightthickness=1,
                     width=38)
        e.pack(fill=tk.X, ipady=8)
        return e

    username_entry = make_field("Username")
    password_entry = make_field("Password", show='\u25cf')
    key_entry      = make_field("Device Key")

    # ── Error label ────────────────────────────────────────────────────────────
    error_var = tk.StringVar()
    tk.Label(card, textvariable=error_var,
             font=('Segoe UI', 10), bg='#161b22', fg='#f85149',
             wraplength=420).pack(pady=(8, 0))

    # ── Activate button ────────────────────────────────────────────────────────
    def do_activate():
        error_var.set('')
        u = username_entry.get().strip()
        p = password_entry.get().strip()
        k = key_entry.get().strip()
        if not u or not p or not k:
            error_var.set("\u26a0  All three fields are required.")
            return
        ok, msg = attempt_activation(u, p, k)
        if ok:
            root.destroy()
            on_success_callback()
        else:
            error_var.set(f"\u2716  {msg}")
            _shake(root, card)

    btn_frame = tk.Frame(card, bg='#161b22')
    btn_frame.pack(pady=(16, 32), padx=48, fill=tk.X)

    tk.Button(btn_frame, text="Activate Software",
              font=('Segoe UI', 11, 'bold'),
              bg='#1f6feb', fg='white',
              activebackground='#388bfd', activeforeground='white',
              relief='flat', bd=0, cursor='hand2', pady=10,
              command=do_activate).pack(fill=tk.X)

    # ── Footer ─────────────────────────────────────────────────────────────────
    tk.Label(card,
             text="Satpuda Core  •  Billing. Management. Simplified.",
             font=('Segoe UI', 8), bg='#161b22', fg='#484f58').pack(pady=(0, 16))

    # ── Bindings ───────────────────────────────────────────────────────────────
    username_entry.bind('<Return>', lambda e: password_entry.focus())
    password_entry.bind('<Return>', lambda e: key_entry.focus())
    key_entry.bind('<Return>',      lambda e: do_activate())

    def _on_close():
        if messagebox.askyesno(
                "Exit",
                "Activation is required to use this software.\nAre you sure you want to exit?",
                parent=root):
            root.destroy()
            import sys
            sys.exit(0)

    root.bind('<Escape>', lambda e: _on_close())
    root.protocol("WM_DELETE_WINDOW", _on_close)
    username_entry.focus()
    root.mainloop()


def _shake(root, widget):
    """Brief horizontal shake on wrong credentials."""
    orig_x = widget.winfo_x()
    orig_y = widget.winfo_y()

    def step(moves):
        if not moves:
            widget.place_forget()
            widget.pack()
            return
        widget.place(x=orig_x + moves[0], y=orig_y)
        root.after(30, lambda: step(moves[1:]))

    offsets = [10, -10, 8, -8, 5, -5, 0]
    widget.place(x=orig_x, y=orig_y,
                 width=widget.winfo_width(),
                 height=widget.winfo_height())
    step(offsets)
