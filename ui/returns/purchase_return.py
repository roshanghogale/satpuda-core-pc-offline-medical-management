import tkinter as tk
from tkinter import messagebox
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno
try:
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import *
except ImportError:
    from tkinter import ttk
from datetime import datetime
import re
from core.font_config import *
from core.alert_colors import get_alert_color
from core.scroll_manager import make_scrollable, open_dialog
from core.calc_engine import calc_return_refund
from core.layout_config import is_strip_count_type, parse_tablets_per_stripe
from widgets.searchable_combo import SearchableCombo


class PurchaseReturnPage:
    def __init__(self, parent, conn):
        self.conn = conn
        self.cursor = conn.cursor()
        self.parent = parent
        self.return_items = []
        self._purchase_id = None
        self._supplier_id = None
        self._orig_items_data = []
        self._ensure_table()
        self._build_ui()
        self._setup_nav()
        self.parent.after(150, self.purchase_search.focus)

    # ── DB ────────────────────────────────────────────────────────────────

    def _ensure_table(self):
        """
        Simple schema — qty stores strips (for tablet/bolus) or units (for others).
        No extra columns needed. Works even if all data is deleted.
        """
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS purchase_returns (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                return_no     TEXT UNIQUE,
                purchase_id   INTEGER,
                supplier_id   INTEGER,
                return_date   DATE,
                refund_amount REAL DEFAULT 0,
                discount      REAL DEFAULT 0,
                reason        TEXT,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (purchase_id) REFERENCES purchases(id),
                FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS purchase_return_items (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                return_id   INTEGER,
                medicine_id INTEGER,
                qty         REAL DEFAULT 0,
                rate        REAL DEFAULT 0,
                amount      REAL DEFAULT 0,
                FOREIGN KEY (return_id)   REFERENCES purchase_returns(id),
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
        search_frame = ttk.LabelFrame(inner, text="Step 1 — Find Original Purchase")
        search_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(search_frame, text="Purchase No / Supplier:").grid(
            row=0, column=0, padx=6, pady=6, sticky=tk.W)
        self.purchase_search = SearchableCombo(search_frame, width=32)
        self.purchase_search.grid(row=0, column=1, padx=6, pady=6)
        self.purchase_search.entry.bind('<FocusIn>', lambda e: self._reload_purchases(), add='+')
        self.purchase_search.bind('<<ComboboxSelected>>', self._on_purchase_select)
        self.purchase_search.next_focus_widget = lambda: self.load_btn.focus()

        try:
            self.load_btn = ttk.Button(search_frame, text="Load Purchase  [Enter]",
                                       command=self._on_purchase_select,
                                       bootstyle="info", width=20)
        except Exception:
            self.load_btn = ttk.Button(search_frame, text="Load Purchase  [Enter]",
                                       command=self._on_purchase_select, width=20)
        self.load_btn.grid(row=0, column=2, padx=8, pady=6)

        self.purchase_info_var = tk.StringVar(value="No purchase loaded — type purchase no or supplier name above")
        ttk.Label(search_frame, textvariable=self.purchase_info_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS),
                  foreground=get_alert_color('info')).grid(
            row=0, column=3, padx=12, pady=6, sticky=tk.W)

        # ── Row 2: Original purchase items ────────────────────────────────
        orig_frame = ttk.LabelFrame(
            inner, text="Step 2 — Select Item to Return  [F2 = focus list  |  Enter = add selected]")
        orig_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        orig_cols = ('Medicine', 'Batch', 'Purchased', 'Returnable', 'Rate', 'Type')
        self.orig_tree = ttk.Treeview(orig_frame, columns=orig_cols,
                                      show='headings', height=5, style='Large.Treeview')
        col_w = {'Medicine': 170, 'Batch': 80, 'Purchased': 110,
                 'Returnable': 120, 'Rate': 80, 'Type': 70}
        for c in orig_cols:
            self.orig_tree.heading(c, text=c)
            self.orig_tree.column(c, width=col_w.get(c, 90))
        sb1 = ttk.Scrollbar(orig_frame, orient=tk.VERTICAL, command=self.orig_tree.yview)
        self.orig_tree.configure(yscrollcommand=sb1.set)
        self.orig_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb1.pack(side=tk.RIGHT, fill=tk.Y)
        self.orig_tree.bind('<Double-1>', self._add_return_item_dialog)
        self.orig_tree.bind('<Return>', self._add_return_item_dialog)

        # Add button below orig tree
        orig_btn_row = ttk.Frame(inner)
        orig_btn_row.pack(fill=tk.X, pady=(0, 6))
        try:
            self.add_btn = ttk.Button(orig_btn_row,
                                      text="➕ Add Selected to Return  [Enter on row]",
                                      command=self._add_return_item_dialog,
                                      bootstyle="success", width=38)
        except Exception:
            self.add_btn = ttk.Button(orig_btn_row,
                                      text="➕ Add Selected to Return  [Enter on row]",
                                      command=self._add_return_item_dialog, width=38)
        self.add_btn.pack(side=tk.LEFT, padx=4)

        # ── Row 3: Return items ───────────────────────────────────────────
        ret_frame = ttk.LabelFrame(
            inner, text="Step 3 — Items to Return  [F3 = focus list  |  Delete / Remove btn = remove]")
        ret_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        ret_cols = ('Medicine', 'Batch', 'Return Qty', 'Rate', 'Amount')
        self.ret_tree = ttk.Treeview(ret_frame, columns=ret_cols,
                                     show='headings', height=4, style='Large.Treeview')
        col_w2 = {'Medicine': 170, 'Batch': 80, 'Return Qty': 140, 'Rate': 80, 'Amount': 90}
        for c in ret_cols:
            self.ret_tree.heading(c, text=c)
            self.ret_tree.column(c, width=col_w2.get(c, 90))
        sb2 = ttk.Scrollbar(ret_frame, orient=tk.VERTICAL, command=self.ret_tree.yview)
        self.ret_tree.configure(yscrollcommand=sb2.set)
        self.ret_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb2.pack(side=tk.RIGHT, fill=tk.Y)
        self.ret_tree.bind('<Delete>', lambda e: self._remove_return_item())

        # Remove button below ret tree
        ret_btn_row = ttk.Frame(inner)
        ret_btn_row.pack(fill=tk.X, pady=(0, 6))
        try:
            self.remove_btn = ttk.Button(ret_btn_row,
                                         text="➖ Remove Selected  [Delete key]",
                                         command=self._remove_return_item,
                                         bootstyle="warning", width=30)
        except Exception:
            self.remove_btn = ttk.Button(ret_btn_row,
                                         text="➖ Remove Selected  [Delete key]",
                                         command=self._remove_return_item, width=30)
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

        ttk.Label(summary_frame, text="Credit to Supplier:").grid(
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

        hist_cols = ('Return No', 'Date', 'Purchase No', 'Supplier', 'Credit', 'Reason')
        self.hist_tree = ttk.Treeview(hist_frame, columns=hist_cols,
                                      show='headings', height=4, style='Large.Treeview')
        hw = {'Return No': 110, 'Date': 100, 'Purchase No': 120,
              'Supplier': 160, 'Credit': 90, 'Reason': 200}
        for c in hist_cols:
            self.hist_tree.heading(c, text=c)
            self.hist_tree.column(c, width=hw.get(c, 100))
        sb3 = ttk.Scrollbar(hist_frame, orient=tk.VERTICAL, command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=sb3.set)
        self.hist_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb3.pack(side=tk.RIGHT, fill=tk.Y)

        self._reload_purchases()
        self._load_history()

    # ── Keyboard navigation ───────────────────────────────────────────────

    def _setup_nav(self):
        root = self.parent.winfo_toplevel()

        # F2 → orig_tree, F3 → ret_tree, F5 → save, F6 → clear
        root.bind('<F2>', lambda e: self._focus_tree(self.orig_tree), add='+')
        root.bind('<F3>', lambda e: self._focus_tree(self.ret_tree),  add='+')
        root.bind('<F5>', lambda e: self._save_return(),              add='+')
        root.bind('<F6>', lambda e: self._clear(),                    add='+')

        # Linear nav: search → load → add → remove → reason → discount → save → clear
        nav = [
            self.purchase_search.entry,
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

        # Enter on buttons
        self.load_btn.bind('<Return>',   lambda e: self._on_purchase_select())
        self.add_btn.bind('<Return>',    lambda e: self._add_return_item_dialog())
        self.remove_btn.bind('<Return>', lambda e: self._remove_return_item())
        self.save_btn.bind('<Return>',   lambda e: self._save_return())
        self.clear_btn.bind('<Return>',  lambda e: self._clear())

        # Enter on entries
        self.reason_entry.bind('<Return>', lambda e: self.discount_entry.focus())
        self.discount_entry.bind('<Return>', lambda e: self.save_btn.focus())

        # Escape from trees
        self.orig_tree.bind('<Escape>', lambda e: self.purchase_search.focus())
        self.ret_tree.bind('<Escape>',  lambda e: self.reason_entry.focus())

        # Tab from trees → action buttons
        self.orig_tree.bind('<Tab>', lambda e: (self.add_btn.focus(), 'break'))
        self.ret_tree.bind('<Tab>',  lambda e: (self.remove_btn.focus(), 'break'))

        # FocusIn: select all
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

    # ── Core helpers ──────────────────────────────────────────────────────

    def _reload_purchases(self):
        self.cursor.execute("""
            SELECT COALESCE(p.bill_number, p.purchase_no) || ' — ' || s.name
            FROM purchases p JOIN suppliers s ON p.supplier_id = s.id
            ORDER BY p.id DESC LIMIT 300
        """)
        self.purchase_search.configure(values=[r[0] for r in self.cursor.fetchall()])

    def _on_purchase_select(self, event=None):
        val = self.purchase_search.get().strip()
        if not val:
            return
        bill_no = val.split(' — ')[0].strip()
        self.cursor.execute("""
            SELECT p.id, COALESCE(p.bill_number, p.purchase_no),
                   p.purchase_date, s.name, s.id
            FROM purchases p JOIN suppliers s ON p.supplier_id = s.id
            WHERE p.bill_number = ? OR p.purchase_no = ?
            ORDER BY p.id DESC LIMIT 1
        """, (bill_no, bill_no))
        row = self.cursor.fetchone()
        if not row:
            showwarning("Not Found", f"Purchase '{bill_no}' not found.")
            return
        self._purchase_id = row[0]
        self._supplier_id = row[4]
        self.purchase_info_var.set(
            f"Purchase: {row[1]}  |  Date: {row[2]}  |  Supplier: {row[3]}")
        self._load_orig_items()
        # Auto-focus orig_tree after loading
        self.parent.after(100, lambda: self._focus_tree(self.orig_tree))

    @staticmethod
    def _get_tps(unit_str, med_type):
        """
        Return tablets-per-strip (int >= 1) from medicines.unit.
        medicines.unit is set by purchase.py as plain int string e.g. '10'.
        For non-tablet types always returns 1.
        """
        if not is_strip_count_type(med_type):
            return 1
        return parse_tablets_per_stripe(unit_str)

    def _already_returned(self, purchase_id, medicine_id):
        """
        Sum of qty (strips/units) already returned for this exact
        purchase_id + medicine_id combination across all past returns.
        """
        self.cursor.execute("""
            SELECT COALESCE(SUM(pri.qty), 0)
            FROM purchase_return_items pri
            JOIN purchase_returns pr ON pri.return_id = pr.id
            WHERE pr.purchase_id = ?
              AND pri.medicine_id = ?
        """, (purchase_id, medicine_id))
        row = self.cursor.fetchone()
        return float(row[0]) if row else 0.0

    def _load_orig_items(self):
        for item in self.orig_tree.get_children():
            self.orig_tree.delete(item)
        self._orig_items_data = []

        self.cursor.execute("""
            SELECT m.name, pi.batch_no, pi.qty, pi.rate,
                   COALESCE(pi.type, ''), pi.medicine_id,
                   COALESCE(m.unit, '1')
            FROM purchase_items pi
            JOIN medicines m ON pi.medicine_id = m.id
            WHERE pi.purchase_id = ?
        """, (self._purchase_id,))

        for row in self.cursor.fetchall():
            med_name, batch, orig_qty, rate, med_type, med_id, unit = row
            orig_qty  = float(orig_qty)
            tps       = self._get_tps(unit, med_type)
            is_tablet = is_strip_count_type(med_type)

            # How many strips/units already returned for THIS purchase + medicine
            already   = self._already_returned(self._purchase_id, med_id)
            remaining = max(0.0, orig_qty - already)

            unit_word = "strips" if is_tablet else "units"

            self._orig_items_data.append({
                'name':      med_name,
                'batch':     batch or '',
                'orig_qty':  orig_qty,   # strips (tablet) or units (others)
                'rate':      float(rate),
                'med_type':  med_type,
                'med_id':    med_id,
                'tps':       tps,        # tablets per strip; 1 for non-tablet
                'remaining': remaining,  # strips/units still returnable
            })

            self.orig_tree.insert('', tk.END, values=(
                med_name,
                batch or '',
                f"{orig_qty:.0f} {unit_word}",
                f"{remaining:.0f} {unit_word}",
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
        d   = self._orig_items_data[idx]

        is_tablet  = is_strip_count_type(d['med_type'])
        unit_label = "strips" if is_tablet else "units"
        remaining  = d['remaining']
        tps        = d['tps']

        if remaining <= 0:
            showwarning(
                "Fully Returned",
                f"All {d['orig_qty']:.0f} {unit_label} of {d['name']} "
                f"have already been returned.")
            return

        # Block duplicate in current session
        for item in self.return_items:
            if item['med_id'] == d['med_id']:
                showwarning(
                    "Already Added",
                    f"{d['name']} is already in the return list.\n"
                    f"Remove it first to change the quantity.")
                return

        dlg = open_dialog(self.parent, f"Return — {d['name']}",
                          width=360, height=175, resizable=False)
        body = dlg.content

        # Show clear info to user
        if is_tablet:
            info = f"Returnable: {remaining:.0f} strips  (×{tps} = {int(remaining * tps)} tablets in stock)"
        else:
            info = f"Returnable: {remaining:.0f} {unit_label}"

        ttk.Label(body, text=info,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS)).pack(pady=(12, 4))

        qty_entry = ttk.Entry(body, width=14)
        qty_entry.pack(pady=4)
        qty_entry.insert(0, str(int(remaining)))
        qty_entry.select_range(0, tk.END)
        qty_entry.focus()

        def _confirm():
            try:
                qty = float(qty_entry.get())
            except ValueError:
                showerror("Invalid",
                                     f"Enter a valid number of {unit_label}.",
                                     parent=dlg)
                return

            # Hard cap: cannot exceed remaining strips/units
            if qty <= 0 or qty > remaining:
                already_done = d['orig_qty'] - remaining
                showerror(
                    "Invalid",
                    f"Must be 1 – {remaining:.0f} {unit_label}.\n"
                    f"Purchased: {d['orig_qty']:.0f}  |  Already returned: {already_done:.0f}",
                    parent=dlg)
                return

            # stock_deduction:
            #   tablet/bolus → strips × tps  (removes tablets from stock_qty)
            #   others       → qty           (removes units from stock_qty)
            stock_deduction = qty * tps if is_tablet else qty

            self.return_items.append({
                'med_id':          d['med_id'],
                'name':            d['name'],
                'batch':           d['batch'],
                'qty':             qty,             # strips or units — saved to DB
                'tps':             tps,
                'is_tablet':       is_tablet,
                'stock_deduction': stock_deduction, # tablets or units — used for stock update
                'rate':            d['rate'],
                'amount':          round(qty * d['rate'], 2),
            })
            self._refresh_ret_tree()
            self._update_summary()
            dlg.destroy()
            # Auto-focus ret_tree after adding
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
            return
        idx = self.ret_tree.index(sel[0])
        del self.return_items[idx]
        self._refresh_ret_tree()
        self._update_summary()

    def _refresh_ret_tree(self):
        for item in self.ret_tree.get_children():
            self.ret_tree.delete(item)
        for item in self.return_items:
            if item['is_tablet']:
                qty_label = (f"{item['qty']:.0f} strips "
                             f"× {item['tps']} = {item['stock_deduction']:.0f} tablets")
            else:
                qty_label = f"{item['qty']:.0f} units"
            self.ret_tree.insert('', tk.END, values=(
                item['name'],
                item['batch'],
                qty_label,
                f"{item['rate']:.2f}",
                f"{item['amount']:.2f}",
            ))

    def _update_summary(self, event=None):
        try:
            disc = float(self.discount_entry.get() or 0)
        except ValueError:
            disc = 0
        result = calc_return_refund(self.return_items, disc)
        self.refund_var.set(f"{result['refund_amount']:.2f}")

    def _save_return(self):
        if not self._purchase_id:
            showwarning("No Purchase", "Please load a purchase first.")
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

        # PHASE 1: collision-safe return number
        self.cursor.execute("SELECT COALESCE(MAX(id),0)+1 FROM purchase_returns")
        return_no = f"PR{self.cursor.fetchone()[0]}"

        try:
            self.cursor.execute("""
                INSERT INTO purchase_returns
                    (return_no, purchase_id, supplier_id, return_date,
                     refund_amount, discount, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (return_no, self._purchase_id, self._supplier_id,
                  datetime.now().date(), refund, disc, reason))
            return_id = self.cursor.lastrowid

            for item in self.return_items:
                self.cursor.execute("""
                    INSERT INTO purchase_return_items
                        (return_id, medicine_id, qty, rate, amount)
                    VALUES (?, ?, ?, ?, ?)
                """, (return_id, item['med_id'],
                      item['qty'], item['rate'], item['amount']))
                # stock_deduction = strips×tps for tablets, units for others
                self.cursor.execute("""
                    UPDATE medicines SET stock_qty = MAX(0, stock_qty - ?) WHERE id = ?
                """, (item['stock_deduction'], item['med_id']))

            # PHASE 3.4 / 6.2: do NOT patch purchases.total_due directly.
            # Supplier balance is maintained exclusively by recalculate_supplier_due().
            self.conn.commit()

            from core.purchase_service import recalculate_supplier_due
            recalculate_supplier_due(self.conn, self._supplier_id)

            showinfo(
                "Success",
                f"Return {return_no} saved.\n"
                f"Credit to supplier: ₹{refund:.2f}\n"
                f"Stock reduced for {len(self.return_items)} item(s).")
            self._clear()
            self._load_history()

        except Exception as e:
            self.conn.rollback()
            showerror("Error", f"Failed to save return: {e}")

    def _load_history(self):
        for item in self.hist_tree.get_children():
            self.hist_tree.delete(item)
        self.cursor.execute("""
            SELECT pr.return_no, pr.return_date,
                   COALESCE(p.bill_number, p.purchase_no),
                   s.name, pr.refund_amount, COALESCE(pr.reason, '')
            FROM purchase_returns pr
            JOIN purchases p ON pr.purchase_id = p.id
            JOIN suppliers s ON pr.supplier_id = s.id
            ORDER BY pr.id DESC LIMIT 200
        """)
        for r in self.cursor.fetchall():
            self.hist_tree.insert('', tk.END,
                                  values=(r[0], r[1], r[2], r[3],
                                          f"₹{r[4]:.2f}", r[5]))

    def _clear(self):
        self._purchase_id = None
        self._supplier_id = None
        self._orig_items_data = []
        self.return_items.clear()
        self.purchase_search.set('')
        self.purchase_info_var.set("No purchase loaded")
        self.reason_entry.delete(0, tk.END)
        self.discount_entry.delete(0, tk.END)
        self.discount_entry.insert(0, "0")
        self.refund_var.set("0.00")
        for t in (self.orig_tree, self.ret_tree):
            for item in t.get_children():
                t.delete(item)
        self.purchase_search.focus()
