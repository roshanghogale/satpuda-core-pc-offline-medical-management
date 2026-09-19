import tkinter as tk
from tkinter import messagebox
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno
try:
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import *
except ImportError:
    from tkinter import ttk
from datetime import datetime, timedelta
from core.font_config import *
from core.alert_colors import get_alert_color
from core.scroll_manager import make_scrollable, open_dialog, scroll_to_widget
from core.calc_engine import calc_return_refund
from core.customer_service import recalculate_customer_due
from core.layout_config import is_strip_count_type
from core.sales_return_prefs import load_sales_return_lookup_days
from widgets.searchable_combo import SearchableCombo


class SalesReturnPage:
    def __init__(self, parent, conn):
        self.conn = conn
        self.cursor = conn.cursor()
        self.parent = parent
        self.return_items = []
        self._sale_id = None
        self._customer_id = None
        self._orig_items_data = []
        self._ensure_table()
        self._build_ui()
        self._setup_nav()
        self.parent.after(150, self.bill_search.focus)

    # ── DB ────────────────────────────────────────────────────────────────

    def _ensure_table(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS sales_returns (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                return_no       TEXT UNIQUE,
                sale_id         INTEGER,
                customer_id     INTEGER,
                return_date     DATE,
                refund_amount   REAL DEFAULT 0,
                discount        REAL DEFAULT 0,
                reason          TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sale_id)     REFERENCES sales(id),
                FOREIGN KEY (customer_id) REFERENCES customers(id)
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS sales_return_items (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                return_id       INTEGER,
                medicine_id     INTEGER,
                qty             INTEGER,
                rate            REAL,
                amount          REAL,
                FOREIGN KEY (return_id)   REFERENCES sales_returns(id),
                FOREIGN KEY (medicine_id) REFERENCES medicines(id)
            )
        """)
        self.conn.commit()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        inner = make_scrollable(self.parent)
        self._inner_frame = inner
        inner.configure(padding=(12, 12))

        # ── Step 1: Find bill (by customer/bill OR by medicine) ─────────
        self._lookup_days = load_sales_return_lookup_days()
        search_frame = ttk.LabelFrame(
            inner,
            text=self._step1_label(),
        )
        search_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(search_frame, text="Bill No / Customer:").grid(
            row=0, column=0, padx=6, pady=6, sticky=tk.W)
        self.bill_search = SearchableCombo(search_frame, width=34)
        self.bill_search.grid(row=0, column=1, padx=6, pady=6, sticky=tk.W)
        self.bill_search.entry.bind('<FocusIn>', lambda e: self._reload_bills(), add='+')
        self.bill_search.bind('<<ComboboxSelected>>', self._on_bill_select)
        self.bill_search.bind_apply_on_select(self._on_bill_select)

        ttk.Label(search_frame, text="or Medicine:").grid(
            row=1, column=0, padx=6, pady=6, sticky=tk.W)
        self.med_search = SearchableCombo(search_frame, width=34)
        self.med_search.grid(row=1, column=1, padx=6, pady=6, sticky=tk.W)
        self.med_search.entry.bind('<FocusIn>', lambda e: self._reload_medicines(), add='+')
        self.med_search.bind('<<ComboboxSelected>>', self._on_medicine_select)
        self.med_search.bind_apply_on_select(self._on_medicine_select)

        try:
            self.load_btn = ttk.Button(
                search_frame, text="Load Bill  [Enter]",
                command=self._on_bill_select, bootstyle="info", width=18,
            )
        except Exception:
            self.load_btn = ttk.Button(
                search_frame, text="Load Bill  [Enter]",
                command=self._on_bill_select, width=18,
            )
        self.load_btn.grid(row=0, column=2, rowspan=2, padx=8, pady=6, sticky=tk.N)

        self.bill_info_var = tk.StringVar(
            value="Search by bill / customer, or pick a medicine to list recent bills",
        )
        ttk.Label(
            search_frame, textvariable=self.bill_info_var,
            font=(FONT_FAMILY, FONT_SIZE_LABELS),
            foreground=get_alert_color('info'),
            wraplength=520, justify=tk.LEFT,
        ).grid(row=0, column=3, rowspan=2, padx=12, pady=6, sticky=tk.W)

        self.bill_search.next_focus_widget = lambda: self.med_search.entry.focus_set()
        self.med_search.next_focus_widget = lambda: self.load_btn.focus()

        # ── Step 2: Pick medicine + qty (inline, no popup) ──────────────
        pick_frame = ttk.LabelFrame(
            inner,
            text="Step 2 — Add Return  [F2 = bill items  |  pick medicine → qty → Enter]",
        )
        pick_frame.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(pick_frame, text="Medicine in bill:").grid(
            row=0, column=0, padx=6, pady=6, sticky=tk.W)
        self.bill_med_search = SearchableCombo(pick_frame, width=28)
        self.bill_med_search.grid(row=0, column=1, padx=6, pady=6, sticky=tk.W)
        self.bill_med_search.bind('<<ComboboxSelected>>', self._on_bill_med_select)
        self.bill_med_search.bind_apply_on_select(self._on_bill_med_select)

        ttk.Label(pick_frame, text="Return Qty:").grid(
            row=0, column=2, padx=(12, 6), pady=6, sticky=tk.W)
        self.return_qty_entry = ttk.Entry(pick_frame, width=10)
        self.return_qty_entry.grid(row=0, column=3, padx=6, pady=6, sticky=tk.W)
        self.return_qty_entry.bind('<Return>', lambda e: self._add_return_item_inline())

        try:
            self.add_btn = ttk.Button(
                pick_frame, text="Add to Return  [Enter]",
                command=self._add_return_item_inline,
                bootstyle="success", width=22,
            )
        except Exception:
            self.add_btn = ttk.Button(
                pick_frame, text="Add to Return  [Enter]",
                command=self._add_return_item_inline, width=22,
            )
        self.add_btn.grid(row=0, column=4, padx=8, pady=6)

        self.selected_info_var = tk.StringVar(
            value="Load a bill, then pick a medicine from the loaded bill",
        )
        ttk.Label(
            pick_frame, textvariable=self.selected_info_var,
            font=(FONT_FAMILY, FONT_SIZE_LABELS),
            foreground=get_alert_color('muted'),
            wraplength=640, justify=tk.LEFT,
        ).grid(row=1, column=0, columnspan=5, padx=6, pady=(0, 6), sticky=tk.W)

        orig_frame = ttk.Frame(inner)
        orig_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        orig_cols = ('Medicine', 'Batch', 'Qty Sold', 'Returnable', 'Rate', 'Type')
        self.orig_tree = ttk.Treeview(
            orig_frame, columns=orig_cols,
            show='headings', height=5, style='Large.Treeview',
        )
        col_w = {
            'Medicine': 170, 'Batch': 80, 'Qty Sold': 110,
            'Returnable': 120, 'Rate': 80, 'Type': 70,
        }
        for c in orig_cols:
            self.orig_tree.heading(c, text=c)
            self.orig_tree.column(c, width=col_w.get(c, 90))
        sb1 = ttk.Scrollbar(orig_frame, orient=tk.VERTICAL, command=self.orig_tree.yview)
        self.orig_tree.configure(yscrollcommand=sb1.set)
        self.orig_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb1.pack(side=tk.RIGHT, fill=tk.Y)
        from core.tree_action_menu import setup_tree_actions
        setup_tree_actions(
            orig_frame,
            self.orig_tree,
            [("Add to Return", self._add_return_item_inline)],
            on_double=self._on_orig_double_click,
            escape_to=self.bill_med_search.entry,
        )
        self.orig_tree.bind('<<TreeviewSelect>>', self._on_orig_select)

        # ── Step 3: Return items ──────────────────────────────────────────
        ret_frame = ttk.LabelFrame(
            inner,
            text="Step 3 — Items to Return  [F3 = focus list  |  Delete = remove]",
        )
        ret_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        ret_cols = ('Medicine', 'Batch', 'Return Qty', 'Rate', 'Amount')
        self.ret_tree = ttk.Treeview(
            ret_frame, columns=ret_cols,
            show='headings', height=4, style='Large.Treeview',
        )
        col_w2 = {'Medicine': 170, 'Batch': 80, 'Return Qty': 140, 'Rate': 80, 'Amount': 90}
        for c in ret_cols:
            self.ret_tree.heading(c, text=c)
            self.ret_tree.column(c, width=col_w2.get(c, 90))
        sb2 = ttk.Scrollbar(ret_frame, orient=tk.VERTICAL, command=self.ret_tree.yview)
        self.ret_tree.configure(yscrollcommand=sb2.set)
        self.ret_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb2.pack(side=tk.RIGHT, fill=tk.Y)

        ret_btn_row = ttk.Frame(inner)
        ret_btn_row.pack(fill=tk.X, pady=(0, 6))
        try:
            self.remove_btn = ttk.Button(
                ret_btn_row, text="Remove Selected  [Delete]",
                command=self._remove_return_item,
                bootstyle="warning", width=28,
            )
        except Exception:
            self.remove_btn = ttk.Button(
                ret_btn_row, text="Remove Selected  [Delete]",
                command=self._remove_return_item, width=28,
            )
        self.remove_btn.pack(side=tk.LEFT, padx=4)

        setup_tree_actions(
            ret_frame,
            self.ret_tree,
            [("Remove from Return", self._remove_return_item)],
            on_delete=lambda e: self._remove_return_item(),
            escape_to=self.remove_btn,
        )

        # ── Step 4: Summary ───────────────────────────────────────────────
        summary_frame = ttk.LabelFrame(inner, text="Step 4 — Confirm & Save")
        summary_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(summary_frame, text="Reason:").grid(
            row=0, column=0, padx=6, pady=6, sticky=tk.W)
        self.reason_entry = ttk.Entry(summary_frame, width=28)
        self.reason_entry.grid(row=0, column=1, padx=6, pady=6, sticky=tk.W)

        ttk.Label(summary_frame, text="Discount %:").grid(
            row=0, column=2, padx=6, pady=6, sticky=tk.W)
        self.discount_entry = ttk.Entry(summary_frame, width=8)
        self.discount_entry.insert(0, "0")
        self.discount_entry.grid(row=0, column=3, padx=6, pady=6)
        self.discount_entry.bind('<KeyRelease>', self._update_summary)

        ttk.Label(summary_frame, text="Refund Amount:").grid(
            row=0, column=4, padx=10, pady=6, sticky=tk.W)
        self.refund_var = tk.StringVar(value="0.00")
        ttk.Label(
            summary_frame, textvariable=self.refund_var,
            font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold'),
            foreground=get_alert_color('success'),
        ).grid(row=0, column=5, padx=6, pady=6)

        try:
            self.save_btn = ttk.Button(
                summary_frame, text="Save Return  [F5]",
                command=self._save_return, bootstyle="danger", width=18,
            )
            self.clear_btn = ttk.Button(
                summary_frame, text="Clear  [F6]",
                command=self._clear, bootstyle="secondary", width=14,
            )
        except Exception:
            self.save_btn = ttk.Button(
                summary_frame, text="Save Return  [F5]",
                command=self._save_return, width=18,
            )
            self.clear_btn = ttk.Button(
                summary_frame, text="Clear  [F6]",
                command=self._clear, width=14,
            )
        self.save_btn.grid(row=0, column=6, padx=10, pady=6)
        self.clear_btn.grid(row=0, column=7, padx=4, pady=6)

        # ── History ───────────────────────────────────────────────────────
        hist_frame = ttk.LabelFrame(inner, text="Return History")
        hist_frame.pack(fill=tk.BOTH, expand=True)

        hist_cols = ('Return No', 'Date', 'Bill No', 'Customer', 'Refund', 'Reason')
        self.hist_tree = ttk.Treeview(
            hist_frame, columns=hist_cols,
            show='headings', height=4, style='Large.Treeview',
        )
        hw = {
            'Return No': 110, 'Date': 100, 'Bill No': 100,
            'Customer': 150, 'Refund': 90, 'Reason': 200,
        }
        for c in hist_cols:
            self.hist_tree.heading(c, text=c)
            self.hist_tree.column(c, width=hw.get(c, 100))
        sb3 = ttk.Scrollbar(hist_frame, orient=tk.VERTICAL, command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=sb3.set)
        self.hist_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb3.pack(side=tk.RIGHT, fill=tk.Y)

        from core.tree_action_menu import setup_tree_actions
        setup_tree_actions(
            hist_frame,
            self.hist_tree,
            [("Delete Return", self._delete_history_return)],
            on_delete=lambda e: self._delete_history_return(),
        )

        self._reload_bills()
        self._reload_medicines()
        self._load_history()

    # ── Keyboard navigation ───────────────────────────────────────────────

    def _setup_nav(self):
        from core.keyboard_registry import KeyboardRegistry, PageBindings
        bindings = PageBindings(
            page_id='sales_return',
            first_focus=lambda: self.bill_search.entry.focus_set(),
            on_f5=self._save_return,
            on_f6=self._clear,
            on_end=lambda: self.reason_entry.focus_set(),
            f2_target=lambda: self._focus_tree(self.orig_tree),
            f3_target=lambda: self._focus_tree(self.ret_tree),
        )
        self._inner_frame._keyboard_bindings = bindings
        KeyboardRegistry.register_page(self._inner_frame, bindings)

        nav = [
            self.bill_search.entry,
            self.med_search.entry,
            self.load_btn,
            self.bill_med_search.entry,
            self.return_qty_entry,
            self.add_btn,
            self.remove_btn,
            self.reason_entry,
            self.discount_entry,
            self.save_btn,
            self.clear_btn,
        ]
        n = len(nav)

        def _next(i):
            def h(e):
                nav[(i + 1) % n].focus()
                return 'break'
            return h

        def _prev(i):
            def h(e):
                nav[(i - 1) % n].focus()
                return 'break'
            return h

        for i, w in enumerate(nav):
            w.bind('<Down>', _next(i), add='+')
            w.bind('<Up>', _prev(i), add='+')
            w.bind('<Tab>', _next(i), add='+')

        self.load_btn.bind('<Return>', lambda e: self._on_bill_select())
        self.add_btn.bind('<Return>', lambda e: self._add_return_item_inline())
        self.remove_btn.bind('<Return>', lambda e: self._remove_return_item())
        self.save_btn.bind('<Return>', lambda e: self._save_return())
        self.clear_btn.bind('<Return>', lambda e: self._clear())
        self.reason_entry.bind('<Return>', lambda e: (self.discount_entry.focus(), 'break'))
        self.discount_entry.bind('<Return>', lambda e: self._save_return())
        self.bill_med_search.entry.bind('<Return>', self._bill_med_enter, add='+')
        self.orig_tree.bind('<Escape>', lambda e: self.bill_med_search.entry.focus())
        self.ret_tree.bind('<Escape>', lambda e: self.reason_entry.focus())
        self.orig_tree.bind('<Tab>', lambda e: (self.return_qty_entry.focus(), 'break'))
        self.ret_tree.bind('<Tab>', lambda e: (self.remove_btn.focus(), 'break'))

        for w in (self.reason_entry, self.discount_entry, self.return_qty_entry):
            w.bind('<FocusIn>', lambda e: e.widget.select_range(0, tk.END), add='+')

    def _bill_med_enter(self, event=None):
        self._on_bill_med_select()
        self.return_qty_entry.focus_set()
        return 'break'

    def _focus_tree(self, tree):
        items = tree.get_children()
        if not items:
            return
        sel = tree.selection()
        target = sel[0] if sel else items[0]
        tree.selection_set(target)
        tree.focus(target)
        tree.focus()
        tree.see(target)
        if tree is self.orig_tree:
            self._on_orig_select()

    # ── Step 1 helpers ────────────────────────────────────────────────────

    def _step1_label(self) -> str:
        days = load_sales_return_lookup_days()
        return (
            f"Step 1 — Find Original Bill  "
            f"(last {days} day(s) when searching by medicine)"
        )

    def _lookup_cutoff(self) -> str:
        days = load_sales_return_lookup_days()
        return (datetime.now().date() - timedelta(days=days)).isoformat()

    def _reload_bills(self):
        self._lookup_days = load_sales_return_lookup_days()
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.desktop_returns_service import search_sales_bills
                res = search_sales_bills(self.conn, q="", medicine="")
                labels = [b.get("label") or "" for b in (res.get("bills") or [])]
                self.bill_search.configure(values=labels)
                try:
                    self.bill_search.update_list()
                except Exception:
                    pass
                return
        except Exception:
            pass
        self.cursor.execute("""
            SELECT s.bill_no || ' — ' || c.name
            FROM sales s JOIN customers c ON s.customer_id = c.id
            WHERE date(s.bill_date) >= date(?)
            ORDER BY s.id DESC LIMIT 300
        """, (self._lookup_cutoff(),))
        self.bill_search.configure(values=[r[0] for r in self.cursor.fetchall()])
        try:
            self.bill_search.update_list()
        except Exception:
            pass

    def _reload_medicines(self):
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.online_catalog import search_medicine_names
                names = [
                    str(r.get("name") or "").strip()
                    for r in (search_medicine_names("", limit=500, show_zero=True) or [])
                    if str(r.get("name") or "").strip()
                ]
                # Unique preserve order
                seen = set()
                uniq = []
                for n in names:
                    key = n.lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    uniq.append(n)
                self.med_search.configure(values=uniq)
                try:
                    self.med_search.update_list()
                except Exception:
                    pass
                return
        except Exception:
            pass
        self.cursor.execute("""
            SELECT DISTINCT name FROM medicines
            WHERE COALESCE(name, '') != ''
            ORDER BY name COLLATE NOCASE
        """)
        self.med_search.configure(values=[r[0] for r in self.cursor.fetchall()])
        try:
            self.med_search.update_list()
        except Exception:
            pass

    def _on_medicine_select(self, event=None):
        med_name = self.med_search.get().strip()
        if not med_name:
            return
        days = load_sales_return_lookup_days()
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.desktop_returns_service import search_sales_bills
                res = search_sales_bills(self.conn, medicine=med_name)
                bills = res.get("bills") or []
                if not bills:
                    self.bill_info_var.set(
                        f"No bills in the last {days} day(s) contain '{med_name}'.",
                    )
                    return
                if len(bills) == 1:
                    self.bill_info_var.set(
                        f"1 bill found with '{med_name}' — loading…",
                    )
                    self._load_bill_by_id(int(bills[0]["sale_id"]), med_hint=med_name)
                    return
                from widgets.recent_records_picker import show_recent_records_picker
                picker_rows = [
                    (
                        int(b["sale_id"]),
                        b.get("bill_no") or "",
                        b.get("bill_date") or "",
                        b.get("customer") or "",
                        "",
                    )
                    for b in bills
                ]
                self.bill_info_var.set(
                    f"{len(bills)} bills in last {days} day(s) with '{med_name}' — pick one",
                )
                show_recent_records_picker(
                    self.parent,
                    f"Bills with '{med_name}' (last {days} days)",
                    picker_rows,
                    lambda sale_id: self._load_bill_by_id(sale_id, med_hint=med_name),
                )
                return
        except Exception as exc:
            showwarning("Online", str(exc) or "Bill search failed.", parent=self.parent)
            return

        cutoff = self._lookup_cutoff()
        self.cursor.execute("""
            SELECT s.id, s.bill_no, s.bill_date, c.name
            FROM sales s
            JOIN customers c ON s.customer_id = c.id
            JOIN sales_items si ON si.sale_id = s.id
            JOIN medicines m ON m.id = si.medicine_id
            WHERE m.name = ? AND date(s.bill_date) >= date(?)
            GROUP BY s.id
            ORDER BY s.id DESC
            LIMIT 80
        """, (med_name, cutoff))
        rows = self.cursor.fetchall()
        if not rows:
            self.bill_info_var.set(
                f"No bills in the last {days} day(s) contain '{med_name}'.",
            )
            return
        if len(rows) == 1:
            self.bill_info_var.set(
                f"1 bill found with '{med_name}' — loading…",
            )
            self._load_bill_by_id(rows[0][0], med_hint=med_name)
            return
        from widgets.recent_records_picker import show_recent_records_picker
        picker_rows = [(r[0], r[1], r[2], r[3], '') for r in rows]
        self.bill_info_var.set(
            f"{len(rows)} bills in last {days} day(s) with '{med_name}' — pick one",
        )
        show_recent_records_picker(
            self.parent,
            f"Bills with '{med_name}' (last {days} days)",
            picker_rows,
            lambda sale_id: self._load_bill_by_id(sale_id, med_hint=med_name),
        )

    def _parse_bill_no(self, val: str) -> str:
        return val.split(' — ')[0].strip()

    def _load_bill_by_id(self, sale_id, med_hint=None):
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.desktop_returns_service import load_sales_bill_for_return
                res = load_sales_bill_for_return(self.conn, int(sale_id))
                if not res.get("ok"):
                    showwarning(
                        "Not Found",
                        res.get("error") or "Bill not found.",
                        parent=self.parent,
                    )
                    return
                self._sale_id = int(res["sale_id"])
                self._customer_id = int(res.get("customer_id") or 0)
                self.bill_search.set(
                    f"{res.get('bill_no')} — {res.get('customer')}  ({res.get('bill_date')})"
                )
                due_bits = (
                    f"  |  Bill: ₹{float(res.get('bill_total') or 0):.2f}"
                    f"  |  Paid: ₹{float(res.get('bill_paid') or 0):.2f}"
                    f"  |  Bill due: ₹{float(res.get('bill_due') or 0):.2f}"
                    f"  |  Customer due: ₹{float(res.get('previous_due') or 0):.2f}"
                )
                if float(res.get("previous_credit") or 0):
                    due_bits += f"  |  Credit: ₹{float(res.get('previous_credit') or 0):.2f}"
                self.bill_info_var.set(
                    f"Bill: {res.get('bill_no')}  |  Date: {res.get('bill_date')}  "
                    f"|  Customer: {res.get('customer')}{due_bits}",
                )
                self._apply_loaded_items(res.get("items") or [])
                self._reload_bill_medicines(med_hint=med_hint)
                if med_hint:
                    self.parent.after(120, lambda: self.return_qty_entry.focus_set())
                else:
                    self.parent.after(120, lambda: self.bill_med_search.focus())
                return
        except Exception as exc:
            showwarning("Online", str(exc) or "Bill load failed.", parent=self.parent)
            return

        self.cursor.execute("""
            SELECT s.id, s.bill_no, s.bill_date, s.discount, c.name, c.id
            FROM sales s JOIN customers c ON s.customer_id = c.id
            WHERE s.id = ?
        """, (sale_id,))
        row = self.cursor.fetchone()
        if not row:
            showwarning("Not Found", "Bill not found.", parent=self.parent)
            return
        self._sale_id = row[0]
        self._customer_id = row[5]
        self.bill_search.set(f"{row[1]} — {row[4]}  ({row[2]})")
        try:
            from core.customer_service import get_customer_due
            cust_due, cust_credit = get_customer_due(self.conn, int(row[5]))
        except Exception:
            cust_due, cust_credit = 0.0, 0.0
        self.cursor.execute(
            "SELECT COALESCE(total_amount,0), COALESCE(amount_paid,0), "
            "COALESCE(due_amount,0) FROM sales WHERE id=?",
            (row[0],),
        )
        pay = self.cursor.fetchone() or (0, 0, 0)
        due_bits = (
            f"  |  Bill: ₹{float(pay[0]):.2f}"
            f"  |  Paid: ₹{float(pay[1]):.2f}"
            f"  |  Bill due: ₹{float(pay[2]):.2f}"
            f"  |  Customer due: ₹{float(cust_due):.2f}"
        )
        if cust_credit:
            due_bits += f"  |  Credit: ₹{float(cust_credit):.2f}"
        self.bill_info_var.set(
            f"Bill: {row[1]}  |  Date: {row[2]}  |  Customer: {row[4]}{due_bits}",
        )
        self._load_orig_items()
        self._reload_bill_medicines(med_hint=med_hint)
        if med_hint:
            self.parent.after(120, lambda: self.return_qty_entry.focus_set())
        else:
            self.parent.after(120, lambda: self.bill_med_search.focus())

    def _on_bill_select(self, event=None):
        val = self.bill_search.get().strip()
        if not val:
            return
        bill_no = self._parse_bill_no(val)
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.desktop_returns_service import search_sales_bills
                res = search_sales_bills(self.conn, q=bill_no)
                bills = res.get("bills") or []
                match = None
                for b in bills:
                    if str(b.get("bill_no") or "").strip() == bill_no:
                        match = b
                        break
                if match is None and bills:
                    match = bills[0]
                if not match:
                    showwarning(
                        "Not Found", f"Bill '{bill_no}' not found.",
                        parent=self.parent,
                        focus_after=self._focus_bill_search_at_top,
                    )
                    return
                self._load_bill_by_id(int(match["sale_id"]))
                return
        except Exception as exc:
            showwarning("Online", str(exc) or "Bill search failed.", parent=self.parent)
            return

        self.cursor.execute("""
            SELECT s.id, s.bill_no, s.bill_date, s.discount, c.name, c.id
            FROM sales s JOIN customers c ON s.customer_id = c.id
            WHERE s.bill_no = ?
        """, (bill_no,))
        row = self.cursor.fetchone()
        if not row:
            showwarning(
                "Not Found", f"Bill '{bill_no}' not found.",
                parent=self.parent,
                focus_after=self._focus_bill_search_at_top,
            )
            return
        self._load_bill_by_id(row[0])

    def _focus_bill_search_at_top(self):
        try:
            self.bill_search.hide_list()
            self.med_search.hide_list()
            self.bill_med_search.hide_list()
        except Exception:
            pass
        canvas = getattr(self._inner_frame, '_canvas', None)
        if canvas is not None:
            try:
                canvas.update_idletasks()
                canvas.yview_moveto(0)
            except Exception:
                pass
        try:
            self.bill_search.focus(open_dropdown=False)
            scroll_to_widget(self._inner_frame, self.bill_search.entry)
        except Exception:
            pass

    # ── Step 2 helpers ────────────────────────────────────────────────────

    def _already_returned(self, sale_id, medicine_id):
        self.cursor.execute("""
            SELECT COALESCE(SUM(sri.qty), 0)
            FROM sales_return_items sri
            JOIN sales_returns sr ON sri.return_id = sr.id
            WHERE sr.sale_id = ? AND sri.medicine_id = ?
        """, (sale_id, medicine_id))
        row = self.cursor.fetchone()
        return float(row[0]) if row else 0.0

    def _apply_loaded_items(self, items):
        """Populate orig lines from desktop_returns_service / online payload."""
        self._orig_items_data = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            med_type = str(it.get("type") or "")
            is_tablet = bool(it.get("is_tablet")) if "is_tablet" in it else is_strip_count_type(med_type)
            self._orig_items_data.append({
                'name': str(it.get("name") or ""),
                'batch': str(it.get("batch") or ""),
                'orig_qty': float(it.get("orig_qty") or 0),
                'rate': float(it.get("rate") or 0),
                'amount': float(it.get("amount") or 0),
                'med_type': med_type,
                'med_id': int(it.get("medicine_id") or 0),
                'is_tablet': is_tablet,
                'remaining': float(
                    it.get("remaining_qty")
                    if it.get("remaining_qty") is not None
                    else it.get("remaining")
                    or 0
                ),
            })
        self._refresh_orig_tree()

    def _load_orig_items(self):
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode() and self._sale_id:
                from core.desktop_returns_service import load_sales_bill_for_return
                res = load_sales_bill_for_return(self.conn, int(self._sale_id))
                if res.get("ok"):
                    self._apply_loaded_items(res.get("items") or [])
                    return
        except Exception:
            pass
        self._orig_items_data = []
        self.cursor.execute("""
            SELECT m.name, m.batch_no, si.qty, si.rate, si.amount,
                   COALESCE(m.type,''), si.medicine_id
            FROM sales_items si
            JOIN medicines m ON si.medicine_id = m.id
            WHERE si.sale_id = ?
        """, (self._sale_id,))
        for row in self.cursor.fetchall():
            med_name, batch, orig_qty, rate, amount, med_type, med_id = row
            orig_qty = float(orig_qty)
            is_tablet = is_strip_count_type(med_type)
            already = self._already_returned(self._sale_id, med_id)
            remaining = max(0.0, orig_qty - already)
            self._orig_items_data.append({
                'name': med_name,
                'batch': batch or '',
                'orig_qty': orig_qty,
                'rate': float(rate),
                'amount': float(amount),
                'med_type': med_type,
                'med_id': med_id,
                'is_tablet': is_tablet,
                'remaining': remaining,
            })
        self._refresh_orig_tree()

    def _reload_bill_medicines(self, med_hint=None):
        if not self._sale_id:
            self.bill_med_search.configure(values=[])
            self.bill_med_search.set('')
            return
        names = sorted(
            {d['name'] for d in self._orig_items_data},
            key=lambda s: s.casefold(),
        )
        self.bill_med_search.configure(values=names)
        try:
            self.bill_med_search.update_list()
        except Exception:
            pass
        hint = (med_hint or '').strip()
        if hint and hint in names:
            self.bill_med_search.set(hint)
            self._on_bill_med_select()
        else:
            self.bill_med_search.set('')

    def _on_bill_med_select(self, event=None):
        med_name = self.bill_med_search.get().strip()
        if not med_name or not self._orig_items_data:
            self._on_orig_select()
            return
        for idx, d in enumerate(self._orig_items_data):
            if d['name'] != med_name:
                continue
            iid = f'orig_{idx}'
            if iid in self.orig_tree.get_children():
                self.orig_tree.selection_set(iid)
                self.orig_tree.focus(iid)
                self.orig_tree.see(iid)
            self._on_orig_select()
            return

    def _refresh_orig_tree(self):
        for item in self.orig_tree.get_children():
            self.orig_tree.delete(item)
        for idx, d in enumerate(self._orig_items_data):
            unit_word = "tablets" if d['is_tablet'] else "units"
            self.orig_tree.insert(
                '', tk.END, iid=f'orig_{idx}', values=(
                    d['name'],
                    d['batch'],
                    f"{d['orig_qty']:.0f} {unit_word}",
                    f"{d['remaining']:.0f} {unit_word}",
                    f"{d['rate']:.2f}",
                    d['med_type'],
                ),
            )
        self._on_orig_select()

    def _selected_orig_data(self):
        sel = self.orig_tree.selection()
        if not sel:
            return None
        try:
            idx = int(sel[0].replace('orig_', ''))
        except (ValueError, AttributeError):
            idx = self.orig_tree.index(sel[0])
        if idx < 0 or idx >= len(self._orig_items_data):
            return None
        return self._orig_items_data[idx]

    def _on_orig_select(self, event=None):
        d = self._selected_orig_data()
        if not d:
            self.selected_info_var.set("Select a medicine row from the loaded bill")
            return
        unit_label = "tablets" if d['is_tablet'] else "units"
        if d['remaining'] <= 0:
            self.selected_info_var.set(
                f"{d['name']} — already fully returned",
            )
            return
        self.selected_info_var.set(
            f"{d['name']} ({d['batch'] or 'no batch'}) — "
            f"returnable {d['remaining']:.0f} {unit_label} "
            f"(sold {d['orig_qty']:.0f})",
        )
        self.return_qty_entry.delete(0, tk.END)
        self.return_qty_entry.insert(0, str(int(d['remaining'])))

    def _on_orig_double_click(self, event=None):
        self._on_orig_select()
        self.return_qty_entry.focus_set()
        self.return_qty_entry.select_range(0, tk.END)

    def _add_return_item_inline(self, event=None):
        d = self._selected_orig_data()
        if not d:
            showinfo("No Selection", "Select a medicine row from the bill list first.")
            return

        unit_label = "tablets" if d['is_tablet'] else "units"
        remaining = d['remaining']
        if remaining <= 0:
            showwarning(
                "Fully Returned",
                f"All {d['orig_qty']:.0f} {unit_label} of {d['name']} "
                f"have already been returned.",
            )
            return

        for item in self.return_items:
            if item['medicine_id'] == d['med_id']:
                showwarning(
                    "Already Added",
                    f"{d['name']} is already in the return list.\n"
                    f"Remove it first to change the quantity.",
                )
                return

        try:
            qty = float(self.return_qty_entry.get().strip() or 0)
        except ValueError:
            showwarning("Invalid", f"Enter a valid number of {unit_label}.")
            self.return_qty_entry.focus_set()
            return

        if qty <= 0 or qty > remaining:
            showwarning(
                "Invalid",
                f"Qty must be 1 – {remaining:.0f} {unit_label}.",
            )
            self.return_qty_entry.focus_set()
            return

        effective_rate = (
            d['amount'] / d['orig_qty'] if d['orig_qty'] > 0 else d['rate']
        )
        self.return_items.append({
            'medicine_id': d['med_id'],
            'name': d['name'],
            'batch': d['batch'],
            'qty': qty,
            'orig_qty': d['orig_qty'],
            'rate': d['rate'],
            'amount': d['amount'],
            'return_amount': round(qty * effective_rate, 2),
            'is_tablet': d['is_tablet'],
        })
        self._refresh_ret_tree()
        self._update_summary()
        self._load_orig_items()
        self.parent.after(80, lambda: self._focus_tree(self.ret_tree))

    def _remove_return_item(self):
        sel = self.ret_tree.selection()
        if not sel:
            return
        idx = self.ret_tree.index(sel[0])
        del self.return_items[idx]
        self._refresh_ret_tree()
        self._update_summary()
        if self._sale_id:
            self._load_orig_items()

    def _refresh_ret_tree(self):
        for item in self.ret_tree.get_children():
            self.ret_tree.delete(item)
        for item in self.return_items:
            qty_label = (
                f"{item['qty']:.0f} tablets" if item.get('is_tablet')
                else f"{item['qty']:.0f} units"
            )
            self.ret_tree.insert('', tk.END, values=(
                item['name'],
                item['batch'],
                qty_label,
                f"{item['rate']:.2f}",
                f"{item.get('return_amount', item['amount']):.2f}",
            ))

    def _update_summary(self, event=None):
        try:
            disc = float(self.discount_entry.get() or 0)
        except ValueError:
            disc = 0
        result = calc_return_refund(self.return_items, disc)
        self.refund_var.set(f"{result['refund_amount']:.2f}")

    def _save_return(self):
        # Online Hybrid A: allow enqueue during short drops (no ensure_can_mutate).
        if not self._sale_id:
            showwarning(
                "No Bill", "Please load a bill first.",
                parent=self.parent,
                focus_after=self._focus_bill_search_at_top,
            )
            return
        if not self.return_items:
            showwarning("No Items", "Please add items to return.")
            return
        try:
            disc = float(self.discount_entry.get() or 0)
        except ValueError:
            disc = 0

        result = calc_return_refund(self.return_items, disc)
        refund = result['refund_amount']
        reason = self.reason_entry.get().strip()

        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.desktop_returns_service import save_sales_return
                body = {
                    "sale_id": int(self._sale_id),
                    "customer_id": int(self._customer_id or 0),
                    "customer_name": "",
                    "bill_no": self._parse_bill_no(self.bill_search.get() or ""),
                    "discount": disc,
                    "reason": reason,
                    "items": [
                        {
                            "medicine_id": int(it["medicine_id"]),
                            "qty": float(it["qty"]),
                            "rate": float(it.get("rate") or 0),
                        }
                        for it in self.return_items
                    ],
                }
                info = self.bill_info_var.get() or ""
                if "Customer:" in info:
                    try:
                        body["customer_name"] = info.split("Customer:")[-1].split("|")[0].strip()
                    except Exception:
                        pass
                res = save_sales_return(self.conn, body)
                if not res.get("ok"):
                    showerror("Error", res.get("error") or "Failed to save return.")
                    return
                return_no = res.get("return_no") or ""
                showinfo(
                    "Success",
                    f"Return {return_no} saved.\n"
                    f"Refund: ₹{float(res.get('refund_amount') or refund):.2f}\n"
                    f"Stock restored for {len(self.return_items)} item(s).",
                )
                self._clear()
                self._load_history()
                return
        except Exception as e:
            showerror("Error", f"Failed to save return: {e}")
            return

        self.cursor.execute("SELECT COALESCE(MAX(id),0)+1 FROM sales_returns")
        return_no = f"SR{self.cursor.fetchone()[0]}"

        try:
            self.cursor.execute("""
                INSERT INTO sales_returns
                    (return_no, sale_id, customer_id, return_date,
                     refund_amount, discount, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                return_no, self._sale_id, self._customer_id,
                datetime.now().date(), refund, disc, reason,
            ))
            return_id = self.cursor.lastrowid

            for item in self.return_items:
                self.cursor.execute("""
                    INSERT INTO sales_return_items
                        (return_id, medicine_id, qty, rate, amount)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    return_id, item['medicine_id'],
                    item['qty'], item['rate'], item['amount'],
                ))
                self.cursor.execute("""
                    UPDATE medicines SET stock_qty = stock_qty + ? WHERE id = ?
                """, (item['qty'], item['medicine_id']))

            self.conn.commit()
            recalculate_customer_due(self.conn, self._customer_id)
            try:
                from core.sync_coordinator import after_sales_return_saved
                after_sales_return_saved(self.conn, int(return_id), self._customer_id)
            except Exception:
                pass

            showinfo(
                "Success",
                f"Return {return_no} saved.\n"
                f"Refund: ₹{refund:.2f}\n"
                f"Stock restored for {len(self.return_items)} item(s).",
            )
            self._clear()
            self._load_history()

        except Exception as e:
            self.conn.rollback()
            showerror("Error", f"Failed to save return: {e}")

    def _load_history(self):
        for item in self.hist_tree.get_children():
            self.hist_tree.delete(item)
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core import store_query_client as sq
                data = sq.list_sales_returns(limit=200) or {}
                for r in data.get("rows") or []:
                    if not isinstance(r, dict):
                        continue
                    rid = r.get("id") or r.get("local_id") or ""
                    self.hist_tree.insert(
                        '', tk.END, iid=str(rid),
                        values=(
                            r.get("return_no") or "",
                            str(r.get("return_date") or "")[:10],
                            r.get("bill_no") or "",
                            r.get("customer_name") or "",
                            f"₹{float(r.get('refund_amount') or 0):.2f}",
                            r.get("reason") or "",
                        ),
                    )
                return
        except Exception:
            pass
        self.cursor.execute("""
            SELECT sr.id, sr.return_no, sr.return_date, s.bill_no,
                   c.name, sr.refund_amount, COALESCE(sr.reason,'')
            FROM sales_returns sr
            JOIN sales s     ON sr.sale_id     = s.id
            JOIN customers c ON sr.customer_id = c.id
            WHERE COALESCE(sr.deleted, 0) = 0
            ORDER BY sr.id DESC LIMIT 200
        """)
        for r in self.cursor.fetchall():
            self.hist_tree.insert('', tk.END, iid=str(r[0]), values=(
                r[1], r[2], r[3], r[4], f"₹{r[5]:.2f}", r[6],
            ))

    def _delete_history_return(self):
        """Delete a saved sales return and push soft-delete + related sync."""
        sel = self.hist_tree.selection()
        if not sel:
            showerror("Delete Return", "Select a return in history first.")
            return
        return_id = int(sel[0])
        vals = self.hist_tree.item(sel[0], 'values')
        return_no = vals[0] if vals else str(return_id)
        if not askyesno(
            "Confirm Delete",
            f"Delete sales return {return_no}?\n"
            "Stock will be reduced again and the return will sync as deleted.",
        ):
            return
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.online_mutation_queue import enqueue

                enqueue(
                    collection="sales_returns",
                    op="delete",
                    payload={"id": int(return_id)},
                    local_id=int(return_id),
                )
                showinfo("Success", "Return delete queued — will sync.")
                self._load_history()
                return
        except Exception as exc:
            showerror("Error", f"Failed to delete return: {exc}")
            return
        try:
            self.cursor.execute(
                "SELECT customer_id FROM sales_returns WHERE id=?",
                (return_id,),
            )
            row = self.cursor.fetchone()
            customer_id = int(row[0]) if row and row[0] else None
            self.cursor.execute(
                "SELECT medicine_id, qty FROM sales_return_items WHERE return_id=?",
                (return_id,),
            )
            lines = self.cursor.fetchall()
            medicine_ids = [int(m) for m, _ in lines]
            # Reverse the stock restore done when the return was saved.
            for med_id, qty in lines:
                self.cursor.execute(
                    "UPDATE medicines SET stock_qty = MAX(0, stock_qty - ?) WHERE id=?",
                    (int(qty or 0), med_id),
                )
            # Soft-delete locally so push can send deleted=true.
            self.cursor.execute(
                """
                UPDATE sales_returns
                SET deleted=1,
                    updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                    version=COALESCE(version,1)+1,
                    sync_status='pending'
                WHERE id=?
                """,
                (return_id,),
            )
            self.conn.commit()
            if customer_id:
                from core.customer_service import recalculate_customer_due
                recalculate_customer_due(self.conn, customer_id)
            from core.sync_coordinator import after_sales_return_deleted
            after_sales_return_deleted(
                self.conn, return_id, customer_id, medicine_ids,
            )
            showinfo("Success", f"Return {return_no} deleted.")
            self._load_history()
        except Exception as exc:
            self.conn.rollback()
            showerror("Error", f"Failed to delete return: {exc}")

    def _clear(self):
        self._sale_id = None
        self._customer_id = None
        self._orig_items_data = []
        self.return_items.clear()
        self.bill_search.set('')
        self.med_search.set('')
        self.bill_med_search.set('')
        self.bill_med_search.configure(values=[])
        self.return_qty_entry.delete(0, tk.END)
        self.bill_info_var.set(
            "Search by bill / customer, or pick a medicine to list recent bills",
        )
        self.selected_info_var.set("Load a bill, then pick a medicine from the loaded bill")
        self.reason_entry.delete(0, tk.END)
        self.discount_entry.delete(0, tk.END)
        self.discount_entry.insert(0, "0")
        self.refund_var.set("0.00")
        for t in (self.orig_tree, self.ret_tree):
            for item in t.get_children():
                t.delete(item)
        self._reload_bills()
        self.bill_search.focus()
