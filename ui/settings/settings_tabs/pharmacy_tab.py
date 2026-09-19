from __future__ import annotations

import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk
from tkinter import messagebox
import os
from core.font_config import *
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno
from ui.settings.settings_tabs.appearance_scroll import AppearanceScrollPane
from core.settings_section_nav import wire_settings_section_nav, bindings_for_sectioned_tab


_NAV_SECTIONS = [
    ('profile',       'Pharmacy Profile'),
    ('bill_template', 'Bill Template'),
    ('bill_fields',   'Bill Fields'),
    ('bill_text',     'Bill Text Lines'),
    ('bill_logo',     'Bill Logo'),
    ('bill_paper',    'Paper & Copies'),
    ('bill_sales',    'Sales Print Buttons'),
    ('printer',       'Printer Setup'),
]


class PharmacyTab:
    TAB_NAME = 'Pharmacy Profile'

    def __init__(self, notebook, conn):
        self.conn = conn
        self.cursor = conn.cursor()
        self._panels = {}
        self._nav_buttons = {}

        outer = ttk.Frame(notebook)
        self.outer = outer
        notebook.add(outer, text=self.TAB_NAME)

        shell = ttk.Frame(outer)
        shell.pack(fill=tk.BOTH, expand=True)

        nav_outer = ttk.LabelFrame(shell, text="Sections")
        nav_outer.pack(side=tk.LEFT, fill=tk.Y, padx=(8, 4), pady=8)
        nav_scroll = ttk.Frame(nav_outer)
        nav_scroll.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        for section_id, label in _NAV_SECTIONS:
            btn = ttk.Button(
                nav_scroll, text=label, width=22,
                command=lambda k=section_id: self._show_section(k),
            )
            btn.pack(fill=tk.X, pady=2)
            self._nav_buttons[section_id] = btn

        right_col = ttk.Frame(shell)
        right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 8), pady=8)
        self._scroller = AppearanceScrollPane(right_col)
        self._content_host = self._scroller.frame

        self._build_profile_panel()
        self._build_bill_template_panel()
        self._build_bill_fields_panel()
        self._build_bill_text_panel()
        self._build_bill_logo_panel()
        self._build_bill_paper_panel()
        self._build_bill_sales_panel()
        self._build_printer_panel()
        self._active_section = 'profile'
        self._show_section('profile')
        self._load()
        wire_settings_section_nav(
            self, self._nav_buttons, [s[0] for s in _NAV_SECTIONS], self._show_section)

    def get_keyboard_bindings(self):
        return bindings_for_sectioned_tab(
            self, first_focus=lambda: self.pharmacy_name.focus_set())

    def _panel(self, section_id):
        wrapper = ttk.Frame(self._content_host)
        self._panels[section_id] = wrapper
        return wrapper

    def _show_section(self, section_id):
        if section_id not in self._panels:
            return
        self._active_section = section_id
        for frame in self._panels.values():
            frame.pack_forget()
        panel = self._panels[section_id]
        panel.pack(side=tk.TOP, fill=tk.X, anchor='n')

        def _after_show():
            self._scroller.bind_wheel_recursive()
            self._scroller.refresh()
            self._scroller.scroll_to_top()

        panel.after_idle(_after_show)
        for key, btn in self._nav_buttons.items():
            try:
                btn.configure(bootstyle='primary' if key == section_id else 'secondary')
            except Exception:
                pass

    def select_section(self, section_id: str = 'profile'):
        if section_id == 'bill':
            section_id = 'bill_template'
        self._show_section(section_id)

    def _add_bill_save_button(self, frame):
        ttk.Button(
            frame, text="Save Bill Print Style", command=self._save_bill_only,
        ).pack(anchor=tk.W, padx=10, pady=(4, 12))

    def _build_profile_panel(self):
        frame = self._panel('profile')
        form = ttk.LabelFrame(frame, text="Pharmacy Information")
        form.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(form, text="Pharmacy Name:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.pharmacy_name = ttk.Entry(form, width=40)
        self.pharmacy_name.grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(form, text="Address:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.pharmacy_address = tk.Text(form, width=40, height=3)
        self.pharmacy_address.grid(row=1, column=1, padx=5, pady=5)

        ttk.Label(form, text="Phone:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=5)
        self.pharmacy_phone = ttk.Entry(form, width=40)
        self.pharmacy_phone.grid(row=2, column=1, padx=5, pady=5)

        ttk.Label(form, text="Email:").grid(row=3, column=0, sticky=tk.W, padx=5, pady=5)
        self.pharmacy_email = ttk.Entry(form, width=40)
        self.pharmacy_email.grid(row=3, column=1, padx=5, pady=5)

        ttk.Label(form, text="GSTIN:").grid(row=4, column=0, sticky=tk.W, padx=5, pady=5)
        self.pharmacy_gstin = ttk.Entry(form, width=40)
        self.pharmacy_gstin.grid(row=4, column=1, padx=5, pady=5)

        ttk.Label(form, text="DL Number:").grid(row=5, column=0, sticky=tk.W, padx=5, pady=5)
        self.pharmacy_dl = ttk.Entry(form, width=40)
        self.pharmacy_dl.grid(row=5, column=1, padx=5, pady=5)

        ttk.Label(form, text="FSSAI Number:").grid(row=6, column=0, sticky=tk.W, padx=5, pady=5)
        self.pharmacy_fssai = ttk.Entry(form, width=40)
        self.pharmacy_fssai.grid(row=6, column=1, padx=5, pady=5)

        ttk.Label(form, text="FSSAI on bill:").grid(row=7, column=0, sticky=tk.W, padx=5, pady=5)
        self.show_fssai_on_bill = tk.BooleanVar(master=form, value=False)
        ttk.Checkbutton(form, variable=self.show_fssai_on_bill, text="Print FSSAI on sale bills").grid(
            row=7, column=1, sticky=tk.W, padx=5, pady=5)

        from core.bill_config import load_bill_print_settings
        _bill_defaults = load_bill_print_settings()
        ttk.Label(form, text="GST on bill:").grid(row=8, column=0, sticky=tk.W, padx=5, pady=5)
        self.bill_show_gst = tk.BooleanVar(master=form, value=bool(_bill_defaults.get('show_gst', True)))
        ttk.Checkbutton(
            form, variable=self.bill_show_gst,
            text="Print GST amount on sale bills",
        ).grid(row=8, column=1, sticky=tk.W, padx=5, pady=5)

        self.bill_show_discount = tk.BooleanVar(master=form, value=bool(_bill_defaults.get('show_discount', True)))
        ttk.Label(form, text="Discount on bill:").grid(row=9, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Checkbutton(
            form, variable=self.bill_show_discount,
            text="Print applied discount on sale bills",
        ).grid(row=9, column=1, sticky=tk.W, padx=5, pady=5)

        ttk.Label(form, text="Enable GST:").grid(row=10, column=0, sticky=tk.W, padx=5, pady=5)
        self.gst_enabled = tk.BooleanVar()
        ttk.Checkbutton(form, variable=self.gst_enabled, text="Calculate GST on sale items").grid(
            row=10, column=1, sticky=tk.W, padx=5, pady=5)

        ttk.Label(form, text="Bill Logo:").grid(row=11, column=0, sticky=tk.W, padx=5, pady=5)
        logo_frame = ttk.Frame(form)
        logo_frame.grid(row=11, column=1, sticky=tk.W, padx=5, pady=5)
        self._logo_path_var = tk.StringVar(value="No logo selected")
        ttk.Label(logo_frame, textvariable=self._logo_path_var,
                  font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
                  width=35, anchor='w').pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(logo_frame, text="📁 Choose Image", command=self._choose_logo).pack(side=tk.LEFT, padx=2)
        ttk.Button(logo_frame, text="✖ Remove", command=self._remove_logo).pack(side=tk.LEFT, padx=2)

        self._logo_preview = ttk.Label(form, text="")
        self._logo_preview.grid(row=12, column=1, sticky=tk.W, padx=5, pady=2)

        save_btn = ttk.Button(form, text="Save Profile", command=self.save)
        save_btn.grid(row=13, column=1, pady=10)

        # ── App Login (optional gate on every app open) ───────────────────
        login = ttk.LabelFrame(frame, text="App Login")
        login.pack(fill=tk.X, padx=10, pady=(0, 10))
        ttk.Label(
            login,
            text="When enabled, the app asks for this username and password every time it opens.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=520,
            justify=tk.LEFT,
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=5, pady=(8, 4))

        self.login_enabled = tk.BooleanVar(master=login, value=False)
        ttk.Checkbutton(
            login,
            variable=self.login_enabled,
            text="Require login when the app opens",
            command=self._toggle_login_fields,
        ).grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)

        ttk.Label(login, text="Username:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=5)
        self.login_username = ttk.Entry(login, width=40)
        self.login_username.grid(row=2, column=1, padx=5, pady=5)

        ttk.Label(login, text="Password:").grid(row=3, column=0, sticky=tk.W, padx=5, pady=5)
        self.login_password = ttk.Entry(login, width=40, show='*')
        self.login_password.grid(row=3, column=1, padx=5, pady=5)

        ttk.Label(login, text="Confirm Password:").grid(row=4, column=0, sticky=tk.W, padx=5, pady=5)
        self.login_password2 = ttk.Entry(login, width=40, show='*')
        self.login_password2.grid(row=4, column=1, padx=5, pady=5)

        ttk.Label(
            login,
            text="Leave password blank when saving to keep the current password.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).grid(row=5, column=1, sticky=tk.W, padx=5, pady=(0, 8))

        self._load_login_prefs_into_ui()
        self._toggle_login_fields()

        # Navigation
        self.pharmacy_name.bind('<Return>', lambda e: self.pharmacy_address.focus())
        self.pharmacy_name.bind('<Down>',   lambda e: self.pharmacy_address.focus())
        self.pharmacy_address.bind('<FocusIn>', lambda e: self._wire_address_nav())
        self.pharmacy_phone.bind('<Return>', lambda e: self.pharmacy_email.focus())
        self.pharmacy_phone.bind('<Down>',   lambda e: self.pharmacy_email.focus())
        self.pharmacy_phone.bind('<Up>',     lambda e: self.pharmacy_address.focus())
        self.pharmacy_email.bind('<Return>', lambda e: self.pharmacy_gstin.focus())
        self.pharmacy_email.bind('<Down>',   lambda e: self.pharmacy_gstin.focus())
        self.pharmacy_email.bind('<Up>',     lambda e: self.pharmacy_phone.focus())
        self.pharmacy_gstin.bind('<Return>', lambda e: self.pharmacy_dl.focus())
        self.pharmacy_gstin.bind('<Down>',   lambda e: self.pharmacy_dl.focus())
        self.pharmacy_gstin.bind('<Up>',     lambda e: self.pharmacy_email.focus())
        self.pharmacy_dl.bind('<Return>', lambda e: self.pharmacy_fssai.focus())
        self.pharmacy_dl.bind('<Down>',   lambda e: self.pharmacy_fssai.focus())
        self.pharmacy_dl.bind('<Up>',     lambda e: self.pharmacy_gstin.focus())
        self.pharmacy_fssai.bind('<Return>', lambda e: save_btn.focus())
        self.pharmacy_fssai.bind('<Down>',   lambda e: save_btn.focus())
        self.pharmacy_fssai.bind('<Up>',     lambda e: self.pharmacy_dl.focus())
        save_btn.bind('<Return>', lambda e: self.save())
        save_btn.bind('<Up>',     lambda e: self.pharmacy_dl.focus())
        self.login_username.bind('<Return>', lambda e: self.login_password.focus())
        self.login_password.bind('<Return>', lambda e: self.login_password2.focus())
        self.login_password2.bind('<Return>', lambda e: self.save())

    def _build_bill_template_panel(self):
        frame = self._panel('bill_template')
        bill_frame = ttk.LabelFrame(frame, text="Bill Template")
        bill_frame.pack(fill=tk.X, padx=10, pady=10)

        from core.bill_config import AVAILABLE_TEMPLATES
        self._bill_templates = AVAILABLE_TEMPLATES
        ttk.Label(bill_frame, text="Bill template:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=4)
        self.bill_template = ttk.Combobox(
            bill_frame, width=36, state="readonly",
            values=list(AVAILABLE_TEMPLATES.values()))
        self.bill_template.grid(row=0, column=1, sticky=tk.W, padx=5, pady=4)
        ttk.Label(
            bill_frame,
            text="GST Vertical Rotated (Classic) matches the standard pharmacy GST receipt.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=520,
        ).grid(row=1, column=0, columnspan=2, sticky=tk.W, padx=5, pady=(0, 4))
        self._add_bill_save_button(frame)

    def _build_bill_fields_panel(self):
        frame = self._panel('bill_fields')
        from core.bill_config import DEFAULT_BILL_PRINT_SETTINGS, BILL_PRINT_FIELD_OPTIONS

        bill_frame = ttk.LabelFrame(frame, text="Show on Printed Bill")
        bill_frame.pack(fill=tk.X, padx=10, pady=10)

        self._bill_field_vars = {}
        opts_outer = ttk.Frame(bill_frame)
        opts_outer.pack(fill=tk.X, padx=5, pady=4)
        row_idx = 0
        for _group_title, fields in BILL_PRINT_FIELD_OPTIONS:
            group = ttk.LabelFrame(opts_outer, text=_group_title)
            group.grid(row=row_idx, column=0, sticky=tk.EW, padx=2, pady=(0, 6))
            for col, (key, label) in enumerate(fields):
                default = bool(DEFAULT_BILL_PRINT_SETTINGS.get(key, False))
                var = tk.BooleanVar(value=default)
                self._bill_field_vars[key] = var
                ttk.Checkbutton(group, variable=var, text=label).grid(
                    row=col // 3, column=col % 3, sticky=tk.W, padx=4, pady=1)
            row_idx += 1
        opts_outer.columnconfigure(0, weight=1)

        if hasattr(self, 'bill_show_gst'):
            self._bill_field_vars['show_gst'] = self.bill_show_gst
        elif 'show_gst' in self._bill_field_vars:
            self.bill_show_gst = self._bill_field_vars['show_gst']
        if hasattr(self, 'bill_show_discount'):
            self._bill_field_vars['show_discount'] = self.bill_show_discount
        elif 'show_discount' in self._bill_field_vars:
            self.bill_show_discount = self._bill_field_vars['show_discount']
        self._add_bill_save_button(frame)

    def _build_bill_text_panel(self):
        frame = self._panel('bill_text')
        from core.bill_config import DEFAULT_BILL_PRINT_SETTINGS

        text_outer = ttk.LabelFrame(frame, text="Bill Text Lines")
        text_outer.pack(fill=tk.X, padx=10, pady=10)
        ttk.Label(text_outer, text="Blessing line (top):").grid(
            row=0, column=0, sticky=tk.W, padx=5, pady=3)
        self._blessing_line_var = tk.StringVar(
            value=DEFAULT_BILL_PRINT_SETTINGS.get("blessing_line", "SHREE GANESHAY NAMAH"))
        bless_entry_row = ttk.Frame(text_outer)
        bless_entry_row.grid(row=0, column=1, sticky=tk.EW, padx=5, pady=3)
        ttk.Entry(bless_entry_row, textvariable=self._blessing_line_var, width=40).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        self._show_blessing_var = tk.BooleanVar(
            value=bool(DEFAULT_BILL_PRINT_SETTINGS.get("show_blessing", True)))
        ttk.Checkbutton(
            bless_entry_row, text="Print on bill", variable=self._show_blessing_var,
        ).pack(side=tk.LEFT, padx=(8, 0))
        if "show_blessing" in getattr(self, "_bill_field_vars", {}):
            self._bill_field_vars["show_blessing"] = self._show_blessing_var
        ttk.Label(text_outer, text="Recovery wish (footer):").grid(
            row=1, column=0, sticky=tk.W, padx=5, pady=3)
        self._recovery_wish_var = tk.StringVar(
            value=DEFAULT_BILL_PRINT_SETTINGS.get(
                "recovery_wish_line", "I WISH FOR YOUR SPEEDY RECOVERY."))
        ttk.Entry(text_outer, textvariable=self._recovery_wish_var, width=48).grid(
            row=1, column=1, sticky=tk.EW, padx=5, pady=3)
        ttk.Label(text_outer, text="GST strip line (e.g. HAVE A NICE DAY):").grid(
            row=2, column=0, sticky=tk.W, padx=5, pady=3)
        self._gst_day_line_var = tk.StringVar(
            value=DEFAULT_BILL_PRINT_SETTINGS.get("gst_day_line", "HAVE A NICE DAY"))
        ttk.Entry(text_outer, textvariable=self._gst_day_line_var, width=48).grid(
            row=2, column=1, sticky=tk.EW, padx=5, pady=3)
        text_outer.columnconfigure(1, weight=1)
        ttk.Label(
            text_outer,
            text="Uncheck “Print on bill” to hide Shree Ganeshay Namah. Same option is under Bill Fields → Invoice header.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=520,
        ).grid(row=3, column=0, columnspan=2, sticky=tk.W, padx=5, pady=(4, 2))
        self._add_bill_save_button(frame)

    def _build_bill_logo_panel(self):
        frame = self._panel('bill_logo')
        from core.bill_config import DEFAULT_BILL_PRINT_SETTINGS

        logo_outer = ttk.LabelFrame(
            frame, text="Bill Logo (from Pharmacy Profile)")
        logo_outer.pack(fill=tk.X, padx=10, pady=10)
        self._logo_top_right_var = tk.BooleanVar(
            value=bool(DEFAULT_BILL_PRINT_SETTINGS.get("show_logo_top_right", False)))
        self._logo_center_wm_var = tk.BooleanVar(
            value=bool(DEFAULT_BILL_PRINT_SETTINGS.get("show_logo_center_watermark", False)))
        ttk.Checkbutton(
            logo_outer, text="Small logo — top right of invoice",
            variable=self._logo_top_right_var,
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=5, pady=2)
        ttk.Label(logo_outer, text="Top margin (mm):").grid(
            row=1, column=0, sticky=tk.W, padx=5, pady=3)
        self._logo_tr_margin_top = ttk.Spinbox(
            logo_outer, from_=0, to=25, increment=0.5, width=6, format="%.1f")
        self._logo_tr_margin_top.grid(row=1, column=1, sticky=tk.W, padx=5, pady=3)
        self._logo_tr_margin_top.delete(0, tk.END)
        self._logo_tr_margin_top.insert(0, str(DEFAULT_BILL_PRINT_SETTINGS.get(
            "logo_top_right_margin_top", 1.2)))
        ttk.Label(logo_outer, text="Right margin (mm):").grid(
            row=2, column=0, sticky=tk.W, padx=5, pady=3)
        self._logo_tr_margin_right = ttk.Spinbox(
            logo_outer, from_=0, to=25, increment=0.5, width=6, format="%.1f")
        self._logo_tr_margin_right.grid(row=2, column=1, sticky=tk.W, padx=5, pady=3)
        self._logo_tr_margin_right.delete(0, tk.END)
        self._logo_tr_margin_right.insert(0, str(DEFAULT_BILL_PRINT_SETTINGS.get(
            "logo_top_right_margin_right", 1.5)))
        ttk.Checkbutton(
            logo_outer, text="Center watermark behind medicines",
            variable=self._logo_center_wm_var,
        ).grid(row=3, column=0, columnspan=2, sticky=tk.W, padx=5, pady=2)
        ttk.Label(logo_outer, text="Watermark transparency %:").grid(
            row=4, column=0, sticky=tk.W, padx=5, pady=3)
        self._logo_opacity = ttk.Spinbox(logo_outer, from_=5, to=80, width=5)
        self._logo_opacity.grid(row=4, column=1, sticky=tk.W, padx=5, pady=3)
        self._logo_opacity.delete(0, tk.END)
        self._logo_opacity.insert(0, str(int(DEFAULT_BILL_PRINT_SETTINGS.get(
            "logo_watermark_opacity", 15))))
        ttk.Label(
            logo_outer,
            text="Upload the logo image under Pharmacy Profile. You can enable one or both positions.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=520,
        ).grid(row=5, column=0, columnspan=2, sticky=tk.W, padx=5, pady=(4, 2))
        self._add_bill_save_button(frame)

    def _build_bill_paper_panel(self):
        frame = self._panel('bill_paper')
        from core.bill_config import DEFAULT_BILL_PRINT_SETTINGS
        bill_frame = ttk.LabelFrame(frame, text="Paper & Copies")
        bill_frame.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(bill_frame, text="Paper size:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=4)
        self.bill_paper = ttk.Combobox(
            bill_frame, width=12, state="readonly", values=["A5", "A6", "A4"])
        self.bill_paper.grid(row=0, column=1, sticky=tk.W, padx=5, pady=4)
        ttk.Label(
            bill_frame,
            text="Page copies: how many times each printed sheet is sent to the printer.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=520,
        ).grid(row=1, column=0, columnspan=2, sticky=tk.W, padx=5, pady=(0, 4))

        ttk.Label(bill_frame, text="Page copies:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=4)
        self.bill_copies = ttk.Spinbox(bill_frame, from_=1, to=4, width=5)
        self.bill_copies.grid(row=2, column=1, sticky=tk.W, padx=5, pady=4)
        from core.bill_config import bill_size_mode_combo_values, DEFAULT_BILL_PRINT_SETTINGS
        ttk.Label(bill_frame, text="Bill size:").grid(
            row=3, column=0, sticky=tk.W, padx=5, pady=4)
        self._bill_size_mode = ttk.Combobox(
            bill_frame, width=22, state="readonly", values=bill_size_mode_combo_values())
        self._bill_size_mode.grid(row=3, column=1, sticky=tk.W, padx=5, pady=4)
        ttk.Label(bill_frame, text="Shrink % (dot matrix):").grid(
            row=4, column=0, sticky=tk.W, padx=5, pady=4)
        self._bill_size_pct = ttk.Spinbox(
            bill_frame, from_=85, to=98, width=5)
        self._bill_size_pct.grid(row=4, column=1, sticky=tk.W, padx=5, pady=4)
        self._bill_size_pct.delete(0, tk.END)
        self._bill_size_pct.insert(0, str(int(DEFAULT_BILL_PRINT_SETTINGS.get("bill_size_pct", 92))))
        ttk.Label(bill_frame, text="Medicines per bill page:").grid(
            row=5, column=0, sticky=tk.W, padx=5, pady=4)
        self._items_per_bill_page = ttk.Spinbox(
            bill_frame, from_=1, to=50, width=5)
        self._items_per_bill_page.grid(row=5, column=1, sticky=tk.W, padx=5, pady=4)
        self._items_per_bill_page.delete(0, tk.END)
        self._items_per_bill_page.insert(0, str(int(
            DEFAULT_BILL_PRINT_SETTINGS.get("items_per_bill_page", 10))))
        ttk.Label(bill_frame, text="Dot matrix line spacing:").grid(
            row=6, column=0, sticky=tk.W, padx=5, pady=4)
        self._dot_matrix_line_spacing = ttk.Spinbox(
            bill_frame, from_=22, to=32, width=5)
        self._dot_matrix_line_spacing.grid(row=6, column=1, sticky=tk.W, padx=5, pady=4)
        self._dot_matrix_line_spacing.delete(0, tk.END)
        self._dot_matrix_line_spacing.insert(0, str(int(
            DEFAULT_BILL_PRINT_SETTINGS.get("dot_matrix_line_spacing", 30))))
        ttk.Label(bill_frame, text="Dot matrix top offset (cm):").grid(
            row=7, column=0, sticky=tk.W, padx=5, pady=4)
        self._dot_matrix_top_offset = ttk.Spinbox(
            bill_frame, from_=0.0, to=3.0, increment=0.1, width=6)
        self._dot_matrix_top_offset.grid(row=7, column=1, sticky=tk.W, padx=5, pady=4)
        self._dot_matrix_top_offset.delete(0, tk.END)
        self._dot_matrix_top_offset.insert(0, str(
            DEFAULT_BILL_PRINT_SETTINGS.get("dot_matrix_top_offset_cm", 0.8)))
        ttk.Label(bill_frame, text="Dot matrix table borders:").grid(
            row=8, column=0, sticky=tk.W, padx=5, pady=4)
        self._dot_matrix_vertical_borders = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            bill_frame,
            text="Show vertical | borders (off = horizontal lines only)",
            variable=self._dot_matrix_vertical_borders,
        ).grid(row=8, column=1, sticky=tk.W, padx=5, pady=4)
        ttk.Label(bill_frame, text="A5 single-copy position:").grid(
            row=9, column=0, sticky=tk.W, padx=5, pady=4)
        self._a5_single_pos = ttk.Combobox(
            bill_frame, width=22, state="readonly", values=["Bottom half", "Top half"])
        self._a5_single_pos.grid(row=9, column=1, sticky=tk.W, padx=5, pady=4)
        ttk.Label(bill_frame, text="A6 source half on A5 PDF:").grid(
            row=10, column=0, sticky=tk.W, padx=5, pady=4)
        self._a6_source_half = ttk.Combobox(
            bill_frame, width=22, state="readonly", values=["Bottom half", "Top half"])
        self._a6_source_half.grid(row=10, column=1, sticky=tk.W, padx=5, pady=4)
        ttk.Label(bill_frame, text="A4 single-copy position:").grid(
            row=11, column=0, sticky=tk.W, padx=5, pady=4)
        self._a4_single_pos = ttk.Combobox(
            bill_frame, width=22, state="readonly", values=["Bottom half", "Top half"])
        self._a4_single_pos.grid(row=11, column=1, sticky=tk.W, padx=5, pady=4)
        ttk.Label(bill_frame, text="A4 two-copy layout:").grid(
            row=12, column=0, sticky=tk.W, padx=5, pady=4)
        self._a4_two_copy_layout = ttk.Combobox(
            bill_frame, width=22, state="readonly",
            values=["Side by side", "Top and bottom"])
        self._a4_two_copy_layout.grid(row=12, column=1, sticky=tk.W, padx=5, pady=4)
        ttk.Label(
            bill_frame,
            text="Medicines per bill page: max lines on one printed bill; extra medicines continue "
                 "on the next page/slip (Continued…). For A6 dot matrix use 8–10 so lines fit on paper. "
                 "Dot matrix line spacing 28–32 = more space between lines (default 30). "
                 "Top offset cm: reverse-feed before print to start bill higher on A6 slip (default 0.8). "
                 "Turn off vertical borders for horizontal-rule-only bills. "
                 "Shrink % fits the bill on A5/A6 without clipping borders.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=520,
        ).grid(row=13, column=0, columnspan=2, sticky=tk.W, padx=5, pady=(0, 4))
        self._bill_size_mode.bind("<<ComboboxSelected>>", self._on_bill_size_mode_changed)
        self._add_bill_save_button(frame)

    def _build_bill_sales_panel(self):
        frame = self._panel('bill_sales')
        slots_outer = ttk.LabelFrame(frame, text="Print Sales Buttons (Sales Page)")
        slots_outer.pack(fill=tk.X, padx=10, pady=10)

        self._print_slot_vars = {}
        from core.bill_config import bill_size_mode_combo_values
        for idx, slot in enumerate((1, 2)):
            slot_frame = ttk.LabelFrame(slots_outer, text=f"Print Sales {slot}")
            slot_frame.pack(fill=tk.X, padx=6, pady=6)

            ttk.Label(slot_frame, text="Button label:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=3)
            label_var = tk.StringVar(value=f"Print Sales {slot}")
            ttk.Entry(slot_frame, textvariable=label_var, width=18).grid(
                row=0, column=1, sticky=tk.W, padx=5, pady=3)

            ttk.Label(slot_frame, text="Paper:").grid(row=0, column=2, sticky=tk.W, padx=5, pady=3)
            paper = ttk.Combobox(slot_frame, width=6, state="readonly", values=["A5", "A6", "A4"])
            paper.grid(row=0, column=3, sticky=tk.W, padx=5, pady=3)

            ttk.Label(slot_frame, text="Bill copies:").grid(row=0, column=4, sticky=tk.W, padx=5, pady=3)
            copies = ttk.Spinbox(slot_frame, from_=1, to=4, width=4)
            copies.grid(row=0, column=5, sticky=tk.W, padx=5, pady=3)

            ttk.Label(slot_frame, text="Size:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=3)
            size = ttk.Combobox(
                slot_frame, width=22, state="readonly", values=bill_size_mode_combo_values())
            size.grid(row=1, column=1, sticky=tk.W, padx=5, pady=3)

            ttk.Label(slot_frame, text="Half position:").grid(row=1, column=2, sticky=tk.W, padx=5, pady=3)
            a6_half = ttk.Combobox(
                slot_frame, width=12, state="readonly", values=["Bottom half", "Top half"])
            a6_half.grid(row=1, column=3, sticky=tk.W, padx=5, pady=3)

            ttk.Label(slot_frame, text="A4 2-copy layout:").grid(row=1, column=4, sticky=tk.W, padx=5, pady=3)
            two_layout = ttk.Combobox(
                slot_frame, width=14, state="readonly",
                values=["Side by side", "Top and bottom"])
            two_layout.grid(row=1, column=5, sticky=tk.W, padx=5, pady=3)

            ttk.Label(slot_frame, text="Shortcut:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=3)
            key_var = tk.StringVar(value="F7" if slot == 1 else "F8")
            ttk.Entry(slot_frame, textvariable=key_var, width=8).grid(
                row=2, column=1, sticky=tk.W, padx=5, pady=3)

            self._print_slot_vars[slot] = {
                'label': label_var,
                'paper': paper,
                'a6_half': a6_half,
                'two_layout': two_layout,
                'copies': copies,
                'size': size,
                'key': key_var,
            }

        hint = ttk.Label(
            slots_outer,
            text="Bill copies = identical slips of the same bill on one sheet "
                 "(A5: 1–2, A6: 1, A4: 1–4). Long bills (12+ lines) become multiple "
                 "pages — one sheet per 12 medicines; copies duplicates that page only. "
                 "e.g. 36 lines → 3 sheets; A5×2 = 3 A5 pages with 2 identical slips each. "
                 "Non-final pages show Continued...; last page shows Total. "
                 "Half position = A5/A6 single-copy placement.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=560,
        )
        hint.pack(anchor=tk.W, padx=8, pady=(4, 2))

        from core.bill_config import SALES_ENTER_ACTIONS
        enter_labels = list(SALES_ENTER_ACTIONS.values())
        enter_frame = ttk.Frame(slots_outer)
        enter_frame.pack(fill=tk.X, padx=6, pady=(6, 2))
        ttk.Label(enter_frame, text="Enter on Online (Cash mode):").grid(
            row=0, column=0, sticky=tk.W, padx=5, pady=3)
        self._cash_online_enter_combo = ttk.Combobox(
            enter_frame, values=enter_labels, width=22, state="readonly",
        )
        self._cash_online_enter_combo.grid(row=0, column=1, sticky=tk.W, padx=5, pady=3)
        ttk.Label(enter_frame, text="Enter on Rounding (Due mode):").grid(
            row=1, column=0, sticky=tk.W, padx=5, pady=3)
        self._due_rounding_enter_combo = ttk.Combobox(
            enter_frame, values=enter_labels, width=22, state="readonly",
        )
        self._due_rounding_enter_combo.grid(row=1, column=1, sticky=tk.W, padx=5, pady=3)
        ttk.Label(
            enter_frame,
            text="Choose what happens when you press Enter after payment on the Sales page.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=560,
        ).grid(row=2, column=0, columnspan=2, sticky=tk.W, padx=5, pady=(2, 4))
        self._add_bill_save_button(frame)

    def _build_printer_panel(self):
        frame = self._panel('printer')
        pf = ttk.LabelFrame(frame, text="Silent Printer Setup (Windows)")
        pf.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(
            pf,
            text="Invoices print directly to the selected printer via SumatraPDF.\n"
                 "Paper size, orientation, tray, and quality use Windows printer preferences.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=560,
        ).grid(row=0, column=0, columnspan=3, sticky=tk.W, padx=5, pady=(4, 8))

        ttk.Label(pf, text="Default printer:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=4)
        self._printer_combo = ttk.Combobox(pf, width=42, state='readonly')
        self._printer_combo.grid(row=1, column=1, sticky=tk.W, padx=5, pady=4)
        ttk.Button(pf, text="Refresh", command=self._refresh_printer_list, width=10).grid(
            row=1, column=2, sticky=tk.W, padx=5, pady=4)

        ttk.Label(pf, text="Print Sales 1 printer:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=4)
        self._printer_slot1_combo = ttk.Combobox(pf, width=42, state='readonly')
        self._printer_slot1_combo.grid(row=2, column=1, columnspan=2, sticky=tk.W, padx=5, pady=4)

        ttk.Label(pf, text="Print Sales 2 printer:").grid(row=3, column=0, sticky=tk.W, padx=5, pady=4)
        self._printer_slot2_combo = ttk.Combobox(pf, width=42, state='readonly')
        self._printer_slot2_combo.grid(row=3, column=1, columnspan=2, sticky=tk.W, padx=5, pady=4)

        ttk.Label(
            pf,
            text="Leave slot printers blank to use the default printer.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).grid(row=4, column=0, columnspan=3, sticky=tk.W, padx=5, pady=(0, 4))

        ttk.Label(pf, text="SumatraPDF path:").grid(row=5, column=0, sticky=tk.W, padx=5, pady=4)
        self._sumatra_path_var = tk.StringVar()
        sumatra_row = ttk.Frame(pf)
        sumatra_row.grid(row=5, column=1, columnspan=2, sticky=tk.W, padx=5, pady=4)
        self._sumatra_entry = ttk.Entry(sumatra_row, textvariable=self._sumatra_path_var, width=40)
        self._sumatra_entry.pack(side=tk.LEFT)
        ttk.Button(sumatra_row, text="Refresh", command=self._refresh_sumatra_path, width=8).pack(
            side=tk.LEFT, padx=(4, 0))
        ttk.Button(sumatra_row, text="Browse…", command=self._browse_sumatra, width=8).pack(
            side=tk.LEFT, padx=(4, 0))
        ttk.Label(
            pf,
            text="Auto-detects tools\\SumatraPDF64.exe or SumatraPDF32.exe (dev and EXE).",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).grid(row=6, column=0, columnspan=3, sticky=tk.W, padx=5, pady=(0, 4))

        self._silent_print_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            pf, variable=self._silent_print_var,
            text="Silent print (no Windows dialog) for Print Sales 1 / 2",
        ).grid(row=7, column=0, columnspan=3, sticky=tk.W, padx=5, pady=4)

        # Silent (SumatraPDF) print and the Windows print dialog ask the driver
        # for grayscale while this is on.
        self._black_only_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            pf, variable=self._black_only_var,
            text="Print in black only (saves colour ink)",
        ).grid(row=8, column=0, columnspan=3, sticky=tk.W, padx=5, pady=4)

        ttk.Label(pf, text="Printer type:").grid(row=9, column=0, sticky=tk.W, padx=5, pady=4)
        self._printer_type_combo = ttk.Combobox(
            pf,
            values=["Standard (HTML/PDF)", "Dot Matrix (9-pin ESC/P)"],
            state="readonly",
            width=42,
        )
        self._printer_type_combo.grid(row=9, column=1, columnspan=2, sticky=tk.W, padx=5, pady=4)
        ttk.Label(
            pf,
            text="For LX-310: select \"EPSON LX-310 ESC/P\" (native driver), NOT \"EPSON LX-310\" (Class Driver).",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=560,
        ).grid(row=10, column=0, columnspan=3, sticky=tk.W, padx=5, pady=(0, 2))
        ttk.Label(
            pf,
            text="Dot Matrix tries RAW ESC/P first, then GDI monospace (Courier) if needed.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=560,
        ).grid(row=11, column=0, columnspan=3, sticky=tk.W, padx=5, pady=(0, 4))
        try:
            from core.print_log import print_log_path as _print_log_path
            _plog = _print_log_path()
        except Exception:
            _plog = "config/print_log.txt"
        ttk.Label(
            pf,
            text=f"Print debug log: {_plog}",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).grid(row=12, column=0, columnspan=3, sticky=tk.W, padx=5, pady=(0, 4))

        btn_row = ttk.Frame(pf)
        btn_row.grid(row=13, column=0, columnspan=3, sticky=tk.W, padx=5, pady=10)
        ttk.Button(btn_row, text="Save Printer Settings", command=self._save_printer_settings).pack(
            side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_row, text="Test Print", command=self._test_printer).pack(side=tk.LEFT)

        self._load_printer_settings()

    def _printer_combo_values(self) -> list[str]:
        return ['(Use Windows default)'] + list(getattr(self, '_installed_printers', []) or [])

    def _refresh_printer_list(self):
        from core.printer_manager import PrinterManager, PrinterError
        try:
            self._installed_printers = PrinterManager.get_installed_printers()
            if not PrinterManager.is_spooler_running():
                # Listed from Windows registry — printing still needs Spooler started.
                showwarning(
                    "Print Spooler stopped",
                    "Printers were loaded from Windows settings, but the Print Spooler "
                    "service is stopped.\n\n"
                    "Start it before printing:\n"
                    "Services → Print Spooler → Start\n"
                    "(or: net start spooler as Administrator).",
                    parent=self.outer,
                )
        except PrinterError as exc:
            showerror("Printers", str(exc), parent=self.outer)
            self._installed_printers = []
        values = self._printer_combo_values()
        for combo in (
            self._printer_combo,
            self._printer_slot1_combo,
            self._printer_slot2_combo,
        ):
            combo['values'] = values

    def _load_printer_settings(self):
        from core.printer_manager import PrinterManager
        self._refresh_printer_list()
        cfg = PrinterManager.load_settings()
        default = (cfg.get('selected_printer') or '').strip()
        slot1 = (cfg.get('print_slot_1_printer') or '').strip()
        slot2 = (cfg.get('print_slot_2_printer') or '').strip()
        if not default:
            default = PrinterManager.get_default_printer()
        self._printer_combo.set(default or '(Use Windows default)')
        self._printer_slot1_combo.set(slot1 or '(Use Windows default)')
        self._printer_slot2_combo.set(slot2 or '(Use Windows default)')
        self._refresh_sumatra_path(cfg=cfg)
        self._silent_print_var.set(bool(cfg.get('silent_print_enabled', True)))
        self._black_only_var.set(PrinterManager.is_black_only_print(cfg))
        from core.printer_manager import PRINTER_TYPE_DOT_MATRIX
        if (cfg.get('printer_type') or '').strip().lower() == PRINTER_TYPE_DOT_MATRIX:
            self._printer_type_combo.set("Dot Matrix (9-pin ESC/P)")
        else:
            self._printer_type_combo.set("Standard (HTML/PDF)")

    def _printer_type_from_combo(self) -> str:
        from core.printer_manager import PRINTER_TYPE_DOT_MATRIX, PRINTER_TYPE_STANDARD
        val = (self._printer_type_combo.get() or '').strip()
        if val.startswith("Dot Matrix"):
            return PRINTER_TYPE_DOT_MATRIX
        return PRINTER_TYPE_STANDARD

    def _refresh_sumatra_path(self, cfg=None):
        import os
        from core.printer_manager import PrinterManager
        if cfg is None:
            cfg = PrinterManager.load_settings()
        saved = (cfg.get('sumatra_path') or '').strip()
        if saved and os.path.isfile(saved):
            self._sumatra_path_var.set(saved)
            return
        detected = PrinterManager.find_bundled_sumatra_path()
        if not detected:
            detected = PrinterManager.find_sumatra_path('')
        self._sumatra_path_var.set(
            detected or '(not found — add SumatraPDF64.exe to tools folder)')

    def _printer_name_from_combo(self, combo) -> str:
        val = (combo.get() or '').strip()
        if not val or val == '(Use Windows default)':
            return ''
        return val

    def _save_printer_settings(self):
        import os
        from core.printer_manager import PrinterManager
        try:
            entered = (self._sumatra_path_var.get() or '').strip()
            auto = PrinterManager.find_bundled_sumatra_path()
            if entered.startswith('(not found'):
                entered = ''
            if entered and auto and os.path.normcase(entered) == os.path.normcase(auto):
                entered = ''
            elif entered and not os.path.isfile(entered):
                entered = ''
            PrinterManager.save_settings({
                'selected_printer': self._printer_name_from_combo(self._printer_combo),
                'print_slot_1_printer': self._printer_name_from_combo(self._printer_slot1_combo),
                'print_slot_2_printer': self._printer_name_from_combo(self._printer_slot2_combo),
                'sumatra_path': entered,
                'silent_print_enabled': bool(self._silent_print_var.get()),
                'print_black_only': bool(self._black_only_var.get()),
                'printer_type': self._printer_type_from_combo(),
            })
            self._refresh_sumatra_path()
            showinfo("Success", "Printer settings saved!")
        except Exception as exc:
            showerror("Error", f"Failed to save printer settings: {exc}")

    def _browse_sumatra(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Select SumatraPDF.exe",
            filetypes=[("SumatraPDF", "SumatraPDF.exe"), ("Executables", "*.exe"), ("All files", "*.*")],
        )
        if path:
            self._sumatra_path_var.set(path)

    def _test_printer(self):
        import threading
        from core.printer_manager import PrinterManager, PrinterError
        printer = self._printer_name_from_combo(self._printer_combo)
        if not printer:
            printer = None

        def _work():
            try:
                PrinterManager.test_print(printer)
                self.outer.after(0, lambda: showinfo(
                    "Test Print", "Test page sent to the printer.", parent=self.outer))
            except PrinterError as exc:
                self.outer.after(0, lambda e=str(exc): showerror(
                    "Test Print Failed", e, parent=self.outer))

        threading.Thread(target=_work, daemon=True, name='PrinterTest').start()

    def _save_bill_only(self):
        try:
            self._collect_bill_settings()
            showinfo("Success", "Bill print settings saved!")
        except Exception as e:
            showerror("Error", f"Failed to save bill settings: {e}")

    def _choose_logo(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Select Logo Image",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"), ("All files", "*.*")])
        if path:
            self._logo_path_var.set(path)
            self._update_logo_preview(path)

    def _remove_logo(self):
        self._logo_path_var.set("No logo selected")
        self._logo_preview.config(image='', text='')
        self._logo_preview._img = None

    def _update_logo_preview(self, path):
        try:
            from PIL import Image, ImageTk
            img = Image.open(path)
            img.thumbnail((120, 60))
            photo = ImageTk.PhotoImage(img)
            self._logo_preview.config(image=photo, text='')
            self._logo_preview._img = photo
        except Exception:
            self._logo_preview.config(text=f"Selected: {os.path.basename(path)}", image='')

    def _wire_address_nav(self):
        if getattr(self, '_address_nav_wired', False):
            return
        self._address_nav_wired = True
        self.pharmacy_address.bind('<Up>',
            lambda e: self.pharmacy_name.focus()
            if int(self.pharmacy_address.index(tk.INSERT).split('.')[0]) <= 1 else None, add='+')
        self.pharmacy_address.bind('<Down>',
            lambda e: self.pharmacy_phone.focus()
            if int(self.pharmacy_address.index(tk.INSERT).split('.')[0]) >= int(
                self.pharmacy_address.index(tk.END + '-1c').split('.')[0]) else None, add='+')

    def _template_key_from_label(self, label):
        for key, text in self._bill_templates.items():
            if text == label:
                return key
        return "classic"

    def _template_label_from_key(self, key):
        return self._bill_templates.get(key, self._bill_templates.get("classic", ""))

    def _on_bill_size_mode_changed(self, event=None):
        from core.bill_config import BILL_SIZE_DOT_MATRIX, bill_size_mode_from_label, bill_size_mode_label
        if not hasattr(self, '_bill_size_pct'):
            return
        is_dm = bill_size_mode_from_label(self._bill_size_mode.get()) == BILL_SIZE_DOT_MATRIX
        state = 'normal' if is_dm else 'disabled'
        self._bill_size_pct.configure(state=state)

    def _apply_bill_settings_to_ui(self, settings):
        key = settings.get("template", "classic")
        self.bill_template.set(self._template_label_from_key(key))
        for field_key, var in getattr(self, "_bill_field_vars", {}).items():
            var.set(bool(settings.get(field_key, True)))
        if hasattr(self, "bill_show_gst"):
            self.bill_show_gst.set(bool(settings.get("show_gst", True)))
        if hasattr(self, "bill_show_discount"):
            self.bill_show_discount.set(bool(settings.get("show_discount", True)))
        paper = (settings.get("paper_size") or "A5").upper()
        self.bill_paper.set(paper if paper in ("A4", "A5", "A6") else "A5")
        self.bill_copies.delete(0, tk.END)
        self.bill_copies.insert(0, str(int(settings.get("copies", 1))))
        if hasattr(self, '_bill_size_mode'):
            from core.bill_config import bill_size_mode_label, get_bill_size_mode, get_bill_size_pct
            self._bill_size_mode.set(bill_size_mode_label(get_bill_size_mode(settings)))
            if hasattr(self, '_bill_size_pct'):
                self._bill_size_pct.delete(0, tk.END)
                self._bill_size_pct.insert(0, str(int(get_bill_size_pct(settings))))
            self._on_bill_size_mode_changed()
        if hasattr(self, '_items_per_bill_page'):
            from core.bill_config import get_items_per_bill_page
            self._items_per_bill_page.delete(0, tk.END)
            self._items_per_bill_page.insert(0, str(get_items_per_bill_page(settings)))
        if hasattr(self, '_dot_matrix_line_spacing'):
            self._dot_matrix_line_spacing.delete(0, tk.END)
            self._dot_matrix_line_spacing.insert(0, str(int(
                settings.get("dot_matrix_line_spacing", 30))))
        if hasattr(self, '_dot_matrix_vertical_borders'):
            self._dot_matrix_vertical_borders.set(
                bool(settings.get("dot_matrix_vertical_borders", True)))
        if hasattr(self, '_dot_matrix_top_offset'):
            self._dot_matrix_top_offset.delete(0, tk.END)
            self._dot_matrix_top_offset.insert(0, str(
                settings.get("dot_matrix_top_offset_cm", 0.8)))
        if hasattr(self, '_a5_single_pos'):
            pos = str(settings.get("a5_single_copy_position") or "bottom").strip().lower()
            self._a5_single_pos.set("Top half" if pos == "top" else "Bottom half")
        if hasattr(self, '_a6_source_half'):
            pos = str(settings.get("a6_source_half") or "bottom").strip().lower()
            self._a6_source_half.set("Top half" if pos == "top" else "Bottom half")
        if hasattr(self, '_a4_single_pos'):
            pos = str(settings.get("a4_single_copy_position") or "bottom").strip().lower()
            self._a4_single_pos.set("Top half" if pos == "top" else "Bottom half")
        if hasattr(self, '_a4_two_copy_layout'):
            layout = str(settings.get("a4_two_copy_layout") or "side_by_side").strip().lower()
            self._a4_two_copy_layout.set(
                "Top and bottom" if layout in ("top_bottom", "stacked", "top and bottom")
                else "Side by side"
            )
        if hasattr(self, '_blessing_line_var'):
            self._blessing_line_var.set(
                settings.get("blessing_line", "SHREE GANESHAY NAMAH"))
        if hasattr(self, '_show_blessing_var'):
            self._show_blessing_var.set(bool(settings.get("show_blessing", True)))
            if "show_blessing" in getattr(self, "_bill_field_vars", {}):
                self._bill_field_vars["show_blessing"] = self._show_blessing_var
        if hasattr(self, '_recovery_wish_var'):
            self._recovery_wish_var.set(
                settings.get("recovery_wish_line", "I WISH FOR YOUR SPEEDY RECOVERY."))
        if hasattr(self, '_gst_day_line_var'):
            self._gst_day_line_var.set(
                settings.get("gst_day_line", "HAVE A NICE DAY"))
        if hasattr(self, '_logo_top_right_var'):
            self._logo_top_right_var.set(bool(settings.get("show_logo_top_right", False)))
        if hasattr(self, '_logo_tr_margin_top'):
            self._logo_tr_margin_top.delete(0, tk.END)
            self._logo_tr_margin_top.insert(0, str(settings.get(
                "logo_top_right_margin_top", 1.2)))
        if hasattr(self, '_logo_tr_margin_right'):
            self._logo_tr_margin_right.delete(0, tk.END)
            self._logo_tr_margin_right.insert(0, str(settings.get(
                "logo_top_right_margin_right", 1.5)))
        if hasattr(self, '_logo_center_wm_var'):
            self._logo_center_wm_var.set(bool(settings.get("show_logo_center_watermark", False)))
        if hasattr(self, '_logo_opacity'):
            self._logo_opacity.delete(0, tk.END)
            self._logo_opacity.insert(0, str(int(settings.get("logo_watermark_opacity", 15))))
        for slot, vars_ in getattr(self, '_print_slot_vars', {}).items():
            slot_cfg = settings.get(f'print_slot_{slot}') or {}
            if isinstance(slot_cfg, dict):
                vars_['label'].set(slot_cfg.get('label') or f'Print Sales {slot}')
                paper = (slot_cfg.get('paper_size') or ('A5' if slot == 1 else 'A4')).upper()
                vars_['paper'].set(paper if paper in ('A4', 'A5', 'A6') else 'A5')
                vars_['copies'].delete(0, tk.END)
                vars_['copies'].insert(0, str(int(slot_cfg.get('copies') or (2 if slot == 1 else 1))))
                pos = str(slot_cfg.get('a6_source_half') or settings.get('a6_source_half') or 'bottom').strip().lower()
                vars_['a6_half'].set("Top half" if pos == "top" else "Bottom half")
                if 'two_layout' in vars_:
                    layout = str(
                        slot_cfg.get('a4_two_copy_layout')
                        or settings.get('a4_two_copy_layout')
                        or 'side_by_side'
                    ).strip().lower()
                    vars_['two_layout'].set(
                        "Top and bottom" if layout in ("top_bottom", "stacked", "top and bottom")
                        else "Side by side"
                    )
                from core.bill_config import bill_size_mode_label, get_bill_size_mode
                slot_mode = slot_cfg.get('bill_size_mode')
                if slot_mode:
                    vars_['size'].set(bill_size_mode_label(slot_mode))
                else:
                    vars_['size'].set(bill_size_mode_label(get_bill_size_mode(settings)))
            key = settings.get(f'print_slot_{slot}_key') or ('F7' if slot == 1 else 'F8')
            vars_['key'].set(str(key).upper())
        from core.bill_config import get_sales_enter_action, sales_enter_action_label
        if hasattr(self, '_cash_online_enter_combo'):
            self._cash_online_enter_combo.set(
                sales_enter_action_label(
                    get_sales_enter_action(settings, 'cash_online_enter_action')))
        if hasattr(self, '_due_rounding_enter_combo'):
            self._due_rounding_enter_combo.set(
                sales_enter_action_label(
                    get_sales_enter_action(settings, 'due_rounding_enter_action')))

    def _collect_bill_settings(self):
        from core.bill_config import load_bill_print_settings, save_bill_print_settings
        merged = load_bill_print_settings()
        merged.update({
            "template": self._template_key_from_label(self.bill_template.get()),
            "paper_size": (self.bill_paper.get() or "A5").upper(),
            "copies": max(1, min(10, int(self.bill_copies.get() or 1))),
        })
        for field_key, var in getattr(self, "_bill_field_vars", {}).items():
            merged[field_key] = bool(var.get())
        if hasattr(self, "bill_show_gst"):
            merged["show_gst"] = self.bill_show_gst.get()
        if hasattr(self, "bill_show_discount"):
            merged["show_discount"] = self.bill_show_discount.get()
        if hasattr(self, '_blessing_line_var'):
            merged["blessing_line"] = self._blessing_line_var.get().strip() or "SHREE GANESHAY NAMAH"
        if hasattr(self, '_show_blessing_var'):
            merged["show_blessing"] = bool(self._show_blessing_var.get())
            if "show_blessing" in getattr(self, "_bill_field_vars", {}):
                self._bill_field_vars["show_blessing"].set(merged["show_blessing"])
        if hasattr(self, '_recovery_wish_var'):
            merged["recovery_wish_line"] = (
                self._recovery_wish_var.get().strip() or "I WISH FOR YOUR SPEEDY RECOVERY.")
        if hasattr(self, '_gst_day_line_var'):
            merged["gst_day_line"] = (
                self._gst_day_line_var.get().strip() or "HAVE A NICE DAY")
        if hasattr(self, '_logo_top_right_var'):
            merged["show_logo_top_right"] = bool(self._logo_top_right_var.get())
        if hasattr(self, '_logo_tr_margin_top'):
            try:
                merged["logo_top_right_margin_top"] = max(
                    0.0, min(25.0, float(self._logo_tr_margin_top.get() or 1.2)))
            except ValueError:
                merged["logo_top_right_margin_top"] = 1.2
        if hasattr(self, '_logo_tr_margin_right'):
            try:
                merged["logo_top_right_margin_right"] = max(
                    0.0, min(25.0, float(self._logo_tr_margin_right.get() or 1.5)))
            except ValueError:
                merged["logo_top_right_margin_right"] = 1.5
        if hasattr(self, '_bill_size_mode'):
            from core.bill_config import bill_size_mode_from_label, get_bill_size_pct
            merged["bill_size_mode"] = bill_size_mode_from_label(self._bill_size_mode.get())
            try:
                merged["bill_size_pct"] = int(get_bill_size_pct({
                    "bill_size_pct": self._bill_size_pct.get()}))
            except ValueError:
                merged["bill_size_pct"] = 92
            merged["margins"] = {"top": 0, "bottom": 0, "left": 0, "right": 0}
        if hasattr(self, '_items_per_bill_page'):
            from core.bill_config import get_items_per_bill_page
            try:
                merged["items_per_bill_page"] = get_items_per_bill_page({
                    "items_per_bill_page": int(self._items_per_bill_page.get() or 10),
                })
            except ValueError:
                merged["items_per_bill_page"] = 10
        if hasattr(self, '_dot_matrix_line_spacing'):
            try:
                merged["dot_matrix_line_spacing"] = max(
                    22, min(32, int(self._dot_matrix_line_spacing.get() or 30)))
            except ValueError:
                merged["dot_matrix_line_spacing"] = 30
        if hasattr(self, '_dot_matrix_vertical_borders'):
            merged["dot_matrix_vertical_borders"] = bool(
                self._dot_matrix_vertical_borders.get())
        if hasattr(self, '_dot_matrix_top_offset'):
            try:
                merged["dot_matrix_top_offset_cm"] = round(max(
                    0.0, min(3.0, float(self._dot_matrix_top_offset.get() or 1.5))), 1)
            except ValueError:
                merged["dot_matrix_top_offset_cm"] = 1.5
        if hasattr(self, '_a5_single_pos'):
            merged["a5_single_copy_position"] = (
                "top" if (self._a5_single_pos.get() or "").strip().lower().startswith("top")
                else "bottom"
            )
        if hasattr(self, '_a6_source_half'):
            merged["a6_source_half"] = (
                "top" if (self._a6_source_half.get() or "").strip().lower().startswith("top")
                else "bottom"
            )
        if hasattr(self, '_a4_single_pos'):
            merged["a4_single_copy_position"] = (
                "top" if (self._a4_single_pos.get() or "").strip().lower().startswith("top")
                else "bottom"
            )
        if hasattr(self, '_a4_two_copy_layout'):
            merged["a4_two_copy_layout"] = (
                "top_bottom"
                if (self._a4_two_copy_layout.get() or "").strip().lower().startswith("top")
                else "side_by_side"
            )
        if hasattr(self, '_logo_center_wm_var'):
            merged["show_logo_center_watermark"] = bool(self._logo_center_wm_var.get())
        if hasattr(self, '_logo_opacity'):
            try:
                merged["logo_watermark_opacity"] = max(
                    5, min(80, int(self._logo_opacity.get() or 15)))
            except ValueError:
                merged["logo_watermark_opacity"] = 15
        for slot, vars_ in getattr(self, '_print_slot_vars', {}).items():
            paper = (vars_['paper'].get() or ('A5' if slot == 1 else 'A4')).upper()
            copies = int(vars_['copies'].get() or (2 if slot == 1 else 1))
            if paper == 'A6':
                max_c = 1
            elif paper == 'A5':
                max_c = 2
            else:
                max_c = 4
            copies = max(1, min(max_c, copies))
            from core.bill_config import bill_size_mode_from_label, BILL_SIZE_DOT_MATRIX, BILL_SIZE_NORMAL
            slot_entry = {
                'label': (vars_['label'].get() or f'Print Sales {slot}').strip(),
                'paper_size': paper,
                'a6_source_half': (
                    "top" if (vars_['a6_half'].get() or "").strip().lower().startswith("top")
                    else "bottom"
                ),
                'a4_two_copy_layout': (
                    "top_bottom"
                    if vars_.get('two_layout') and (
                        vars_['two_layout'].get() or ""
                    ).strip().lower().startswith("top")
                    else "side_by_side"
                ),
                'copies': copies,
            }
            slot_mode = bill_size_mode_from_label(vars_['size'].get())
            if slot_mode == BILL_SIZE_DOT_MATRIX:
                slot_entry['bill_size_mode'] = BILL_SIZE_DOT_MATRIX
            else:
                slot_entry['bill_size_mode'] = BILL_SIZE_NORMAL
            merged[f'print_slot_{slot}'] = slot_entry
            merged[f'print_slot_{slot}_key'] = (vars_['key'].get() or ('F7' if slot == 1 else 'F8')).strip().upper()
        from core.bill_config import sales_enter_action_from_label
        if hasattr(self, '_cash_online_enter_combo'):
            merged['cash_online_enter_action'] = sales_enter_action_from_label(
                self._cash_online_enter_combo.get())
        if hasattr(self, '_due_rounding_enter_combo'):
            merged['due_rounding_enter_action'] = sales_enter_action_from_label(
                self._due_rounding_enter_combo.get())
        save_bill_print_settings(merged)
        return merged

    def _load(self):
        from core.bill_config import load_bill_print_settings

        self._apply_bill_settings_to_ui(load_bill_print_settings())
        self._load_login_prefs_into_ui()
        self._toggle_login_fields()

        try:
            from core.sync_prefs import is_online_mode
            online = bool(is_online_mode())
        except Exception:
            online = False

        if online:
            from core.background_workers import run_in_thread

            def _fetch():
                from core.pharmacy_profile_io import load_pharmacy_profile
                return load_pharmacy_profile(self.conn)

            run_in_thread(
                _fetch,
                name="PharmacyProfileLoad",
                root=self.outer,
                on_success=self._apply_pharmacy_profile,
                on_error=lambda _exc: None,
            )
            return

        profile = None
        try:
            from core.pharmacy_profile_io import load_pharmacy_profile
            profile = load_pharmacy_profile(self.conn)
        except Exception:
            profile = None
        if not profile or not any(
            str(profile.get(k) or "").strip()
            for k in ("name", "address", "phone", "gstin", "dl_number")
        ):
            try:
                self.cursor.execute("""
                    SELECT name, address, phone, email, gstin, dl_number,
                           gst_enabled, COALESCE(logo_path,''),
                           COALESCE(fssai_number,''), COALESCE(show_fssai_on_bill,0)
                    FROM pharmacy_profile LIMIT 1
                """)
                row = self.cursor.fetchone()
                if row:
                    profile = {
                        "name": row[0] or "",
                        "address": row[1] or "",
                        "phone": row[2] or "",
                        "email": row[3] or "",
                        "gstin": row[4] or "",
                        "dl_number": row[5] or "",
                        "gst_enabled": bool(row[6]),
                        "logo_path": row[7] or "",
                        "fssai_number": row[8] or "",
                        "show_fssai_on_bill": bool(row[9]),
                    }
            except Exception:
                profile = None
        self._apply_pharmacy_profile(profile)

    def _apply_pharmacy_profile(self, profile):
        if not profile:
            return
        if not any(
            str(profile.get(k) or "").strip()
            for k in ("name", "address", "phone", "gstin", "dl_number")
        ):
            return
        try:
            if not self.pharmacy_name.winfo_exists():
                return
        except tk.TclError:
            return
        if self.pharmacy_name.get().strip():
            return
        self.pharmacy_name.insert(0, profile.get("name") or "")
        self.pharmacy_address.insert(tk.END, profile.get("address") or "")
        self.pharmacy_phone.insert(0, profile.get("phone") or "")
        self.pharmacy_email.insert(0, profile.get("email") or "")
        self.pharmacy_gstin.insert(0, profile.get("gstin") or "")
        self.pharmacy_dl.insert(0, profile.get("dl_number") or "")
        self.gst_enabled.set(bool(profile.get("gst_enabled", True)))
        logo_path = profile.get("logo_path") or ""
        self.pharmacy_fssai.insert(0, profile.get("fssai_number") or "")
        self.show_fssai_on_bill.set(bool(profile.get("show_fssai_on_bill")))
        if logo_path and os.path.exists(logo_path):
            self._logo_path_var.set(logo_path)
            self._update_logo_preview(logo_path)

    def _load_login_prefs_into_ui(self):
        try:
            from core.login_prefs import load_login_prefs
            prefs = load_login_prefs()
        except Exception:
            prefs = {}
        if not hasattr(self, 'login_enabled'):
            return
        self.login_enabled.set(bool(prefs.get('enabled')))
        self.login_username.delete(0, tk.END)
        self.login_username.insert(0, prefs.get('username') or '')
        self.login_password.delete(0, tk.END)
        self.login_password2.delete(0, tk.END)

    def _toggle_login_fields(self):
        if not hasattr(self, 'login_username'):
            return
        state = 'normal' if self.login_enabled.get() else 'disabled'
        for w in (self.login_username, self.login_password, self.login_password2):
            try:
                w.configure(state=state)
            except Exception:
                pass

    def _save_login_prefs(self):
        from core.login_prefs import load_login_prefs, save_login_prefs
        enabled = bool(self.login_enabled.get())
        username = (self.login_username.get() or '').strip()
        password = self.login_password.get() or ''
        password2 = self.login_password2.get() or ''
        current = load_login_prefs()
        has_existing = bool(current.get('password_hash') and current.get('salt'))

        if enabled:
            if not username:
                raise ValueError('App Login username is required when login is enabled.')
            if password or password2:
                if password != password2:
                    raise ValueError('App Login passwords do not match.')
                if len(password) < 4:
                    raise ValueError('App Login password must be at least 4 characters.')
                save_login_prefs(enabled=True, username=username, password=password)
            elif has_existing:
                save_login_prefs(
                    enabled=True, username=username, password=None,
                    keep_existing_password=True,
                )
            else:
                raise ValueError('Set an App Login password before enabling login.')
        else:
            save_login_prefs(
                enabled=False,
                username=username,
                password=password if password else None,
                keep_existing_password=not bool(password),
            )
        self.login_password.delete(0, tk.END)
        self.login_password2.delete(0, tk.END)

    def save(self):
        try:
            logo_path = self._logo_path_var.get()
            if logo_path == "No logo selected":
                logo_path = ''
            from core.pharmacy_profile_io import save_pharmacy_profile

            save_pharmacy_profile(
                self.conn,
                {
                    "name": self.pharmacy_name.get(),
                    "address": self.pharmacy_address.get(1.0, tk.END).strip(),
                    "phone": self.pharmacy_phone.get(),
                    "email": self.pharmacy_email.get(),
                    "gstin": self.pharmacy_gstin.get(),
                    "dl_number": self.pharmacy_dl.get(),
                    "gst_enabled": bool(self.gst_enabled.get()),
                    "logo_path": logo_path,
                    "fssai_number": self.pharmacy_fssai.get().strip(),
                    "show_fssai_on_bill": bool(self.show_fssai_on_bill.get()),
                },
            )
            try:
                self._collect_bill_settings()
            except OSError:
                pass
            try:
                self._save_login_prefs()
            except ValueError as ve:
                showerror("App Login", str(ve))
                return
            except OSError:
                pass
            showinfo("Success", "Pharmacy profile and bill print settings saved!")
        except Exception as e:
            showerror("Error", f"Failed to save profile: {e}")
