"""
ui/settings/settings_tabs/suppliers_tab.py
───────────────────────────────────────────
Supplier list page — aligned with centralized accounting model.

Phase 2 compliance:
  - Displays suppliers.total_due / total_credit (single source of truth)
  - Status: Due / Credit / Cleared derived from those columns
  - No aggregation from purchases table
  - No last-bill snapshot usage
"""
import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk
from tkinter import messagebox
from core.font_config import *
from core.alert_colors import get_alert_color
from core.layout_config import SUPPLIERS_ROWS
from core.column_config import apply_column_visibility, all_column_names
from core.scroll_manager import make_scrollable, open_dialog
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno


class SuppliersTab:
    def __init__(self, parent, conn):
        self.conn = conn
        self.cursor = conn.cursor()
        frame = make_scrollable(parent)
        self._build(frame)
        self.load()

    def _build(self, frame):
        # Summary bar at top
        sum_frame = ttk.LabelFrame(frame, text="Supplier Summary")
        sum_frame.pack(fill=tk.X, padx=10, pady=(10, 4))

        self.total_suppliers_var = tk.StringVar(value="0")
        self.total_due_var       = tk.StringVar(value="₹0.00")
        self.total_credit_var    = tk.StringVar(value="₹0.00")

        for col, (lbl, var, color) in enumerate([
            ("Total Suppliers:", self.total_suppliers_var, None),
            ("Total Due:",       self.total_due_var,       'danger'),
            ("Total Credit:",    self.total_credit_var,    'success'),
        ]):
            ttk.Label(sum_frame, text=lbl).grid(row=0, column=col * 2, padx=12, pady=6)
            kw = {'font': (FONT_FAMILY, FONT_SIZE_LABELS, 'bold')}
            if color:
                kw['foreground'] = get_alert_color(color)
            ttk.Label(sum_frame, textvariable=var, **kw).grid(
                row=0, column=col * 2 + 1, padx=12, pady=6)

        try:
            ttk.Button(sum_frame, text="↺ Recalculate All",
                       command=self._recalculate_all,
                       bootstyle="warning", width=18).grid(
                row=0, column=6, padx=12, pady=6)
        except Exception:
            ttk.Button(sum_frame, text="Recalculate All",
                       command=self._recalculate_all, width=18).grid(
                row=0, column=6, padx=12, pady=6)

        # Supplier list tree — Phase 2: includes Due / Credit / Status columns
        list_frame = ttk.LabelFrame(frame, text="Suppliers List")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self._all_columns = tuple(all_column_names('suppliers'))
        col_widths = {
            'Name': 180, 'Phone': 110, 'GSTIN': 140,
            'Address': 220, 'Total Due': 100, 'Credit': 90, 'Status': 80,
        }
        self.tree = ttk.Treeview(list_frame, columns=self._all_columns, show='headings',
                                 height=SUPPLIERS_ROWS, style='Large.Treeview')
        for col in self._all_columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=col_widths.get(col, 100))
        apply_column_visibility(self.tree, 'suppliers', self._all_columns)

        from core.alert_colors import get_tree_tag_colors
        clr = get_tree_tag_colors()
        self.tree.tag_configure('has_due',    background=clr['due_bg'],     foreground=clr['due_fg'])
        self.tree.tag_configure('has_credit', background=clr['cleared_bg'], foreground=clr['cleared_fg'])

        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self._menu = tk.Menu(list_frame, tearoff=0)
        self._menu.add_command(label="Edit Supplier",        command=self.edit)
        self._menu.add_command(label="Recalculate Balance",  command=self._recalc_selected)
        self._menu.add_separator()
        self._menu.add_command(label="Delete Supplier",      command=self.delete)
        self.tree.bind("<Button-3>",         self._show_menu)
        self.tree.bind("<Button-2>",         self._show_menu)
        self.tree.bind("<Control-Button-1>", self._show_menu)
        self.tree.bind('<Return>',  lambda e: self._tree_menu())
        self.tree.bind('<Escape>',
                       lambda e: self.tree.selection_remove(*self.tree.selection()))

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
        """
        Phase 2: reads total_due / total_credit from suppliers table.
        No aggregation from purchases.
        """
        for item in self.tree.get_children():
            self.tree.delete(item)

        self.cursor.execute("""
            SELECT id, name, phone, COALESCE(gstin,''), COALESCE(address,''),
                   COALESCE(total_due,0), COALESCE(total_credit,0)
            FROM suppliers ORDER BY name
        """)
        self.suppliers_data = self.cursor.fetchall()

        total_due    = 0.0
        total_credit = 0.0

        for row in self.suppliers_data:
            sid, name, phone, gstin, address, due, credit = row
            due    = float(due)
            credit = float(credit)
            total_due    += due
            total_credit += credit

            if due > 0:
                status = "Due"
                tag    = 'has_due'
            elif credit > 0:
                status = "Credit"
                tag    = 'has_credit'
            else:
                status = "Cleared"
                tag    = ''

            self.tree.insert('', tk.END, iid=str(sid), values=(
                name, phone or '', gstin, address,
                f"₹{due:.2f}"    if due    else '-',
                f"₹{credit:.2f}" if credit else '-',
                status,
            ), tags=(tag,) if tag else ())

        self.total_suppliers_var.set(str(len(self.suppliers_data)))
        self.total_due_var.set(f"₹{total_due:.2f}")
        self.total_credit_var.set(f"₹{total_credit:.2f}")

    def _recalc_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        supplier_id = int(sel[0])
        from core.purchase_service import recalculate_supplier_due
        recalculate_supplier_due(self.conn, supplier_id)
        self.load()

    def _recalculate_all(self):
        from core.purchase_service import recalculate_supplier_due
        for row in self.suppliers_data:
            try:
                recalculate_supplier_due(self.conn, row[0])
            except Exception as e:
                print(f"[RECALC] supplier {row[0]}: {e}")
        self.load()
        showinfo("Done", "All supplier balances recalculated.")

    def edit(self):
        sel = self.tree.selection()
        if not sel:
            return
        supplier_id = int(sel[0])
        values = self.tree.item(sel[0])['values']

        dlg = open_dialog(self.tree, "Edit Supplier", width=540, height=400, resizable=False)
        body = dlg.content
        body.grid_columnconfigure(1, weight=1)

        ttk.Label(body, text="Supplier Name:").grid(row=0, column=0, padx=12, pady=10, sticky=tk.W)
        name_e = ttk.Entry(body, width=36)
        name_e.grid(row=0, column=1, padx=12, pady=10, sticky=tk.EW)
        name_e.insert(0, values[0])

        ttk.Label(body, text="Phone:").grid(row=1, column=0, padx=12, pady=10, sticky=tk.W)
        phone_e = ttk.Entry(body, width=36)
        phone_e.grid(row=1, column=1, padx=12, pady=10, sticky=tk.EW)
        phone_e.insert(0, values[1])

        ttk.Label(body, text="GSTIN:").grid(row=2, column=0, padx=12, pady=10, sticky=tk.W)
        gstin_e = ttk.Entry(body, width=36)
        gstin_e.grid(row=2, column=1, padx=12, pady=10, sticky=tk.EW)
        gstin_e.insert(0, values[2])

        ttk.Label(body, text="Address:").grid(row=3, column=0, padx=12, pady=10, sticky=tk.W)
        addr_e = tk.Text(body, width=36, height=3)
        addr_e.grid(row=3, column=1, padx=12, pady=10, sticky=tk.EW)
        addr_e.insert(tk.END, values[3])

        def save():
            try:
                self.cursor.execute(
                    "UPDATE suppliers SET name=?,phone=?,gstin=?,address=? WHERE id=?",
                    (name_e.get(), phone_e.get(), gstin_e.get(),
                     addr_e.get(1.0, tk.END).strip(), supplier_id))
                self.conn.commit()
                showinfo("Success", "Supplier updated successfully!")
                dlg.destroy()
                self.load()
            except Exception as e:
                showerror("Error", f"Failed to update supplier: {e}")

        name_e.bind('<Return>',  lambda e: phone_e.focus())
        name_e.bind('<Down>',    lambda e: phone_e.focus())
        phone_e.bind('<Return>', lambda e: gstin_e.focus())
        phone_e.bind('<Down>',   lambda e: gstin_e.focus())
        phone_e.bind('<Up>',     lambda e: name_e.focus())
        gstin_e.bind('<Return>', lambda e: addr_e.focus())
        gstin_e.bind('<Down>',   lambda e: addr_e.focus())
        gstin_e.bind('<Up>',     lambda e: phone_e.focus())
        dlg.bind('<Escape>', lambda e: dlg.destroy())

        sb_btn = ttk.Button(dlg.footer, text="Save Changes", command=save)
        sb_btn.pack(side=tk.LEFT, padx=8)
        cb_btn = ttk.Button(dlg.footer, text="Cancel", command=dlg.destroy)
        cb_btn.pack(side=tk.LEFT, padx=8)
        sb_btn.bind('<Return>', lambda e: save())
        cb_btn.bind('<Return>', lambda e: dlg.destroy())
        name_e.focus()

    def delete(self):
        sel = self.tree.selection()
        if not sel:
            return
        supplier_id = int(sel[0])
        name = self.tree.item(sel[0])['values'][0]
        if not askyesno("Confirm Delete", f"Delete supplier {name}?"):
            return
        try:
            self.cursor.execute(
                "SELECT COUNT(*) FROM purchases WHERE supplier_id=?", (supplier_id,))
            if self.cursor.fetchone()[0] > 0:
                showwarning(
                    "Cannot Delete",
                    "Supplier has purchase history and cannot be deleted.")
                return
            self.cursor.execute("DELETE FROM suppliers WHERE id=?", (supplier_id,))
            self.conn.commit()
            showinfo("Success", "Supplier deleted successfully!")
            self.load()
        except Exception as e:
            showerror("Error", f"Failed to delete supplier: {e}")
