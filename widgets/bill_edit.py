import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

from core.alert_colors import get_alert_color
from core.font_config import *
from core.calc_engine import calc_bill_summary, calc_payment_result, auto_round
from core.billing_service import update_existing_bill
from core.layout_config import BILLING_ROWS, is_strip_count_type, parse_tablets_per_stripe, load_layout
from core.column_config import get_visible_columns
from core.margin_utils import (
    display_mrp_per_unit,
    enrich_medicine_margin_fields,
    format_line_margin_display,
    format_total_margin_display,
    line_net_margin,
    margin_column_heading,
    show_margin_column,
    show_total_margin,
    total_net_margin,
)
from core.customer_service import get_all_doctor_names
from core.village_service import village_names_for_ui
from widgets.two_step_medicine_combo import TwoStepMedicineCombo
from widgets.searchable_combo import SearchableCombo


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
                   COALESCE(s.previous_credit,0), COALESCE(c.address, '')
            FROM sales s JOIN customers c ON s.customer_id=c.id
            WHERE s.id=?
        """, (self.sale_id,))
        self.sale_data       = self.cursor.fetchone()
        self.previous_due    = self.sale_data[9] or 0
        self.previous_credit = self.sale_data[17] or 0
        self._stored_cash    = float(self.sale_data[7] or 0)
        self._stored_online  = float(self.sale_data[8] or 0)

        # GST % as sold, not today's rate: saving the edit writes these lines back.
        # A line with no rate on it or on the medicine stays blank (NULL), not 0%.
        self.cursor.execute("""
            SELECT si.medicine_id, si.qty, si.rate, si.amount,
                   COALESCE(si.item_discount, 0),
                   m.name, m.batch_no, m.expiry_date, m.type, m.schedule,
                   COALESCE(si.gst_percent, m.gst_percent)
            FROM sales_items si JOIN medicines m ON si.medicine_id=m.id
            WHERE si.sale_id=?
        """, (self.sale_id,))
        for item in self.cursor.fetchall():
            self.selected_medicines.append({
                'id':                item[0],
                'qty':               item[1],
                'rate':              item[2],
                'amount':            item[3],
                'medicine_discount': item[4],
                'name':              item[5],
                'batch':             item[6],
                'expiry':            item[7],
                'type':              item[8] or '',
                'display_type':      item[8] or 'N/A',
                'schedule':          item[9] or '',
                'gst_percent':       item[10] if item[10] not in (None, '') else None,
            })
            self._enrich_med_from_db(self.selected_medicines[-1])

    def _enrich_med_from_db(self, med):
        self.cursor.execute("""
            SELECT m.type, COALESCE(m.unit,'1'), COALESCE(m.location,''), COALESCE(m.mrp,0),
                   (SELECT pi.rate FROM purchase_items pi
                    WHERE pi.medicine_id=m.id ORDER BY pi.id DESC LIMIT 1)
            FROM medicines m WHERE m.id=?
        """, (med['id'],))
        info = self.cursor.fetchone()
        if not info:
            return
        list_mrp = float(info[3] or med.get('rate') or 0)
        purchase_rate = float(info[4] or 0)
        enrich_medicine_margin_fields(med, list_mrp, purchase_rate, info[0], info[1])
        med['location'] = (info[2] or '').strip()
        if 'original_amount' not in med:
            med['original_amount'] = round(med['qty'] * med['rate'], 2)

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        main = ttk.Frame(self.parent)
        main.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)

        cf = ttk.LabelFrame(main, text="Customer Information")
        cf.pack(fill=tk.X, pady=(0, 15))

        ttk.Label(cf, text="Payment:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.payment_mode = SearchableCombo(cf, values=('Cash', 'Due'), width=8, listbox_height=2)
        self.payment_mode.grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)
        paid_total = float(self.sale_data[6] or 0)
        bill_total = float(self.sale_data[4] or 0)
        if paid_total + 0.01 < bill_total:
            self.payment_mode.set('Due')
        else:
            self.payment_mode.set('Cash')
        self.payment_mode.next_focus_widget = lambda: self.customer_name.focus()
        self.payment_mode.bind('<<ComboboxSelected>>', self._on_payment_mode_change)
        self.payment_mode.bind_apply_on_select(self._on_payment_mode_change)
        self.payment_mode.entry.bind('<KeyPress>', self._payment_mode_key, add='+')

        ttk.Label(cf, text="Customer Name:").grid(row=0, column=2, sticky=tk.W, padx=5, pady=5)
        self.customer_name = ttk.Entry(cf, width=20)
        self.customer_name.grid(row=0, column=3, padx=5, pady=5)
        self.customer_name.insert(0, self.sale_data[15] or '')

        ttk.Label(cf, text="Phone:").grid(row=0, column=4, sticky=tk.W, padx=5, pady=5)
        self.customer_phone = ttk.Entry(cf, width=15)
        self.customer_phone.grid(row=0, column=5, padx=5, pady=5)
        self.customer_phone.insert(0, self.sale_data[16] or '')

        ttk.Label(cf, text="Address (Village):").grid(row=0, column=6, sticky=tk.W, padx=5, pady=5)
        self.customer_address = SearchableCombo(cf, values=village_names_for_ui(self.conn), width=22)
        self.customer_address.grid(row=0, column=7, padx=5, pady=5)
        if self.sale_data[18]:
            self.customer_address.set(self.sale_data[18])

        ttk.Label(cf, text="Previous Due:").grid(row=1, column=6, sticky=tk.W, padx=5, pady=5)
        self.previous_due_var = tk.StringVar(value=f"{self.previous_due:.2f}")
        ttk.Label(cf, textvariable=self.previous_due_var,
                  foreground=get_alert_color('warning')).grid(row=1, column=7, padx=5, pady=5)

        ttk.Label(cf, text="Doctor Name:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.doctor_name = SearchableCombo(cf, width=18)
        self.doctor_name.grid(row=1, column=1, padx=5, pady=5)
        self.all_doctors = get_all_doctor_names(self.conn)
        self.doctor_name.configure(values=self.all_doctors)
        if self.sale_data[13]:
            self.doctor_name.set(str(self.sale_data[13]))
        self.doctor_name.bind('<<ComboboxSelected>>', self._on_doctor_select)
        self.doctor_name.next_focus_widget = lambda: self._on_doctor_select()
        self.doctor_name.entry.bind(
            '<FocusIn>',
            lambda e: self.doctor_name.configure(values=get_all_doctor_names(self.conn)),
            add='+')

        ttk.Label(cf, text="Doctor Phone:").grid(row=1, column=2, sticky=tk.W, padx=5, pady=5)
        self.doctor_phone = ttk.Entry(cf, width=15)
        self.doctor_phone.grid(row=1, column=3, padx=5, pady=5)

        ttk.Label(cf, text="Bill Date:").grid(row=1, column=4, sticky=tk.W, padx=5, pady=5)
        self.bill_date = ttk.Entry(cf, width=12)
        self.bill_date.grid(row=1, column=5, padx=5, pady=5, sticky=tk.W)
        bill_dt = self.sale_data[3] or ''
        if bill_dt and ' ' in str(bill_dt):
            bill_dt = str(bill_dt).split(' ')[0]
        self.bill_date.insert(0, bill_dt or datetime.now().strftime('%Y-%m-%d'))

        mf = ttk.LabelFrame(main, text="Medicine Selection")
        mf.pack(fill=tk.X, pady=(0, 15))

        ttk.Label(mf, text="Medicine:").grid(row=0, column=0, sticky=tk.W, padx=8, pady=8)
        self.medicine_combo = TwoStepMedicineCombo(mf, self.conn, width=60)
        self.medicine_combo.grid(row=0, column=1, padx=8, pady=8)
        self.medicine_combo.bind('<<ComboboxSelected>>', lambda e: self.quantity.focus())
        self.medicine_combo.next_focus_widget = lambda: self.quantity.focus()
        self.medicine_combo.empty_enter_callback = self._focus_overall_discount

        ttk.Label(mf, text="Quantity:").grid(row=0, column=2, sticky=tk.W, padx=5, pady=5)
        self.quantity = ttk.Entry(mf, width=10)
        self.quantity.grid(row=0, column=3, padx=5, pady=5)
        self.quantity.bind('<Return>', lambda e: self.medicine_discount.focus())

        ttk.Label(mf, text="Disc ₹:").grid(row=0, column=4, sticky=tk.W, padx=5, pady=5)
        self.medicine_discount = ttk.Entry(mf, width=8)
        self.medicine_discount.grid(row=0, column=5, padx=5, pady=5)
        self.medicine_discount.insert(0, "0")
        self.medicine_discount.bind('<Return>', lambda e: self._add_medicine())

        try:
            ttk.Button(mf, text="Add Medicine", command=self._add_medicine,
                       bootstyle="success").grid(row=0, column=6, padx=5, pady=5)
        except Exception:
            ttk.Button(mf, text="Add Medicine", command=self._add_medicine
                       ).grid(row=0, column=6, padx=5, pady=5)

        sf = ttk.LabelFrame(main, text="Selected Medicines")
        sf.pack(fill=tk.BOTH, expand=True, pady=(0, 15))

        self._all_columns = (
            'Medicine', 'Batch', 'Expiry', 'Qty', 'Type', 'MRP',
            'Disc ₹', 'Margin ₹', 'Amount', 'Schedule', 'Location',
        )
        self.medicine_tree = ttk.Treeview(
            sf, columns=self._all_columns, show='headings',
            height=BILLING_ROWS, style='Large.Treeview',
        )
        col_widths = {
            'Medicine': 140, 'Batch': 70, 'Expiry': 70, 'Qty': 50, 'Type': 50,
            'MRP': 60, 'Disc ₹': 55, 'Margin ₹': 65, 'Amount': 70,
            'Schedule': 60, 'Location': 80,
        }
        for col in self._all_columns:
            self.medicine_tree.heading(col, text=col)
            self.medicine_tree.column(col, width=col_widths.get(col, 80))
        try:
            self.medicine_tree.heading('Margin ₹', text=margin_column_heading())
        except Exception:
            pass
        if not show_margin_column():
            self.medicine_tree.column('Margin ₹', width=0, stretch=False)
        sb = ttk.Scrollbar(sf, orient=tk.VERTICAL, command=self.medicine_tree.yview)
        self.medicine_tree.configure(yscrollcommand=sb.set)
        self.medicine_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self.medicine_tree.bind('<Double-1>', self._edit_quantity)
        self.medicine_tree.bind('<Return>',   self._edit_quantity)
        self.medicine_tree.bind('<Delete>',   self._remove_medicine)
        self.medicine_tree.bind('<Escape>',   lambda e: self.discount.focus())
        self.parent.bind('<F2>', lambda e: self._focus_tree(), add='+')

        # Summary — same layout as main Sales screen
        sumf = ttk.LabelFrame(main, text="Billing Summary")
        sumf.pack(fill=tk.X, pady=(0, 10))

        stored_disc_rs = float(self.sale_data[5] or 0)
        self.cursor.execute("SELECT COALESCE(discount_pct,0) FROM sales WHERE id=?", (self.sale_id,))
        r2 = self.cursor.fetchone()
        stored_disc_pct = float(r2[0]) if r2 else 0.0
        self._disc_editing = None

        ttk.Label(sumf, text="Overall Disc %:").grid(row=0, column=0, sticky=tk.W, padx=4, pady=2)
        self.discount_pct = ttk.Entry(sumf, width=6)
        self.discount_pct.grid(row=0, column=1, padx=4, pady=2)
        self.discount_pct.insert(0, f"{stored_disc_pct:.4g}")
        self.discount_pct.bind('<KeyRelease>', self._on_disc_pct_change)
        self.discount_pct.bind('<Return>', lambda e: self.discount.focus())
        self.discount_pct.bind('<FocusIn>', lambda e: e.widget.select_range(0, tk.END))

        ttk.Label(sumf, text="Overall Disc ₹:").grid(row=0, column=2, sticky=tk.W, padx=4, pady=2)
        self.discount = ttk.Entry(sumf, width=7)
        self.discount.grid(row=0, column=3, padx=4, pady=2)
        self.discount.insert(0, f"{stored_disc_rs:.2f}")
        self.discount.bind('<KeyRelease>', self._on_disc_rs_change)
        self.discount.bind('<Return>', lambda e: self.rounding.focus())
        self.discount.bind('<FocusIn>', lambda e: e.widget.select_range(0, tk.END))

        ttk.Label(sumf, text="Rounding:").grid(row=0, column=4, sticky=tk.W, padx=4, pady=2)
        self.rounding = ttk.Entry(sumf, width=7)
        self.rounding.grid(row=0, column=5, padx=4, pady=2)
        self.cursor.execute("SELECT rounding FROM sales WHERE id=?", (self.sale_id,))
        r = self.cursor.fetchone()
        self.rounding.insert(0, str(r[0] if r and r[0] is not None else 0))
        self._rounding_touched = True  # keep DB rounding unless user wants auto again
        self.rounding.bind('<KeyRelease>', self._on_rounding_change)
        self.rounding.bind('<Return>', self._rounding_enter)
        self.rounding.bind('<FocusIn>', lambda e: e.widget.select_range(0, tk.END))

        ttk.Label(sumf, text="Cash:").grid(row=0, column=6, sticky=tk.W, padx=4, pady=2)
        self.cash_paid = ttk.Entry(sumf, width=9)
        self.cash_paid.grid(row=0, column=7, padx=4, pady=2)
        self.cash_paid.insert(0, str(self.sale_data[7] or 0))
        self.cash_paid.bind('<KeyRelease>', self._calculate_total)
        self.cash_paid.bind('<Return>', lambda e: self.online_paid.focus())
        self.cash_paid.bind('<FocusIn>', lambda e: e.widget.select_range(0, tk.END))

        ttk.Label(sumf, text="Online:").grid(row=0, column=8, sticky=tk.W, padx=4, pady=2)
        self.online_paid = ttk.Entry(sumf, width=9)
        self.online_paid.grid(row=0, column=9, padx=4, pady=2)
        self.online_paid.insert(0, str(self.sale_data[8] or 0))
        self.online_paid.bind('<KeyRelease>', self._calculate_total)
        self.online_paid.bind('<Return>', lambda e: self._save_bill())
        self.online_paid.bind('<FocusIn>', lambda e: e.widget.select_range(0, tk.END))

        self.total_amount_var = tk.StringVar(value="0.00")
        self.total_due_var = tk.StringVar(value="0.00")

        ttk.Label(sumf, text="Subtotal:").grid(row=1, column=0, sticky=tk.W, padx=4, pady=2)
        self.subtotal_var = tk.StringVar(value="0.00")
        ttk.Label(sumf, textvariable=self.subtotal_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS)).grid(
            row=1, column=1, sticky=tk.W, padx=4, pady=2)

        self._total_margin_lbl = ttk.Label(sumf, text="Total Margin:")
        self.total_margin_var = tk.StringVar(value="0.00")
        self._total_margin_val = ttk.Label(
            sumf, textvariable=self.total_margin_var,
            font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold'),
        )
        self._refresh_margin_summary_visibility()

        ttk.Label(sumf, text="Total Paid:").grid(row=1, column=2, sticky=tk.W, padx=4, pady=2)
        self.amount_paid_var = tk.StringVar(value="0.00")
        ttk.Label(sumf, textvariable=self.amount_paid_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold'),
                  foreground=get_alert_color('success')).grid(
            row=1, column=3, sticky=tk.W, padx=4, pady=2)

        ttk.Label(sumf, text="Due Amount:").grid(row=1, column=4, sticky=tk.W, padx=4, pady=2)
        self.due_amount_var = tk.StringVar(value="0.00")
        ttk.Label(sumf, textvariable=self.due_amount_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(
            row=1, column=5, sticky=tk.W, padx=4, pady=2)

        self.gst_percent_var = tk.StringVar(value="Included in MRP")
        ttk.Label(sumf, text="GST %:").grid(row=2, column=0, sticky=tk.W, padx=4, pady=4)
        ttk.Label(sumf, textvariable=self.gst_percent_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS)).grid(
            row=2, column=1, columnspan=6, sticky=tk.W, padx=4, pady=4)

        action_outer = ttk.Frame(sumf)
        action_outer.grid(row=0, column=10, rowspan=2, sticky=tk.NE, padx=(12, 4), pady=4)
        totals_col = ttk.Frame(action_outer)
        totals_col.grid(row=0, column=0, rowspan=2, sticky=tk.NW, padx=(0, 14))
        ttk.Label(totals_col, text="Total Amount:",
                  font=(FONT_FAMILY, FONT_SIZE_LABELS)).grid(row=0, column=0, sticky=tk.W, pady=(0, 2))
        ttk.Label(totals_col, textvariable=self.total_amount_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(row=1, column=0, sticky=tk.W, pady=(0, 8))
        ttk.Label(totals_col, text="Total Due:",
                  font=(FONT_FAMILY, FONT_SIZE_LABELS)).grid(row=2, column=0, sticky=tk.W, pady=(0, 2))
        ttk.Label(totals_col, textvariable=self.total_due_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold'),
                  foreground=get_alert_color('danger')).grid(row=3, column=0, sticky=tk.W)

        save_col = ttk.Frame(action_outer)
        save_col.grid(row=0, column=1, rowspan=2, sticky=tk.N, padx=(0, 8))
        self.recalc_btn = ttk.Button(
            save_col, text="Recalculate", command=self._calculate_total, width=20,
        )
        self.recalc_btn.pack(pady=(0, 6))
        self.save_btn = ttk.Button(save_col, text="Save Changes", command=self._save_bill, width=20)
        self.save_btn.pack(pady=(0, 6))
        self.cancel_btn = ttk.Button(save_col, text="Cancel", command=self.parent.destroy, width=20)
        self.cancel_btn.pack()

        self._on_payment_mode_change()

        nav = [self.discount_pct, self.discount, self.rounding, self.cash_paid,
               self.online_paid, self.recalc_btn, self.save_btn, self.cancel_btn]
        n = len(nav)
        for i, w in enumerate(nav):
            w.bind('<Up>',   lambda e, i=i: nav[(i-1)%n].focus(), add='+')
            w.bind('<Down>', lambda e, i=i: nav[(i+1)%n].focus(), add='+')
        self.save_btn.bind('<Return>',   lambda e: self._save_bill())
        self.cancel_btn.bind('<Return>', lambda e: self.parent.destroy())
        from core.dialog_escape import bind_escape_to_close
        bind_escape_to_close(self.parent, on_close=self.parent.destroy)

        self._sync_medicine_reserved_stock()
        self._update_tree()
        self._calculate_total()

    # ── Doctor helpers ────────────────────────────────────────────────────

    def _on_doctor_select(self, event=None):
        self.medicine_combo.focus_step1()

    def _focus_overall_discount(self):
        try:
            self.discount_pct.focus_set()
            self.discount_pct.select_range(0, tk.END)
        except Exception:
            pass

    def _is_due_payment(self) -> bool:
        return (self.payment_mode.get() or 'Cash').strip().lower() == 'due'

    def _payment_mode_key(self, event=None):
        keysym = (event.keysym or '').lower()
        if keysym == 'd':
            self.payment_mode.set('Due')
            self._on_payment_mode_change()
            return 'break'
        if keysym == 'c':
            was_due = self._is_due_payment()
            self.payment_mode.set('Cash')
            self._on_payment_mode_change()
            if was_due:
                self._focus_cash_payment()
            return 'break'
        return None

    def _focus_cash_payment(self):
        try:
            self.cash_paid.focus_set()
            self.cash_paid.select_range(0, tk.END)
        except Exception:
            pass

    def _validate_cash_payment(self) -> bool:
        if self._is_due_payment():
            return True
        cash_s = (self.cash_paid.get() or '').strip()
        online_s = (self.online_paid.get() or '').strip()
        if not cash_s and not online_s:
            messagebox.showwarning(
                "Payment Required",
                "Please enter amount in Cash or Online before saving.",
            )
            self._focus_cash_payment()
            return False
        try:
            total_paid = float(cash_s or 0) + float(online_s or 0)
        except ValueError:
            messagebox.showwarning(
                "Invalid Payment",
                "Please enter valid numbers in Cash or Online.",
            )
            self._focus_cash_payment()
            return False
        if total_paid <= 0:
            messagebox.showwarning(
                "Payment Required",
                "Cash or Online amount must be greater than zero.",
            )
            self._focus_cash_payment()
            return False
        return True

    def _on_payment_mode_change(self, event=None):
        if self._is_due_payment():
            self.cash_paid.delete(0, tk.END)
            self.cash_paid.insert(0, '0')
            self.online_paid.delete(0, tk.END)
            self.online_paid.insert(0, '0')
            for w in (self.cash_paid, self.online_paid):
                try:
                    w.configure(state='disabled')
                except Exception:
                    pass
        else:
            for w in (self.cash_paid, self.online_paid):
                try:
                    w.configure(state='normal')
                except Exception:
                    pass
            cash_s = (self.cash_paid.get() or '').strip()
            online_s = (self.online_paid.get() or '').strip()
            if cash_s in ('0', '0.0', '0.00') and online_s in ('0', '0.0', '0.00', ''):
                self.cash_paid.delete(0, tk.END)
                self.online_paid.delete(0, tk.END)
            elif not (self.cash_paid.get() or '').strip() and self._stored_cash:
                self.cash_paid.insert(0, str(self._stored_cash))
            elif not (self.online_paid.get() or '').strip() and self._stored_online:
                self.online_paid.insert(0, str(self._stored_online))
        self._calculate_total()

    def _on_rounding_change(self, event=None):
        self._rounding_touched = True
        self._calculate_total()

    def _rounding_enter(self, event=None):
        if self._is_due_payment():
            self._save_bill()
            return 'break'
        self.cash_paid.focus_set()
        try:
            self.cash_paid.select_range(0, tk.END)
        except Exception:
            pass
        return 'break'

    def _show_location_enabled(self):
        return bool(load_layout().get('billing_show_location_column', False))

    def _fmt_location(self, raw):
        return (raw or '').strip()

    def _refresh_margin_summary_visibility(self):
        if show_total_margin():
            self._total_margin_lbl.grid(row=1, column=6, sticky=tk.W, padx=4, pady=2)
            self._total_margin_val.grid(row=1, column=7, sticky=tk.W, padx=4, pady=2)
        else:
            self._total_margin_lbl.grid_remove()
            self._total_margin_val.grid_remove()

    def _tree_row_values(self, med):
        row = [
            med['name'], med['batch'], med['expiry'],
            med['qty'], med.get('display_type', 'N/A'), f"{display_mrp_per_unit(med):.2f}",
            f"{med.get('medicine_discount', 0):.2f}",
            format_line_margin_display(med),
            f"{med['amount']:.2f}",
            med['schedule'], med.get('location', ''),
        ]
        col_map = {c: i for i, c in enumerate(self._all_columns)}
        visible = get_visible_columns('billing', self._all_columns)
        if not self._show_location_enabled():
            visible = [c for c in visible if c != 'Location']
        if not show_margin_column():
            visible = [c for c in visible if c != 'Margin ₹']
        return tuple(row[col_map[c]] for c in visible if c in col_map)

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

    def _qty_in_bill(self, medicine_id):
        return sum(m['qty'] for m in self.selected_medicines if m['id'] == medicine_id)

    def _sync_medicine_reserved_stock(self):
        reserved = {}
        for m in self.selected_medicines:
            reserved[m['id']] = reserved.get(m['id'], 0) + m['qty']
        self.medicine_combo.set_reserved_stock(reserved)

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
        try:
            from core.billing_layout_prefs import schedule_requires_doctor
            needs_doc = schedule_requires_doctor(sel.get("schedule"))
        except Exception:
            needs_doc = bool(sel.get("schedule"))
        if needs_doc and not self.doctor_name.get().strip():
            messagebox.showwarning(
                "Doctor Required",
                "H1 / X (and other schedules if enabled in Settings) require a doctor name.",
            )
            return
        try:
            from datetime import date
            from core.batch_visibility import is_expired_as_of
            raw = self.bill_date.get().strip()
            bill_date = date.today()
            for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
                try:
                    bill_date = datetime.strptime(raw, fmt).date()
                    break
                except ValueError:
                    continue
            if is_expired_as_of(sel['expiry'], bill_date):
                messagebox.showerror("Expired Medicine",
                                     f"{sel['name']} expired on {sel['expiry']}.")
                return
        except Exception:
            pass

        self.cursor.execute(
            "SELECT COALESCE(stock_qty, 0) FROM medicines WHERE id=?", (sel['id'],))
        db_stock = int((self.cursor.fetchone() or [0])[0] or 0)
        available = db_stock - self._qty_in_bill(sel['id'])
        if available <= 0:
            messagebox.showerror("Out of Stock",
                                 f"{sel['name']} is fully reserved in this bill.")
            return
        if qty > available:
            messagebox.showerror("Insufficient Stock",
                                 f"Only {int(available)} units available.")
            return

        self.cursor.execute(
            "SELECT type, unit, gst_percent, location, mrp, "
            "(SELECT pi.rate FROM purchase_items pi WHERE pi.medicine_id=medicines.id "
            " ORDER BY pi.id DESC LIMIT 1) "
            "FROM medicines WHERE id=? LIMIT 1", (sel['id'],))
        info = self.cursor.fetchone()
        med_type = info[0] if info else ''
        unit_value = info[1] if info else '1'
        gst_pct = float(info[2]) if info and info[2] else 0.0
        location = self._fmt_location(info[3] if info else '')
        list_mrp = float(info[4] if info else sel.get('mrp') or 0)
        purchase_rate = float(info[5] if info and info[5] is not None else sel.get('rate') or 0)

        if is_strip_count_type(med_type or ''):
            try:
                ups = parse_tablets_per_stripe(unit_value)
                rate = list_mrp / ups
            except (ValueError, ZeroDivisionError):
                rate = list_mrp
        else:
            rate = list_mrp

        try:
            med_disc_rs = float(self.medicine_discount.get() or 0)
        except ValueError:
            med_disc_rs = 0

        base = round(qty * rate, 2)
        med_disc_rs = min(med_disc_rs, base)
        amount = round(base - med_disc_rs, 2)

        existing = next((m for m in self.selected_medicines if m['id'] == sel['id']), None)
        if existing:
            total_base = round((existing['qty'] + qty) * existing['rate'], 2)
            test_disc = round(existing.get('medicine_discount', 0) + med_disc_rs, 2)
            test_disc = min(test_disc, total_base)
            existing['qty'] += qty
            existing['original_amount'] = total_base
            existing['medicine_discount'] = test_disc
            existing['amount'] = round(total_base - test_disc, 2)
            enrich_medicine_margin_fields(
                existing, list_mrp, purchase_rate, med_type, unit_value)
            existing['location'] = location
        else:
            med_row = {
                'id':               sel['id'],
                'name':             sel['name'],
                'batch':            sel['batch'],
                'expiry':           sel['expiry'],
                'qty':              qty,
                'rate':             rate,
                'amount':           amount,
                'original_amount':  base,
                'medicine_discount':med_disc_rs,
                'schedule':         sel.get('schedule', ''),
                'type':             med_type or '',
                'display_type':     med_type or 'N/A',
                'gst_percent':      gst_pct,
                'location':         location,
                'unit':             unit_value,
            }
            enrich_medicine_margin_fields(
                med_row, list_mrp, purchase_rate, med_type, unit_value)
            self.selected_medicines.append(med_row)
        self._sync_medicine_reserved_stock()
        self._update_tree()
        self._calculate_total()
        self.quantity.delete(0, tk.END)
        self.medicine_discount.delete(0, tk.END)
        self.medicine_discount.insert(0, "0")
        self.medicine_combo.set('')
        self.medicine_combo.selected_medicine = None

    def _update_tree(self):
        for item in self.medicine_tree.get_children():
            self.medicine_tree.delete(item)
        for med in self.selected_medicines:
            enrich_medicine_margin_fields(
                med,
                med.get('list_mrp') or med.get('rate') or 0,
                med.get('purchase_rate') or 0,
                med.get('type') or '',
                med.get('unit') or '1',
            )
            self.medicine_tree.insert('', tk.END, values=self._tree_row_values(med))

    def _edit_quantity(self, event=None):
        sel = self.medicine_tree.selection()
        if not sel:
            return
        values = self.medicine_tree.item(sel[0])['values']

        from core.scroll_manager import open_dialog
        dlg = open_dialog(self.parent, "Edit Quantity", width=360, height=200, resizable=False)
        body = dlg.content
        ttk.Label(body, text=f"Medicine: {values[0]}",
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).pack(pady=(12, 6))
        ttk.Label(body, text="Quantity (0 to remove):").pack()
        qty_e = ttk.Entry(body, width=22)
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
                    med = self.selected_medicines[idx]
                    self.cursor.execute(
                        "SELECT COALESCE(stock_qty, 0) FROM medicines WHERE id=?",
                        (med['id'],))
                    db_stock = int((self.cursor.fetchone() or [0])[0] or 0)
                    if new_qty > db_stock:
                        messagebox.showerror("Insufficient Stock",
                                             f"Only {db_stock} units available in stock.")
                        return
                    med['qty']    = new_qty
                    med['amount'] = round(new_qty * med['rate'], 2)
                self._sync_medicine_reserved_stock()
                self._update_tree()
                self._calculate_total()
                dlg.destroy()
                self.medicine_tree.focus()
            except ValueError:
                messagebox.showerror("Invalid Input", "Please enter a valid quantity.")

        qty_e.bind('<Return>', lambda e: update())
        dlg.bind('<Escape>', lambda e: dlg.destroy())
        ub = ttk.Button(dlg.footer, text="Update", command=update)
        ub.pack(side=tk.LEFT, padx=6)
        cb = ttk.Button(dlg.footer, text="Cancel", command=dlg.destroy)
        cb.pack(side=tk.LEFT, padx=6)
        ub.bind('<Return>', lambda e: update())
        cb.bind('<Return>', lambda e: dlg.destroy())

    def _remove_medicine(self, event):
        sel = self.medicine_tree.selection()
        if sel:
            idx = self.medicine_tree.index(sel[0])
            del self.selected_medicines[idx]
            self._sync_medicine_reserved_stock()
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
        gst_pcts = [m.get('gst_percent', 0) for m in self.selected_medicines
                    if (m.get('gst_percent') or 0) > 0]
        if gst_pcts:
            unique = list(set(gst_pcts))
            self.gst_percent_var.set(
                f"{unique[0]}% (Included in MRP)" if len(unique) == 1
                else "Mixed GST (Included in MRP)")
        else:
            self.gst_percent_var.set("No GST")

        try:
            disc_rs = float(self.discount.get() or 0)
        except ValueError:
            disc_rs = 0
        try:
            rounding = float(self.rounding.get() or 0)
        except ValueError:
            rounding = 0

        summary = calc_bill_summary(self.selected_medicines, rounding=0, discount_rs=disc_rs)
        # Preserve stored / manual rounding on edit (same as new-bill _rounding_touched).
        if not getattr(self, '_rounding_touched', True):
            rounding = auto_round(summary['pre_round_total'])
            self.rounding.delete(0, tk.END)
            self.rounding.insert(0, f"{rounding:.2f}")

        summary = calc_bill_summary(self.selected_medicines, rounding=rounding, discount_rs=disc_rs)
        self.subtotal_var.set(f"{summary['subtotal']:.2f}")
        self.total_amount_var.set(f"{summary['total_amount']:.2f}")
        if hasattr(self, 'total_margin_var'):
            self.total_margin_var.set(
                format_total_margin_display(self.selected_medicines, disc_rs))

        try:
            if self._is_due_payment():
                cash = 0.0
                online = 0.0
            else:
                cash = float(self.cash_paid.get() or 0)
                online = float(self.online_paid.get() or 0)
        except ValueError:
            cash = 0
            online = 0

        pay = calc_payment_result(summary['total_amount'], cash, online,
                                  self.previous_due, self.previous_credit)
        self.amount_paid_var.set(f"{pay['amount_paid']:.2f}")
        self.due_amount_var.set(f"{pay['due_amount']:.2f}")
        self.total_due_var.set(f"{pay['total_due']:.2f}")

    # ── Save ──────────────────────────────────────────────────────────────

    def _save_bill(self):
        if not self.selected_medicines:
            messagebox.showwarning("No Items", "Please add medicines to the bill.")
            return
        if not self._validate_cash_payment():
            return
        try:
            disc_rs  = float(self.discount.get() or 0)
            disc_pct = float(self.discount_pct.get() or 0)
            rounding = float(self.rounding.get() or 0)
            if self._is_due_payment():
                cash = 0.0
                online = 0.0
            else:
                cash   = float(self.cash_paid.get() or 0)
                online = float(self.online_paid.get() or 0)

            addr = self.customer_address.get().strip()
            if addr:
                self.cursor.execute(
                    "UPDATE customers SET address=? "
                    "WHERE id=(SELECT customer_id FROM sales WHERE id=?)",
                    (addr, self.sale_id))

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
                bill_date     = self.bill_date.get().strip(),
            )
            messagebox.showinfo("Success", "Bill updated successfully!")
            self.parent.destroy()
            if self.refresh_callback:
                self.refresh_callback()
        except Exception as e:
            self.conn.rollback()
            messagebox.showerror("Error", f"Failed to update bill: {e}")
