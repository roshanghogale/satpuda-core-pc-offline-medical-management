"""
ui/purchase/purchase_history.py
────────────────────────────────
Purchase History page — aligned with centralized accounting model.

Data sources (Phase 1 compliance):
  - purchases.total_amount          ← bill value
  - purchases.amount_paid_at_entry  ← payment made at purchase time (write-once)
  - purchase_returns.refund_amount  ← per-bill returns (summed)
  - suppliers.total_due             ← authoritative supplier balance
  - suppliers.total_credit          ← authoritative supplier credit

REMOVED:
  - purchases.total_due             (stale snapshot — not used)
  - purchases.credit_amount         (stale snapshot — not used)
  - mutated purchases.amount_paid   (not used for display)
  - "Paid via Payment" distribution column (misleading — removed)
"""
import tkinter as tk
from tkinter import ttk, messagebox
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno
from datetime import date, datetime
import sqlite3
import logging

from core.alert_colors import get_alert_color
from core.font_config import *
from core.layout_config import PURCHASE_HISTORY_ROWS, SCHEDULES
from core.column_config import apply_column_visibility, all_column_names
from core.scroll_manager import make_scrollable, open_dialog
from core.export_manager import export_data
from core.column_config import export_table
from widgets.searchable_combo import SearchableCombo
from ui.purchase.purchase_history_edit import open_edit_window, delete_purchase


class PurchaseHistoryPage:

    def __init__(self, parent, conn):
        self.conn   = conn
        self.cursor = conn.cursor()
        self.parent = parent
        self.purchase_data = []

        self._build_ui()
        self._load_supplier_filter()
        self.load_purchase_history()
        self.parent.after(100, self._setup_arrow_nav)
        self.parent.after(200, self.from_date.focus)

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        main_frame = make_scrollable(self.parent)
        self._inner_frame = main_frame
        main_frame.configure(padding=(10, 10))

        # Filter bar
        ff = ttk.LabelFrame(main_frame, text="Filter Options")
        ff.pack(fill=tk.X, pady=5)

        ttk.Label(ff, text="From Date:").grid(row=0, column=0, padx=5, pady=5)
        self.from_date = ttk.Entry(ff, width=12)
        self.from_date.grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(ff, text="To Date:").grid(row=0, column=2, padx=5, pady=5)
        self.to_date = ttk.Entry(ff, width=12)
        self.to_date.grid(row=0, column=3, padx=5, pady=5)

        ttk.Label(ff, text="Fin Year:").grid(row=0, column=4, padx=5, pady=5)
        self._fy_years = self._build_fy_list()
        self.fy_filter = SearchableCombo(ff, values=self._fy_years, width=10)
        self.fy_filter.grid(row=0, column=5, padx=5, pady=5)
        self.fy_filter.entry.bind('<<ComboboxSelected>>', lambda e: self._apply_fy_filter(), add='+')
        self.fy_filter.entry.bind('<Return>', lambda e: self._apply_fy_filter(), add='+')

        ttk.Label(ff, text="Supplier:").grid(row=1, column=0, padx=5, pady=5)
        self.supplier_filter = SearchableCombo(ff, width=20)
        self.supplier_filter.grid(row=1, column=1, padx=5, pady=5)
        self.supplier_filter.entry.bind('<FocusIn>', lambda e: self._load_supplier_filter(), add='+')

        ttk.Label(ff, text="Status:").grid(row=1, column=2, padx=5, pady=5)
        self.due_filter = SearchableCombo(
            ff, values=['Due Only', 'Credit Only', 'Paid / Cleared'], width=15)
        self.due_filter.grid(row=1, column=3, padx=5, pady=5)

        ttk.Label(ff, text="Schedule:").grid(row=1, column=4, padx=5, pady=5)
        self.schedule_filter = SearchableCombo(
            ff, values=[s for s in SCHEDULES if s] + ['Non-Scheduled'], width=15)
        self.schedule_filter.grid(row=1, column=5, padx=5, pady=5)

        self.apply_btn = ttk.Button(ff, text="Apply Filter", command=self.apply_filter)
        self.apply_btn.grid(row=0, column=6, padx=10, pady=5)
        ttk.Button(ff, text="Clear Filter", command=self.clear_filter).grid(
            row=1, column=6, padx=5, pady=5)
        try:
            ttk.Button(ff, text="📤 Export", command=self._export_menu,
                       bootstyle="info").grid(row=0, column=7, padx=10, pady=5,
                                              rowspan=2, sticky='ns')
        except Exception:
            ttk.Button(ff, text="📤 Export", command=self._export_menu).grid(
                row=0, column=7, padx=10, pady=5, rowspan=2, sticky='ns')

        # Tree — Phase 3: removed "Paid via Payment" column
        tf = ttk.Frame(main_frame)
        tf.pack(fill=tk.BOTH, expand=True, pady=5)

        self._all_columns = tuple(all_column_names('purchase_history'))
        widths = {
            'Bill No': 110, 'Date': 95, 'Supplier': 160, 'Phone': 105,
            'Final Amount': 110, 'Paid at Entry': 105, 'Paid via Payment': 115,
            'Returns': 80, 'Entry Due': 90, 'Status': 90, 'Items': 55,
        }
        self.purchase_tree = ttk.Treeview(
            tf, columns=self._all_columns, show='headings',
            height=PURCHASE_HISTORY_ROWS, style='Large.Treeview')
        for col in self._all_columns:
            self.purchase_tree.heading(col, text=col)
            self.purchase_tree.column(col, width=widths.get(col, 90))
        apply_column_visibility(self.purchase_tree, 'purchase_history', self._all_columns)

        vsb = ttk.Scrollbar(tf, orient=tk.VERTICAL,   command=self.purchase_tree.yview)
        hsb = ttk.Scrollbar(tf, orient=tk.HORIZONTAL, command=self.purchase_tree.xview)
        self.purchase_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.purchase_tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        tf.grid_rowconfigure(0, weight=1)
        tf.grid_columnconfigure(0, weight=1)

        from core.alert_colors import get_tree_tag_colors as _gtc
        _clr = _gtc()
        self.purchase_tree.tag_configure('account_cleared', background=_clr['cleared_bg'], foreground=_clr['cleared_fg'])
        self.purchase_tree.tag_configure('has_due',         background=_clr['due_bg'],     foreground=_clr['due_fg'])

        self._ctx = tk.Menu(self.parent, tearoff=0)
        self._ctx.add_command(label="Edit Purchase",   command=self.edit_purchase)
        self._ctx.add_command(label="Delete Purchase", command=self.delete_purchase)
        for ev in ("<Button-3>", "<Button-2>", "<Control-Button-1>"):
            self.purchase_tree.bind(ev, self._show_ctx)
        self.purchase_tree.bind("<Double-1>", self.edit_purchase)

        # Summary — Phase 7: uses suppliers table for due/credit
        sf = ttk.LabelFrame(main_frame, text="Purchase Summary")
        sf.pack(fill=tk.X, pady=5)

        self.total_purchases_var = tk.StringVar()
        self.total_amount_var    = tk.StringVar()
        self.total_entry_paid_var= tk.StringVar()
        self.total_returns_var   = tk.StringVar()
        self.total_due_var       = tk.StringVar()
        self.total_credit_var    = tk.StringVar()
        self.total_items_var     = tk.StringVar()

        summary_fields = [
            ("Total Purchases:",      self.total_purchases_var, None),
            ("Final Amount:",          self.total_amount_var,    'success'),
            ("Paid at Entry:",         self.total_entry_paid_var,None),
            ("Total Returns:",         self.total_returns_var,   'info'),
            ("Total Due (All Time):",  self.total_due_var,       'danger'),
            ("Total Credit (All Time):",self.total_credit_var,   'success'),
            ("Total Items:",           self.total_items_var,     None),
        ]
        for col, (lbl, var, color) in enumerate(summary_fields):
            ttk.Label(sf, text=lbl).grid(row=0, column=col * 2, padx=8, pady=5)
            kw = {'font': (FONT_FAMILY, FONT_SIZE_LABELS, 'bold')}
            if color:
                kw['foreground'] = get_alert_color(color)
            ttk.Label(sf, textvariable=var, **kw).grid(
                row=0, column=col * 2 + 1, padx=8, pady=5)

    # ── Data loading ──────────────────────────────────────────────────────

    def _load_supplier_filter(self):
        try:
            self.cursor.execute("SELECT DISTINCT name FROM suppliers ORDER BY name")
            self.supplier_filter.configure(
                values=[r[0] for r in self.cursor.fetchall()])
        except sqlite3.Error as e:
            logging.error(f"Error loading suppliers: {e}")

    def _base_query(self, use_schedule_join=False):
        """
        Fetches final_amount (after discount/rounding), paid_at_entry,
        paid_via_payment (from supplier_payments), and returns per bill.
        """
        pay_sub = ("(SELECT COALESCE(SUM(sp.amount),0) FROM supplier_payments sp "
                   " WHERE sp.supplier_id=p.supplier_id "
                   " AND sp.id <= (SELECT COALESCE(MAX(sp2.id),0) FROM supplier_payments sp2 "
                   "               WHERE sp2.supplier_id=p.supplier_id))")
        # Simpler: just show total payments for the supplier (not per-bill distributed)
        pay_sub = ("COALESCE((SELECT SUM(sp.amount) FROM supplier_payments sp "
                   "          WHERE sp.supplier_id=p.supplier_id), 0)")
        if use_schedule_join:
            return (
                "SELECT DISTINCT COALESCE(p.bill_number,p.purchase_no), p.purchase_date,"
                " s.name, s.phone,"
                " COALESCE(p.final_amount, p.total_amount),"
                " COALESCE(p.amount_paid_at_entry, p.amount_paid, 0),"
                " COALESCE((SELECT SUM(pr.refund_amount) FROM purchase_returns pr"
                "           WHERE pr.purchase_id=p.id), 0),"
                " (SELECT COUNT(*) FROM purchase_items WHERE purchase_id=p.id),"
                " p.id, p.account_cleared,"
                " p.supplier_id"
                " FROM purchases p"
                " JOIN suppliers s ON p.supplier_id=s.id"
                " JOIN purchase_items pi ON p.id=pi.purchase_id"
                " WHERE 1=1"
            )
        return (
            "SELECT COALESCE(p.bill_number,p.purchase_no), p.purchase_date,"
            " s.name, s.phone,"
            " COALESCE(p.final_amount, p.total_amount),"
            " COALESCE(p.amount_paid_at_entry, p.amount_paid, 0),"
            " COALESCE((SELECT SUM(pr.refund_amount) FROM purchase_returns pr"
            "           WHERE pr.purchase_id=p.id), 0),"
            " COUNT(pi.id),"
            " p.id, p.account_cleared,"
            " p.supplier_id"
            " FROM purchases p"
            " JOIN suppliers s ON p.supplier_id=s.id"
            " LEFT JOIN purchase_items pi ON p.id=pi.purchase_id"
            " WHERE 1=1"
        )

    def load_purchase_history(self):
        for item in self.purchase_tree.get_children():
            self.purchase_tree.delete(item)
        try:
            q = self._base_query() + " GROUP BY p.id ORDER BY p.purchase_date DESC, p.id DESC"
            self.cursor.execute(q)
            self.purchase_data = self.cursor.fetchall()
        except sqlite3.Error as e:
            logging.error(f"Error loading purchase history: {e}")
            self.purchase_data = []
            showerror("Database Error", "Failed to load purchase history.")

        self._populate_tree(self.purchase_data)
        self.update_summary()

    def apply_filter(self):
        fd  = self.from_date.get().strip()
        td  = self.to_date.get().strip()
        sup = self.supplier_filter.get().strip()
        due = self.due_filter.get()
        sch = self.schedule_filter.get()

        use_sch = sch and sch != 'All'
        q = self._base_query(use_schedule_join=use_sch)
        params = []

        if fd:  q += " AND p.purchase_date>=?"; params.append(fd)
        if td:  q += " AND p.purchase_date<=?"; params.append(td)
        if sup: q += " AND s.name=?";           params.append(sup)
        if use_sch:
            if sch == 'Non-Scheduled':
                q += " AND (pi.schedule IS NULL OR pi.schedule='')"
            else:
                q += " AND pi.schedule=?"; params.append(sch)

        if not use_sch:
            q += " GROUP BY p.id"
        q += " ORDER BY p.purchase_date DESC, p.id DESC"

        self.cursor.execute(q, params)
        data = self.cursor.fetchall()

        # due filter uses final_amount vs entry_paid + returns
        if due:
            filtered = []
            for p in data:
                final_amt   = float(p[4] or 0)
                entry_paid  = float(p[5] or 0)
                returns     = float(p[6] or 0)
                entry_due   = round(max(0.0, final_amt - entry_paid - returns), 2)
                if due == 'Due Only'         and entry_due > 0:               filtered.append(p)
                elif due == 'Credit Only'    and entry_paid + returns > final_amt: filtered.append(p)
                elif due == 'Paid / Cleared' and entry_due == 0:              filtered.append(p)
            data = filtered

        for item in self.purchase_tree.get_children():
            self.purchase_tree.delete(item)
        self._populate_tree(data)
        self.update_summary(data)

    def _populate_tree(self, data):
        """
        Shows final_amount (after discount/rounding), paid_at_entry,
        paid_via_payment (supplier payments distributed oldest-first),
        and entry_due = final_amount - entry_paid - paid_via_payment - returns.
        """
        # Build per-supplier payment pool distributed oldest-first
        supplier_ids = list({p[10] for p in data if p[10]})
        paid_via_payment_map = {}  # purchase_id -> amount paid via supplier_payments
        for sid in supplier_ids:
            self.cursor.execute(
                "SELECT id, COALESCE(final_amount,total_amount), "
                "COALESCE(amount_paid_at_entry,amount_paid,0) "
                "FROM purchases WHERE supplier_id=? ORDER BY id ASC", (sid,))
            bills = self.cursor.fetchall()
            self.cursor.execute(
                "SELECT COALESCE(SUM(amount),0) FROM supplier_payments WHERE supplier_id=?",
                (sid,))
            pool = round(float(self.cursor.fetchone()[0] or 0), 2)
            for bill_id, bill_final, bill_entry_paid in bills:
                bill_final      = float(bill_final or 0)
                bill_entry_paid = float(bill_entry_paid or 0)
                unpaid = round(max(0.0, bill_final - bill_entry_paid), 2)
                if pool <= 0 or unpaid <= 0:
                    paid_via_payment_map[bill_id] = 0.0
                else:
                    applied = min(pool, unpaid)
                    paid_via_payment_map[bill_id] = round(applied, 2)
                    pool = round(pool - applied, 2)

        for p in data:
            final_amt  = float(p[4] or 0)
            entry_paid = float(p[5] or 0)
            returns    = float(p[6] or 0)
            item_count = p[7]
            purchase_id= p[8]
            ac         = p[9]
            via_payment= paid_via_payment_map.get(purchase_id, 0.0)

            entry_due = round(max(0.0, final_amt - entry_paid - via_payment - returns), 2)

            if ac == 1 or entry_due == 0:
                status = "Cleared"
                tags   = ['account_cleared']
            elif entry_paid + via_payment > 0:
                status = "Partial"
                tags   = ['has_due']
            else:
                status = "Due"
                tags   = ['has_due']

            vals = (
                p[0], p[1], p[2], p[3],
                f"₹{final_amt:.2f}",
                f"₹{entry_paid:.2f}" if entry_paid else "-",
                f"₹{via_payment:.2f}" if via_payment else "-",
                f"₹{returns:.2f}"    if returns    else "-",
                f"₹{entry_due:.2f}"  if entry_due  else "-",
                status,
                item_count,
            )
            self.purchase_tree.insert(
                '', tk.END, iid=str(purchase_id), values=vals, tags=tags)

            pass  # tags configured once in _build_ui

    def clear_filter(self):
        self.from_date.delete(0, tk.END)
        self.to_date.delete(0, tk.END)
        self.supplier_filter.set('')
        self.due_filter.set('')
        self.schedule_filter.set('')
        self.fy_filter.set('')
        self.load_purchase_history()

    def update_summary(self, data=None):
        """
        Phase 7: summary uses:
          - filtered purchases for total_amount, entry_paid, returns, items
          - suppliers table for total_due / total_credit (always global)
        """
        if data is None:
            data = self.purchase_data

        n            = len(data)
        total        = sum(float(p[4] or 0) for p in data)   # final_amount
        entry_paid   = sum(float(p[5] or 0) for p in data)
        total_returns= sum(float(p[6] or 0) for p in data)
        items        = sum(int(p[7] or 0) for p in data)

        # Phase 7: due/credit from suppliers table — single source of truth
        self.cursor.execute(
            "SELECT COALESCE(SUM(total_due),0), COALESCE(SUM(total_credit),0) "
            "FROM suppliers")
        row = self.cursor.fetchone()
        global_due    = float(row[0] or 0)
        global_credit = float(row[1] or 0)

        self.total_purchases_var.set(str(n))
        self.total_amount_var.set(f"₹{total:.2f}")
        self.total_entry_paid_var.set(f"₹{entry_paid:.2f}")
        self.total_returns_var.set(f"₹{total_returns:.2f}")
        self.total_due_var.set(f"₹{global_due:.2f}")
        self.total_credit_var.set(f"₹{global_credit:.2f}")
        self.total_items_var.set(str(items))

    # ── Context menu / actions ────────────────────────────────────────────

    def _show_ctx(self, event):
        if self.purchase_tree.selection():
            self._ctx.post(event.x_root, event.y_root)

    def _selected_id(self):
        sel = self.purchase_tree.selection()
        if not sel:
            return None
        try:
            return int(sel[0])
        except (ValueError, IndexError):
            return None

    def edit_purchase(self, event=None):
        sel = self.purchase_tree.selection()
        if not sel:
            return
        purchase_id = self._selected_id()
        if not purchase_id:
            showerror("Error", "Purchase not found.")
            return
        bill_label = self.purchase_tree.item(sel[0])['values'][0]
        open_edit_window(self.parent, self.conn, purchase_id,
                         bill_label, self.load_purchase_history)

    def delete_purchase(self):
        sel = self.purchase_tree.selection()
        if not sel:
            return
        purchase_id = self._selected_id()
        if not purchase_id:
            showerror("Error", "Purchase not found.")
            return
        bill_label = self.purchase_tree.item(sel[0])['values'][0]
        delete_purchase(self.conn, purchase_id, bill_label, self.load_purchase_history)

    # ── Exports ───────────────────────────────────────────────────────────

    def _export_menu(self):
        dlg = open_dialog(self.parent, "Export Purchase Reports",
                          width=320, height=280, resizable=False)
        body = dlg.content
        for label, cmd in [
            ("Current View",        self._export_current_view),
            ("Purchase Register",   self._export_purchase_register),
            ("Monthly Summary",     self._export_monthly_summary),
            ("Supplier Due Report", self._export_supplier_due),
            ("GST Purchase Report", self._export_gst_purchase),
        ]:
            ttk.Button(body, text=label, width=36,
                       command=lambda c=cmd, d=dlg: [d.destroy(), c()]
                       ).pack(pady=4, padx=10)

    def _get_date_range(self):
        return self.from_date.get().strip(), self.to_date.get().strip()

    def _export_current_view(self):
        from core.column_config import export_tree_current_view
        cols, rows = export_tree_current_view(self.purchase_tree)
        if not rows:
            showinfo("No Records", "No data visible.")
            return
        export_data(self.parent, 'Purchases - Current View',
                    cols, rows, 'purchases_current_view')

    def _export_purchase_register(self):
        """Phase 1: uses amount_paid_at_entry, not stale snapshot columns."""
        fd, td = self._get_date_range()
        q = """SELECT COALESCE(p.bill_number,p.purchase_no), p.purchase_date,
                      s.name, s.phone, p.total_amount,
                      COALESCE(p.amount_paid_at_entry, p.amount_paid, 0),
                      COALESCE((SELECT SUM(pr.refund_amount) FROM purchase_returns pr
                                WHERE pr.purchase_id=p.id), 0)
               FROM purchases p JOIN suppliers s ON p.supplier_id=s.id WHERE 1=1"""
        params = []
        if fd: q += ' AND p.purchase_date>=?'; params.append(fd)
        if td: q += ' AND p.purchase_date<=?'; params.append(td)
        q += ' ORDER BY p.purchase_date DESC'
        self.cursor.execute(q, params)
        rows = self.cursor.fetchall()
        if not rows:
            showinfo("No Records", "No purchases found.")
            return
        export_table(self.parent, 'Purchase Register',
                     ['Bill No', 'Date', 'Supplier', 'Phone',
                      'Final Amount', 'Paid at Entry', 'Returns'],
                     rows, 'purchase_register', 'purchase_history', 'purchase_register')

    def _export_monthly_summary(self):
        fd, td = self._get_date_range()
        q = """SELECT strftime('%Y-%m',p.purchase_date), COUNT(*),
                      SUM(p.total_amount),
                      SUM(COALESCE(p.amount_paid_at_entry, p.amount_paid, 0))
               FROM purchases p WHERE 1=1"""
        params = []
        if fd: q += ' AND p.purchase_date>=?'; params.append(fd)
        if td: q += ' AND p.purchase_date<=?'; params.append(td)
        q += " GROUP BY strftime('%Y-%m',p.purchase_date) ORDER BY 1 DESC"
        self.cursor.execute(q, params)
        raw = self.cursor.fetchall()
        if not raw:
            showinfo("No Records", "No purchases found.")
            return

        def fmt(ym):
            try:
                return datetime.strptime(ym, '%Y-%m').strftime('%b-%Y')
            except Exception:
                return ym

        rows = [[fmt(r[0]), r[1], f'{r[2]:.2f}', f'{r[3]:.2f}'] for r in raw]
        rows.append(['TOTAL',
                     sum(r[1] for r in raw),
                     f'{sum(r[2] for r in raw):.2f}',
                     f'{sum(r[3] for r in raw):.2f}'])
        export_table(self.parent, 'Monthly Purchase Summary',
                      ['Month', 'Purchases', 'Final Amount', 'Paid at Entry'],
                      rows, 'monthly_purchase_summary', 'purchase_history', 'monthly_summary')

    def _export_supplier_due(self):
        """Phase 2: reads from suppliers table — single source of truth."""
        self.cursor.execute("""
            SELECT s.name, s.phone,
                   COALESCE(s.total_due, 0), COALESCE(s.total_credit, 0)
            FROM suppliers s
            WHERE s.total_due > 0
            ORDER BY s.total_due DESC
        """)
        rows = self.cursor.fetchall()
        if not rows:
            showinfo("No Records", "No outstanding dues.")
            return
        export_table(self.parent, 'Supplier Due Report',
                      ['Name', 'Phone', 'Total Due', 'Credit'],
                      rows, 'supplier_due_report', 'suppliers', 'supplier_due')

    def _export_gst_purchase(self):
        fd, td = self._get_date_range()
        q = """SELECT COALESCE(p.bill_number,p.purchase_no), p.purchase_date,
                      s.name, m.name, pi.hsn_code,
                      COALESCE(pi.gst_pct,pi.gst_value,0), pi.qty, pi.rate,
                      COALESCE(pi.item_amount,pi.amount,0)
               FROM purchase_items pi
               JOIN purchases p ON pi.purchase_id=p.id
               JOIN suppliers s ON p.supplier_id=s.id
               JOIN medicines m ON pi.medicine_id=m.id WHERE 1=1"""
        params = []
        if fd: q += ' AND p.purchase_date>=?'; params.append(fd)
        if td: q += ' AND p.purchase_date<=?'; params.append(td)
        q += ' ORDER BY p.purchase_date DESC'
        self.cursor.execute(q, params)
        rows = self.cursor.fetchall()
        if not rows:
            showinfo("No Records", "No items found.")
            return
        export_table(self.parent, 'GST Purchase Report',
                      ['Bill No', 'Date', 'Supplier', 'Medicine',
                       'HSN', 'GST%', 'Qty', 'Rate', 'Amount'],
                      rows, 'gst_purchase_report', 'purchase_history', 'gst_purchase')

    # ── Keyboard nav ──────────────────────────────────────────────────────

    def _setup_arrow_nav(self):
        nav = [self.from_date, self.to_date,
               self.supplier_filter.entry, self.due_filter.entry,
               self.schedule_filter.entry, self.apply_btn]
        n = len(nav)
        plain = {0, 1}
        btns  = {5}

        def make_next(i):
            def h(event):
                if event.keysym == 'Right':
                    try:
                        if event.widget.index(tk.INSERT) < len(event.widget.get()):
                            return None
                    except Exception:
                        pass
                nav[(i + 1) % n].focus()
                return 'break'
            return h

        def make_prev(i):
            def h(event):
                if event.keysym == 'Left':
                    try:
                        if event.widget.index(tk.INSERT) > 0:
                            return None
                    except Exception:
                        pass
                nav[(i - 1) % n].focus()
                return 'break'
            return h

        for i, w in enumerate(nav):
            if i in plain or i in btns:
                w.bind('<Up>',   make_prev(i), add='+')
                w.bind('<Down>', make_next(i), add='+')
            w.bind('<Left>',  make_prev(i), add='+')
            w.bind('<Right>', make_next(i), add='+')
        self.purchase_tree.bind('<Return>', lambda e: self._tree_menu())

    def _tree_menu(self):
        sel = self.purchase_tree.selection()
        if not sel:
            return
        try:
            bbox = self.purchase_tree.bbox(sel[0])
            if bbox:
                self._ctx.post(
                    self.purchase_tree.winfo_rootx() + bbox[0],
                    self.purchase_tree.winfo_rooty() + bbox[1] + bbox[3])
        except Exception:
            pass

    # ── FY filter ─────────────────────────────────────────────────────────

    @staticmethod
    def _build_fy_list():
        today = date.today()
        cur = today.year if today.month >= 4 else today.year - 1
        return list(reversed([f"{y}-{str(y + 1)[2:]}" for y in range(2020, cur + 1)]))

    def _apply_fy_filter(self):
        val = self.fy_filter.get().strip()
        if not val or '-' not in val or val not in self._fy_years:
            return
        try:
            y = int(val.split('-')[0])
        except ValueError:
            return
        self.from_date.delete(0, tk.END)
        self.from_date.insert(0, f"{y}-04-01")
        self.to_date.delete(0, tk.END)
        self.to_date.insert(0, f"{y + 1}-03-31")
        self.apply_filter()
