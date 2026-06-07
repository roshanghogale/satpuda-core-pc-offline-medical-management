import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk
from tkinter import messagebox
import sqlite3
from core.font_config import *
from core.layout_config import DOCTORS_ROWS
from core.column_config import apply_column_visibility, all_column_names
from core.scroll_manager import make_scrollable, open_dialog
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno


class DoctorsTab:
    def __init__(self, parent, conn, embedded=False):
        self.conn = conn
        self.cursor = conn.cursor()
        if embedded:
            frame = ttk.Frame(parent)
            frame.pack(fill=tk.BOTH, expand=True)
        else:
            frame = make_scrollable(parent)
        self._build(frame)
        self.load()

    def _build(self, frame):
        add_form = ttk.LabelFrame(frame, text="Add Doctor")
        add_form.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(add_form, text="Doctor Name:").grid(row=0, column=0, padx=5, pady=5)
        self.doctor_name = ttk.Entry(add_form, width=25)
        self.doctor_name.grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(add_form, text="Registration No:").grid(row=0, column=2, padx=5, pady=5)
        self.doctor_reg_no = ttk.Entry(add_form, width=20)
        self.doctor_reg_no.grid(row=0, column=3, padx=5, pady=5)
        self.doctor_reg_no.insert(0, "e.g. MMC/2021/67890")
        self.doctor_reg_no.bind('<FocusIn>',
            lambda e: self.doctor_reg_no.delete(0, tk.END)
            if self.doctor_reg_no.get() == "e.g. MMC/2021/67890" else None)
        self.doctor_reg_no.bind('<FocusOut>',
            lambda e: self.doctor_reg_no.insert(0, "e.g. MMC/2021/67890")
            if not self.doctor_reg_no.get().strip() else None)

        ttk.Label(add_form, text="Phone:").grid(row=0, column=4, padx=5, pady=5)
        self.doctor_phone = ttk.Entry(add_form, width=15)
        self.doctor_phone.grid(row=0, column=5, padx=5, pady=5)

        add_btn = ttk.Button(add_form, text="Add Doctor", command=self.add)
        add_btn.grid(row=0, column=6, padx=10, pady=5)

        self.doctor_name.bind('<Return>', lambda e: self.doctor_reg_no.focus())
        self.doctor_name.bind('<Right>',  lambda e: self.doctor_reg_no.focus())
        self.doctor_reg_no.bind('<Return>', lambda e: self.doctor_phone.focus())
        self.doctor_reg_no.bind('<Right>',  lambda e: self.doctor_phone.focus())
        self.doctor_reg_no.bind('<Left>',   lambda e: self.doctor_name.focus())
        self.doctor_phone.bind('<Return>', lambda e: self.add())
        self.doctor_phone.bind('<Left>',   lambda e: self.doctor_reg_no.focus())
        add_btn.bind('<Return>', lambda e: self.add())

        list_frame = ttk.LabelFrame(frame, text="Doctors List  (Right-click to Edit / Delete)")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self._all_columns = tuple(all_column_names('doctors'))
        self.tree = ttk.Treeview(list_frame, columns=self._all_columns, show='headings',
                                 height=DOCTORS_ROWS, style='Large.Treeview')
        col_widths = {'Name': 180, 'Registration No': 160, 'Phone': 120, 'Created Date': 150}
        for col in self._all_columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=col_widths.get(col, 150))
        apply_column_visibility(self.tree, 'doctors', self._all_columns)

        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        from core.tree_action_menu import setup_tree_actions
        self._action_menu = setup_tree_actions(
            list_frame,
            self.tree,
            [
                ("Edit Doctor", self.edit),
                ("Delete Doctor", self.delete),
            ],
            escape_to=self.doctor_name,
        )
        self._menu = self._action_menu.ctx_menu

    def _show_menu(self, event):
        if self.tree.selection():
            self._menu.post(event.x_root, event.y_root)

    def _tree_menu(self):
        sel = self.tree.selection()
        if not sel:
            return
        try:
            bbox = self.tree.bbox(sel[0])
            if bbox:
                self._menu.post(
                    self.tree.winfo_rootx() + bbox[0],
                    self.tree.winfo_rooty() + bbox[1] + bbox[3])
        except Exception:
            pass

    def load(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.cursor.execute(
            "SELECT name, registration_number, phone, created_at, id FROM doctors ORDER BY name")
        self.doctors_data = self.cursor.fetchall()
        for row in self.doctors_data:
            self.tree.insert('', tk.END, values=row[:-1])

    def add(self):
        name   = self.doctor_name.get().strip().upper()
        reg_no = self.doctor_reg_no.get().strip()
        phone  = self.doctor_phone.get().strip()
        if reg_no == "e.g. MMC/2021/67890":
            reg_no = ''
        if not name:
            showwarning("Missing Information", "Please enter doctor name.")
            return
        if not reg_no:
            showwarning("Missing Information", "Please enter registration number.")
            return
        try:
            self.cursor.execute("SELECT id FROM doctors WHERE UPPER(name)=?", (name,))
            if self.cursor.fetchone():
                showwarning("Duplicate", "Doctor with this name already exists.")
                return
            self.cursor.execute(
                "INSERT INTO doctors (name, registration_number, phone) VALUES (?,?,?)",
                (name, reg_no, phone))
            self.conn.commit()
            showinfo("Success", "Doctor added successfully!")
            self.doctor_name.delete(0, tk.END)
            self.doctor_reg_no.delete(0, tk.END)
            self.doctor_reg_no.insert(0, "e.g. MMC/2021/67890")
            self.doctor_phone.delete(0, tk.END)
            self.load()
            self.doctor_name.focus()
        except sqlite3.IntegrityError:
            showerror("Error", "Doctor already exists.")
        except Exception as e:
            showerror("Error", f"Failed to add doctor: {e}")

    def edit(self):
        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0])['values']
        doctor_id = doctor_row = None
        for row in self.doctors_data:
            if str(row[0]) == str(values[0]):
                doctor_id, doctor_row = row[4], row
                break
        if not doctor_id:
            showerror("Error", "Could not find doctor record.")
            return

        dlg = open_dialog(self.tree, "Edit Doctor", width=500, height=290, resizable=False)
        body = dlg.content
        body.grid_columnconfigure(1, weight=1)

        ttk.Label(body, text="Doctor Name:").grid(row=0, column=0, padx=12, pady=10, sticky=tk.W)
        name_e = ttk.Entry(body, width=34)
        name_e.grid(row=0, column=1, padx=12, pady=10, sticky=tk.EW)
        name_e.insert(0, doctor_row[0])

        ttk.Label(body, text="Registration No:").grid(row=1, column=0, padx=12, pady=10, sticky=tk.W)
        reg_e = ttk.Entry(body, width=34)
        reg_e.grid(row=1, column=1, padx=12, pady=10, sticky=tk.EW)
        reg_e.insert(0, doctor_row[1] or '')

        ttk.Label(body, text="Phone:").grid(row=2, column=0, padx=12, pady=10, sticky=tk.W)
        phone_e = ttk.Entry(body, width=34)
        phone_e.grid(row=2, column=1, padx=12, pady=10, sticky=tk.EW)
        phone_e.insert(0, doctor_row[2] or '')

        def save():
            new_name  = name_e.get().strip().upper()
            new_reg   = reg_e.get().strip()
            new_phone = phone_e.get().strip()
            if not new_name:
                showwarning("Missing", "Doctor name cannot be empty.", parent=dlg)
                return
            if not new_reg:
                showwarning("Missing", "Registration number cannot be empty.", parent=dlg)
                return
            try:
                self.cursor.execute(
                    "UPDATE doctors SET name=?,registration_number=?,phone=? WHERE id=?",
                    (new_name, new_reg, new_phone, doctor_id))
                self.conn.commit()
                showinfo("Success", "Doctor updated successfully!")
                dlg.destroy()
                self.load()
            except Exception as e:
                showerror("Error", f"Failed to update doctor: {e}")

        name_e.bind('<Return>', lambda e: reg_e.focus())
        name_e.bind('<Down>',   lambda e: reg_e.focus())
        reg_e.bind('<Return>',  lambda e: phone_e.focus())
        reg_e.bind('<Down>',    lambda e: phone_e.focus())
        reg_e.bind('<Up>',      lambda e: name_e.focus())
        phone_e.bind('<Return>', lambda e: save())
        phone_e.bind('<Up>',     lambda e: reg_e.focus())
        dlg.bind('<Escape>', lambda e: dlg.destroy())

        sb = ttk.Button(dlg.footer, text="Save Changes", command=save)
        sb.pack(side=tk.LEFT, padx=8)
        cb = ttk.Button(dlg.footer, text="Cancel", command=dlg.destroy)
        cb.pack(side=tk.LEFT, padx=8)
        sb.bind('<Return>', lambda e: save())
        cb.bind('<Return>', lambda e: dlg.destroy())
        name_e.focus()

    def delete(self):
        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0])['values']
        if not askyesno("Confirm Delete", f"Delete doctor {values[0]}?"):
            return
        doctor_id = None
        for row in self.doctors_data:
            if str(row[0]) == str(values[0]):
                doctor_id = row[4]
                break
        if not doctor_id:
            showerror("Error", "Could not find doctor record.")
            return
        try:
            self.cursor.execute("DELETE FROM doctors WHERE id=?", (doctor_id,))
            self.conn.commit()
            showinfo("Success", "Doctor deleted successfully!")
            self.load()
        except Exception as e:
            showerror("Error", f"Failed to delete doctor: {e}")
