import tkinter as tk
from tkinter import ttk, messagebox

from core.alert_colors import get_alert_color
from core.font_config import *
from core.calc_engine import calc_bill_summary, calc_payment_result
from core.billing_service import update_existing_bill
from widgets.two_step_medicine_combo import TwoStepMedicineCombo


class BillEditPage:

    def __init__(self, parent, conn, sale_id, refresh_callback):
        self.conn             = conn
        self.cursor           = conn.cursor()
        self.parent           = parent
        self.sale_id          = sale_id
        self.refresh_callback = refresh_callback
        self.selected_medicines = []
        self.previous_due       = 0

        self._load_sale_data()
        self._build_ui()

    # ── Data loading ──────────────────────────────────────────────────────

    def _load_sale_data(self):
        self.cursor.execute("""
            SELECT s.id, s.bill_no, s.customer_id, s.bill_date, s.total_amount,
                   s.discount, s.amount_paid,
                   COALESCE(s.cash_paid,0), COALESCE(s.online_paid,0),
                   s.previous_due, s.total_due, s.due_amount, s.credit_amount,
                   s.doctor_name, s.created_at, c.name, c.phone,
                   COALESCE(s.previous_credit,0)
            FROM sales s JOIN customers c ON s.customer_id=c.id
            WHERE s.id=?
        """, (self.sale_id,))
        self.sale_data       = self.cursor.fetchone()
        self.previous_due    = self.sale_data[9] or 0
        self.previous_credit = self.sale_data[17] or 0

        self.cursor.execute("""
            SELECT si.*, m.name, m.batch_no, m.expiry_date, m.type, m.schedule, m.gst_percent,
                   COALESCE(si.item_discount, 0)
            FROM sales_items si JOIN medicines m ON si.medicine_id=m.id
            WHERE si.sale_id=?
        """, (self.sale_id,))
        for item in self.cursor.fetchall():
            self.selected_medicines.append({
                'id':               item[2],
                'name':             item[7],
                'batch':            item[8],
                'expiry':           item[9],
                'qty':              item[3],
                'rate':             item[4],
                'amount':           item[6],
                'schedule':         item[11] or '',
                'type':             item[10] or '',
                'display_type':     item[10] or 'N/A',
                'gst_percent':      item[12] or 0,
                'medicine_discount':item[13],
            })

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        main = ttk.Frame(self.parent)
        main.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)

        # Customer info
        cf = ttk.LabelFrame(main, text="Customer Information")
        cf.pack(fill=tk.X, pady=(0, 15))

        ttk.Label(cf, text="Customer Name:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.customer_name = ttk.Entry(cf, width=20)
        self.customer_name.grid(row=0, column=1, padx=5, pady=5)
        self.customer_name.insert(0, self.sale_data[15] or '')

        ttk.Label(cf, text="Phone:").grid(row=0, column=2, sticky=tk.W, padx=5, pady=5)
        self.customer_phone = ttk.Entry(cf, width=15)
        self.customer_phone.grid(row=0, column=3, padx=5, pady=5)
        self.customer_phone.insert(0, self.sale_data[16] or '')

        ttk.Label(cf, text="Doctor Name:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.doctor_name = ttk.Entry(cf, width=18)
        self.doctor_name.grid(row=1, column=1, padx=5, pady=5)
        if self.sale_data[13]:
            self.doctor_name.insert(0, str(self.sale_data[13]))

        # Add medicine
        mf = ttk.LabelFrame(main, text="Add Medicine")
        mf.pack(fill=tk.X, pady=(0, 15))

        ttk.Label(mf, text="Medicine:").grid(row=0, column=0, sticky=tk.W, padx=8, pady=8)
        self.medicine_combo = TwoStepMedicineCombo(mf, self.conn, width=60)
        self.medicine_combo.grid(row=0, column=1, padx=8, pady=8)
        self.medicine_combo.bind('<<ComboboxSelected>>', lambda e: self.quantity.focus())
        self.medicine_combo.next_focus_widget = lambda: self.quantity.focus()

        ttk.Label(mf, text="Quantity:").grid(row=0, column=2, sticky=tk.W, padx=5, pady=5)
        self.quantity = ttk.Entry(mf, width=10)
        self.quantity.grid(row=0, column=3, padx=5, pady=5)
        self.quantity.bind('<Return>', lambda e: self._add_medicine())

        try:
            ttk.Button(mf, text="Add Medicine", command=self._add_medicine,
                       bootstyle="success").grid(row=0, column=4, padx=5, pady=5)
        except Exception:
            ttk.Button(mf, text="Add Medicine", command=self._add_medicine
                       ).grid(row=0, column=4, padx=5, pady=5)

        # Items tree
        sf = ttk.LabelFrame(main, text="Bill Items")
        sf.pack(fill=tk.BOTH, expand=True, pady=(0, 15))

        cols = ('Medicine','Batch','Expiry','Qty','Type','Rate','Amount','Schedule')
        self.medicine_tree = ttk.Treeview(sf, columns=cols, show='headings', height=8)
        for col in cols:
            self.medicine_tree.heading(col, text=col)
            self.medicine_tree.column(col, width=120)
        sb = ttk.Scrollbar(sf, orient=tk.VERTICAL, command=self.medicine_tree.yview)
        self.medicine_tree.configure(yscrollcommand=sb.set)
        self.medicine_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self.medicine_tree.bind('<Double-1>', self._edit_quantity)
        self.medicine_tree.bind('<Return>',   self._edit_quantity)
        self.medicine_tree.bind('<Delete>',   self._remove_medicine)
        self.medicine_tree.bind('<Escape>',   lambda e: self.discount.focus())
        self.parent.bind('<F2>', lambda e: self._focus_tree(), add='+')

        # Summary
        sumf = ttk.LabelFrame(main, text="Bill Summary")
        sumf.pack(fill=tk.X, pady=(0, 10))

        # Load stored discount values directly
        stored_disc_rs  = float(self.sale_data[5] or 0)
        self.cursor.execute("SELECT COALESCE(discount_pct,0) FROM sales WHERE id=?", (self.sale_id,))
        r2 = self.cursor.fetchone()
        stored_disc_pct = float(r2[0]) if r2 else 0.0
        self._disc_editing = None

        def _entry(row, col, lbl, width, default):
            ttk.Label(sumf, text=lbl).grid(row=row, column=col, sticky=tk.W, padx=5, pady=3)
            e = ttk.Entry(sumf, width=width)
            e.grid(row=row, column=col+1, padx=5, pady=3)
            e.insert(0, default)
            e.bind('<KeyRelease>', self._calculate_total)
            e.bind('<FocusIn>', lambda ev: ev.widget.select_range(0, tk.END))
            return e

        self.discount_pct = _entry(0, 0, "Overall Disc %:", 7, f"{stored_disc_pct:.4g}")
        self.discount     = _entry(0, 2, "Overall Disc ₹:", 8, f"{stored_disc_rs:.2f}")
        self.discount_pct.bind('<KeyRelease>', self._on_disc_pct_change)
        self.discount.bind('<KeyRelease>',     self._on_disc_rs_change)
        self.cash_paid   = _entry(0, 4, "Cash Paid:",  10, str(self.sale_data[7] or 0))
        self.online_paid = _entry(0, 6, "Online Paid:",10, str(self.sale_data[8] or 0))

        # Rounding
        ttk.Label(sumf, text="Rounding:").grid(row=0, column=8, sticky=tk.W, padx=5, pady=3)
        self.rounding = ttk.Entry(sumf, width=8)
        self.rounding.grid(row=0, column=9, padx=5, pady=3)
        self.cursor.execute("SELECT rounding FROM sales WHERE id=?", (self.sale_id,))
        r = self.cursor.fetchone()
        self.rounding.insert(0, str(r[0] if r and r[0] is not None else 0))
        self.rounding.bind('<KeyRelease>', self._calculate_total)
        self.rounding.bind('<FocusIn>', lambda e: e.widget.select_range(0, tk.END))

        # Tab/Return chain
        self.discount_pct.bind('<Return>', lambda e: self.discount.focus())
        self.discount.bind('<Return>',     lambda e: self.cash_paid.focus())
        self.cash_paid.bind('<Return>',    lambda e: self.online_paid.focus())
        self.online_paid.bind('<Return>',  lambda e: self.rounding.focus())

        ttk.Label(sumf, text="Total Amount:").grid(row=0, column=10, sticky=tk.W, padx=15, pady=3)
        self.total_amount_var = tk.StringVar(value="0.00")
        ttk.Label(sumf, textvariable=self.total_amount_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(
            row=0, column=11, sticky=tk.W, padx=5, pady=3)

        ttk.Label(sumf, text="Net Amount:").grid(row=0, column=12, sticky=tk.W, padx=15, pady=3)
        self.net_amount_var = tk.StringVar(value="0.00")
        ttk.Label(sumf, textvariable=self.net_amount_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold'),
                  foreground=get_alert_color('info')).grid(
            row=0, column=13, sticky=tk.W, padx=5, pady=3)

        ttk.Label(sumf, text="Previous Due:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        self.previous_due_var = tk.StringVar(value=f"{self.previous_due:.2f}")
        ttk.Label(sumf, textvariable=self.previous_due_var,
                  font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
                  foreground=get_alert_color('warning')).grid(
            row=1, column=1, sticky=tk.W, padx=5, pady=2)

        ttk.Label(sumf, text="Total Paid:").grid(row=1, column=2, sticky=tk.W, padx=5, pady=2)
        self.amount_paid_var = tk.StringVar(value="0.00")
        ttk.Label(sumf, textvariable=self.amount_paid_var,
                  font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT, 'bold'),
                  foreground=get_alert_color('success')).grid(
            row=1, column=3, sticky=tk.W, padx=5, pady=2)

        ttk.Label(sumf, text="Due Amount:").grid(row=1, column=4, sticky=tk.W, padx=5, pady=2)
        self.due_amount_var = tk.StringVar(value="0.00")
        ttk.Label(sumf, textvariable=self.due_amount_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(
            row=1, column=5, sticky=tk.W, padx=5, pady=2)

        ttk.Label(sumf, text="Total Due:").grid(row=1, column=8, sticky=tk.W, padx=15, pady=2)
        self.total_due_var = tk.StringVar(value="0.00")
        ttk.Label(sumf, textvariable=self.total_due_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold'),
                  foreground=get_alert_color('danger')).grid(
            row=1, column=9, sticky=tk.W, padx=5, pady=2)

        # Buttons
        bf = ttk.Frame(main)
        bf.pack(fill=tk.X, pady=10)
        self.save_btn   = ttk.Button(bf, text="Save Changes", command=self._save_bill)
        self.save_btn.pack(side=tk.LEFT, padx=5)
        self.cancel_btn = ttk.Button(bf, text="Cancel", command=self.parent.destroy)
        self.cancel_btn.pack(side=tk.LEFT, padx=5)

        nav = [self.discount_pct, self.discount, self.cash_paid, self.online_paid,
               self.rounding, self.save_btn, self.cancel_btn]
        n = len(nav)
        for i, w in enumerate(nav):
            w.bind('<Up>',   lambda e, i=i: nav[(i-1)%n].focus(), add='+')
            w.bind('<Down>', lambda e, i=i: nav[(i+1)%n].focus(), add='+')
        self.save_btn.bind('<Return>',   lambda e: self._save_bill())
        self.cancel_btn.bind('<Return>', lambda e: self.parent.destroy())
        self.parent.bind('<Escape>', lambda e: self.parent.destroy())

        self._update_tree()
        self._calculate_total()

    # ── Two-way discount binding ─────────────────────────────────────────

    def _on_disc_pct_change(self, event=None):
        if self._disc_editing == 'rs':
            return
        self._disc_editing = 'pct'
        try:
            pct = float(self.discount_pct.get() or 0)
            subtotal = round(sum(m['amount'] for m in self.selected_medicines), 2)
            rs = round(subtotal * pct / 100, 2)
            self.discount.delete(0, tk.END)
            self.discount.insert(0, f"{rs:.2f}")
        except ValueError:
            pass
        self._disc_editing = None
        self._calculate_total()

    def _on_disc_rs_change(self, event=None):
        if self._disc_editing == 'pct':
            return
        self._disc_editing = 'rs'
        try:
            rs = float(self.discount.get() or 0)
            subtotal = round(sum(m['amount'] for m in self.selected_medicines), 2)
            pct = round(rs / subtotal * 100, 2) if subtotal > 0 else 0.0
            self.discount_pct.delete(0, tk.END)
            self.discount_pct.insert(0, f"{pct:.2f}")
        except ValueError:
            pass
        self._disc_editing = None
        self._calculate_total()

    # ── Medicine actions ──────────────────────────────────────────────────

    def _add_medicine(self):
        sel      = self.medicine_combo.get_selected_medicine()
        qty_text = self.quantity.get()
        if not sel or not qty_text:
            messagebox.showwarning("Missing Information",
                                   "Please select medicine and enter quantity.")
            return
        try:
            qty = int(qty_text)
        except ValueError:
            messagebox.showerror("Invalid Quantity", "Please enter a valid quantity.")
            return

        self.cursor.execute(
            "SELECT gst_percent FROM medicines WHERE id=? LIMIT 1", (sel['id'],))
        gst_row = self.cursor.fetchone()
        gst_pct = float(gst_row[0]) if gst_row and gst_row[0] else 0.0

        self.selected_medicines.append({
            'id':               sel['id'],
            'name':             sel['name'],
            'batch':            sel['batch'],
            'expiry':           sel['expiry'],
            'qty':              qty,
            'rate':             sel['rate'],
            'amount':           round(qty * sel['rate'], 2),
            'schedule':         sel.get('schedule', ''),
            'type':             sel.get('type', ''),
            'display_type':     sel.get('type', '') or 'N/A',
            'gst_percent':      gst_pct,
            'medicine_discount':0,
        })
        self._update_tree()
        self._calculate_total()
        self.quantity.delete(0, tk.END)
        self.medicine_combo.set('')

    def _update_tree(self):
        for item in self.medicine_tree.get_children():
            self.medicine_tree.delete(item)
        for med in self.selected_medicines:
            self.medicine_tree.insert('', tk.END, values=(
                med['name'], med['batch'], med['expiry'],
                med['qty'], med.get('display_type', 'N/A'),
                f"{med['rate']:.2f}", f"{med['amount']:.2f}", med['schedule'],
            ))

    def _edit_quantity(self, event=None):
        sel = self.medicine_tree.selection()
        if not sel:
            return
        values = self.medicine_tree.item(sel[0])['values']

        dlg = tk.Toplevel(self.parent)
        dlg.title("Edit Quantity")
        dlg.geometry("360x190")
        dlg.resizable(False, False)
        dlg.grab_set()

        ttk.Label(dlg, text=f"Medicine: {values[0]}",
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).pack(pady=(12, 6))
        ttk.Label(dlg, text="Quantity (0 to remove):").pack()
        qty_e = ttk.Entry(dlg, width=22)
        qty_e.pack(pady=6)
        qty_e.insert(0, str(values[3]))
        qty_e.select_range(0, tk.END)
        qty_e.focus()

        def update():
            try:
                new_qty = int(qty_e.get())
                idx = self.medicine_tree.index(sel[0])
                if new_qty == 0:
                    del self.selected_medicines[idx]
                else:
                    self.selected_medicines[idx]['qty']    = new_qty
                    self.selected_medicines[idx]['amount'] = new_qty * self.selected_medicines[idx]['rate']
                self._update_tree()
                self._calculate_total()
                dlg.destroy()
                self.medicine_tree.focus()
            except ValueError:
                messagebox.showerror("Invalid Input", "Please enter a valid quantity.")

        qty_e.bind('<Return>', lambda e: update())
        dlg.bind('<Escape>', lambda e: dlg.destroy())
        bf = ttk.Frame(dlg)
        bf.pack(pady=8)
        ub = ttk.Button(bf, text="Update", command=update)
        ub.pack(side=tk.LEFT, padx=6)
        cb = ttk.Button(bf, text="Cancel", command=dlg.destroy)
        cb.pack(side=tk.LEFT, padx=6)
        ub.bind('<Return>', lambda e: update())
        cb.bind('<Return>', lambda e: dlg.destroy())

    def _remove_medicine(self, event):
        sel = self.medicine_tree.selection()
        if sel:
            idx = self.medicine_tree.index(sel[0])
            del self.selected_medicines[idx]
            self._update_tree()
            self._calculate_total()

    def _focus_tree(self):
        items = self.medicine_tree.get_children()
        if not items:
            return
        sel = self.medicine_tree.selection()
        target = sel[0] if sel else items[0]
        self.medicine_tree.selection_set(target)
        self.medicine_tree.focus(target)
        self.medicine_tree.focus()
        self.medicine_tree.see(target)

    # ── Calculate ─────────────────────────────────────────────────────────

    def _calculate_total(self, event=None):
        try: disc_rs = float(self.discount.get() or 0)
        except ValueError: disc_rs = 0
        try: rounding = float(self.rounding.get() or 0)
        except ValueError: rounding = 0
        try: cash = float(self.cash_paid.get() or 0)
        except ValueError: cash = 0
        try: online = float(self.online_paid.get() or 0)
        except ValueError: online = 0

        from core.calc_engine import calc_bill_summary, calc_payment_result
        summary = calc_bill_summary(self.selected_medicines, rounding=rounding, discount_rs=disc_rs)
        pay     = calc_payment_result(summary['total_amount'], cash, online,
                                      self.previous_due, self.previous_credit)

        self.total_amount_var.set(f"{summary['total_amount']:.2f}")
        self.net_amount_var.set(f"{round(summary['total_amount'] + self.previous_due - self.previous_credit, 2):.2f}")
        self.amount_paid_var.set(f"{pay['amount_paid']:.2f}")
        self.due_amount_var.set(f"{pay['due_amount']:.2f}")
        self.total_due_var.set(f"{pay['due_amount']:.2f}")

    # ── Save ──────────────────────────────────────────────────────────────

    def _save_bill(self):
        if not self.selected_medicines:
            messagebox.showwarning("No Items", "Please add medicines to the bill.")
            return
        try:
            disc_rs  = float(self.discount.get() or 0)
            disc_pct = float(self.discount_pct.get() or 0)
            rounding = float(self.rounding.get() or 0)
            cash     = float(self.cash_paid.get() or 0)
            online   = float(self.online_paid.get() or 0)

            update_existing_bill(
                conn          = self.conn,
                sale_id       = self.sale_id,
                medicines     = self.selected_medicines,
                discount_pct  = disc_pct,
                discount_rs   = disc_rs,
                rounding      = rounding,
                cash_paid     = cash,
                online_paid   = online,
                customer_name = self.customer_name.get(),
                customer_phone= self.customer_phone.get(),
                doctor_name   = self.doctor_name.get(),
                previous_due  = self.previous_due,
            )
            messagebox.showinfo("Success", "Bill updated successfully!")
            self.parent.destroy()
            if self.refresh_callback:
                self.refresh_callback()
        except Exception as e:
            self.conn.rollback()
            messagebox.showerror("Error", f"Failed to update bill: {e}")
