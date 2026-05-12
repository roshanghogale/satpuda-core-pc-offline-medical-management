"""
ui/settings/settings_tabs/customer_payment_tab.py
--------------------------------------------------
Standalone customer payment entry, history, edit, delete.
Fully integrated with recalculate_customer_due (single source of truth).
"""
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
from core.customer_service import recalculate_customer_due
from widgets.searchable_combo import SearchableCombo


class CustomerPaymentTab:
    def __init__(self, notebook, conn):
        self.conn   = conn
        self.cursor = conn.cursor()
        self._editing_id = None   # None = new, int = editing existing

        outer = ttk.Frame(notebook)
        notebook.add(outer, text="Customer Payment")
        frame = make_scrollable(outer)
        self._build(frame, outer)

    def _build(self, frame, outer):
        # -- Entry form -------------------------------------------------------
        form = ttk.LabelFrame(frame, text="Record Customer Payment")
        form.pack(fill=tk.X, padx=10, pady=(10, 6))

        ttk.Label(form, text="Customer:").grid(row=0, column=0, sticky=tk.W, padx=8, pady=6)
        self.cust_combo = SearchableCombo(form, width=28)
        self.cust_combo.grid(row=0, column=1, padx=8, pady=6, sticky=tk.W)

        self.cust_due_var = tk.StringVar(value="Outstanding Due: Rs.0.00")
        ttk.Label(form, textvariable=self.cust_due_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold'),
                  foreground=get_alert_color('danger')).grid(
            row=0, column=2, columnspan=2, padx=12, pady=6, sticky=tk.W)

        ttk.Label(form, text="Cash (Rs):").grid(row=1, column=0, sticky=tk.W, padx=8, pady=6)
        self.cash_entry = ttk.Entry(form, width=14)
        self.cash_entry.grid(row=1, column=1, padx=8, pady=6, sticky=tk.W)
        self.cash_entry.insert(0, "0")

        ttk.Label(form, text="Online (Rs):").grid(row=1, column=2, sticky=tk.W, padx=8, pady=6)
        self.online_entry = ttk.Entry(form, width=14)
        self.online_entry.grid(row=1, column=3, padx=8, pady=6, sticky=tk.W)
        self.online_entry.insert(0, "0")

        ttk.Label(form, text="Total Amount:").grid(row=2, column=0, sticky=tk.W, padx=8, pady=6)
        self.total_var = tk.StringVar(value="Rs.0.00")
        ttk.Label(form, textvariable=self.total_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(
            row=2, column=1, padx=8, pady=6, sticky=tk.W)

        ttk.Label(form, text="Payment Date:").grid(row=2, column=2, sticky=tk.W, padx=8, pady=6)
        self.date_entry = ttk.Entry(form, width=14)
        self.date_entry.insert(0, date.today().strftime('%Y-%m-%d'))
        self.date_entry.grid(row=2, column=3, padx=8, pady=6, sticky=tk.W)

        ttk.Label(form, text="Reference:").grid(row=3, column=0, sticky=tk.W, padx=8, pady=6)
        self.ref_entry = ttk.Entry(form, width=28)
        self.ref_entry.grid(row=3, column=1, padx=8, pady=6, sticky=tk.W)

        ttk.Label(form, text="Note:").grid(row=3, column=2, sticky=tk.W, padx=8, pady=6)
        self.note_entry = ttk.Entry(form, width=28)
        self.note_entry.grid(row=3, column=3, padx=8, pady=6, sticky=tk.W)

        btn_row = ttk.Frame(form)
        btn_row.grid(row=4, column=0, columnspan=4, pady=8, padx=8, sticky=tk.W)
        try:
            ttk.Button(btn_row, text="Save Payment  [F5]",
                       command=self._save, bootstyle="success", width=20).pack(side=tk.LEFT, padx=6)
            ttk.Button(btn_row, text="Clear",
                       command=self._clear, bootstyle="secondary", width=10).pack(side=tk.LEFT, padx=6)
            ttk.Button(btn_row, text="Delete Selected",
                       command=self._delete, bootstyle="danger", width=16).pack(side=tk.LEFT, padx=6)
        except Exception:
            ttk.Button(btn_row, text="Save Payment", command=self._save, width=18).pack(side=tk.LEFT, padx=6)
            ttk.Button(btn_row, text="Clear",        command=self._clear).pack(side=tk.LEFT, padx=6)
            ttk.Button(btn_row, text="Delete",       command=self._delete).pack(side=tk.LEFT, padx=6)

        # -- History ----------------------------------------------------------
        hf = ttk.LabelFrame(frame, text="Payment History  (double-click to edit)")
        hf.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))

        hist_cols = ('ID', 'Date', 'Customer', 'Cash', 'Online', 'Total', 'Mode', 'Reference', 'Note')
        self.hist_tree = ttk.Treeview(hf, columns=hist_cols, show='headings',
                                      height=12, style='Large.Treeview')
        hw = {'ID': 50, 'Date': 100, 'Customer': 160, 'Cash': 90,
              'Online': 90, 'Total': 90, 'Mode': 80, 'Reference': 130, 'Note': 180}
        for c in hist_cols:
            self.hist_tree.heading(c, text=c)
            self.hist_tree.column(c, width=hw.get(c, 100))
        sb = ttk.Scrollbar(hf, orient=tk.VERTICAL, command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=sb.set)
        self.hist_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.hist_tree.bind('<Double-1>', self._on_edit_select)

        # Summary bar
        sumbar = ttk.Frame(frame)
        sumbar.pack(fill=tk.X, padx=10, pady=(0, 6))
        self.total_paid_var = tk.StringVar(value="Total Paid (all): Rs.0.00")
        ttk.Label(sumbar, textvariable=self.total_paid_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold'),
                  foreground=get_alert_color('success')).pack(side=tk.LEFT, padx=15)

        # Bindings
        self.cust_combo.entry.bind('<FocusIn>', lambda e: self._reload_customers(), add='+')
        self.cust_combo.bind('<<ComboboxSelected>>', self._on_customer_select)
        self.cust_combo.next_focus_widget = lambda: self._on_customer_select()
        for w in (self.cash_entry, self.online_entry):
            w.bind('<KeyRelease>', self._update_total)
            w.bind('<FocusIn>', lambda e: e.widget.select_range(0, tk.END), add='+')
        self.cash_entry.bind('<Return>', lambda e: self.online_entry.focus())
        self.online_entry.bind('<Return>', lambda e: self.date_entry.focus())
        self.date_entry.bind('<Return>', lambda e: self.ref_entry.focus())
        self.ref_entry.bind('<Return>', lambda e: self.note_entry.focus())
        self.note_entry.bind('<Return>', lambda e: self._save())
        outer.bind('<F5>', lambda e: self._save(), add='+')

        self._reload_customers()
        self._load_history()

    # -- Helpers --------------------------------------------------------------

    def _reload_customers(self):
        self.cursor.execute("SELECT name FROM customers ORDER BY name")
        self.cust_combo.configure(values=[r[0] for r in self.cursor.fetchall()])

    def _on_customer_select(self, event=None):
        name = self.cust_combo.get().strip()
        if not name:
            return
        self.cursor.execute(
            "SELECT COALESCE(total_due,0), COALESCE(total_credit,0) "
            "FROM customers WHERE UPPER(name)=UPPER(?) LIMIT 1", (name,))
        row = self.cursor.fetchone()
        if row:
            due, credit = float(row[0]), float(row[1])
            if due > 0:
                self.cust_due_var.set(f"Outstanding Due: Rs.{due:.2f}")
            elif credit > 0:
                self.cust_due_var.set(f"Credit Balance: Rs.{credit:.2f}")
            else:
                self.cust_due_var.set("Outstanding Due: Rs.0.00")
            # Pre-fill cash with due amount
            if due > 0 and not self._editing_id:
                self.cash_entry.delete(0, tk.END)
                self.cash_entry.insert(0, f"{due:.2f}")
                self._update_total()
        self.cash_entry.focus()

    def _update_total(self, event=None):
        try:
            cash   = float(self.cash_entry.get() or 0)
            online = float(self.online_entry.get() or 0)
            self.total_var.set(f"Rs.{cash + online:.2f}")
        except ValueError:
            self.total_var.set("Rs.0.00")

    def _get_customer_id(self, name):
        self.cursor.execute(
            "SELECT id FROM customers WHERE UPPER(name)=UPPER(?) LIMIT 1", (name,))
        row = self.cursor.fetchone()
        return row[0] if row else None

    # -- Save -----------------------------------------------------------------

    def _save(self):
        name = self.cust_combo.get().strip()
        if not name:
            messagebox.showwarning("Missing", "Please select a customer.")
            return
        customer_id = self._get_customer_id(name)
        if not customer_id:
            messagebox.showerror("Not Found", f"Customer '{name}' not found.")
            return
        try:
            cash   = float(self.cash_entry.get() or 0)
            online = float(self.online_entry.get() or 0)
        except ValueError:
            messagebox.showerror("Invalid", "Enter valid cash and online amounts.")
            return
        amount = round(cash + online, 2)
        if amount <= 0:
            messagebox.showwarning("Invalid", "Total amount must be greater than zero.")
            return

        if cash > 0 and online > 0:
            mode = 'mixed'
        elif online > 0:
            mode = 'online'
        else:
            mode = 'cash'

        pdate     = self.date_entry.get().strip() or date.today().strftime('%Y-%m-%d')
        reference = self.ref_entry.get().strip()
        note      = self.note_entry.get().strip()

        # Read old balance for debug log
        self.cursor.execute(
            "SELECT COALESCE(total_due,0), COALESCE(total_credit,0) FROM customers WHERE id=?",
            (customer_id,))
        old = self.cursor.fetchone()
        old_due, old_credit = float(old[0]), float(old[1]) if old else (0.0, 0.0)

        try:
            if self._editing_id:
                self.cursor.execute("""
                    UPDATE customer_payments
                    SET payment_date=?, amount=?, payment_mode=?,
                        cash_amount=?, online_amount=?, reference_no=?, note=?
                    WHERE id=?
                """, (pdate, amount, mode, cash, online, reference, note, self._editing_id))
            else:
                self.cursor.execute("""
                    INSERT INTO customer_payments
                        (customer_id, payment_date, amount, payment_mode,
                         cash_amount, online_amount, reference_no, note)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (customer_id, pdate, amount, mode, cash, online, reference, note))

            self.conn.commit()
            new_due, new_credit = recalculate_customer_due(self.conn, customer_id)

            print(f"[PAYMENT] customer={name} old_due={old_due:.2f} old_credit={old_credit:.2f} "
                  f"payment={amount:.2f} new_due={new_due:.2f} new_credit={new_credit:.2f}")

            action = "updated" if self._editing_id else "saved"
            messagebox.showinfo("Success",
                f"Payment {action}.\n"
                f"Amount: Rs.{amount:.2f}  |  Mode: {mode}\n"
                f"New Due: Rs.{new_due:.2f}  |  Credit: Rs.{new_credit:.2f}")
            self._clear()
            self._load_history()

        except Exception as e:
            self.conn.rollback()
            messagebox.showerror("Error", f"Failed to save payment: {e}")

    # -- Delete ---------------------------------------------------------------

    def _delete(self):
        sel = self.hist_tree.selection()
        if not sel:
            messagebox.showinfo("No Selection", "Select a payment row to delete.")
            return
        pay_id = self.hist_tree.item(sel[0])['values'][0]
        cust_name = self.hist_tree.item(sel[0])['values'][2]
        if not messagebox.askyesno("Confirm Delete",
                                   f"Delete this payment for {cust_name}?\n"
                                   "Customer balance will be recalculated."):
            return
        customer_id = self._get_customer_id(cust_name)
        try:
            self.cursor.execute("DELETE FROM customer_payments WHERE id=?", (pay_id,))
            self.conn.commit()
            if customer_id:
                recalculate_customer_due(self.conn, customer_id)
            messagebox.showinfo("Deleted", "Payment deleted and balance recalculated.")
            self._clear()
            self._load_history()
        except Exception as e:
            self.conn.rollback()
            messagebox.showerror("Error", f"Failed to delete: {e}")

    # -- Edit (load into form) ------------------------------------------------

    def _on_edit_select(self, event=None):
        sel = self.hist_tree.selection()
        if not sel:
            return
        vals = self.hist_tree.item(sel[0])['values']
        pay_id    = vals[0]
        cust_name = vals[2]
        self._editing_id = pay_id

        self.cursor.execute(
            "SELECT payment_date, cash_amount, online_amount, reference_no, note "
            "FROM customer_payments WHERE id=?", (pay_id,))
        row = self.cursor.fetchone()
        if not row:
            return

        self.cust_combo.set(cust_name)
        self._on_customer_select()
        self.date_entry.delete(0, tk.END);   self.date_entry.insert(0, row[0] or '')
        self.cash_entry.delete(0, tk.END);   self.cash_entry.insert(0, str(row[1] or 0))
        self.online_entry.delete(0, tk.END); self.online_entry.insert(0, str(row[2] or 0))
        self.ref_entry.delete(0, tk.END);    self.ref_entry.insert(0, row[3] or '')
        self.note_entry.delete(0, tk.END);   self.note_entry.insert(0, row[4] or '')
        self._update_total()

    # -- History --------------------------------------------------------------

    def _load_history(self):
        for item in self.hist_tree.get_children():
            self.hist_tree.delete(item)
        self.cursor.execute("""
            SELECT cp.id, cp.payment_date, c.name,
                   cp.cash_amount, cp.online_amount, cp.amount,
                   cp.payment_mode, COALESCE(cp.reference_no,''), COALESCE(cp.note,'')
            FROM customer_payments cp
            JOIN customers c ON cp.customer_id = c.id
            ORDER BY cp.id DESC LIMIT 300
        """)
        total = 0.0
        for r in self.cursor.fetchall():
            total += float(r[5] or 0)
            self.hist_tree.insert('', tk.END, values=(
                r[0], r[1], r[2],
                f"Rs.{float(r[3]):.2f}", f"Rs.{float(r[4]):.2f}", f"Rs.{float(r[5]):.2f}",
                r[6], r[7], r[8]))
        self.total_paid_var.set(f"Total Paid (all): Rs.{total:.2f}")

    # -- Clear ----------------------------------------------------------------

    def _clear(self):
        self._editing_id = None
        self.cust_combo.set('')
        self.cash_entry.delete(0, tk.END);   self.cash_entry.insert(0, "0")
        self.online_entry.delete(0, tk.END); self.online_entry.insert(0, "0")
        self.date_entry.delete(0, tk.END);   self.date_entry.insert(0, date.today().strftime('%Y-%m-%d'))
        self.ref_entry.delete(0, tk.END)
        self.note_entry.delete(0, tk.END)
        self.total_var.set("Rs.0.00")
        self.cust_due_var.set("Outstanding Due: Rs.0.00")
        self.cust_combo.focus()
