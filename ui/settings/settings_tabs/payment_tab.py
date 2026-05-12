import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk
from tkinter import messagebox
from datetime import date
from core.font_config import *
from core.alert_colors import get_alert_color
from core.scroll_manager import make_scrollable
from widgets.searchable_combo import SearchableCombo


class PaymentTab:
    def __init__(self, notebook, conn):
        self.conn = conn
        self.cursor = conn.cursor()
        self._ensure_table()
        outer = ttk.Frame(notebook)
        notebook.add(outer, text="💳 Payment")
        frame = make_scrollable(outer)
        self._build(frame, outer)

    def _ensure_table(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS supplier_payments (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_no   TEXT UNIQUE,
                supplier_id  INTEGER,
                payment_date DATE,
                amount       REAL DEFAULT 0,
                mode         TEXT DEFAULT 'Cash',
                reference    TEXT,
                due_before   REAL DEFAULT 0,
                due_after    REAL DEFAULT 0,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
            )
        """)
        self.conn.commit()

    def _build(self, frame, outer):
        form = ttk.LabelFrame(frame, text="Record Supplier Payment")
        form.pack(fill=tk.X, padx=10, pady=(10, 6))

        ttk.Label(form, text="Supplier:").grid(row=0, column=0, sticky=tk.W, padx=8, pady=6)
        self.pay_supplier = SearchableCombo(form, width=28)
        self.pay_supplier.grid(row=0, column=1, padx=8, pady=6, sticky=tk.W)

        self.pay_due_var = tk.StringVar(value="Outstanding Due: ₹0.00")
        ttk.Label(form, textvariable=self.pay_due_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold'),
                  foreground=get_alert_color('danger')).grid(
            row=0, column=2, columnspan=2, padx=12, pady=6, sticky=tk.W)

        ttk.Label(form, text="Amount Paid (₹):").grid(row=1, column=0, sticky=tk.W, padx=8, pady=6)
        self.pay_amount = ttk.Entry(form, width=16)
        self.pay_amount.grid(row=1, column=1, padx=8, pady=6, sticky=tk.W)

        ttk.Label(form, text="Payment Mode:").grid(row=1, column=2, sticky=tk.W, padx=8, pady=6)
        self.pay_mode = SearchableCombo(form, values=['Cash','Online','Cheque','NEFT','RTGS','UPI'], width=14)
        self.pay_mode.set('')
        self.pay_mode.grid(row=1, column=3, padx=8, pady=6, sticky=tk.W)

        ttk.Label(form, text="Payment Date:").grid(row=2, column=0, sticky=tk.W, padx=8, pady=6)
        self.pay_date = ttk.Entry(form, width=16)
        self.pay_date.insert(0, date.today().strftime('%Y-%m-%d'))
        self.pay_date.grid(row=2, column=1, padx=8, pady=6, sticky=tk.W)

        ttk.Label(form, text="Reference / Note:").grid(row=2, column=2, sticky=tk.W, padx=8, pady=6)
        self.pay_note = ttk.Entry(form, width=28)
        self.pay_note.grid(row=2, column=3, padx=8, pady=6, sticky=tk.W)

        btn_row = ttk.Frame(form)
        btn_row.grid(row=3, column=0, columnspan=4, pady=8, padx=8, sticky=tk.W)
        try:
            ttk.Button(btn_row, text="✔ Save Payment  [F5]",
                       command=self._save, bootstyle="success", width=22).pack(side=tk.LEFT, padx=6)
            ttk.Button(btn_row, text="↺ Clear",
                       command=self._clear, bootstyle="secondary", width=12).pack(side=tk.LEFT, padx=6)
            ttk.Button(btn_row, text="🗑 Delete Selected",
                       command=self._delete, bootstyle="danger", width=18).pack(side=tk.LEFT, padx=6)
        except Exception:
            ttk.Button(btn_row, text="Save Payment", command=self._save, width=18).pack(side=tk.LEFT, padx=6)
            ttk.Button(btn_row, text="Clear", command=self._clear).pack(side=tk.LEFT, padx=6)
            ttk.Button(btn_row, text="Delete Selected", command=self._delete).pack(side=tk.LEFT, padx=6)

        # History
        hf = ttk.LabelFrame(frame, text="Payment History")
        hf.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))
        hist_cols = ('Payment No','Date','Supplier','Amount','Mode','Reference','Due Before','Due After')
        self.hist_tree = ttk.Treeview(hf, columns=hist_cols, show='headings',
                                      height=12, style='Large.Treeview')
        hw = {'Payment No':110,'Date':100,'Supplier':160,'Amount':100,
              'Mode':90,'Reference':160,'Due Before':110,'Due After':110}
        for c in hist_cols:
            self.hist_tree.heading(c, text=c)
            self.hist_tree.column(c, width=hw.get(c, 100))
        self.hist_tree.column('Amount', anchor='e')
        self.hist_tree.column('Due Before', anchor='e')
        self.hist_tree.column('Due After', anchor='e')
        sb = ttk.Scrollbar(hf, orient=tk.VERTICAL, command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=sb.set)
        self.hist_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        summary = ttk.Frame(frame)
        summary.pack(fill=tk.X, padx=10, pady=(0, 6))
        self.total_paid_var = tk.StringVar(value="Total Paid: ₹0.00")
        ttk.Label(summary, textvariable=self.total_paid_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold'),
                  foreground=get_alert_color('success')).pack(side=tk.LEFT, padx=15)

        # Bindings
        self.pay_supplier.entry.bind('<FocusIn>', lambda e: self._reload_suppliers(), add='+')
        self.pay_supplier.bind('<<ComboboxSelected>>', self._on_supplier_select)
        self.pay_supplier.next_focus_widget = lambda: self._on_supplier_select()
        self.pay_amount.bind('<FocusIn>', lambda e: self.pay_amount.select_range(0, tk.END), add='+')
        self.pay_amount.bind('<Return>', lambda e: self.pay_mode.focus())
        self.pay_date.bind('<FocusIn>', lambda e: self.pay_date.select_range(0, tk.END), add='+')
        self.pay_date.bind('<Return>', lambda e: self.pay_note.focus())
        self.pay_note.bind('<Return>', lambda e: self._save())
        nav = [self.pay_supplier.entry, self.pay_amount, self.pay_mode.entry, self.pay_date, self.pay_note]
        for i, w in enumerate(nav):
            w.bind('<Down>', lambda e, n=nav[(i+1)%len(nav)]: n.focus(), add='+')
            w.bind('<Up>',   lambda e, p=nav[(i-1)%len(nav)]: p.focus(), add='+')
        outer.bind('<F5>', lambda e: self._save(), add='+')

        self._reload_suppliers()
        self._load_history()

    def _reload_suppliers(self):
        self.cursor.execute("SELECT name FROM suppliers ORDER BY name")
        self.pay_supplier.configure(values=[r[0] for r in self.cursor.fetchall()])

    def _on_supplier_select(self, event=None):
        name = self.pay_supplier.get().strip()
        if not name:
            return
        from core.purchase_service import get_supplier_due
        due, _ = get_supplier_due(self.conn, name)
        self.pay_due_var.set(f"Outstanding Due: ₹{due:.2f}")
        self.pay_amount.delete(0, tk.END)
        if due > 0:
            self.pay_amount.insert(0, f"{due:.2f}")
        self.pay_amount.focus()

    def _load_history(self):
        for item in self.hist_tree.get_children():
            self.hist_tree.delete(item)
        self.cursor.execute("""
            SELECT sp.payment_no, sp.payment_date, s.name,
                   sp.amount, sp.mode, COALESCE(sp.reference,''),
                   sp.due_before, sp.due_after
            FROM supplier_payments sp
            JOIN suppliers s ON sp.supplier_id=s.id
            ORDER BY sp.id DESC LIMIT 300
        """)
        total = 0.0
        for r in self.cursor.fetchall():
            total += float(r[3] or 0)
            self.hist_tree.insert('', tk.END, values=(
                r[0], r[1], r[2], f"₹{float(r[3]):.2f}",
                r[4], r[5], f"₹{float(r[6]):.2f}", f"₹{float(r[7]):.2f}"))
        self.total_paid_var.set(f"Total Paid (all): ₹{total:.2f}")

    def _save(self):
        name = self.pay_supplier.get().strip()
        if not name:
            messagebox.showwarning("Missing", "Please select a supplier.")
            return
        try:
            amount = float(self.pay_amount.get() or 0)
        except ValueError:
            messagebox.showerror("Invalid", "Enter a valid payment amount.")
            return
        if amount <= 0:
            messagebox.showwarning("Invalid", "Amount must be greater than zero.")
            return
        mode = self.pay_mode.get().strip()
        if not mode:
            messagebox.showwarning("Missing", "Please select a payment mode.")
            return
        pdate     = self.pay_date.get().strip() or date.today().strftime('%Y-%m-%d')
        reference = self.pay_note.get().strip()

        self.cursor.execute("SELECT id FROM suppliers WHERE name=? LIMIT 1", (name,))
        sup_row = self.cursor.fetchone()
        if not sup_row:
            messagebox.showerror("Not Found", f"Supplier '{name}' not found.")
            return
        supplier_id = sup_row[0]

        from core.purchase_service import get_supplier_due, recalculate_supplier_due
        due_before, _ = get_supplier_due(self.conn, name)
        net        = round(due_before - amount, 2)
        due_after  = max(0.0, net)

        # PHASE 1: collision-safe payment number using MAX(id)+1
        self.cursor.execute("SELECT COALESCE(MAX(id),0)+1 FROM supplier_payments")
        pay_no = f"PAY{self.cursor.fetchone()[0]:04d}"

        try:
            self.cursor.execute("""
                INSERT INTO supplier_payments
                    (payment_no,supplier_id,payment_date,amount,mode,reference,due_before,due_after)
                VALUES (?,?,?,?,?,?,?,?)
            """, (pay_no, supplier_id, pdate, amount, mode, reference, due_before, due_after))

            # PHASE 3.3: do NOT mutate purchases.amount_paid.
            # Supplier balance is maintained exclusively by recalculate_supplier_due().
            self.conn.commit()

            recalculate_supplier_due(self.conn, supplier_id)

            messagebox.showinfo("Payment Saved",
                f"Payment {pay_no} recorded.\n"
                f"Amount: ₹{amount:.2f}  |  Mode: {mode}\n"
                f"Due Before: ₹{due_before:.2f}  →  Due After: ₹{due_after:.2f}")
            self.pay_due_var.set(f"Outstanding Due: ₹{due_after:.2f}")
            self.pay_amount.delete(0, tk.END)
            self.pay_note.delete(0, tk.END)
            self._load_history()
        except Exception as e:
            self.conn.rollback()
            messagebox.showerror("Error", f"Failed to save payment: {e}")

    def _clear(self):
        self.pay_supplier.set('')
        self.pay_amount.delete(0, tk.END)
        self.pay_mode.set('')
        self.pay_note.delete(0, tk.END)
        self.pay_due_var.set("Outstanding Due: ₹0.00")
        self.pay_supplier.focus()

    def _delete(self):
        sel = self.hist_tree.selection()
        if not sel:
            messagebox.showinfo("No Selection", "Select a payment row to delete.")
            return
        pay_no = self.hist_tree.item(sel[0])['values'][0]
        if not messagebox.askyesno("Confirm Delete",
                                   f"Delete payment {pay_no}?\nThis will reverse the due adjustment."):
            return
        try:
            self.cursor.execute(
                "SELECT supplier_id FROM supplier_payments WHERE payment_no=?", (pay_no,))
            row = self.cursor.fetchone()
            if not row:
                messagebox.showerror("Not Found", "Payment record not found.")
                return
            supplier_id = row[0]

            # PHASE 7.3: just delete the row, then recalculate.
            # No manual rebuild of purchases.amount_paid needed.
            self.cursor.execute("DELETE FROM supplier_payments WHERE payment_no=?", (pay_no,))
            self.conn.commit()

            from core.purchase_service import recalculate_supplier_due
            recalculate_supplier_due(self.conn, supplier_id)

            messagebox.showinfo("Deleted", f"Payment {pay_no} deleted and due recalculated.")
            self._load_history()
            self._on_supplier_select()
        except Exception as e:
            self.conn.rollback()
            messagebox.showerror("Error", f"Failed to delete payment: {e}")
