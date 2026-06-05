import tkinter as tk
from tkinter import messagebox
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno
from datetime import datetime, date
import sqlite3

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.alert_colors import get_alert_color
from core.font_config import *
from core.layout_config import SALES_HISTORY_ROWS, get_configured_schedules
from core.column_config import apply_column_visibility, all_column_names
from core.scroll_manager import make_scrollable
from widgets.searchable_combo import SearchableCombo

from ui.sales.sales_history_exports import export_menu
from ui.sales.sales_history_actions import (
    view_bill_details, edit_bill, print_bill, delete_bill
)


class SalesHistoryPage:

    def __init__(self, parent, conn):
        self.conn   = conn
        self.cursor = conn.cursor()
        self.parent = parent
        self.sales_data = []

        self._build_ui()
        self._load_customer_names()
        self.load_sales_history()
        self.parent.after(100, self._setup_arrow_nav)
        self.parent.after(200, self.from_date.focus)

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        main_frame = make_scrollable(self.parent)
        self._inner_frame = main_frame
        main_frame.configure(padding=(10, 10))

        # Filter
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

        ttk.Label(ff, text="Customer:").grid(row=1, column=0, padx=5, pady=5)
        self.customer_filter = SearchableCombo(ff, width=20)
        self.customer_filter.grid(row=1, column=1, padx=5, pady=5)
        self.customer_filter.entry.bind('<FocusIn>', lambda e: self._load_customer_names(), add='+')

        ttk.Label(ff, text="Due:").grid(row=1, column=2, padx=5, pady=5)
        self.due_filter = SearchableCombo(ff, values=['Due Only','Credit Only','Paid / Cleared'], width=15)
        self.due_filter.grid(row=1, column=3, padx=5, pady=5)

        ttk.Label(ff, text="Schedule:").grid(row=1, column=4, padx=5, pady=5)
        self.schedule_filter = SearchableCombo(
            ff, values=get_configured_schedules() + ['Non-Scheduled'], width=15)
        self.schedule_filter.grid(row=1, column=5, padx=5, pady=5)

        self.apply_btn = ttk.Button(ff, text="Apply Filter", command=self.apply_filter)
        self.apply_btn.grid(row=0, column=6, padx=10, pady=5)
        ttk.Button(ff, text="Clear Filter", command=self.clear_filter).grid(row=1, column=6, padx=5, pady=5)
        try:
            ttk.Button(ff, text="📤 Export", command=self._export_menu,
                       bootstyle="info").grid(row=0, column=7, padx=10, pady=5, rowspan=2, sticky='ns')
        except Exception:
            ttk.Button(ff, text="📤 Export", command=self._export_menu).grid(
                row=0, column=7, padx=10, pady=5, rowspan=2, sticky='ns')

        # Tree
        tf = ttk.Frame(main_frame)
        tf.pack(fill=tk.BOTH, expand=True, pady=5)

        self._all_columns = tuple(all_column_names('sales_history'))
        widths = {
            'Bill No': 100, 'Date': 100, 'Customer': 150, 'Phone': 120,
            'Doctor': 120, 'Schedule': 90,
            'Total Amount': 100, 'Amount Paid': 100, 'Cash Paid': 90, 'Online Paid': 90,
            'Previous Due': 100, 'Due Amount': 100, 'Credit Amount': 100, 'Total Due': 100,
        }
        self.sales_tree = ttk.Treeview(tf, columns=self._all_columns, show='headings',
                                       height=SALES_HISTORY_ROWS, style='Large.Treeview')
        for col in self._all_columns:
            self.sales_tree.heading(col, text=col)
            self.sales_tree.column(col, width=widths.get(col, 100))
        apply_column_visibility(self.sales_tree, 'sales_history', self._all_columns)

        vsb = ttk.Scrollbar(tf, orient=tk.VERTICAL,   command=self.sales_tree.yview)
        hsb = ttk.Scrollbar(tf, orient=tk.HORIZONTAL, command=self.sales_tree.xview)
        self.sales_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.sales_tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        tf.grid_rowconfigure(0, weight=1)
        tf.grid_columnconfigure(0, weight=1)

        from core.alert_colors import get_tree_tag_colors as _gtc
        _clr = _gtc()
        self.sales_tree.tag_configure('account_cleared', background=_clr['cleared_bg'], foreground=_clr['cleared_fg'])
        self.sales_tree.tag_configure('bill_cleared',    background=_clr['partial_bg'], foreground=_clr['partial_fg'])
        self.sales_tree.tag_configure('has_due',         background=_clr['due_bg'],     foreground=_clr['due_fg'])

        self._ctx = tk.Menu(self.parent, tearoff=0)
        self._ctx.add_command(label="View Bill Details", command=self._view_bill)
        self._ctx.add_command(label="Edit Bill",         command=self._edit_bill)
        self._ctx.add_command(label="Print Bill",        command=self._print_bill)
        self._ctx.add_separator()
        self._ctx.add_command(label="Delete Bill",       command=self._delete_bill)
        for ev in ("<Button-3>","<Button-2>","<Control-Button-1>"):
            self.sales_tree.bind(ev, self._show_ctx)
        self.sales_tree.bind("<Double-1>", self._view_bill)

        # Summary
        sf = ttk.LabelFrame(main_frame, text="Sales Summary")
        sf.pack(fill=tk.X, pady=5)
        self.total_bills_var    = tk.StringVar()
        self.total_sales_var    = tk.StringVar()
        self.total_due_var      = tk.StringVar()
        self.total_profit_var   = tk.StringVar()
        self.today_revenue_var  = tk.StringVar()
        self.today_profit_var   = tk.StringVar()
        self.today_cash_var     = tk.StringVar()
        self.today_online_var   = tk.StringVar()
        self.month_revenue_var  = tk.StringVar()
        self.month_profit_var   = tk.StringVar()
        self.total_discount_var = tk.StringVar()
        self.total_paid_var     = tk.StringVar()
        self.total_returns_var  = tk.StringVar()

        row0 = [("Total Bills:",    self.total_bills_var,   None),
                ("Total Sales:",    self.total_sales_var,   'success'),
                ("Total Due (All Time):", self.total_due_var, 'danger'),
                ("Total Profit:",   self.total_profit_var,  'success'),
                ("Today Revenue:",  self.today_revenue_var, 'info'),
                ("Today Profit:",   self.today_profit_var,  'success')]
        row1 = [("Today Cash:",     self.today_cash_var,    None),
                ("Today Online:",   self.today_online_var,  None),
                ("Month Revenue:",  self.month_revenue_var, 'info'),
                ("Month Profit:",   self.month_profit_var,  'success'),
                ("Total Discount:", self.total_discount_var,'warning'),
                ("Total Paid:",     self.total_paid_var,    'success')]
        row2 = [("Total Returns:",  self.total_returns_var, 'info')]

        for col, (lbl, var, color) in enumerate(row0):
            ttk.Label(sf, text=lbl).grid(row=0, column=col*2, padx=6, pady=5)
            kw = {'font': (FONT_FAMILY, FONT_SIZE_LABELS, 'bold')}
            if color: kw['foreground'] = get_alert_color(color)
            ttk.Label(sf, textvariable=var, **kw).grid(row=0, column=col*2+1, padx=6, pady=5)
        for col, (lbl, var, color) in enumerate(row1):
            ttk.Label(sf, text=lbl).grid(row=1, column=col*2, padx=6, pady=5)
            kw = {'font': (FONT_FAMILY, FONT_SIZE_LABELS, 'bold')}
            if color: kw['foreground'] = get_alert_color(color)
            ttk.Label(sf, textvariable=var, **kw).grid(row=1, column=col*2+1, padx=6, pady=5)
        for col, (lbl, var, color) in enumerate(row2):
            ttk.Label(sf, text=lbl).grid(row=2, column=col*2, padx=6, pady=5)
            kw = {'font': (FONT_FAMILY, FONT_SIZE_LABELS, 'bold')}
            if color: kw['foreground'] = get_alert_color(color)
            ttk.Label(sf, textvariable=var, **kw).grid(row=2, column=col*2+1, padx=6, pady=5)

    # ── Data loading ──────────────────────────────────────────────────────

    def _load_customer_names(self):
        self.cursor.execute("SELECT DISTINCT name FROM customers ORDER BY name")
        self.customer_filter.configure(values=[r[0] for r in self.cursor.fetchall()])

    def load_sales_history(self):
        for item in self.sales_tree.get_children():
            self.sales_tree.delete(item)
        self.cursor.execute("""
            SELECT s.bill_date, c.name, c.phone,
                   s.total_amount, s.discount, s.amount_paid,
                   COALESCE(s.cash_paid,0), COALESCE(s.online_paid,0),
                   s.previous_due, COALESCE(s.due_amount,0), COALESCE(s.credit_amount,0),
                   COALESCE(s.total_due,0), s.bill_cleared, s.account_cleared,
                   s.bill_no, s.id, s.doctor_name,
                   (SELECT GROUP_CONCAT(DISTINCT NULLIF(m.schedule,''))
                    FROM sales_items si JOIN medicines m ON si.medicine_id=m.id
                    WHERE si.sale_id=s.id) AS bill_schedules
            FROM sales s JOIN customers c ON s.customer_id=c.id
            ORDER BY s.bill_date DESC, s.created_at DESC
        """)
        self.sales_data = self.cursor.fetchall()
        self._populate_tree(self.sales_data)
        self.update_summary(fd='', td='')

    def apply_filter(self):
        fd  = self.from_date.get()
        td  = self.to_date.get()
        cus = self.customer_filter.get().strip()
        due = self.due_filter.get()
        sch = self.schedule_filter.get()

        use_sch = sch and sch != 'All'
        if use_sch:
            q = """SELECT DISTINCT s.bill_date, c.name, c.phone,
                       s.total_amount, s.discount, s.amount_paid,
                       COALESCE(s.cash_paid,0), COALESCE(s.online_paid,0),
                       s.previous_due, COALESCE(s.due_amount,0), COALESCE(s.credit_amount,0),
                       COALESCE(s.total_due,0), s.bill_cleared, s.account_cleared,
                       s.bill_no, s.id, s.doctor_name,
                       (SELECT GROUP_CONCAT(DISTINCT NULLIF(m2.schedule,''))
                        FROM sales_items si2 JOIN medicines m2 ON si2.medicine_id=m2.id
                        WHERE si2.sale_id=s.id) AS bill_schedules
                   FROM sales s
                   JOIN customers c ON s.customer_id=c.id
                   JOIN sales_items si ON s.id=si.sale_id
                   JOIN medicines m ON si.medicine_id=m.id WHERE 1=1"""
        else:
            q = """SELECT s.bill_date, c.name, c.phone,
                       s.total_amount, s.discount, s.amount_paid,
                       COALESCE(s.cash_paid,0), COALESCE(s.online_paid,0),
                       s.previous_due, COALESCE(s.due_amount,0), COALESCE(s.credit_amount,0),
                       COALESCE(s.total_due,0), s.bill_cleared, s.account_cleared,
                       s.bill_no, s.id, s.doctor_name,
                       (SELECT GROUP_CONCAT(DISTINCT NULLIF(m.schedule,''))
                        FROM sales_items si JOIN medicines m ON si.medicine_id=m.id
                        WHERE si.sale_id=s.id) AS bill_schedules
                   FROM sales s JOIN customers c ON s.customer_id=c.id WHERE 1=1"""
        params = []
        if fd:  q += ' AND s.bill_date>=?';         params.append(fd)
        if td:  q += ' AND s.bill_date<=?';         params.append(td)
        if cus: q += ' AND c.name LIKE ?';          params.append(f'%{cus}%')
        if due == 'Due Only':       q += ' AND s.total_due>0 AND s.account_cleared=0'
        elif due == 'Credit Only':  q += ' AND COALESCE(s.credit_amount,0)>0'
        elif due == 'Paid / Cleared': q += ' AND s.account_cleared=1'
        if use_sch:
            if sch == 'Non-Scheduled': q += " AND (m.schedule IS NULL OR m.schedule='')"
            else:                      q += ' AND m.schedule=?'; params.append(sch)
        q += ' ORDER BY s.bill_date DESC, s.created_at DESC'
        self.cursor.execute(q, params)
        data = self.cursor.fetchall()
        for item in self.sales_tree.get_children():
            self.sales_tree.delete(item)
        self._populate_tree(data)
        self.update_summary(data, fd=fd, td=td)

    def _populate_tree(self, data):
        for sale in data:
            total_due, bc, ac = sale[11], sale[12], sale[13]
            bill_no, sale_id  = sale[14], sale[15]
            tags = (['account_cleared'] if ac == 1
                    else ['bill_cleared'] if bc == 1 and ac == 0
                    else ['has_due'] if total_due > 0 else [])
            display_due = 0 if ac == 1 else total_due
            doctor = sale[16] or ''
            schedule = (sale[17] or '') if len(sale) > 17 else ''
            vals = (
                bill_no, sale[0], sale[1], sale[2], doctor, schedule,
                sale[3], sale[5], sale[6], sale[7], sale[8], sale[9], sale[10], display_due,
            )
            self.sales_tree.insert('', tk.END, iid=str(sale_id), values=vals, tags=tags)
        pass  # tags configured once in _build_ui

    def clear_filter(self):
        self.from_date.delete(0, tk.END)
        self.to_date.delete(0, tk.END)
        self.customer_filter.set('')
        self.due_filter.set('')
        self.schedule_filter.set('')
        self.fy_filter.set('')
        self.load_sales_history()

    def update_summary(self, data=None, fd='', td=''):
        if data is None:
            data = self.sales_data
        today_d    = date.today()
        this_month = today_d.replace(day=1)

        n           = len(data)
        total_sales = sum(s[3] for s in data)
        total_disc  = sum(s[4] for s in data)  # overall bill discounts

        # Total Due — single source of truth: customers table
        self.cursor.execute(
            "SELECT COALESCE(SUM(total_due),0) FROM customers WHERE total_due > 0")
        actual_due = self.cursor.fetchone()[0] or 0

        total_standalone_paid = 0.0
        if data:
            # Standalone customer payments — respect active date filter
            if fd and td:
                self.cursor.execute(
                    "SELECT COALESCE(SUM(amount),0) FROM customer_payments "
                    "WHERE payment_date >= ? AND payment_date <= ?", (fd, td))
            elif fd:
                self.cursor.execute(
                    "SELECT COALESCE(SUM(amount),0) FROM customer_payments "
                    "WHERE payment_date >= ?", (fd,))
            elif td:
                self.cursor.execute(
                    "SELECT COALESCE(SUM(amount),0) FROM customer_payments "
                    "WHERE payment_date <= ?", (td,))
            else:
                self.cursor.execute(
                    "SELECT COALESCE(SUM(amount),0) FROM customer_payments")
            total_standalone_paid = self.cursor.fetchone()[0] or 0

        # Add per-item discounts to total discount
        all_ids = [str(s[15]) for s in data]
        if all_ids:
            self.cursor.execute(f"""
                SELECT COALESCE(SUM(item_discount), 0)
                FROM sales_items
                WHERE sale_id IN ({','.join(['?']*len(all_ids))})
            """, all_ids)
            item_disc_total = self.cursor.fetchone()[0] or 0
            total_disc = round(total_disc + item_disc_total, 2)

        def _profit(ids):
            """Use cost_price snapshotted at sale time.
            If cost_price is 0 (sale before any purchase), profit is 0 —
            do NOT fall back to current purchase rate to avoid historical distortion."""
            if not ids:
                return 0
            self.cursor.execute(f"""
                SELECT COALESCE(SUM(
                    si.amount - (si.qty * COALESCE(si.cost_price, 0))
                ), 0)
                FROM sales_items si
                WHERE si.sale_id IN ({','.join(['?']*len(ids))})
            """, ids)
            return self.cursor.fetchone()[0] or 0

        # Total returns for filtered period
        if all_ids:
            self.cursor.execute(f"""
                SELECT COALESCE(SUM(sr.refund_amount),0)
                FROM sales_returns sr
                WHERE sr.sale_id IN ({','.join(['?']*len(all_ids))})
            """, all_ids)
            total_returns = self.cursor.fetchone()[0] or 0
        else:
            total_returns = 0

        today_data   = [s for s in data if str(s[0]) == str(today_d)]
        month_data   = [s for s in data if str(s[0])[:7] == str(this_month)[:7]]

        today_rev    = sum(s[3] for s in today_data)
        today_cash   = sum(s[6] for s in today_data)
        today_online = sum(s[7] for s in today_data)
        month_rev    = sum(s[3] for s in month_data)

        total_billing_paid = sum(s[5] for s in data) if data else 0.0
        total_paid_all = round(total_billing_paid + total_standalone_paid, 2)

        self.total_bills_var.set(str(n))
        self.total_sales_var.set(f"\u20b9{total_sales:.2f}")
        self.total_due_var.set(f"\u20b9{actual_due:.2f}")
        self.total_discount_var.set(f"\u20b9{total_disc:.2f}")
        self.total_profit_var.set(f"\u20b9{_profit(all_ids):.2f}")
        self.today_revenue_var.set(f"\u20b9{today_rev:.2f}")
        self.today_profit_var.set(f"\u20b9{_profit([str(s[15]) for s in today_data]):.2f}")
        self.today_cash_var.set(f"\u20b9{today_cash:.2f}")
        self.today_online_var.set(f"\u20b9{today_online:.2f}")
        self.month_revenue_var.set(f"\u20b9{month_rev:.2f}")
        self.month_profit_var.set(f"\u20b9{_profit([str(s[15]) for s in month_data]):.2f}")
        self.total_paid_var.set(f"\u20b9{total_paid_all:.2f}")
        # Total returns (set if var exists)
        try:
            self.total_returns_var.set(f"\u20b9{total_returns:.2f}")
        except AttributeError:
            pass

    # ── Context menu / actions ────────────────────────────────────────────

    def _show_ctx(self, event):
        if self.sales_tree.selection():
            self._ctx.post(event.x_root, event.y_root)

    def _selected(self):
        sel = self.sales_tree.selection()
        if not sel: return None, None
        try:
            sale_id = int(sel[0])
        except (ValueError, IndexError):
            return None, None
        values = self.sales_tree.item(sel[0])['values']
        return sale_id, values

    def _view_bill(self, event=None):
        sale_id, values = self._selected()
        if sale_id:
            view_bill_details(self.parent, self.conn, sale_id, values, self.sales_data)

    def _edit_bill(self):
        sale_id, values = self._selected()
        if sale_id:
            edit_bill(self.parent, self.conn, sale_id, values, self.load_sales_history)

    def _print_bill(self):
        sale_id, values = self._selected()
        if sale_id:
            print_bill(self.parent, self.conn, sale_id, values)

    def _delete_bill(self):
        sale_id, values = self._selected()
        if sale_id:
            delete_bill(self.conn, sale_id, values, self.load_sales_history)

    # ── Exports ───────────────────────────────────────────────────────────

    def _export_menu(self):
        export_menu(
            parent           = self.parent,
            cursor           = self.cursor,
            from_date_fn     = lambda: self.from_date.get().strip(),
            to_date_fn       = lambda: self.to_date.get().strip(),
            schedule_filter_fn = lambda: self.schedule_filter.get(),
            export_current_view_fn = self._export_current_view,
        )

    def _export_current_view(self):
        from core.export_manager import export_data
        from core.column_config import export_tree_current_view
        cols, rows = export_tree_current_view(self.sales_tree)
        if not rows:
            showinfo("No Records", "No data visible."); return
        export_data(self.parent, 'Sales - Current View', cols, rows, 'sales_current_view')

    # ── Keyboard nav ──────────────────────────────────────────────────────

    def _setup_arrow_nav(self):
        nav = [self.from_date, self.to_date,
               self.customer_filter.entry, self.due_filter.entry,
               self.schedule_filter.entry, self.apply_btn]
        n = len(nav); plain = {0,1}; btns = {5}

        def make_next(i):
            def h(event):
                if event.keysym == 'Right':
                    try:
                        if event.widget.index(tk.INSERT) < len(event.widget.get()):
                            return None
                    except Exception: pass
                nav[(i+1)%n].focus(); return 'break'
            return h

        def make_prev(i):
            def h(event):
                if event.keysym == 'Left':
                    try:
                        if event.widget.index(tk.INSERT) > 0:
                            return None
                    except Exception: pass
                nav[(i-1)%n].focus(); return 'break'
            return h

        for i, w in enumerate(nav):
            if i in plain or i in btns:
                w.bind('<Up>',   make_prev(i), add='+')
                w.bind('<Down>', make_next(i), add='+')
            w.bind('<Left>',  make_prev(i), add='+')
            w.bind('<Right>', make_next(i), add='+')
        self.sales_tree.bind('<Return>', lambda e: self._tree_menu())

    def _tree_menu(self):
        sel = self.sales_tree.selection()
        if not sel: return
        try:
            bbox = self.sales_tree.bbox(sel[0])
            if bbox:
                self._ctx.post(
                    self.sales_tree.winfo_rootx() + bbox[0],
                    self.sales_tree.winfo_rooty() + bbox[1] + bbox[3])
        except Exception: pass

    # ── FY filter ─────────────────────────────────────────────────────────

    @staticmethod
    def _build_fy_list():
        today = date.today()
        cur = today.year if today.month >= 4 else today.year - 1
        return list(reversed([f"{y}-{str(y+1)[2:]}" for y in range(2020, cur+1)]))

    def _apply_fy_filter(self):
        val = self.fy_filter.get().strip()
        if not val or '-' not in val or val not in self._fy_years: return
        try: y = int(val.split('-')[0])
        except ValueError: return
        self.from_date.delete(0, tk.END); self.from_date.insert(0, f"{y}-04-01")
        self.to_date.delete(0, tk.END);   self.to_date.insert(0, f"{y+1}-03-31")
        self.apply_filter()
