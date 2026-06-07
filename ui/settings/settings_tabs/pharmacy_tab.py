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
    ('profile', 'Pharmacy Profile'),
    ('bill',    'Bill Print Style'),
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
        self._build_bill_panel()
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
        self._show_section(section_id)

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
        self.show_fssai_on_bill = tk.BooleanVar(value=False)
        ttk.Checkbutton(form, variable=self.show_fssai_on_bill, text="Print FSSAI on sale bills").grid(
            row=7, column=1, sticky=tk.W, padx=5, pady=5)

        from core.bill_config import load_bill_print_settings
        _bill_defaults = load_bill_print_settings()
        ttk.Label(form, text="GST on bill:").grid(row=8, column=0, sticky=tk.W, padx=5, pady=5)
        self.bill_show_gst = tk.BooleanVar(value=bool(_bill_defaults.get('show_gst', True)))
        ttk.Checkbutton(
            form, variable=self.bill_show_gst,
            text="Print GST amount on sale bills",
        ).grid(row=8, column=1, sticky=tk.W, padx=5, pady=5)

        self.bill_show_discount = tk.BooleanVar(value=bool(_bill_defaults.get('show_discount', True)))
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

    def _build_bill_panel(self):
        frame = self._panel('bill')
        bill_frame = ttk.LabelFrame(frame, text="Sale Bill Print Style")
        bill_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        ttk.Label(bill_frame, text="Bill template:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=4)
        from core.bill_config import AVAILABLE_TEMPLATES, load_bill_print_settings, BILL_PRINT_FIELD_OPTIONS
        self._bill_templates = AVAILABLE_TEMPLATES
        self.bill_template = ttk.Combobox(
            bill_frame, width=36, state="readonly",
            values=list(AVAILABLE_TEMPLATES.values()))
        self.bill_template.grid(row=0, column=1, sticky=tk.W, padx=5, pady=4)

        from core.bill_config import DEFAULT_BILL_PRINT_SETTINGS
        self._bill_field_vars = {}
        opts_outer = ttk.LabelFrame(bill_frame, text="Show on printed bill")
        opts_outer.grid(row=1, column=0, columnspan=2, sticky=tk.NSEW, padx=5, pady=4)
        row_idx = 0
        for _group_title, fields in BILL_PRINT_FIELD_OPTIONS:
            group = ttk.Frame(opts_outer)
            group.grid(row=row_idx, column=0, sticky=tk.W, padx=2, pady=(0, 4))
            for col, (key, label) in enumerate(fields):
                default = bool(DEFAULT_BILL_PRINT_SETTINGS.get(key, True))
                var = tk.BooleanVar(value=default)
                self._bill_field_vars[key] = var
                ttk.Checkbutton(group, variable=var, text=label).grid(
                    row=col // 3, column=col % 3, sticky=tk.W, padx=4, pady=1)
            row_idx += 1

        if hasattr(self, 'bill_show_gst'):
            self._bill_field_vars['show_gst'] = self.bill_show_gst
        elif 'show_gst' in self._bill_field_vars:
            self.bill_show_gst = self._bill_field_vars['show_gst']
        if hasattr(self, 'bill_show_discount'):
            self._bill_field_vars['show_discount'] = self.bill_show_discount
        elif 'show_discount' in self._bill_field_vars:
            self.bill_show_discount = self._bill_field_vars['show_discount']

        ttk.Label(bill_frame, text="Paper size:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=4)
        self.bill_paper = ttk.Combobox(
            bill_frame, width=12, state="readonly", values=["A5", "A4"])
        self.bill_paper.grid(row=2, column=1, sticky=tk.W, padx=5, pady=4)
        ttk.Label(
            bill_frame,
            text="A5 = 2 bills per sheet (classic). A4 = larger / optional 3 copies.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).grid(row=3, column=0, columnspan=2, sticky=tk.W, padx=5, pady=(0, 4))

        ttk.Label(bill_frame, text="Copies (A4 only):").grid(row=4, column=0, sticky=tk.W, padx=5, pady=4)
        self.bill_copies = ttk.Spinbox(bill_frame, from_=1, to=3, width=5)
        self.bill_copies.grid(row=4, column=1, sticky=tk.W, padx=5, pady=4)

        bill_save = ttk.Button(bill_frame, text="Save Bill Print Style", command=self._save_bill_only)
        bill_save.grid(row=5, column=1, sticky=tk.W, padx=5, pady=10)

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
        self.bill_paper.set(paper if paper in ("A4", "A5") else "A5")
        self.bill_copies.delete(0, tk.END)
        self.bill_copies.insert(0, str(int(settings.get("copies", 2))))

    def _collect_bill_settings(self):
        from core.bill_config import load_bill_print_settings, save_bill_print_settings
        merged = load_bill_print_settings()
        merged.update({
            "template": self._template_key_from_label(self.bill_template.get()),
            "paper_size": (self.bill_paper.get() or "A5").upper(),
            "copies": int(self.bill_copies.get() or 2),
        })
        for field_key, var in getattr(self, "_bill_field_vars", {}).items():
            merged[field_key] = bool(var.get())
        if hasattr(self, "bill_show_gst"):
            merged["show_gst"] = self.bill_show_gst.get()
        if hasattr(self, "bill_show_discount"):
            merged["show_discount"] = self.bill_show_discount.get()
        save_bill_print_settings(merged)
        return merged

    def _load(self):
        self.cursor.execute("""
            SELECT name, address, phone, email, gstin, dl_number,
                   gst_enabled, COALESCE(logo_path,''),
                   COALESCE(fssai_number,''), COALESCE(show_fssai_on_bill,0)
            FROM pharmacy_profile LIMIT 1
        """)
        profile = self.cursor.fetchone()
        if not profile:
            return
        self.pharmacy_name.insert(0, profile[0] or '')
        self.pharmacy_address.insert(tk.END, profile[1] or '')
        self.pharmacy_phone.insert(0, profile[2] or '')
        self.pharmacy_email.insert(0, profile[3] or '')
        self.pharmacy_gstin.insert(0, profile[4] or '')
        self.pharmacy_dl.insert(0, profile[5] or '')
        self.gst_enabled.set(bool(profile[6]))
        logo_path = profile[7] or ''
        if len(profile) > 8:
            self.pharmacy_fssai.insert(0, profile[8] or '')
        if len(profile) > 9:
            self.show_fssai_on_bill.set(bool(profile[9]))
        if logo_path and os.path.exists(logo_path):
            self._logo_path_var.set(logo_path)
            self._update_logo_preview(logo_path)
        from core.bill_config import load_bill_print_settings
        self._apply_bill_settings_to_ui(load_bill_print_settings())

    def save(self):
        try:
            logo_path = self._logo_path_var.get()
            if logo_path == "No logo selected":
                logo_path = ''
            self.cursor.execute("SELECT id FROM pharmacy_profile LIMIT 1")
            existing = self.cursor.fetchone()
            data = (
                self.pharmacy_name.get(),
                self.pharmacy_address.get(1.0, tk.END).strip(),
                self.pharmacy_phone.get(),
                self.pharmacy_email.get(),
                self.pharmacy_gstin.get(),
                self.pharmacy_dl.get(),
                self.gst_enabled.get(),
                logo_path,
                self.pharmacy_fssai.get().strip(),
                1 if self.show_fssai_on_bill.get() else 0,
            )
            if existing:
                self.cursor.execute("""
                    UPDATE pharmacy_profile SET name=?,address=?,phone=?,email=?,
                    gstin=?,dl_number=?,gst_enabled=?,logo_path=?,
                    fssai_number=?,show_fssai_on_bill=? WHERE id=?
                """, data + (existing[0],))
            else:
                self.cursor.execute("""
                    INSERT INTO pharmacy_profile
                    (name,address,phone,email,gstin,dl_number,gst_enabled,logo_path,
                     fssai_number,show_fssai_on_bill)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, data)
            self.conn.commit()
            self._collect_bill_settings()
            showinfo("Success", "Pharmacy profile and bill print settings saved!")
        except Exception as e:
            showerror("Error", f"Failed to save profile: {e}")
