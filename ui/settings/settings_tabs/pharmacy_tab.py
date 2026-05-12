import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk
from tkinter import messagebox
import os
from core.font_config import *
from core.scroll_manager import make_scrollable


class PharmacyTab:
    def __init__(self, notebook, conn):
        self.conn = conn
        self.cursor = conn.cursor()
        outer = ttk.Frame(notebook)
        notebook.add(outer, text="Pharmacy Profile")
        frame = make_scrollable(outer)
        self._build(frame)
        self._load()

    def _build(self, frame):
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

        ttk.Label(form, text="Enable GST:").grid(row=6, column=0, sticky=tk.W, padx=5, pady=5)
        self.gst_enabled = tk.BooleanVar()
        ttk.Checkbutton(form, variable=self.gst_enabled, text="Show GST in bills").grid(
            row=6, column=1, sticky=tk.W, padx=5, pady=5)

        ttk.Label(form, text="Bill Logo:").grid(row=7, column=0, sticky=tk.W, padx=5, pady=5)
        logo_frame = ttk.Frame(form)
        logo_frame.grid(row=7, column=1, sticky=tk.W, padx=5, pady=5)
        self._logo_path_var = tk.StringVar(value="No logo selected")
        ttk.Label(logo_frame, textvariable=self._logo_path_var,
                  font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
                  foreground='gray', width=35, anchor='w').pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(logo_frame, text="📁 Choose Image", command=self._choose_logo).pack(side=tk.LEFT, padx=2)
        ttk.Button(logo_frame, text="✖ Remove", command=self._remove_logo).pack(side=tk.LEFT, padx=2)

        self._logo_preview = ttk.Label(form, text="")
        self._logo_preview.grid(row=8, column=1, sticky=tk.W, padx=5, pady=2)

        save_btn = ttk.Button(form, text="Save Profile", command=self.save)
        save_btn.grid(row=9, column=1, pady=10)

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
        self.pharmacy_dl.bind('<Return>', lambda e: self.save())
        self.pharmacy_dl.bind('<Down>',   lambda e: save_btn.focus())
        self.pharmacy_dl.bind('<Up>',     lambda e: self.pharmacy_gstin.focus())
        save_btn.bind('<Return>', lambda e: self.save())
        save_btn.bind('<Up>',     lambda e: self.pharmacy_dl.focus())

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

    def _load(self):
        self.cursor.execute("""
            SELECT name, address, phone, email, gstin, dl_number,
                   gst_enabled, COALESCE(logo_path,'') FROM pharmacy_profile LIMIT 1
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
        if logo_path and os.path.exists(logo_path):
            self._logo_path_var.set(logo_path)
            self._update_logo_preview(logo_path)

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
            )
            if existing:
                self.cursor.execute("""
                    UPDATE pharmacy_profile SET name=?,address=?,phone=?,email=?,
                    gstin=?,dl_number=?,gst_enabled=?,logo_path=? WHERE id=?
                """, data + (existing[0],))
            else:
                self.cursor.execute("""
                    INSERT INTO pharmacy_profile
                    (name,address,phone,email,gstin,dl_number,gst_enabled,logo_path)
                    VALUES (?,?,?,?,?,?,?,?)
                """, data)
            self.conn.commit()
            messagebox.showinfo("Success", "Pharmacy profile saved successfully!")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save profile: {e}")
