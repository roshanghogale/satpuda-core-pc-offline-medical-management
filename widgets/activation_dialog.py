"""
Activation dialog — two-panel modern design.
Font: NirmalaUI (loaded from bundled TTF via ctypes — similar to Roboto).
Layout:
  LEFT  : logo → brand → tagline → 2x2 feature cards → visiting card (small)
  RIGHT : shield → title → fields → button
"""
import os, sys, ctypes, tkinter as tk
from tkinter import messagebox

_BG       = '#f0f4f8'
_PANEL    = '#ffffff'
_LEFT_BG  = '#f8fafc'
_RIGHT_BG = '#ffffff'
_CARD_BG  = '#f1f5f9'
_INPUT_BG = '#ffffff'
_BORDER   = '#cbd5e1'
_FG       = '#1e293b'
_MUTED    = '#64748b'
_ACCENT   = '#2563eb'
_ACCENT_H = '#3b82f6'
_GREEN    = '#16a34a'
_ERROR    = '#dc2626'
_DIV      = '#e2e8f0'

_LEFT_W  = 340
_RIGHT_W = 500
_HEIGHT  = 680

# Font names after loading TTF
_FONT_REG  = 'Nirmala UI'
_FONT_BOLD = 'Nirmala UI'


def _asset(name):
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, 'assets', name)


def _load_fonts():
    """Load bundled TTF files via Windows GDI so tkinter can use them."""
    try:
        gdi = ctypes.windll.gdi32
        for fname in ('NirmalaUI.ttf', 'NirmalaUI_Bold.ttf'):
            path = _asset(fname)
            if os.path.exists(path):
                gdi.AddFontResourceExW(path, 0x10, 0)
    except Exception:
        pass


def _try_img(path, w):
    try:
        from PIL import Image, ImageTk
        img = Image.open(path).convert('RGBA')
        h   = int(img.height * w / img.width)
        return ImageTk.PhotoImage(img.resize((w, h), Image.LANCZOS))
    except Exception:
        return None


def _F(size, bold=False):
    """Return font tuple using loaded NirmalaUI."""
    weight = 'bold' if bold else 'normal'
    return (_FONT_BOLD if bold else _FONT_REG, size, weight)


def show_activation_dialog(on_success_callback):
    from core.license_manager import attempt_activation, prepare_device_key, get_device_key_path
    from core.store_manager import has_registry, setup_initial_store_on_activation, setup_satellite_store_from_restore

    _load_fonts()
    _mode = ['activate']  # 'activate' | 'add_store'

    root = tk.Tk()
    root.title("Satpuda Core — Activation")
    root.state('zoomed')
    root.resizable(True, True)
    root.configure(bg=_BG)

    try:
        from core.window_icon import apply_main_window_icon
        apply_main_window_icon(root)
    except Exception:
        pass

    logo_img = _try_img(_asset('satpuda_logo.png'), 80)
    card_img = _try_img(_asset('card design.png'),  260)   # small card

    # ── Main container centred via place ──────────────────────────────────
    total_w = _LEFT_W + 1 + _RIGHT_W
    main = tk.Frame(root, bg=_PANEL,
                    highlightbackground=_BORDER, highlightthickness=1,
                    width=total_w, height=_HEIGHT)
    main.pack_propagate(False)
    main.place(relx=0.5, rely=0.5, anchor='center')

    # ══════════════════════════════════════════════════════════════════════
    # LEFT PANEL
    # ══════════════════════════════════════════════════════════════════════
    left = tk.Frame(main, bg=_LEFT_BG, width=_LEFT_W, height=_HEIGHT)
    left.place(x=0, y=0)
    left.pack_propagate(False)

    y = 26

    # Logo
    if logo_img:
        lbl = tk.Label(left, image=logo_img, bg=_LEFT_BG)
        lbl.image = logo_img
        lbl.place(relx=0.5, y=y, anchor='n')
        y += 90
    else:
        c = tk.Canvas(left, width=80, height=80, bg=_ACCENT, highlightthickness=0)
        c.place(relx=0.5, y=y, anchor='n')
        c.create_text(40, 40, text="SC", font=_F(22, True), fill='white')
        y += 90

    # Brand  (+4 gap after logo)
    brand = tk.Frame(left, bg=_LEFT_BG)
    brand.place(relx=0.5, y=y, anchor='n')
    tk.Label(brand, text="Satpuda", font=_F(18, True),
             bg=_LEFT_BG, fg=_FG).pack(side=tk.LEFT)
    tk.Label(brand, text="Core", font=_F(18, True),
             bg=_LEFT_BG, fg=_GREEN).pack(side=tk.LEFT)
    y += 32

    # Tagline text  (+4 gap after brand)
    tk.Label(left, text="Billing. Management. Simplified.",
             font=_F(8), bg=_LEFT_BG, fg=_MUTED).place(relx=0.5, y=y, anchor='n')
    y += 22

    # Tagline pill  (+4 gap after text)
    pill = tk.Frame(left, bg=_CARD_BG,
                    highlightbackground=_BORDER, highlightthickness=1)
    pill.place(relx=0.5, y=y, anchor='n')
    tk.Label(pill, text="  Powering healthcare, managing care  ",
             font=_F(8), bg=_CARD_BG, fg=_MUTED, pady=4).pack()
    y += 38

    # 2x2 feature cards  (+4 gap after pill)
    features = [
        ("Billing",   "Easy Billing"),
        ("Inventory", "Smart Inventory"),
        ("Medicines", "Medicine Mgmt"),
        ("Reports",   "Insightful Reports"),
    ]
    grid_f = tk.Frame(left, bg=_LEFT_BG)
    grid_f.place(relx=0.5, y=y, anchor='n')
    for i, (short, label) in enumerate(features):
        r, c = divmod(i, 2)
        fc = tk.Frame(grid_f, bg=_CARD_BG,
                      highlightbackground=_BORDER, highlightthickness=1,
                      width=128, height=52)
        fc.grid(row=r, column=c, padx=3, pady=3)
        fc.pack_propagate(False)
        tk.Frame(fc, bg=_ACCENT, height=3).pack(fill=tk.X)
        tk.Label(fc, text=short, font=_F(8, True),
                 bg=_CARD_BG, fg=_FG).pack(pady=(3, 0))
        tk.Label(fc, text=label, font=_F(7),
                 bg=_CARD_BG, fg=_MUTED).pack()
    y += 128

    # Visiting card  (+8 gap after feature cards)
    if card_img:
        cf = tk.Frame(left, bg=_BORDER,
                      highlightbackground=_BORDER, highlightthickness=1)
        cf.place(relx=0.5, y=y, anchor='n')
        cl = tk.Label(cf, image=card_img, bg=_BORDER)
        cl.image = card_img
        cl.pack(padx=1, pady=1)
    else:
        ph = tk.Frame(left, bg=_CARD_BG, width=260, height=48,
                      highlightbackground=_BORDER, highlightthickness=1)
        ph.place(relx=0.5, y=y, anchor='n')
        ph.pack_propagate(False)
        tk.Label(ph, text="SatpudaCore  |  Billing. Management. Simplified.",
                 font=_F(8, True), bg=_CARD_BG, fg=_ACCENT
                 ).place(relx=0.5, rely=0.5, anchor='center')
    y += 154

    # Dot grid  (+6 gap after card)
    dc = tk.Canvas(left, width=260, height=18,
                   bg=_LEFT_BG, highlightthickness=0)
    dc.place(relx=0.5, y=y, anchor='n')
    for row in range(2):
        for col in range(18):
            x0, y0 = col*14+4, row*8+3
            dc.create_oval(x0, y0, x0+2, y0+2, fill=_BORDER, outline='')

    # ── Divider ───────────────────────────────────────────────────────────
    tk.Frame(main, bg=_DIV, width=1, height=_HEIGHT).place(x=_LEFT_W, y=0)

    # ══════════════════════════════════════════════════════════════════════
    # RIGHT PANEL
    # ══════════════════════════════════════════════════════════════════════
    right = tk.Frame(main, bg=_RIGHT_BG, width=_RIGHT_W, height=_HEIGHT)
    right.place(x=_LEFT_W+1, y=0)
    right.pack_propagate(False)

    ry = 32

    # Shield canvas
    sh = tk.Canvas(right, width=56, height=56,
                   bg=_RIGHT_BG, highlightthickness=0)
    sh.place(relx=0.5, y=ry, anchor='n')
    sh.create_oval(2, 2, 54, 54, fill='#dbeafe', outline=_ACCENT, width=2)
    sh.create_rectangle(16, 26, 40, 42, outline=_FG, width=2, fill='')
    sh.create_arc(20, 14, 36, 30, start=0, extent=180,
                  outline=_FG, width=2, style='arc')
    sh.create_oval(25, 31, 31, 37, fill=_FG, outline='')
    ry += 64

    # Title
    tf = tk.Frame(right, bg=_RIGHT_BG)
    tf.place(relx=0.5, y=ry, anchor='n')
    tk.Label(tf, text="Software ", font=_F(20, True),
             bg=_RIGHT_BG, fg=_FG).pack(side=tk.LEFT)
    tk.Label(tf, text="Activation", font=_F(20, True),
             bg=_RIGHT_BG, fg=_ACCENT).pack(side=tk.LEFT)
    ry += 36

    tk.Label(right,
             text="This device is not activated.\nEnter your credentials to continue.",
             font=_F(9), bg=_RIGHT_BG, fg=_MUTED,
             justify='center').place(relx=0.5, y=ry, anchor='n')
    ry += 52

    # ── Input fields ──────────────────────────────────────────────────────
    FW = 420   # field width
    FX = (_RIGHT_W - FW) // 2

    def _field(label_txt, ph_txt, show=None):
        nonlocal ry
        tk.Label(right, text=label_txt, font=_F(8, True),
                 bg=_RIGHT_BG, fg=_MUTED,
                 anchor='w').place(x=FX, y=ry, width=FW)
        ry += 18

        wrap = tk.Frame(right, bg=_INPUT_BG,
                        highlightbackground=_BORDER, highlightthickness=1,
                        width=FW, height=38)
        wrap.place(x=FX, y=ry)
        wrap.pack_propagate(False)

        # Icon
        ic = tk.Canvas(wrap, width=32, height=36,
                       bg=_INPUT_BG, highlightthickness=0)
        ic.pack(side=tk.LEFT, padx=(6, 0))
        if 'Username' in label_txt:
            ic.create_oval(10, 5, 22, 17, outline=_MUTED, width=1)
            ic.create_arc(6, 17, 26, 30, start=0, extent=180,
                          outline=_MUTED, width=1, style='arc')
        elif 'Password' in label_txt:
            ic.create_rectangle(9, 17, 23, 28, outline=_MUTED, width=1)
            ic.create_arc(12, 10, 20, 20, start=0, extent=180,
                          outline=_MUTED, width=1, style='arc')
        else:
            ic.create_text(16, 18, text="KEY",
                           font=_F(6, True), fill=_MUTED)

        e = tk.Entry(wrap, font=_F(10), show=show,
                     bg=_INPUT_BG, fg=_MUTED,
                     insertbackground=_FG,
                     relief='flat', bd=0, highlightthickness=0)
        e.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, ipady=8)
        e.insert(0, ph_txt)
        e._ph = ph_txt
        e._has_ph = True

        def _fin(ev, w=e, wr=wrap):
            if w._has_ph:
                w.delete(0, tk.END)
                w.config(fg=_FG)
                if show: w.config(show=show)
                w._has_ph = False
            wr.config(highlightbackground=_ACCENT)

        def _fout(ev, w=e, wr=wrap):
            if not w.get():
                w.config(show='', fg=_MUTED)
                w.insert(0, w._ph)
                w._has_ph = True
            wr.config(highlightbackground=_BORDER)

        e.bind('<FocusIn>',  _fin)
        e.bind('<FocusOut>', _fout)

        if show == '\u25cf':
            _vis = [False]
            eye = tk.Label(wrap, text="Show", font=_F(7),
                           bg=_INPUT_BG, fg=_MUTED,
                           cursor='hand2', padx=8)
            eye.pack(side=tk.RIGHT)
            def _tog(ev, w=e, ey=eye, v=_vis):
                v[0] = not v[0]
                if not w._has_ph:
                    w.config(show='' if v[0] else '\u25cf')
                ey.config(text="Hide" if v[0] else "Show")
            eye.bind('<Button-1>', _tog)

        ry += 46
        return e

    u_ent = _field("Username",   "Enter your username")
    p_ent = _field("Password",   "Enter your password",  show='\u25cf')
    k_ent = _field("Device Key", "Enter your device key")
    s_ent = _field("Store Name", "Enter initial store name")

    hint_var = tk.StringVar(
        value="First activation: enter your initial store name (becomes the backup folder name)."
    )
    tk.Label(right, textvariable=hint_var, font=_F(7),
             bg=_RIGHT_BG, fg=_MUTED,
             wraplength=FW, justify='center').place(relx=0.5, y=ry, anchor='n')
    ry += 28

    # Error
    err_var = tk.StringVar()
    tk.Label(right, textvariable=err_var, font=_F(8),
             bg=_RIGHT_BG, fg=_ERROR,
             wraplength=FW, justify='center').place(relx=0.5, y=ry, anchor='n')
    ry += 22

    # Activate button
    def _get(e):
        return '' if e._has_ph else e.get().strip()

    def _admin_login_and_show_key():
        dlg = tk.Toplevel(root)
        dlg.title("Administrator Access")
        dlg.geometry("380x240")
        dlg.resizable(False, False)
        dlg.transient(root)
        dlg.grab_set()
        dlg.configure(bg=_RIGHT_BG)

        tk.Label(
            dlg, text="Administrator Login", font=_F(11, True),
            bg=_RIGHT_BG, fg=_FG
        ).pack(pady=(14, 8))

        form = tk.Frame(dlg, bg=_RIGHT_BG)
        form.pack(fill=tk.X, padx=14)

        tk.Label(form, text="Username", font=_F(8, True), bg=_RIGHT_BG, fg=_MUTED).grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        user_e = tk.Entry(form, font=_F(10), bg=_INPUT_BG, fg=_FG, insertbackground=_FG)
        user_e.grid(row=1, column=0, sticky="ew", pady=(0, 8))

        tk.Label(form, text="Password", font=_F(8, True), bg=_RIGHT_BG, fg=_MUTED).grid(
            row=2, column=0, sticky="w", pady=(0, 4)
        )
        pass_e = tk.Entry(form, font=_F(10), show='\u25cf', bg=_INPUT_BG, fg=_FG, insertbackground=_FG)
        pass_e.grid(row=3, column=0, sticky="ew")
        form.grid_columnconfigure(0, weight=1)

        err = tk.StringVar(value="")
        tk.Label(dlg, textvariable=err, font=_F(8), bg=_RIGHT_BG, fg=_ERROR).pack(pady=(6, 0))

        def _open_key_dialog():
            u = user_e.get().strip()
            p = pass_e.get().strip()
            if u != "satpudacore" or p != "satpudacore":
                err.set("Invalid administrator credentials.")
                return
            try:
                prepare_device_key()
                path = get_device_key_path()
                key = ""
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        key = f.read().strip()
                if not key:
                    err.set("Device key could not be loaded.")
                    return
            except Exception as ex:
                err.set(f"Failed to load device key: {ex}")
                return

            dlg.destroy()
            key_dlg = tk.Toplevel(root)
            key_dlg.title("Device Key")
            key_dlg.geometry("560x220")
            key_dlg.resizable(False, False)
            key_dlg.transient(root)
            key_dlg.grab_set()
            key_dlg.configure(bg=_RIGHT_BG)

            tk.Label(
                key_dlg, text="Device Key", font=_F(12, True), bg=_RIGHT_BG, fg=_FG
            ).pack(pady=(14, 6))
            tk.Label(
                key_dlg, text="Use Copy to paste directly into the activation field.",
                font=_F(8), bg=_RIGHT_BG, fg=_MUTED
            ).pack()

            text_box = tk.Text(
                key_dlg, height=4, wrap='word', font=_F(9),
                bg=_INPUT_BG, fg=_FG, insertbackground=_FG, relief='flat', bd=1
            )
            text_box.pack(fill=tk.BOTH, expand=True, padx=14, pady=10)
            text_box.insert("1.0", key)
            text_box.config(state='disabled')

            btn_row = tk.Frame(key_dlg, bg=_RIGHT_BG)
            btn_row.pack(pady=(0, 12))

            def _copy_key():
                try:
                    root.clipboard_clear()
                    root.clipboard_append(key)
                    root.update_idletasks()
                    messagebox.showinfo("Copied", "Device key copied to clipboard.", parent=key_dlg)
                except Exception:
                    messagebox.showerror("Error", "Failed to copy device key.", parent=key_dlg)

            tk.Button(
                btn_row, text="Copy Device Key", command=_copy_key,
                font=_F(9, True), bg=_ACCENT, fg='white', relief='flat', bd=0, padx=14, pady=6
            ).pack(side=tk.LEFT, padx=6)
            tk.Button(
                btn_row, text="Close", command=key_dlg.destroy,
                font=_F(9), bg=_CARD_BG, fg=_FG, relief='flat', bd=0, padx=14, pady=6
            ).pack(side=tk.LEFT, padx=6)
            from core.dialog_escape import bind_escape_to_close
            bind_escape_to_close(key_dlg, on_close=key_dlg.destroy)

        btn_row = tk.Frame(dlg, bg=_RIGHT_BG)
        btn_row.pack(pady=10)
        tk.Button(
            btn_row, text="Login", command=_open_key_dialog,
            font=_F(9, True), bg=_ACCENT, fg='white', relief='flat', bd=0, padx=14, pady=6
        ).pack(side=tk.LEFT, padx=6)
        tk.Button(
            btn_row, text="Cancel", command=dlg.destroy,
            font=_F(9), bg=_CARD_BG, fg=_FG, relief='flat', bd=0, padx=14, pady=6
        ).pack(side=tk.LEFT, padx=6)

        user_e.bind('<Return>', lambda e: (pass_e.focus_set(), "break")[1])
        pass_e.bind('<Return>', lambda e: (_open_key_dialog(), "break")[1])
        from core.dialog_escape import bind_escape_to_close
        bind_escape_to_close(dlg, on_close=dlg.destroy)
        user_e.focus_set()

    def _finish_store_setup(store_name: str, *, satellite: bool):
        from core.backup_manager import restore_latest_backup_from_drive
        import shutil

        store_name = (store_name or '').strip()
        if not store_name:
            err_var.set("Store name is required.")
            return False
        try:
            if satellite:
                ok, result = restore_latest_backup_from_drive(store_name)
                if not ok:
                    err_var.set(result)
                    return False
                tmp_db = result['db_path'] if isinstance(result, dict) else result
                try:
                    setup_satellite_store_from_restore(store_name, tmp_db)
                finally:
                    tmp_parent = os.path.dirname(tmp_db)
                    if tmp_parent and os.path.isdir(tmp_parent):
                        shutil.rmtree(tmp_parent, ignore_errors=True)
            else:
                setup_initial_store_on_activation(store_name)
        except Exception as ex:
            err_var.set(str(ex))
            return False
        return True

    def _activate():
        err_var.set('')
        u, p, k = _get(u_ent), _get(p_ent), _get(k_ent)
        store_name = _get(s_ent)
        if not u or not p or not k:
            err_var.set("Username, password, and device key are required.")
            return
        ok, msg = attempt_activation(u, p, k)
        if not ok:
            err_var.set(msg)
            _shake(main)
            return

        satellite = _mode[0] == 'add_store'
        needs_store = satellite or not has_registry()
        if needs_store:
            if not store_name:
                err_var.set("Store name is required.")
                return
            if not _finish_store_setup(store_name, satellite=satellite):
                return

        root.destroy()
        on_success_callback()

    def _set_mode(mode):
        _mode[0] = mode
        if mode == 'add_store':
            hint_var.set(
                "Add store: enter the exact store name from the admin device. "
                "The latest Drive backup will be restored."
            )
            btn.config(text="  Connect & Restore Store  ->")
        else:
            if has_registry():
                hint_var.set("Re-activation: store setup is already configured on this device.")
            else:
                hint_var.set(
                    "First activation: enter your initial store name "
                    "(becomes the backup folder name)."
                )
            btn.config(text="  Activate Software  ->")

    btn = tk.Button(right,
                    text="  Activate Software  ->",
                    font=_F(11, True),
                    bg=_ACCENT, fg='white',
                    activebackground=_ACCENT_H,
                    activeforeground='white',
                    relief='flat', bd=0,
                    cursor='hand2', pady=11,
                    command=_activate)
    btn.place(x=FX, y=ry, width=FW)
    btn.bind('<Enter>', lambda e: btn.config(bg=_ACCENT_H))
    btn.bind('<Leave>', lambda e: btn.config(bg=_ACCENT))
    ry += 52

    admin_btn = tk.Button(
        right,
        text="Administrator",
        font=_F(9, True),
        bg=_CARD_BG,
        fg=_FG,
        activebackground='#dbeafe',
        activeforeground='white',
        relief='flat',
        bd=0,
        cursor='hand2',
        pady=7,
        command=_admin_login_and_show_key
    )
    add_store_btn = tk.Button(
        right,
        text="Add Store (restore from Drive)",
        font=_F(9, True),
        bg='#dbeafe',
        fg=_ACCENT,
        activebackground='#bfdbfe',
        activeforeground=_ACCENT,
        relief='flat',
        bd=0,
        cursor='hand2',
        pady=7,
        command=lambda: _set_mode('add_store'),
    )
    add_store_btn.place(x=FX, y=ry, width=FW)
    ry += 40

    admin_btn.place(x=FX, y=ry, width=FW)
    ry += 40

    tk.Label(right, text="Your data is secure and encrypted",
             font=_F(8), bg=_RIGHT_BG, fg=_MUTED
             ).place(relx=0.5, y=ry, anchor='n')

    # ── Bindings ──────────────────────────────────────────────────────────
    def _next(src, dst):
        def h(e):
            dst.focus()
            if dst._has_ph:
                dst.delete(0, tk.END)
                dst.config(fg=_FG)
                dst._has_ph = False
        src.bind('<Return>', h)

    _next(u_ent, p_ent)
    _next(p_ent, k_ent)
    _next(k_ent, s_ent)
    s_ent.bind('<Return>', lambda e: _activate())

    def _on_close():
        if messagebox.askyesno("Exit",
                "Activation is required.\nExit?", parent=root):
            root.destroy()
            sys.exit(0)

    from core.dialog_escape import bind_escape_to_close
    bind_escape_to_close(root, on_close=_on_close)
    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.after(200, u_ent.focus)
    root.mainloop()


def _shake(widget):
    try:
        x0 = widget.winfo_x()
        y0 = widget.winfo_y()
        w  = widget.winfo_width()
        h  = widget.winfo_height()
        moves = [14, -14, 10, -10, 6, -6, 3, -3, 0]

        def step(i=0):
            if i >= len(moves):
                widget.place(relx=0.5, rely=0.5, anchor='center')
                return
            widget.place(x=x0+moves[i], y=y0, width=w, height=h)
            widget.after(28, lambda: step(i+1))

        step()
    except Exception:
        pass
