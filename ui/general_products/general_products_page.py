"""
General Products — standalone rate list (not linked to sales, purchase, or inventory).
"""
import tkinter as tk

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.font_config import (
    FONT_FAMILY, FONT_SIZE_DEFAULT, FONT_SIZE_LABELS, FONT_SIZE_SECTION_TITLE,
)
from core.scroll_manager import make_scrollable, bind_scroll_descendants, refresh_scroll_region
from core.general_product_service import list_products, save_product, delete_product
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno


class GeneralProductsPage:
    def __init__(self, parent, conn):
        self.parent = parent
        self.conn = conn
        self._editing_id = None

        self._inner_frame = make_scrollable(parent)
        inner = self._inner_frame
        inner.configure(padding=(12, 12))

        ttk.Label(
            inner,
            text='General Products',
            font=(FONT_FAMILY, FONT_SIZE_SECTION_TITLE + 2, 'bold'),
        ).pack(anchor=tk.W, pady=(0, 4))
        ttk.Label(
            inner,
            text='Reference rates only — not used in billing, purchase, or stock.',
            font=(FONT_FAMILY, FONT_SIZE_DEFAULT),
            foreground='gray',
        ).pack(anchor=tk.W, pady=(0, 12))

        self._build_entry_panel(inner)
        self._build_list_panel(inner)

        self._refresh_list()
        bind_scroll_descendants(inner, force=True)
        refresh_scroll_region(inner)
        parent.after(50, lambda: bind_scroll_descendants(inner, force=True))
        self.name_entry.focus_set()

    def _build_entry_panel(self, parent):
        frame = ttk.LabelFrame(parent, text='Add / Update Product')
        frame.pack(fill=tk.X, pady=(0, 12))

        row = ttk.Frame(frame)
        row.pack(fill=tk.X, padx=12, pady=12)

        ttk.Label(row, text='Name *', font=(FONT_FAMILY, FONT_SIZE_LABELS)).pack(
            side=tk.LEFT, padx=(0, 6))
        self.name_entry = ttk.Entry(row, width=28)
        self.name_entry.pack(side=tk.LEFT, padx=(0, 12))
        self.name_entry.bind('<Return>', lambda e: self.rate_entry.focus_set())

        ttk.Label(row, text='Rate (₹)', font=(FONT_FAMILY, FONT_SIZE_LABELS)).pack(
            side=tk.LEFT, padx=(0, 6))
        self.rate_entry = ttk.Entry(row, width=10)
        self.rate_entry.pack(side=tk.LEFT, padx=(0, 12))
        self.rate_entry.bind('<Return>', lambda e: self.mrp_entry.focus_set())

        ttk.Label(row, text='MRP (₹)', font=(FONT_FAMILY, FONT_SIZE_LABELS)).pack(
            side=tk.LEFT, padx=(0, 6))
        self.mrp_entry = ttk.Entry(row, width=10)
        self.mrp_entry.pack(side=tk.LEFT, padx=(0, 16))
        self.mrp_entry.bind('<Return>', lambda e: self._save())

        try:
            ttk.Button(row, text='Save', bootstyle='success', command=self._save).pack(
                side=tk.LEFT, padx=(0, 6))
            ttk.Button(row, text='Clear', bootstyle='secondary', command=self._clear_form).pack(
                side=tk.LEFT, padx=(0, 6))
            self.delete_btn = ttk.Button(
                row, text='Delete', bootstyle='danger', command=self._delete, state=tk.DISABLED)
        except Exception:
            ttk.Button(row, text='Save', command=self._save).pack(side=tk.LEFT, padx=(0, 6))
            ttk.Button(row, text='Clear', command=self._clear_form).pack(side=tk.LEFT, padx=(0, 6))
            self.delete_btn = ttk.Button(
                row, text='Delete', command=self._delete, state=tk.DISABLED)
        self.delete_btn.pack(side=tk.LEFT)

        self.status_var = tk.StringVar(value='')
        ttk.Label(
            frame, textvariable=self.status_var,
            font=(FONT_FAMILY, FONT_SIZE_DEFAULT), foreground='#15803d',
        ).pack(anchor=tk.W, padx=12, pady=(0, 8))

    def _build_list_panel(self, parent):
        frame = ttk.LabelFrame(parent, text='General Items List')
        frame.pack(fill=tk.BOTH, expand=True)

        search_row = ttk.Frame(frame)
        search_row.pack(fill=tk.X, padx=12, pady=(12, 8))
        ttk.Label(search_row, text='Search:', font=(FONT_FAMILY, FONT_SIZE_LABELS)).pack(
            side=tk.LEFT, padx=(0, 8))
        self.search_var = tk.StringVar()
        self.search_var.trace_add('write', lambda *_: self._refresh_list())
        search_entry = ttk.Entry(search_row, textvariable=self.search_var, width=36)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        try:
            ttk.Button(search_row, text='Refresh', bootstyle='info', command=self._refresh_list).pack(
                side=tk.LEFT, padx=(8, 0))
        except Exception:
            ttk.Button(search_row, text='Refresh', command=self._refresh_list).pack(
                side=tk.LEFT, padx=(8, 0))

        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        cols = ('name', 'rate', 'mrp')
        self.tree = ttk.Treeview(
            tree_frame, columns=cols, show='headings', height=16, style='Large.Treeview',
        )
        self.tree.heading('name', text='Product Name')
        self.tree.heading('rate', text='Rate (₹)')
        self.tree.heading('mrp', text='MRP (₹)')
        self.tree.column('name', width=320, anchor=tk.W)
        self.tree.column('rate', width=100, anchor=tk.E)
        self.tree.column('mrp', width=100, anchor=tk.E)

        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind('<<TreeviewSelect>>', self._on_select)

        from core.tree_action_menu import setup_tree_actions
        setup_tree_actions(
            parent,
            self.tree,
            [
                ("Load to Form", self._load_selected),
                ("Delete Product", self._delete),
            ],
            on_double=self._load_selected,
            escape_to=self.name_entry,
        )

        self.count_var = tk.StringVar(value='0 items')
        ttk.Label(
            frame, textvariable=self.count_var,
            font=(FONT_FAMILY, FONT_SIZE_DEFAULT), foreground='gray',
        ).pack(anchor=tk.W, padx=12, pady=(0, 10))

    def _parse_amount(self, raw, label):
        text = (raw or '').strip()
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError:
            raise ValueError(f'{label} must be a number.')

    def _save(self):
        name = self.name_entry.get().strip()
        if not name:
            showwarning('General Products', 'Enter a product name.', parent=self.parent)
            return
        try:
            rate = self._parse_amount(self.rate_entry.get(), 'Rate')
            mrp = self._parse_amount(self.mrp_entry.get(), 'MRP')
            pid = save_product(self.conn, name, rate, mrp, self._editing_id)
            self._editing_id = pid
            self.status_var.set(f'Saved: {name}')
            self.delete_btn.configure(state=tk.NORMAL)
            self._refresh_list()
            self._select_id(pid)
        except Exception as e:
            showerror('General Products', str(e), parent=self.parent)

    def _clear_form(self):
        self._editing_id = None
        self.name_entry.delete(0, tk.END)
        self.rate_entry.delete(0, tk.END)
        self.mrp_entry.delete(0, tk.END)
        self.status_var.set('')
        self.delete_btn.configure(state=tk.DISABLED)
        self.tree.selection_remove(self.tree.selection())
        self.name_entry.focus_set()

    def _delete(self):
        if not self._editing_id:
            return
        if not askyesno('Delete', 'Delete this general product?', parent=self.parent):
            return
        try:
            delete_product(self.conn, self._editing_id)
            showinfo('General Products', 'Product deleted.', parent=self.parent)
            self._clear_form()
            self._refresh_list()
        except Exception as e:
            showerror('General Products', str(e), parent=self.parent)

    def _refresh_list(self):
        search = self.search_var.get()
        rows = list_products(self.conn, search)
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            self.tree.insert(
                '', tk.END, iid=str(row['id']),
                values=(
                    row['name'],
                    f"{row['rate']:.2f}",
                    f"{row['mrp']:.2f}",
                ),
            )
        self.count_var.set(f'{len(rows)} item(s)')

    def _select_id(self, product_id):
        iid = str(product_id)
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.see(iid)

    def _on_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        self._load_from_iid(sel[0])

    def _load_selected(self):
        sel = self.tree.selection()
        if sel:
            self._load_from_iid(sel[0])

    def _load_from_iid(self, iid):
        vals = self.tree.item(iid, 'values')
        if not vals:
            return
        self._editing_id = int(iid)
        self.name_entry.delete(0, tk.END)
        self.name_entry.insert(0, vals[0])
        self.rate_entry.delete(0, tk.END)
        self.rate_entry.insert(0, vals[1])
        self.mrp_entry.delete(0, tk.END)
        self.mrp_entry.insert(0, vals[2])
        self.delete_btn.configure(state=tk.NORMAL)
        self.status_var.set('Editing — change fields and click Save')
