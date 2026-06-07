"""
ui/billing_form.py
───────────────────
UI building + form interaction mixin for BillingPage.
Builds all widgets and handles customer/doctor/medicine form events.
No DB saves, no bill generation.
"""
import re
import tkinter as tk
from datetime import datetime, date
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.alert_colors import get_alert_color
from core.font_config import *
from core.layout_config import BILLING_ROWS, is_strip_count_type, parse_tablets_per_stripe
from core.column_config import get_visible_columns
from core.scroll_manager import make_scrollable, open_dialog
from core.customer_service import (
    get_customer_names, get_customer_by_name, get_all_doctor_names
)
from widgets.searchable_combo import SearchableCombo
from widgets.two_step_medicine_combo import TwoStepMedicineCombo


class BillingFormMixin:

    # ── UI building ───────────────────────────────────────────────────────

    def _build_interface(self):
        main_frame = make_scrollable(self.parent)
        self._inner_frame = main_frame
        self._page_canvas = getattr(main_frame, '_canvas', None)
        main_frame.configure(padding=(15, 15))

        # ── Customer / Doctor ─────────────────────────────────────────────
        top = ttk.Frame(main_frame)
        top.pack(fill=tk.X, pady=(0, 15))

        cf = ttk.LabelFrame(top, text="Customer Information")
        cf.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Label(cf, text="Customer Name:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.customer_name = SearchableCombo(cf, values=get_customer_names(self.conn), width=20)
        self.customer_name.grid(row=0, column=1, padx=5, pady=5)
        self.customer_name.bind('<<ComboboxSelected>>', self.on_customer_select)
        self.customer_name.bind('<KeyRelease>', self.check_name_due)
        self.customer_name.entry.bind(
            '<FocusIn>',
            lambda e: self.customer_name.configure(values=get_customer_names(self.conn)),
            add='+')
        self.customer_name.next_focus_widget = lambda: self.customer_phone.focus()

        ttk.Label(cf, text="Phone:").grid(row=0, column=2, sticky=tk.W, padx=5, pady=5)
        self.customer_phone = ttk.Entry(cf, width=15)
        self.customer_phone.grid(row=0, column=3, padx=5, pady=5)
        self.customer_phone.bind('<FocusOut>', self.verify_customer_due)
        self.customer_phone.bind('<Return>', lambda e: self.customer_address.focus())

        ttk.Label(cf, text="Address:").grid(row=0, column=4, sticky=tk.W, padx=5, pady=5)
        self.customer_address = ttk.Entry(cf, width=25)
        self.customer_address.grid(row=0, column=5, padx=5, pady=5)
        self.customer_address.bind('<Return>', lambda e: self.doctor_name.focus())

        ttk.Label(cf, text="Previous Due:").grid(row=0, column=6, sticky=tk.W, padx=5, pady=5)
        self.previous_due_var = tk.StringVar(value="0.00")
        ttk.Label(cf, textvariable=self.previous_due_var,
                  foreground=get_alert_color('danger')).grid(row=0, column=7, padx=5, pady=5)

        ttk.Label(cf, text="Doctor Name:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.doctor_name = SearchableCombo(cf, width=18)
        self.doctor_name.grid(row=1, column=1, padx=5, pady=5)
        self.doctor_name.bind('<<ComboboxSelected>>', self.on_doctor_select)
        self.doctor_name.bind('<KeyRelease>', lambda e: None)  # SearchableCombo filters internally
        self.doctor_name.entry.bind('<Return>', lambda e: self._doctor_enter())
        self.doctor_name.next_focus_widget = lambda: self.on_doctor_select()
        self.reload_doctors()

        ttk.Label(cf, text="Doctor Phone:").grid(row=1, column=2, sticky=tk.W, padx=5, pady=5)
        self.doctor_phone = ttk.Entry(cf, width=15)
        self.doctor_phone.grid(row=1, column=3, padx=5, pady=5)
        self.doctor_phone.bind('<Return>', lambda e: self._focus_bill_date())

        ttk.Label(cf, text="Bill Date:").grid(row=1, column=4, sticky=tk.W, padx=5, pady=5)
        self._bill_date_default = date.today().strftime('%Y-%m-%d')
        self.bill_date_var = tk.StringVar(value=self._bill_date_default)
        self.bill_date = self._make_bill_date_widget(cf)
        self.bill_date.grid(row=1, column=5, padx=5, pady=5, sticky=tk.W)

        # ── Medicine selection ────────────────────────────────────────────
        mf = ttk.LabelFrame(main_frame, text="Medicine Selection")
        mf.pack(fill=tk.X, pady=(0, 15))

        ttk.Label(mf, text="Medicine:").grid(row=0, column=0, sticky=tk.W, padx=8, pady=8)
        self.medicine_combo = TwoStepMedicineCombo(mf, self.conn, width=60)
        self.medicine_combo.grid(row=0, column=1, padx=8, pady=8)
        self.medicine_combo.bind('<<ComboboxSelected>>', self.on_medicine_select)
        self.medicine_combo.next_focus_widget = self.handle_medicine_focus

        ttk.Label(mf, text="Quantity:").grid(row=0, column=2, sticky=tk.W, padx=5, pady=5)
        self.quantity = ttk.Entry(mf, width=10)
        self.quantity.grid(row=0, column=3, padx=5, pady=5)
        self.quantity.bind('<Return>', lambda e: self.medicine_discount.focus())

        ttk.Label(mf, text="Disc ₹:").grid(row=0, column=4, sticky=tk.W, padx=5, pady=5)
        self.medicine_discount = ttk.Entry(mf, width=8)
        self.medicine_discount.grid(row=0, column=5, padx=5, pady=5)
        self.medicine_discount.insert(0, "0")
        self.medicine_discount.bind('<Return>', lambda e: self.add_medicine_and_focus())

        ttk.Label(mf, text="(Tablets for d/strip types, units for ml/g types)",
                  font=(FONT_FAMILY, 8)).grid(row=1, column=2, columnspan=2, sticky=tk.W, padx=5)

        try:
            self.add_medicine_btn = ttk.Button(
                mf, text="Add Medicine", command=self.add_medicine, bootstyle="success")
        except Exception:
            self.add_medicine_btn = ttk.Button(mf, text="Add Medicine", command=self.add_medicine)
        self.add_medicine_btn.grid(row=0, column=6, padx=5, pady=5)

        # ── Medicine tree ─────────────────────────────────────────────────
        sf = ttk.LabelFrame(main_frame, text="Selected Medicines")
        sf.pack(fill=tk.BOTH, expand=True, pady=(0, 15))

        self._all_columns = ('Medicine','Batch','Expiry','Qty','Type','MRP','Disc ₹','Amount','Schedule','Location')
        self.medicine_tree = ttk.Treeview(sf, columns=self._all_columns,
                                          show='headings', height=BILLING_ROWS,
                                          style='Large.Treeview')
        col_widths = {'Medicine':140,'Batch':70,'Expiry':70,'Qty':50,'Type':50,
                      'MRP':60,'Disc ₹':55,'Amount':70,'Schedule':60,'Location':80}
        for col in self._all_columns:
            self.medicine_tree.heading(col, text=col)
            self.medicine_tree.column(col, width=col_widths.get(col, 80))
        self._apply_location_column_visibility()

        sb = ttk.Scrollbar(sf, orient=tk.VERTICAL, command=self.medicine_tree.yview)
        self.medicine_tree.configure(yscrollcommand=sb.set)
        self.medicine_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        # Tree keys: Return/Delete/Escape/Double-1 wired in billing_nav.wire_tree_list

        # ── Summary ───────────────────────────────────────────────────────
        bottom = ttk.Frame(main_frame)
        bottom.pack(fill=tk.X, pady=(0, 10))
        sumf = ttk.LabelFrame(bottom, text="Billing Summary")
        sumf.pack(fill=tk.X)

        # Row 0 — Overall Discount (% and ₹ linked) + Rounding + Cash + Online + Total
        ttk.Label(sumf, text="Overall Disc %:").grid(row=0, column=0, sticky=tk.W, padx=4, pady=2)
        self.discount_pct = ttk.Entry(sumf, width=6)
        self.discount_pct.grid(row=0, column=1, padx=4, pady=2)
        self.discount_pct.insert(0, "0")
        self.discount_pct.bind('<KeyRelease>', self._on_disc_pct_change)
        self.discount_pct.bind('<Return>', lambda e: self.discount.focus())
        self.discount_pct.bind('<FocusIn>', lambda e: e.widget.select_range(0, tk.END))

        ttk.Label(sumf, text="Overall Disc ₹:").grid(row=0, column=2, sticky=tk.W, padx=4, pady=2)
        self.discount = ttk.Entry(sumf, width=7)
        self.discount.grid(row=0, column=3, padx=4, pady=2)
        self.discount.insert(0, "0")
        self.discount.bind('<KeyRelease>', self._on_disc_rs_change)
        self.discount.bind('<Return>', lambda e: self.rounding.focus())
        self.discount.bind('<FocusIn>', lambda e: e.widget.select_range(0, tk.END))

        self._disc_editing = None  # 'pct' or 'rs' — prevents circular updates

        ttk.Label(sumf, text="Rounding:").grid(row=0, column=4, sticky=tk.W, padx=4, pady=2)
        self.rounding = ttk.Entry(sumf, width=7)
        self.rounding.grid(row=0, column=5, padx=4, pady=2)
        self.rounding.insert(0, "0.00")
        self.rounding.bind('<KeyRelease>', self.calculate_total)
        self.rounding.bind('<Return>', lambda e: self.cash_paid.focus())
        self.rounding.bind('<FocusIn>', lambda e: e.widget.select_range(0, tk.END))

        ttk.Label(sumf, text="Cash:").grid(row=0, column=6, sticky=tk.W, padx=4, pady=2)
        self.cash_paid = ttk.Entry(sumf, width=9)
        self.cash_paid.grid(row=0, column=7, padx=4, pady=2)
        self.cash_paid.bind('<KeyRelease>', self.calculate_total)
        self.cash_paid.bind('<Return>', lambda e: self.online_paid.focus())
        self.cash_paid.bind('<FocusIn>', lambda e: e.widget.select_range(0, tk.END))

        ttk.Label(sumf, text="Online:").grid(row=0, column=8, sticky=tk.W, padx=4, pady=2)
        self.online_paid = ttk.Entry(sumf, width=9)
        self.online_paid.grid(row=0, column=9, padx=4, pady=2)
        self.online_paid.bind('<KeyRelease>', self.calculate_total)
        self.online_paid.bind('<Return>', lambda e: self.save_sales())
        self.online_paid.bind('<FocusIn>', lambda e: e.widget.select_range(0, tk.END))

        ttk.Label(sumf, text="Total Amount:").grid(row=0, column=10, sticky=tk.W, padx=8, pady=2)
        self.total_amount_var = tk.StringVar(value="0.00")
        ttk.Label(sumf, textvariable=self.total_amount_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(
            row=0, column=11, sticky=tk.W, padx=4, pady=2)

        # Row 1 — calculated totals
        ttk.Label(sumf, text="Subtotal:").grid(row=1, column=0, sticky=tk.W, padx=4, pady=2)
        self.subtotal_var = tk.StringVar(value="0.00")
        ttk.Label(sumf, textvariable=self.subtotal_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS)).grid(
            row=1, column=1, sticky=tk.W, padx=4, pady=2)

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

        ttk.Label(sumf, text="Total Due:").grid(row=1, column=10, sticky=tk.W, padx=8, pady=2)
        self.total_due_var = tk.StringVar(value="0.00")
        ttk.Label(sumf, textvariable=self.total_due_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(
            row=1, column=11, sticky=tk.W, padx=4, pady=2)

        # Row 2 — GST info + buttons
        self.gst_percent_var = tk.StringVar(value="Included in MRP")
        ttk.Label(sumf, text="GST %:").grid(row=2, column=0, sticky=tk.W, padx=4, pady=4)
        ttk.Label(sumf, textvariable=self.gst_percent_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS)).grid(
            row=2, column=1, columnspan=4, sticky=tk.W, padx=4, pady=4)

        try:
            self.clear_btn = ttk.Button(sumf, text="Clear Form",
                                        command=self.clear_form, bootstyle="warning")
            self.clear_btn.grid(row=2, column=8, padx=6, pady=4, sticky=tk.E)
            self.generate_btn = ttk.Button(sumf, text="Save Sales (F5)",
                                           command=self.save_sales, bootstyle="primary")
            self.generate_btn.grid(row=2, column=9, columnspan=3, padx=6, pady=4, sticky=tk.EW)
        except Exception:
            self.clear_btn = ttk.Button(sumf, text="Clear Form", command=self.clear_form)
            self.clear_btn.grid(row=2, column=8, padx=6, pady=4, sticky=tk.E)
            self.generate_btn = ttk.Button(sumf, text="Save Sales (F5)",
                                           command=self.save_sales)
            self.generate_btn.grid(row=2, column=9, columnspan=3, padx=6, pady=4, sticky=tk.EW)

    def _focus_medicine_name(self):
        try:
            self.medicine_combo.focus_step1()
        except Exception:
            pass

    def _make_bill_date_widget(self, parent):
        def _on_date_enter(_event=None):
            self._focus_medicine_name()
            return 'break'

        try:
            from ttkbootstrap.widgets import DateEntry
            w = DateEntry(parent, dateformat='%Y-%m-%d', width=12, bootstyle='primary')
            try:
                w.entry.configure(textvariable=self.bill_date_var)
            except Exception:
                pass
            entry = getattr(w, 'entry', w)
            entry.bind('<Return>', _on_date_enter, add='+')
            entry.bind('<KP_Enter>', _on_date_enter, add='+')
            w.bind('<<DateEntrySelected>>', lambda e: self._focus_medicine_name())
            return w
        except Exception:
            ent = ttk.Entry(parent, textvariable=self.bill_date_var, width=12)
            ent.bind('<Return>', _on_date_enter, add='+')
            return ent

    def _focus_bill_date(self):
        try:
            if hasattr(self.bill_date, 'entry'):
                self.bill_date.entry.focus_set()
            else:
                self.bill_date.focus_set()
        except Exception:
            pass

    def get_bill_date_value(self):
        raw = (self.bill_date_var.get() or '').strip()
        if not raw:
            return date.today()
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        return date.today()

    def _reset_bill_date_today(self):
        today = date.today().strftime('%Y-%m-%d')
        self._bill_date_default = today
        self.bill_date_var.set(today)
        try:
            if hasattr(self.bill_date, 'set_date'):
                self.bill_date.set_date(date.today())
        except Exception:
            pass

    # ── Two-way discount binding ────────────────────────────────────────────────────

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
        self.calculate_total()

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
        self.calculate_total()

    # ── Doctor helpers ────────────────────────────────────────────────────

    def reload_doctors(self):
        self.all_doctors = get_all_doctor_names(self.conn)
        self.doctor_name.configure(values=self.all_doctors)

    def _doctor_enter(self):
        typed  = self.doctor_name.get().strip().upper()
        values = self.all_doctors
        match  = next((v for v in values if v.upper().startswith(typed)), None)
        if match:
            self.doctor_name.set(match)
            self.on_doctor_select()
        else:
            self.doctor_phone.focus()

    def on_doctor_select(self, event=None):
        name = self.doctor_name.get().strip().upper()
        if not name:
            return
        self.cursor.execute(
            "SELECT phone FROM doctors WHERE UPPER(name)=? LIMIT 1", (name,))
        row = self.cursor.fetchone()
        if row and row[0]:
            self.doctor_phone.delete(0, tk.END)
            self.doctor_phone.insert(0, row[0])
        self.doctor_phone.focus()

    def on_doctor_phone_enter(self, event):
        sel = self.medicine_combo.get_selected_medicine()
        if sel and sel.get('schedule'):
            self.quantity.focus()
        else:
            self.medicine_combo.focus()

    def handle_medicine_focus(self):
        sel = self.medicine_combo.get_selected_medicine()
        if sel and sel.get('schedule') and not self.doctor_name.get():
            return
        self.quantity.focus()

    # ── Customer helpers ──────────────────────────────────────────────────

    def on_customer_select(self, event=None):
        name = self.customer_name.get().strip()
        if not name:
            return
        customer = get_customer_by_name(self.conn, name)
        if not customer:
            return
        self._customer_id = customer['id']
        self.customer_phone.delete(0, tk.END)
        self.customer_phone.insert(0, customer['phone'] or '')
        self.customer_address.delete(0, tk.END)
        self.customer_address.insert(0, customer['address'] or '')
        self._set_previous_due(float(customer['total_due']), float(customer['total_credit']))
        self.calculate_total()

    def check_name_due(self, event):
        self.previous_due = 0
        self.previous_credit = 0
        self.previous_due_var.set("0.00")
        self._customer_id = None
        name = self.customer_name.get().strip()
        if name:
            customer = get_customer_by_name(self.conn, name)
            if customer:
                self._customer_id = customer['id']
                self._set_previous_due(float(customer['total_due']),
                                       float(customer['total_credit']))
        self.calculate_total()

    def verify_customer_due(self, event):
        name = self.customer_name.get().strip()
        if not name:
            self.calculate_total(); return
        customer = get_customer_by_name(self.conn, name)
        if customer:
            self._customer_id = customer['id']
            self._set_previous_due(float(customer['total_due']),
                                   float(customer['total_credit']))
        else:
            self.previous_due    = 0
            self.previous_credit = 0
            self.previous_due_var.set("0.00")
        self.calculate_total()

    def _set_previous_due(self, due, credit):
        self.previous_due    = due
        self.previous_credit = credit
        if due > 0:
            self.previous_due_var.set(f"{due:.2f}")
        elif credit > 0:
            self.previous_due_var.set(f"Credit: {credit:.2f}")
        else:
            self.previous_due_var.set("0.00")

    # ── Medicine helpers ──────────────────────────────────────────────────

    def on_medicine_select(self, event):
        sel = self.medicine_combo.get_selected_medicine()
        if sel:
            if sel.get('schedule') and not self.doctor_name.get():
                showwarning("Doctor Required",
                        "This medicine requires a doctor name.", parent=self.parent)
                self.doctor_name.focus()
                return
            self.quantity.focus()

    def add_medicine(self):
        sel      = self.medicine_combo.get_selected_medicine()
        qty_text = self.quantity.get()
        if not sel or not qty_text:
            showwarning("Missing Information",
                        "Please select medicine and enter quantity.", parent=self.parent)
            if not qty_text:
                self.quantity.focus()
            return
        try:
            qty = int(qty_text)
        except ValueError:
            showerror("Invalid Quantity", "Please enter a valid quantity.", parent=self.parent)
            return
        if sel.get('schedule') and not self.doctor_name.get():
            showwarning("Doctor Required",
                        "Please select a doctor for scheduled medicines.", parent=self.parent)
            return
        try:
            exp = datetime.strptime(sel['expiry'], '%Y-%m-%d')
            if exp <= datetime.now():
                showerror("Expired Medicine",
                          f"{sel['name']} expired on {sel['expiry']}.", parent=self.parent)
                return
        except Exception:
            pass
        if sel['stock'] == 0:
            showerror("Out of Stock", f"{sel['name']} is out of stock.", parent=self.parent)
            return
        if qty > sel['stock']:
            showerror("Insufficient Stock",
                      f"Only {int(sel['stock'])} units available.", parent=self.parent)
            return

        self.cursor.execute(
            "SELECT type, unit, gst_percent, location FROM medicines WHERE id=?",
            (sel['id'],))
        info = self.cursor.fetchone()
        med_type    = info[0] if info else ''
        unit_value  = info[1] if info else '1'
        gst_percent = info[2] if info else 0
        location    = self._fmt_location(info[3] if info else '')

        if is_strip_count_type(med_type or ''):
            try:
                ups  = parse_tablets_per_stripe(unit_value)
                rate = sel['mrp'] / ups
            except (ValueError, ZeroDivisionError):
                rate = sel['mrp']
        else:
            rate = sel['mrp']

        try:
            med_disc_rs = float(self.medicine_discount.get() or 0)
        except ValueError:
            med_disc_rs = 0

        base = round(qty * rate, 2)
        med_disc_rs = min(med_disc_rs, base)  # cap at base
        amount = round(base - med_disc_rs, 2)

        self.selected_medicines.append({
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
            'gst_percent':      gst_percent,
            'location':         location,
        })
        self.update_medicine_tree()
        self.calculate_total()
        self.clear_medicine_fields()
        self.medicine_combo.focus()

    def add_medicine_and_focus(self):
        self.add_medicine()
        try:
            self.medicine_combo.focus_step1()
        except Exception:
            pass

    def clear_medicine_fields(self):
        self.medicine_combo.set('')
        self.quantity.delete(0, tk.END)
        self.medicine_discount.delete(0, tk.END)
        self.medicine_discount.insert(0, "0")

    def update_medicine_tree(self):
        for item in self.medicine_tree.get_children():
            self.medicine_tree.delete(item)
        for med in self.selected_medicines:
            self.medicine_tree.insert('', tk.END, values=(
                med['name'], med['batch'], med['expiry'],
                med['qty'], med.get('display_type', 'N/A'), f"{med['rate']:.2f}",
                f"{med.get('medicine_discount', 0):.2f}", f"{med['amount']:.2f}",
                med['schedule'], med.get('location', ''),
            ))

    def edit_quantity(self, event=None):
        sel = self.medicine_tree.selection()
        if not sel:
            return 'break'
        values = self.medicine_tree.item(sel[0])['values']

        dlg = open_dialog(self.parent, "Edit Medicine", width=360, height=240, resizable=False)
        body = dlg.content
        ttk.Label(body, text=f"Medicine: {values[0]}",
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).pack(pady=(12, 6))

        ff = ttk.Frame(body)
        ff.pack(pady=5, padx=12, fill=tk.X)
        ff.grid_columnconfigure(1, weight=1)

        ttk.Label(ff, text="Quantity (0 to remove):").grid(row=0, column=0, sticky=tk.W, padx=8, pady=6)
        qty_e = ttk.Entry(ff, width=14)
        qty_e.grid(row=0, column=1, padx=8, pady=6, sticky=tk.EW)
        qty_e.insert(0, str(values[3]))

        ttk.Label(ff, text="Discount ₹:").grid(row=1, column=0, sticky=tk.W, padx=8, pady=6)
        disc_e = ttk.Entry(ff, width=14)
        disc_e.grid(row=1, column=1, padx=8, pady=6, sticky=tk.EW)
        disc_e.insert(0, str(values[6]).replace('₹', '').strip())

        def update():
            try:
                new_qty  = int(qty_e.get())
                new_disc = float(disc_e.get() or 0)
                try:
                    idx = self.medicine_tree.index(sel[0])
                except tk.TclError:
                    dlg.destroy(); return
                if new_qty == 0:
                    del self.selected_medicines[idx]
                else:
                    med = self.selected_medicines[idx]
                    med['qty']               = new_qty
                    med['medicine_discount'] = new_disc
                    base = round(new_qty * med['rate'], 2)
                    med['original_amount']   = base
                    med['amount']            = round(base - min(new_disc, base), 2)
                self.update_medicine_tree()
                self.calculate_total()
                dlg.destroy()
                self.medicine_tree.focus()
            except ValueError:
                showerror("Invalid Input", "Please enter valid quantity and discount.",
                          parent=self.parent)

        qty_e.bind('<Down>',   lambda e: (disc_e.focus(), disc_e.select_range(0, tk.END)))
        qty_e.bind('<Return>', lambda e: (disc_e.focus(), disc_e.select_range(0, tk.END)))
        disc_e.bind('<Up>',    lambda e: (qty_e.focus(),  qty_e.select_range(0, tk.END)))
        disc_e.bind('<Return>', lambda e: update())
        dlg.bind('<Escape>', lambda e: dlg.destroy())

        ub = ttk.Button(dlg.footer, text="Update", command=update)
        ub.pack(side=tk.LEFT, padx=6)
        cb = ttk.Button(dlg.footer, text="Cancel", command=dlg.destroy)
        cb.pack(side=tk.LEFT, padx=6)
        ub.bind('<Return>', lambda e: update())
        cb.bind('<Return>', lambda e: dlg.destroy())

        def _focus_qty():
            try:
                qty_e.focus_set()
                qty_e.select_range(0, tk.END)
            except tk.TclError:
                pass

        dlg.after_idle(_focus_qty)
        dlg.after(150, _focus_qty)
        return 'break'

    def _delete_selected_medicine(self):
        sel = self.medicine_tree.selection()
        if not sel:
            return
        try:
            idx = self.medicine_tree.index(sel[0])
            del self.selected_medicines[idx]
            self.update_medicine_tree()
            self.calculate_total()
        except (tk.TclError, IndexError):
            pass

    # ── Location / column helpers ─────────────────────────────────────────

    def _show_location_enabled(self):
        try:
            self.cursor.execute("SELECT show_location FROM shelf_settings LIMIT 1")
            r = self.cursor.fetchone()
            return bool(r[0]) if r else False
        except Exception:
            return False

    def _apply_location_column_visibility(self):
        visible = get_visible_columns('billing', self._all_columns)
        if not self._show_location_enabled():
            visible = [c for c in visible if c != 'Location']
        if not visible:
            visible = list(self._all_columns)
        self.medicine_tree.configure(displaycolumns=visible)

    def _fmt_location(self, raw):
        if not raw or not raw.strip():
            return ''
        s = raw.strip()
        if re.match(r'^r\d', s):
            return s
        nums = re.findall(r'\d+', s)
        if 'box' in s and len(nums) >= 3:
            return f"r{nums[0]}s{nums[1]}b{nums[2]}"
        if len(nums) >= 2:
            return f"r{nums[0]}s{nums[1]}"
        return s
