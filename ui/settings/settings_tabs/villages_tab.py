"""Settings → Contacts: customer villages for sales address dropdown."""
import tkinter as tk

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.font_config import *
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno
from core.village_service import (
    load_villages,
    get_default_village,
    save_villages,
    add_village,
)


class VillagesTab:
    def __init__(self, parent, conn, embedded=False):
        self.conn = conn
        if embedded:
            frame = ttk.Frame(parent)
            frame.pack(fill=tk.BOTH, expand=True)
        else:
            from core.scroll_manager import make_scrollable
            frame = make_scrollable(parent)
        self._build(frame)
        self.load()

    def _build(self, frame):
        add_form = ttk.LabelFrame(frame, text="Add Village")
        add_form.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(add_form, text="Village Name:").grid(row=0, column=0, padx=5, pady=5)
        self.village_name = ttk.Entry(add_form, width=30)
        self.village_name.grid(row=0, column=1, padx=5, pady=5)

        try:
            add_btn = ttk.Button(add_form, text="Add Village", command=self.add, bootstyle="primary")
        except Exception:
            add_btn = ttk.Button(add_form, text="Add Village", command=self.add)
        add_btn.grid(row=0, column=2, padx=10, pady=5)

        self.village_name.bind('<Return>', lambda e: self.add())
        add_btn.bind('<Return>', lambda e: self.add())

        list_frame = ttk.LabelFrame(frame, text="Villages")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        cols = ('name', 'default')
        self.tree = ttk.Treeview(list_frame, columns=cols, show='headings', height=12)
        self.tree.heading('name', text='Village')
        self.tree.heading('default', text='Default')
        self.tree.column('name', width=280)
        self.tree.column('default', width=80, anchor=tk.CENTER)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=scroll.set)

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=tk.X, padx=10, pady=5)
        try:
            ttk.Button(btn_row, text="Set as Default", command=self.set_default,
                       bootstyle="info-outline").pack(side=tk.LEFT, padx=4)
            ttk.Button(btn_row, text="Remove", command=self.remove,
                       bootstyle="danger-outline").pack(side=tk.LEFT, padx=4)
        except Exception:
            ttk.Button(btn_row, text="Set as Default", command=self.set_default).pack(side=tk.LEFT, padx=4)
            ttk.Button(btn_row, text="Remove", command=self.remove).pack(side=tk.LEFT, padx=4)

        ttk.Label(
            frame,
            text="Default village auto-fills the sales address. Press Enter on customer name to skip ahead.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=520,
        ).pack(anchor=tk.W, padx=12, pady=(0, 8))

    def load(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        default = get_default_village(self.conn)
        for name in load_villages(self.conn):
            is_default = default and name.upper() == default.upper()
            self.tree.insert('', tk.END, values=(name, '✓' if is_default else ''))

    def add(self):
        name = self.village_name.get().strip()
        if not name:
            showwarning("Missing Name", "Enter a village name.", parent=self.village_name.winfo_toplevel())
            return
        try:
            add_village(self.conn, name)
        except Exception as exc:
            showerror("Online mode", str(exc), parent=self.village_name.winfo_toplevel())
            return
        self.village_name.delete(0, tk.END)
        self.load()
        showinfo("Added", f"Village '{name}' added.", parent=self.village_name.winfo_toplevel())

    def _selected_name(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return self.tree.item(sel[0], 'values')[0]

    def set_default(self):
        name = self._selected_name()
        if not name:
            showwarning("Select Village", "Select a village from the list.", parent=self.tree.winfo_toplevel())
            return
        try:
            save_villages(self.conn, load_villages(self.conn), name)
        except Exception as exc:
            showerror("Online mode", str(exc), parent=self.tree.winfo_toplevel())
            return
        self.load()
        showinfo("Default Set", f"'{name}' is now the default village.", parent=self.tree.winfo_toplevel())

    def remove(self):
        name = self._selected_name()
        if not name:
            showwarning("Select Village", "Select a village to remove.", parent=self.tree.winfo_toplevel())
            return
        if not askyesno("Remove Village", f"Remove '{name}' from the list?", parent=self.tree.winfo_toplevel()):
            return
        villages = [v for v in load_villages(self.conn) if v.upper() != name.upper()]
        default = get_default_village(self.conn)
        if default and default.upper() == name.upper():
            default = villages[0] if villages else ''
        try:
            save_villages(self.conn, villages, default)
        except Exception as exc:
            showerror("Online mode", str(exc), parent=self.tree.winfo_toplevel())
            return
        self.load()
