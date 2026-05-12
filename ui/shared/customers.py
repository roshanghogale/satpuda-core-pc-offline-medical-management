import tkinter as tk
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.alert_colors import get_alert_color
from core.font_config import *
from core.scroll_manager import make_scrollable
from core.customer_service import get_all_customers, recalculate_customer_due
from core.layout_config import CUSTOMERS_ROWS
from widgets.searchable_combo import SearchableCombo


class CustomersPage:
    def __init__(self, parent, conn):
        self.conn = conn
        self.cursor = conn.cursor()
        self.parent = parent
        self._all_data = []

        self._build_ui()
        self.load_customers()
        self.parent.after(100, self._setup_nav)
        self.parent.after(200, self.search_entry.focus)

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        inner = make_scrollable(self.parent)
        self._inner_frame = inner
        inner.configure(padding=(10, 10))

        # Filter bar
        filter_frame = ttk.LabelFrame(inner, text="Search Customers")
        filter_frame.pack(fill=tk.X, pady=5)

        ttk.Label(filter_frame, text="Search:").grid(row=0, column=0, padx=5, pady=5)
        self.search_entry = SearchableCombo(filter_frame, width=30)
        self.search_entry.grid(row=0, column=1, padx=5, pady=5)
        self.search_entry.bind('<<ComboboxSelected>>', self._filter)
        self.search_entry.entry.bind('<KeyRelease>', self._filter)
        self.search_entry.entry.bind('<FocusIn>', lambda e: self.load_customers(), add='+')

        ttk.Label(filter_frame, text="Due Filter:").grid(row=0, column=2, padx=5, pady=5)
        self.due_filter = SearchableCombo(
            filter_frame, values=['All', 'Due', 'Credit', 'Cleared'], width=14)
        self.due_filter.set('')
        self.due_filter.grid(row=0, column=3, padx=5, pady=5)
        self.due_filter.bind('<<ComboboxSelected>>', self._filter)
        self.due_filter.entry.bind('<FocusIn>', lambda e: self.due_filter.configure(values=['All', 'Due', 'Credit', 'Cleared']), add='+')

        try:
            ttk.Button(filter_frame, text="Recalculate All Dues",
                       command=self._recalculate_all,
                       bootstyle="warning").grid(row=0, column=4, padx=10, pady=5)
            ttk.Button(filter_frame, text="📤 Export",
                       command=self._export_menu,
                       bootstyle="info").grid(row=0, column=5, padx=10, pady=5)
        except Exception:
            ttk.Button(filter_frame, text="Recalculate All Dues",
                       command=self._recalculate_all).grid(row=0, column=4, padx=10, pady=5)
            ttk.Button(filter_frame, text="📤 Export",
                       command=self._export_menu).grid(row=0, column=5, padx=10, pady=5)

        # Treeview
        tree_frame = ttk.Frame(inner)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        cols = ('Name', 'Phone', 'Address', 'Total Due', 'Credit')
        self.tree = ttk.Treeview(
            tree_frame, columns=cols, show='headings',
            height=CUSTOMERS_ROWS, style='Large.Treeview')

        widths = {'Name': 200, 'Phone': 130, 'Address': 260,
                  'Total Due': 110, 'Credit': 110}
        for col in cols:
            self.tree.heading(col, text=col,
                              command=lambda c=col: self._sort(c))
            self.tree.column(col, width=widths.get(col, 120))

        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL,
                            command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL,
                            command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        from core.alert_colors import get_tree_tag_colors
        clr = get_tree_tag_colors()
        self.tree.tag_configure('has_due',    background=clr['due_bg'],     foreground=clr['due_fg'])
        self.tree.tag_configure('has_credit', background=clr['cleared_bg'], foreground=clr['cleared_fg'])

        # Context menu
        self.ctx_menu = tk.Menu(self.parent, tearoff=0)
        self.ctx_menu.add_command(label="View Sales History",
                                  command=self._view_history)
        self.ctx_menu.add_command(label="Recalculate Due",
                                  command=self._recalc_selected)
        self.tree.bind('<Button-3>', self._show_ctx)
        self.tree.bind('<Double-1>', self._view_history)
        self.tree.bind('<Return>',   lambda e: self._view_history())

        # Summary bar
        summary = ttk.LabelFrame(inner, text="Summary")
        summary.pack(fill=tk.X, pady=5)

        self.total_customers_var = tk.StringVar()
        self.total_due_var       = tk.StringVar()
        self.total_credit_var    = tk.StringVar()

        for i, (lbl, var, color) in enumerate([
            ("Total Customers:", self.total_customers_var, None),
            ("Total Due:",       self.total_due_var,       get_alert_color('danger')),
            ("Total Credit:",    self.total_credit_var,    get_alert_color('success')),
        ]):
            ttk.Label(summary, text=lbl).grid(row=0, column=i*2,     padx=10, pady=5)
            kw = {'font': (FONT_FAMILY, FONT_SIZE_LABELS, 'bold')}
            if color:
                kw['foreground'] = color
            ttk.Label(summary, textvariable=var, **kw).grid(
                row=0, column=i*2+1, padx=10, pady=5)

    # ── Data ─────────────────────────────────────────────────────────────

    def _export_menu(self):
        from core.scroll_manager import open_dialog
        dlg = open_dialog(self.parent, "Export Customer Reports", width=320, height=160, resizable=False)
        reports = [
            ("Customer List",        self._export_customer_list),
            ("Customer Due List",    self._export_customer_due),
        ]
        for label, cmd in reports:
            ttk.Button(dlg, text=label, width=36,
                       command=lambda c=cmd, d=dlg: [d.destroy(), c()]
                       ).pack(pady=4, padx=10)

    def _export_customer_list(self):
        from core.export_manager import export_data
        rows = [(c['name'], c['phone'] or '', c['address'] or '',
                 f"{float(c['total_due']):.2f}",
                 f"{float(c['total_credit']):.2f}")
                for c in self._all_data]
        if not rows:
            showinfo("No Records", "No customers found.", parent=self.parent)
            return
        headers = ['Name', 'Phone', 'Address', 'Total Due', 'Credit']
        export_data(self.parent, 'Customer List', headers, rows, 'customer_list')

    def _export_customer_due(self):
        from core.export_manager import export_data
        self.cursor.execute("""
            SELECT c.name, c.phone,
                   c.total_due, c.total_credit
            FROM customers c
            WHERE c.total_due > 0
            ORDER BY c.total_due DESC
        """)
        rows = self.cursor.fetchall()
        if not rows:
            showinfo("No Records", "No customers with outstanding dues found.", parent=self.parent)
            return
        headers = ['Customer', 'Phone', 'Total Due', 'Credit']
        export_data(self.parent, 'Customer Due List', headers, rows, 'customer_due_list')

    def load_customers(self):
        self._all_data = get_all_customers(self.conn)
        names = [c['name'] for c in self._all_data]
        self.search_entry.configure(values=names)
        self._render(self._all_data)

    def _render(self, data):
        for item in self.tree.get_children():
            self.tree.delete(item)

        total_due = total_credit = 0.0
        for c in data:
            due    = float(c['total_due'])
            credit = float(c['total_credit'])
            total_due    += due
            total_credit += credit

            tag = 'has_due' if due > 0 else ('has_credit' if credit > 0 else '')
            self.tree.insert('', tk.END,
                             iid=str(c['id']),
                             values=(c['name'], c['phone'] or '',
                                     c['address'] or '',
                                     f"{due:.2f}" if due else '',
                                     f"{credit:.2f}" if credit else ''),
                             tags=(tag,) if tag else ())

        self.total_customers_var.set(str(len(data)))
        self.total_due_var.set(f"₹{total_due:.2f}")
        self.total_credit_var.set(f"₹{total_credit:.2f}")

    def _filter(self, event=None):
        query  = self.search_entry.get().strip().upper()
        filt   = self.due_filter.get()
        result = []
        for c in self._all_data:
            if query and query not in c['name'].upper() \
                     and query not in (c['phone'] or ''):
                continue
            due    = float(c['total_due'])
            credit = float(c['total_credit'])
            if filt == 'Due'     and due    <= 0: continue
            if filt == 'Credit'  and credit <= 0: continue
            if filt == 'Cleared' and (due > 0 or credit > 0): continue
            # 'All' or '' shows everything
            result.append(c)
        self._render(result)

    # ── Sort ──────────────────────────────────────────────────────────────

    _sort_state = {}

    def _sort(self, col):
        rev = self._sort_state.get(col, False)
        key_map = {
            'Name': lambda c: c['name'],
            'Phone': lambda c: c['phone'] or '',
            'Address': lambda c: c['address'] or '',
            'Total Due': lambda c: float(c['total_due']),
            'Credit': lambda c: float(c['total_credit']),
        }
        self._all_data.sort(key=key_map[col], reverse=rev)
        self._sort_state[col] = not rev
        self._filter()

    # ── Actions ───────────────────────────────────────────────────────────

    def _show_ctx(self, event):
        if self.tree.selection():
            self.ctx_menu.post(event.x_root, event.y_root)

    def _view_history(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        customer_id   = int(sel[0])
        customer_name = self.tree.item(sel[0])['values'][0]
        self._open_history_dialog(customer_id, customer_name)

    def _open_history_dialog(self, customer_id, customer_name):
        from core.scroll_manager import open_dialog
        dlg = open_dialog(self.parent, f"Sales History — {customer_name}",
                          width=960, height=640, resizable=False)

        cols = ('Date', 'Bill No', 'Total', 'Paid', 'Due', 'Total Due')
        tree = ttk.Treeview(dlg, columns=cols, show='headings',
                            height=18, style='Large.Treeview')
        for col in cols:
            tree.heading(col, text=col)
            tree.column(col, width=130)
        tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.cursor.execute(
            """SELECT bill_date, bill_no, total_amount, amount_paid,
                      COALESCE(due_amount,0), COALESCE(total_due,0)
               FROM sales WHERE customer_id=? ORDER BY id DESC""",
            (customer_id,)
        )
        for row in self.cursor.fetchall():
            tree.insert('', tk.END, values=row)

    def _recalc_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        recalculate_customer_due(self.conn, int(sel[0]))
        self.load_customers()

    def _recalculate_all(self):
        for c in self._all_data:
            recalculate_customer_due(self.conn, c['id'])
        self.load_customers()
        showinfo("Done", "All customer dues recalculated.", parent=self.parent)

    # ── Keyboard nav ──────────────────────────────────────────────────────

    def _setup_nav(self):
        self.tree.bind('<Escape>', lambda e: self.search_entry.focus())
