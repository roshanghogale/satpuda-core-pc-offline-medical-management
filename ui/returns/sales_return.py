import tkinter as tk
from tkinter import messagebox
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno
try:
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import *
except ImportError:
    from tkinter import ttk
from datetime import datetime
from core.font_config import *
from core.alert_colors import get_alert_color
from core.scroll_manager import make_scrollable, open_dialog
from core.calc_engine import calc_return_refund
from core.customer_service import recalculate_customer_due
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

        # ── Row 1: Search bar ─────────────────────────────────────────────
        search_frame = ttk.LabelFrame(inner, text="Step 1 — Find Original Bill")
        search_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(search_frame, text="Bill No / Customer:").grid(
            row=0, column=0, padx=6, pady=6, sticky=tk.W)
        self.bill_search = SearchableCombo(search_frame, width=30)
        self.bill_search.grid(row=0, column=1, padx=6, pady=6)
        self.bill_search.entry.bind('<FocusIn>', lambda e: self._reload_bills(), add='+')
        self.bill_search.bind('<<ComboboxSelected>>', self._on_bill_select)
        self.bill_search.next_focus_widget = lambda: self.load_btn.focus()

        try:
            self.load_btn = ttk.Button(search_frame, text="Load Bill  [Enter]",
                                       command=self._on_bill_select, bootstyle="info", width=16)
        except Exception:
            self.load_btn = ttk.Button(search_frame, text="Load Bill  [Enter]",
                                       command=self._on_bill_select, width=16)
        self.load_btn.grid(row=0, column=2, padx=8, pady=6)

        self.bill_info_var = tk.StringVar(value="No bill loaded — type bill no or customer name above")
        ttk.Label(search_frame, textvariable=self.bill_info_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS),
                  foreground=get_alert_color('info')).grid(
            row=0, column=3, padx=12, pady=6, sticky=tk.W)

        # ── Row 2: Original bill items ────────────────────────────────────
        orig_frame = ttk.LabelFrame(
            inner, text="Step 2 — Select Item to Return  [F2 = focus list  |  Enter = add selected]")
        orig_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        orig_cols = ('Medicine', 'Batch', 'Qty Sold', 'Returnable', 'Rate', 'Type')
        self.orig_tree = ttk.Treeview(orig_frame, columns=orig_cols,
                                      show='headings', height=5, style='Large.Treeview')
        col_w = {'Medicine': 180, 'Batch': 80, 'Qty Sold': 100,
                 'Returnable': 110, 'Rate': 80, 'Type': 70}
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
            [("Add to Return", self._add_return_item_dialog)],
            on_double=self._add_return_item_dialog,
            escape_to=self.bill_search.entry,
        )

        # Add button below orig tree
        orig_btn_row = ttk.Frame(inner)
        orig_btn_row.pack(fill=tk.X, pady=(0, 6))
        try:
            self.add_btn = ttk.Button(orig_btn_row,
                                      text="➕ Add Selected to Return  [Enter on row]",
                                      command=self._add_return_item_dialog,
                                      bootstyle="success", width=36)
        except Exception:
            self.add_btn = ttk.Button(orig_btn_row,
                                      text="➕ Add Selected to Return  [Enter on row]",
                                      command=self._add_return_item_dialog, width=36)
        self.add_btn.pack(side=tk.LEFT, padx=4)

        # ── Row 3: Return items ───────────────────────────────────────────
        ret_frame = ttk.LabelFrame(
            inner, text="Step 3 — Items to Return  [F3 = focus list  |  Delete / Remove btn = remove]")
        ret_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        ret_cols = ('Medicine', 'Batch', 'Return Qty', 'Rate', 'Amount')
        self.ret_tree = ttk.Treeview(ret_frame, columns=ret_cols,
                                     show='headings', height=4, style='Large.Treeview')
        col_w2 = {'Medicine': 180, 'Batch': 80, 'Return Qty': 120, 'Rate': 80, 'Amount': 90}
        for c in ret_cols:
            self.ret_tree.heading(c, text=c)
            self.ret_tree.column(c, width=col_w2.get(c, 90))
        sb2 = ttk.Scrollbar(ret_frame, orient=tk.VERTICAL, command=self.ret_tree.yview)
        self.ret_tree.configure(yscrollcommand=sb2.set)
        self.ret_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb2.pack(side=tk.RIGHT, fill=tk.Y)
        setup_tree_actions(
            ret_frame,
            self.ret_tree,
            [("Remove from Return", self._remove_return_item)],
            on_delete=lambda e: self._remove_return_item(),
            escape_to=self.remove_btn,
        )

        # Remove button below ret tree
        ret_btn_row = ttk.Frame(inner)
        ret_btn_row.pack(fill=tk.X, pady=(0, 6))
        try:
            self.remove_btn = ttk.Button(ret_btn_row,
                                         text="➖ Remove Selected  [Delete key]",
                                         command=self._remove_return_item,
                                         bootstyle="warning", width=28)
        except Exception:
            self.remove_btn = ttk.Button(ret_btn_row,
                                         text="➖ Remove Selected  [Delete key]",
                                         command=self._remove_return_item, width=28)
        self.remove_btn.pack(side=tk.LEFT, padx=4)

        # ── Row 4: Summary ────────────────────────────────────────────────
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
        ttk.Label(summary_frame, textvariable=self.refund_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold'),
                  foreground=get_alert_color('success')).grid(
            row=0, column=5, padx=6, pady=6)

        try:
            self.save_btn = ttk.Button(summary_frame, text="✔ Save Return  [F5]",
                                       command=self._save_return,
                                       bootstyle="danger", width=18)
            self.clear_btn = ttk.Button(summary_frame, text="✖ Clear  [F6]",
                                        command=self._clear,
                                        bootstyle="secondary", width=14)
        except Exception:
            self.save_btn = ttk.Button(summary_frame, text="✔ Save Return  [F5]",
                                       command=self._save_return, width=18)
            self.clear_btn = ttk.Button(summary_frame, text="✖ Clear  [F6]",
                                        command=self._clear, width=14)
        self.save_btn.grid(row=0, column=6, padx=10, pady=6)
        self.clear_btn.grid(row=0, column=7, padx=4, pady=6)

        # ── Row 5: History ────────────────────────────────────────────────
        hist_frame = ttk.LabelFrame(inner, text="Return History")
        hist_frame.pack(fill=tk.BOTH, expand=True)

        hist_cols = ('Return No', 'Date', 'Bill No', 'Customer', 'Refund', 'Reason')
        self.hist_tree = ttk.Treeview(hist_frame, columns=hist_cols,
                                      show='headings', height=4, style='Large.Treeview')
        hw = {'Return No': 110, 'Date': 100, 'Bill No': 100,
              'Customer': 150, 'Refund': 90, 'Reason': 200}
        for c in hist_cols:
            self.hist_tree.heading(c, text=c)
            self.hist_tree.column(c, width=hw.get(c, 100))
        sb3 = ttk.Scrollbar(hist_frame, orient=tk.VERTICAL, command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=sb3.set)
        self.hist_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb3.pack(side=tk.RIGHT, fill=tk.Y)

        self._reload_bills()
        self._load_history()

    # ── Keyboard navigation ───────────────────────────────────────────────

    def _setup_nav(self):
        from core.keyboard_registry import KeyboardRegistry, PageBindings
        bindings = PageBindings(
            page_id='sales_return',
            first_focus=lambda: self.bill_search.entry.focus_set(),
            on_f5=self._save_return,
            on_f6=self._clear,
            f2_target=lambda: self._focus_tree(self.orig_tree),
            f3_target=lambda: self._focus_tree(self.ret_tree),
        )
        self._inner_frame._keyboard_bindings = bindings
        KeyboardRegistry.register_page(self._inner_frame, bindings)

        # Full linear nav order:
        # bill_search → load_btn → [orig_tree] → add_btn → [ret_tree] → remove_btn
        # → reason → discount → save_btn → clear_btn → bill_search
        nav = [
            self.bill_search.entry,
            self.load_btn,
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
            w.bind('<Down>',  _next(i), add='+')
            w.bind('<Up>',    _prev(i), add='+')
            w.bind('<Tab>',   _next(i), add='+')

        # Enter on load_btn / save_btn / clear_btn / add_btn / remove_btn
        self.load_btn.bind('<Return>',   lambda e: self._on_bill_select())
        self.add_btn.bind('<Return>',    lambda e: self._add_return_item_dialog())
        self.remove_btn.bind('<Return>', lambda e: self._remove_return_item())
        self.save_btn.bind('<Return>',   lambda e: self._save_return())
        self.clear_btn.bind('<Return>',  lambda e: self._clear())

        # Enter on reason → discount, Enter on discount → save
        self.reason_entry.bind('<Return>', lambda e: self.discount_entry.focus())
        self.discount_entry.bind('<Return>', lambda e: self.save_btn.focus())

        # Escape from trees → back to search
        self.orig_tree.bind('<Escape>', lambda e: self.bill_search.focus())
        self.ret_tree.bind('<Escape>',  lambda e: self.reason_entry.focus())

        # orig_tree: Tab → add_btn, Escape → search
        self.orig_tree.bind('<Tab>', lambda e: (self.add_btn.focus(), 'break'))

        # ret_tree: Tab → remove_btn
        self.ret_tree.bind('<Tab>', lambda e: (self.remove_btn.focus(), 'break'))

        # FocusIn on entries: select all
        for w in (self.reason_entry, self.discount_entry):
            w.bind('<FocusIn>', lambda e: e.widget.select_range(0, tk.END), add='+')

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

    # ── Helpers ───────────────────────────────────────────────────────────

    def _reload_bills(self):
        self.cursor.execute("""
            SELECT s.bill_no || ' — ' || c.name
            FROM sales s JOIN customers c ON s.customer_id = c.id
            ORDER BY s.id DESC LIMIT 300
        """)
        self.bill_search.configure(values=[r[0] for r in self.cursor.fetchall()])

    def _on_bill_select(self, event=None):
        val = self.bill_search.get().strip()
        if not val:
            return
        bill_no = val.split(' — ')[0].strip()
        self.cursor.execute("""
            SELECT s.id, s.bill_no, s.bill_date, s.discount, c.name, c.id
            FROM sales s JOIN customers c ON s.customer_id = c.id
            WHERE s.bill_no = ?
        """, (bill_no,))
        row = self.cursor.fetchone()
        if not row:
            showwarning("Not Found", f"Bill '{bill_no}' not found.")
            return
        self._sale_id = row[0]
        self._customer_id = row[5]
        self.bill_info_var.set(
            f"Bill: {row[1]}  |  Date: {row[2]}  |  Customer: {row[4]}")
        self._load_orig_items()
        # Auto-focus orig_tree after loading
        self.parent.after(100, lambda: self._focus_tree(self.orig_tree))

    def _already_returned(self, sale_id, medicine_id):
        self.cursor.execute("""
            SELECT COALESCE(SUM(sri.qty), 0)
            FROM sales_return_items sri
            JOIN sales_returns sr ON sri.return_id = sr.id
            WHERE sr.sale_id = ? AND sri.medicine_id = ?
        """, (sale_id, medicine_id))
        row = self.cursor.fetchone()
        return float(row[0]) if row else 0.0

    def _load_orig_items(self):
        for item in self.orig_tree.get_children():
            self.orig_tree.delete(item)
        self.cursor.execute("""
            SELECT m.name, m.batch_no, si.qty, si.rate, si.amount,
                   COALESCE(m.type,''), si.medicine_id
            FROM sales_items si
            JOIN medicines m ON si.medicine_id = m.id
            WHERE si.sale_id = ?
        """, (self._sale_id,))
        self._orig_items_data = []
        for r in self.cursor.fetchall():
            med_name, batch, orig_qty, rate, amount, med_type, med_id = r
            orig_qty  = float(orig_qty)
            already   = self._already_returned(self._sale_id, med_id)
            remaining = max(0.0, orig_qty - already)
            self._orig_items_data.append((
                med_name, batch, orig_qty, rate, amount, med_type, med_id, remaining
            ))
            self.orig_tree.insert('', tk.END, values=(
                med_name,
                batch or '',
                f"{orig_qty:.0f}",
                f"{remaining:.0f}  ({'returnable' if remaining > 0 else 'fully returned'})",
                f"{rate:.2f}",
                med_type,
            ))

    def _add_return_item_dialog(self, event=None):
        sel = self.orig_tree.selection()
        if not sel:
            showinfo("No Selection",
                                "Select a medicine row first (use ↑↓ arrow keys).")
            return
        idx = self.orig_tree.index(sel[0])
        med_name, batch, orig_qty, rate, amount, med_type, med_id, remaining = \
            self._orig_items_data[idx]

        if remaining <= 0:
            showwarning(
                "Fully Returned",
                f"All {int(orig_qty)} units of {med_name} have already been returned.")
            return

        for item in self.return_items:
            if item['medicine_id'] == med_id:
                showwarning(
                    "Already Added",
                    f"{med_name} is already in the return list.\n"
                    f"Remove it first to change the quantity.")
                return

        dlg = open_dialog(self.parent, f"Return — {med_name}",
                          width=340, height=175, resizable=False)
        body = dlg.content
        ttk.Label(body,
                  text=f"Returnable: {int(remaining)}  "
                       f"(Sold: {int(orig_qty)}, Already returned: {int(orig_qty - remaining)})",
                  font=(FONT_FAMILY, FONT_SIZE_LABELS)).pack(pady=(12, 4))
        qty_entry = ttk.Entry(body, width=14)
        qty_entry.pack(pady=4)
        qty_entry.insert(0, str(int(remaining)))
        qty_entry.select_range(0, tk.END)
        qty_entry.focus()

        def _confirm():
            try:
                qty = int(qty_entry.get())
            except ValueError:
                showerror("Invalid", "Enter a valid integer quantity.", parent=dlg)
                return
            if qty <= 0 or qty > remaining:
                showerror(
                    "Invalid",
                    f"Must be 1 – {int(remaining)}.\n"
                    f"(Sold: {int(orig_qty)}, Already returned: {int(orig_qty - remaining)})",
                    parent=dlg)
                return
            self.return_items.append({
                'medicine_id': med_id,
                'name':        med_name,
                'batch':       batch or '',
                'qty':         qty,
                'orig_qty':    orig_qty,
                'rate':        float(rate),
                'amount':      float(amount),
            })
            self._refresh_ret_tree()
            self._update_summary()
            dlg.destroy()
            # Focus ret_tree after adding
            self.parent.after(80, lambda: self._focus_tree(self.ret_tree))

        qty_entry.bind('<Return>', lambda e: _confirm())
        dlg.bind('<Escape>', lambda e: dlg.destroy())
        ok_btn = ttk.Button(dlg.footer, text="Add", command=_confirm)
        ok_btn.pack(side=tk.LEFT, padx=6)
        ca_btn = ttk.Button(dlg.footer, text="Cancel", command=dlg.destroy)
        ca_btn.pack(side=tk.LEFT, padx=6)
        ok_btn.bind('<Return>', lambda e: _confirm())
        ca_btn.bind('<Return>', lambda e: dlg.destroy())
        ok_btn.bind('<Tab>', lambda e: (ca_btn.focus(), 'break'))
        ca_btn.bind('<Tab>', lambda e: (ok_btn.focus(), 'break'))

    def _remove_return_item(self):
        sel = self.ret_tree.selection()
        if not sel:
            showinfo("No Selection",
                                "Select a row in the return list first (F3 to focus).")
            return
        idx = self.ret_tree.index(sel[0])
        del self.return_items[idx]
        self._refresh_ret_tree()
        self._update_summary()

    def _refresh_ret_tree(self):
        for item in self.ret_tree.get_children():
            self.ret_tree.delete(item)
        for item in self.return_items:
            self.ret_tree.insert('', tk.END, values=(
                item['name'], item['batch'],
                item['qty'], f"{item['rate']:.2f}", f"{item['amount']:.2f}"
            ))

    def _update_summary(self, event=None):
        try:
            disc = float(self.discount_entry.get() or 0)
        except ValueError:
            disc = 0
        result = calc_return_refund(self.return_items, disc)
        self.refund_var.set(f"{result['refund_amount']:.2f}")

    def _save_return(self):
        if not self._sale_id:
            showwarning("No Bill", "Please load a bill first.")
            return
        if not self.return_items:
            showwarning("No Items", "Please add items to return.")
            return
        try:
            disc = float(self.discount_entry.get() or 0)
        except ValueError:
            disc = 0

        result  = calc_return_refund(self.return_items, disc)
        refund  = result['refund_amount']
        reason  = self.reason_entry.get().strip()

        self.cursor.execute("SELECT COALESCE(MAX(id),0)+1 FROM sales_returns")
        return_no = f"SR{self.cursor.fetchone()[0]}"

        try:
            self.cursor.execute("""
                INSERT INTO sales_returns
                    (return_no, sale_id, customer_id, return_date, refund_amount, discount, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (return_no, self._sale_id, self._customer_id,
                  datetime.now().date(), refund, disc, reason))
            return_id = self.cursor.lastrowid

            for item in self.return_items:
                self.cursor.execute("""
                    INSERT INTO sales_return_items
                        (return_id, medicine_id, qty, rate, amount)
                    VALUES (?, ?, ?, ?, ?)
                """, (return_id, item['medicine_id'],
                      item['qty'], item['rate'], item['amount']))
                self.cursor.execute("""
                    UPDATE medicines SET stock_qty = stock_qty + ? WHERE id = ?
                """, (item['qty'], item['medicine_id']))

            self.conn.commit()
            recalculate_customer_due(self.conn, self._customer_id)

            showinfo("Success",
                                f"Return {return_no} saved.\n"
                                f"Refund: ₹{refund:.2f}\n"
                                f"Stock restored for {len(self.return_items)} item(s).")
            self._clear()
            self._load_history()

        except Exception as e:
            self.conn.rollback()
            showerror("Error", f"Failed to save return: {e}")

    def _load_history(self):
        for item in self.hist_tree.get_children():
            self.hist_tree.delete(item)
        self.cursor.execute("""
            SELECT sr.return_no, sr.return_date, s.bill_no,
                   c.name, sr.refund_amount, COALESCE(sr.reason,'')
            FROM sales_returns sr
            JOIN sales s     ON sr.sale_id     = s.id
            JOIN customers c ON sr.customer_id = c.id
            ORDER BY sr.id DESC LIMIT 200
        """)
        for r in self.cursor.fetchall():
            self.hist_tree.insert('', tk.END,
                                  values=(r[0], r[1], r[2], r[3],
                                          f"₹{r[4]:.2f}", r[5]))

    def _clear(self):
        self._sale_id = None
        self._customer_id = None
        self._orig_items_data = []
        self.return_items.clear()
        self.bill_search.set('')
        self.bill_info_var.set("No bill loaded — type bill no or customer name above")
        self.reason_entry.delete(0, tk.END)
        self.discount_entry.delete(0, tk.END)
        self.discount_entry.insert(0, "0")
        self.refund_var.set("0.00")
        for t in (self.orig_tree, self.ret_tree):
            for item in t.get_children():
                t.delete(item)
        self.bill_search.focus()
