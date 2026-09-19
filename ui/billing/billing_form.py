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
from core.layout_config import BILLING_ROWS, is_strip_count_type, parse_tablets_per_stripe, resolve_tablets_per_stripe
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
    validate_bill_discounts,
)
from core.scroll_manager import make_scrollable, open_dialog
from core.customer_service import (
    get_customer_names, get_customer_by_name, get_all_doctor_names,
    COUNTER_SALE, is_counter_sale_name,
)
from core.village_service import village_names_for_ui, get_default_village
from widgets.searchable_combo import SearchableCombo
from widgets.two_step_medicine_combo import TwoStepMedicineCombo


class BillingFormMixin:

    # ── UI building ───────────────────────────────────────────────────────

    def _build_interface(self):
        if hasattr(self, '_build_tab_bar'):
            self._build_tab_bar(self.parent)
        main_frame = make_scrollable(self.parent)
        self._inner_frame = main_frame
        self._page_canvas = getattr(main_frame, '_canvas', None)
        main_frame.configure(padding=(15, 15))

        # ── Customer / Doctor ─────────────────────────────────────────────
        top = ttk.Frame(main_frame)
        top.pack(fill=tk.X, pady=(0, 15))

        cf = ttk.LabelFrame(top, text="Customer Information")
        cf.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._customer_info_frame = cf

        self._lbl_payment = ttk.Label(cf, text="Payment:")
        self.payment_mode = SearchableCombo(cf, values=('Cash', 'Due'), width=8, listbox_height=2)
        self.payment_mode.set('')
        self.payment_mode.bind('<<ComboboxSelected>>', self._on_payment_mode_change)
        self.payment_mode.bind_apply_on_select(self._on_payment_mode_change)
        self.payment_mode.entry.bind('<KeyPress>', self._payment_mode_key, add='+')

        self._lbl_customer = ttk.Label(cf, text="Customer Name:")
        self.customer_name = SearchableCombo(cf, values=[], width=20)
        self.customer_name.bind('<<ComboboxSelected>>', self.on_customer_select)
        self.customer_name.bind('<KeyRelease>', self.check_name_due)
        self.customer_name.entry.bind(
            '<FocusIn>',
            lambda e: self._refresh_customer_names(show_list=True),
            add='+')
        self.customer_name.next_focus_widget = lambda: self._customer_name_enter()

        self._lbl_phone = ttk.Label(cf, text="Phone:")
        self.customer_phone = ttk.Entry(cf, width=15)
        self.customer_phone.bind('<FocusOut>', self.verify_customer_due)
        self.customer_phone.bind('<Return>', lambda e: self._focus_customer_address())

        self._lbl_address = ttk.Label(cf, text="Address (Village):")
        self.customer_address = SearchableCombo(cf, values=[], width=22)
        self.customer_address.entry.bind(
            '<FocusIn>',
            lambda e: self.reload_villages(show_list=True),
            add='+')
        self.customer_address.next_focus_widget = lambda: self._address_enter()
        self._apply_default_village()

        self._lbl_previous_due = ttk.Label(cf, text="Previous Due:")
        self.previous_due_var = tk.StringVar(value="0.00")
        self._previous_due_display = ttk.Label(
            cf, textvariable=self.previous_due_var,
            foreground=get_alert_color('danger'),
        )
        try:
            self.quick_add_btn = ttk.Button(
                cf,
                text="Add No Stock (Ctrl+Shift+M)",
                command=self.open_quick_sale_medicine_dialog,
                bootstyle='warning-outline',
            )
        except Exception:
            self.quick_add_btn = ttk.Button(
                cf,
                text="Add No Stock (Ctrl+Shift+M)",
                command=self.open_quick_sale_medicine_dialog,
            )

        self._lbl_doctor = ttk.Label(cf, text="Doctor Name:")
        self.doctor_name = SearchableCombo(cf, width=18)
        self.doctor_name.bind('<<ComboboxSelected>>', self.on_doctor_select)
        self.doctor_name.entry.bind(
            '<FocusIn>',
            lambda e: self._refresh_doctors(show_list=True),
            add='+')
        self.doctor_name.next_focus_widget = lambda: self.on_doctor_select()
        self.all_doctors = []

        self._lbl_doctor_phone = ttk.Label(cf, text="Doctor Phone:")
        self.doctor_phone = ttk.Entry(cf, width=15)
        self.doctor_phone.bind('<Return>', lambda e: self._focus_bill_date())

        self._lbl_bill_date = ttk.Label(cf, text="Bill Date:")
        self._bill_date_default = date.today().strftime('%Y-%m-%d')
        self.bill_date_var = tk.StringVar(value=self._bill_date_default)
        self.bill_date = self._make_bill_date_widget(cf)
        try:
            self.bill_date_var.trace_add('write', lambda *_: self._on_bill_date_changed())
        except Exception:
            pass

        self._apply_sales_field_layout()

        # ── Medicine selection ────────────────────────────────────────────
        mf = ttk.LabelFrame(main_frame, text="Medicine Selection")
        mf.pack(fill=tk.X, pady=(0, 15))

        ttk.Label(mf, text="Medicine:").grid(row=0, column=0, sticky=tk.W, padx=8, pady=8)
        self.medicine_combo = TwoStepMedicineCombo(mf, self.conn, width=60)
        self.medicine_combo.bill_date_getter = self.get_bill_date_value
        self.medicine_combo.grid(row=0, column=1, padx=8, pady=8)
        self.medicine_combo.bind('<<ComboboxSelected>>', self.on_medicine_select)
        self.medicine_combo.next_focus_widget = self.handle_medicine_focus
        self.medicine_combo.empty_enter_callback = self._focus_overall_discount

        ttk.Label(mf, text="Quantity:").grid(row=0, column=2, sticky=tk.W, padx=5, pady=5)
        self.quantity = ttk.Entry(mf, width=10)
        self.quantity.grid(row=0, column=3, padx=5, pady=5)
        self.quantity.bind('<Return>', self._on_quantity_return)

        self._disc_lbl = ttk.Label(mf, text="Disc ₹:")
        self._disc_lbl.grid(row=0, column=4, sticky=tk.W, padx=5, pady=5)
        self.medicine_discount = ttk.Entry(mf, width=8)
        self.medicine_discount.grid(row=0, column=5, padx=5, pady=5)
        self.medicine_discount.insert(0, "0")
        self.medicine_discount.bind('<Return>', lambda e: self.add_medicine_and_focus())
        self._refresh_item_discount_label()
        self._refresh_item_discount_visibility()

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

        self._all_columns = (
            'Medicine', 'Batch', 'Expiry', 'Qty', 'Type', 'MRP',
            'Disc ₹', 'Margin ₹', 'Amount', 'Schedule', 'Location',
        )
        self.medicine_tree = ttk.Treeview(sf, columns=self._all_columns,
                                          show='headings', height=BILLING_ROWS,
                                          style='Large.Treeview')
        col_widths = {
            'Medicine': 140, 'Batch': 70, 'Expiry': 70, 'Qty': 50, 'Type': 50,
            'MRP': 60, 'Disc ₹': 55, 'Margin ₹': 65, 'Amount': 70,
            'Schedule': 60, 'Location': 80,
        }
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
        self.rounding.bind('<KeyRelease>', self._on_rounding_change)
        self.rounding.bind('<Return>', self._rounding_enter)
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
        self.online_paid.bind('<Return>', self._online_enter)
        self.online_paid.bind('<FocusIn>', lambda e: e.widget.select_range(0, tk.END))

        self.total_amount_var = tk.StringVar(value="0.00")
        self.total_due_var = tk.StringVar(value="0.00")

        # Row 1 — calculated totals
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

        # Row 2 — GST info
        self.gst_percent_var = tk.StringVar(value="Included in MRP")
        ttk.Label(sumf, text="GST %:").grid(row=2, column=0, sticky=tk.W, padx=4, pady=4)
        ttk.Label(sumf, textvariable=self.gst_percent_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS)).grid(
            row=2, column=1, columnspan=6, sticky=tk.W, padx=4, pady=4)

        # Row 0–1 right — three columns: totals | save/clear | print buttons
        action_outer = ttk.Frame(sumf)
        action_outer.grid(row=0, column=10, rowspan=2, sticky=tk.NE, padx=(12, 4), pady=4)
        action_outer.columnconfigure(0, weight=0)
        action_outer.columnconfigure(1, weight=0)
        action_outer.columnconfigure(2, weight=0)

        totals_col = ttk.Frame(action_outer)
        totals_col.grid(row=0, column=0, rowspan=2, sticky=tk.NW, padx=(0, 14))
        ttk.Label(totals_col, text="Total Amount:",
                  font=(FONT_FAMILY, FONT_SIZE_LABELS)).grid(row=0, column=0, sticky=tk.W, pady=(0, 2))
        ttk.Label(totals_col, textvariable=self.total_amount_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(row=1, column=0, sticky=tk.W, pady=(0, 8))
        ttk.Label(totals_col, text="Total Due:",
                  font=(FONT_FAMILY, FONT_SIZE_LABELS)).grid(row=2, column=0, sticky=tk.W, pady=(0, 2))
        ttk.Label(totals_col, textvariable=self.total_due_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(row=3, column=0, sticky=tk.W)

        save_col = ttk.Frame(action_outer)
        save_col.grid(row=0, column=1, rowspan=2, sticky=tk.N, padx=(0, 8))

        print_col = ttk.Frame(action_outer)
        print_col.grid(row=0, column=2, rowspan=2, sticky=tk.N)

        from core.bill_config import load_bill_print_settings, get_print_slot_key
        _ps = load_bill_print_settings()
        _k1 = get_print_slot_key(_ps, 1)
        _k2 = get_print_slot_key(_ps, 2)
        _s1 = _ps.get('print_slot_1') or {}
        _s2 = _ps.get('print_slot_2') or {}
        _l1 = _s1.get('label') or 'Print Sales 1'
        _l2 = _s2.get('label') or 'Print Sales 2'
        _p1 = (_s1.get('paper_size') or 'A5').upper()
        _p2 = (_s2.get('paper_size') or 'A4').upper()
        c1 = int(_s1.get('copies') or 2)
        c2 = int(_s2.get('copies') or 1)
        from core.bill_config import BILL_SIZE_DOT_MATRIX, get_bill_size_mode
        dm1 = get_bill_size_mode(_s1) == BILL_SIZE_DOT_MATRIX
        dm2 = get_bill_size_mode(_s2) == BILL_SIZE_DOT_MATRIX
        suffix1 = ' · DM' if dm1 else ''
        suffix2 = ' · DM' if dm2 else ''

        try:
            self.generate_btn = ttk.Button(
                save_col, text="Save Sales (F5)", command=self.save_sales, bootstyle="primary", width=20)
            self.clear_btn = ttk.Button(
                save_col, text="Clear Form", command=self.clear_form, bootstyle="warning", width=20)
            self.print_sales_1_btn = ttk.Button(
                print_col, text=f"{_l1} ({_k1}) — {_p1}×{c1}{suffix1}",
                command=lambda: self._print_sales_slot(1), bootstyle="info-outline", width=20)
            self.print_sales_2_btn = ttk.Button(
                print_col, text=f"{_l2} ({_k2}) — {_p2}×{c2}{suffix2}",
                command=lambda: self._print_sales_slot(2), bootstyle="info-outline", width=20)
        except Exception:
            self.generate_btn = ttk.Button(save_col, text="Save Sales (F5)", command=self.save_sales, width=20)
            self.clear_btn = ttk.Button(save_col, text="Clear Form", command=self.clear_form, width=20)
            self.print_sales_1_btn = ttk.Button(
                print_col, text=f"{_l1} ({_k1}) — {_p1}×{c1}{suffix1}",
                command=lambda: self._print_sales_slot(1), width=20)
            self.print_sales_2_btn = ttk.Button(
                print_col, text=f"{_l2} ({_k2}) — {_p2}×{c2}{suffix2}",
                command=lambda: self._print_sales_slot(2), width=20)

        self.generate_btn.grid(row=0, column=0, sticky=tk.EW, pady=(0, 4))
        self.clear_btn.grid(row=1, column=0, sticky=tk.EW)
        self.print_sales_1_btn.grid(row=0, column=0, sticky=tk.EW, pady=(0, 4))
        self.print_sales_2_btn.grid(row=1, column=0, sticky=tk.EW)
        top = self.parent.winfo_toplevel()
        if not getattr(top, '_startup_prewarm', False):
            self.parent.after(0, self._schedule_billing_reference_data)

    def _schedule_billing_reference_data(self):
        from core.background_workers import run_in_thread

        def _load():
            try:
                from core.sync_prefs import is_online_mode
                from core.online_catalog import prefetch_hot

                if is_online_mode():
                    prefetch_hot()
            except Exception:
                pass
            return (
                get_customer_names(self.conn),
                get_all_doctor_names(self.conn),
                village_names_for_ui(self.conn),
            )

        run_in_thread(
            _load,
            name='BillingReferenceData',
            root=self.parent,
            on_success=self._apply_billing_reference_data,
        )
        try:
            from core.medicine_pack_repair import schedule_pack_repair
            schedule_pack_repair(self.parent)
        except Exception:
            pass

    def _apply_billing_reference_data(self, payload):
        customers, doctors, villages = payload
        try:
            import time

            self._customer_names_loaded_at = time.time()
            self.customer_name.configure(values=customers or [])
            self.all_doctors = list(doctors or [])
            self.doctor_name.configure(values=self.all_doctors)
            self.customer_address.configure(values=villages or [])
        except Exception:
            pass

    def _sales_payment_mode_enabled(self) -> bool:
        return bool(getattr(self, '_payment_mode_enabled', True))

    def _sales_payment_after_bill_date(self) -> bool:
        from core.sales_form_prefs import POSITION_AFTER_BILL_DATE
        return getattr(self, '_payment_mode_position', '') == POSITION_AFTER_BILL_DATE

    def _refresh_item_discount_label(self):
        try:
            from core.billing_layout_prefs import item_discount_is_percent

            text = "Disc %:" if item_discount_is_percent() else "Disc ₹:"
            if hasattr(self, "_disc_lbl"):
                self._disc_lbl.configure(text=text)
        except Exception:
            pass

    def _item_discount_visible(self) -> bool:
        try:
            from core.billing_layout_prefs import show_item_discount_field
            return bool(show_item_discount_field())
        except Exception:
            return True

    def _refresh_item_discount_visibility(self):
        show = self._item_discount_visible()
        try:
            if show:
                self._disc_lbl.grid()
                self.medicine_discount.grid()
            else:
                self._disc_lbl.grid_remove()
                self.medicine_discount.grid_remove()
                self.medicine_discount.delete(0, tk.END)
                self.medicine_discount.insert(0, "0")
        except Exception:
            pass

    def _on_quantity_return(self, event=None):
        if self._item_discount_visible():
            try:
                self.medicine_discount.focus_set()
            except Exception:
                self.add_medicine_and_focus()
        else:
            self.add_medicine_and_focus()
        return 'break'

    def _apply_sales_field_layout(self):
        from core.sales_form_prefs import (
            load_payment_mode_enabled,
            load_payment_mode_position,
            POSITION_FIRST,
            POSITION_AFTER_BILL_DATE,
        )

        self._payment_mode_enabled = load_payment_mode_enabled()
        self._payment_mode_position = load_payment_mode_position()
        self._refresh_item_discount_label()
        self._refresh_item_discount_visibility()
        pad = {'padx': 5, 'pady': 5}
        sticky_w = {'sticky': tk.W}

        for w in (
            self._lbl_payment, self.payment_mode,
            self._lbl_customer, self.customer_name,
            self._lbl_phone, self.customer_phone,
            self._lbl_address, self.customer_address,
            self._lbl_previous_due, self._previous_due_display,
            self.quick_add_btn,
            self._lbl_doctor, self.doctor_name,
            self._lbl_doctor_phone, self.doctor_phone,
            self._lbl_bill_date, self.bill_date,
        ):
            try:
                w.grid_remove()
            except Exception:
                pass

        show_payment = self._sales_payment_mode_enabled()
        payment_first = show_payment and self._payment_mode_position == POSITION_FIRST

        if payment_first:
            self._lbl_payment.grid(row=0, column=0, **sticky_w, **pad)
            self.payment_mode.grid(row=0, column=1, **pad, sticky=tk.W)
            self._lbl_customer.grid(row=0, column=2, **sticky_w, **pad)
            self.customer_name.grid(row=0, column=3, **pad)
            self._lbl_phone.grid(row=0, column=4, **sticky_w, **pad)
            self.customer_phone.grid(row=0, column=5, **pad)
            self._lbl_address.grid(row=0, column=6, **sticky_w, **pad)
            self.customer_address.grid(row=0, column=7, **pad)
            self._lbl_doctor.grid(row=1, column=0, **sticky_w, **pad)
            self.doctor_name.grid(row=1, column=1, **pad)
            self._lbl_doctor_phone.grid(row=1, column=2, **sticky_w, **pad)
            self.doctor_phone.grid(row=1, column=3, **pad)
            self._lbl_bill_date.grid(row=1, column=4, **sticky_w, **pad)
            self.bill_date.grid(row=1, column=5, **pad, sticky=tk.W)
            self._lbl_previous_due.grid(row=1, column=6, **sticky_w, **pad)
            self._previous_due_display.grid(row=1, column=7, **pad)
            self.quick_add_btn.grid(row=1, column=8, columnspan=2, **pad, sticky=tk.W)
        else:
            self._lbl_customer.grid(row=0, column=0, **sticky_w, **pad)
            self.customer_name.grid(row=0, column=1, **pad)
            self._lbl_phone.grid(row=0, column=2, **sticky_w, **pad)
            self.customer_phone.grid(row=0, column=3, **pad)
            self._lbl_address.grid(row=0, column=4, **sticky_w, **pad)
            self.customer_address.grid(row=0, column=5, **pad)
            self._lbl_doctor.grid(row=1, column=0, **sticky_w, **pad)
            self.doctor_name.grid(row=1, column=1, **pad)
            self._lbl_doctor_phone.grid(row=1, column=2, **sticky_w, **pad)
            self.doctor_phone.grid(row=1, column=3, **pad)
            self._lbl_bill_date.grid(row=1, column=4, **sticky_w, **pad)
            self.bill_date.grid(row=1, column=5, **pad, sticky=tk.W)
            col = 6
            if show_payment and self._payment_mode_position == POSITION_AFTER_BILL_DATE:
                self._lbl_payment.grid(row=1, column=col, **sticky_w, **pad)
                self.payment_mode.grid(row=1, column=col + 1, **pad, sticky=tk.W)
            else:
                self._lbl_previous_due.grid(row=1, column=col, **sticky_w, **pad)
                self._previous_due_display.grid(row=1, column=col + 1, **pad)
                self.quick_add_btn.grid(row=1, column=col + 2, columnspan=2, **pad, sticky=tk.W)
            if show_payment and self._payment_mode_position == POSITION_AFTER_BILL_DATE:
                self._lbl_previous_due.grid(row=0, column=6, **sticky_w, **pad)
                self._previous_due_display.grid(row=0, column=7, **pad)
                self.quick_add_btn.grid(row=0, column=8, columnspan=2, **pad, sticky=tk.W)

        self._wire_customer_focus_chain()

    def _wire_customer_focus_chain(self):
        if self._sales_payment_mode_enabled() and not self._sales_payment_after_bill_date():
            self.payment_mode.next_focus_widget = lambda: self.customer_name.focus()
        else:
            self.payment_mode.next_focus_widget = lambda: self._focus_medicine_name()

    def _focus_payment_mode_field(self):
        if not self._sales_payment_mode_enabled():
            return
        try:
            if self.payment_mode.winfo_exists():
                self.payment_mode.focus(open_dropdown=True)
        except tk.TclError:
            pass

    def _bill_date_enter(self, event=None):
        if self._sales_payment_mode_enabled() and self._sales_payment_after_bill_date():
            self._focus_payment_mode_field()
        else:
            self._focus_medicine_name()
        return 'break'

    def _focus_medicine_name(self):
        try:
            self.medicine_combo.focus_step1()
        except Exception:
            pass

    def _make_bill_date_widget(self, parent):
        def _on_date_enter(_event=None):
            return self._bill_date_enter()

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
            w.bind('<<DateEntrySelected>>', lambda e: self._bill_date_enter())
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

    def _on_bill_date_changed(self):
        """Refresh medicine batch filter when bill date changes — never steal focus."""
        raw = (self.bill_date_var.get() or "").strip()
        # Ignore partial typing (e.g. "2026-08-1") so DateEntry keeps focus.
        if len(raw) < 10:
            return
        try:
            datetime.strptime(raw[:10], "%Y-%m-%d")
        except ValueError:
            return
        last = getattr(self, "_last_bill_date_for_med_clear", None)
        if last == raw[:10]:
            return
        self._last_bill_date_for_med_clear = raw[:10]
        try:
            self.medicine_combo._clear_medicine_selection(focus=False)
        except Exception:
            pass

    def get_bill_date_value(self):
        raw = (self.bill_date_var.get() or '').strip()
        # Prefer live entry text while editing DateEntry (var can lag / reset).
        try:
            ent = getattr(self.bill_date, "entry", None) or self.bill_date
            typed = (ent.get() or "").strip()
            if typed:
                raw = typed
        except Exception:
            pass
        if not raw:
            return date.today()
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
            try:
                return datetime.strptime(raw[:10] if fmt == '%Y-%m-%d' else raw, fmt).date()
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

    def _set_bill_date_value(self, value):
        """Set bill date from YYYY-MM-DD string (works with DateEntry or Entry)."""
        raw = str(value or '').strip()
        if not raw:
            self._reset_bill_date_today()
            return
        if ' ' in raw:
            raw = raw.split(' ')[0]
        self.bill_date_var.set(raw)
        self._last_bill_date_for_med_clear = raw[:10]
        try:
            if hasattr(self.bill_date, 'set_date'):
                self.bill_date.set_date(datetime.strptime(raw[:10], '%Y-%m-%d').date())
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
        try:
            od = float(self.discount.get() or 0)
        except ValueError:
            od = 0.0
        validate_bill_discounts(self.parent, self.selected_medicines, od)

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
        try:
            od = float(self.discount.get() or 0)
        except ValueError:
            od = 0.0
        validate_bill_discounts(self.parent, self.selected_medicines, od)

    # ── Doctor helpers ────────────────────────────────────────────────────

    def reload_doctors(self):
        from core.background_workers import run_in_thread
        run_in_thread(
            lambda: get_all_doctor_names(self.conn),
            name='BillingDoctorsReload',
            root=self.parent,
            on_success=lambda names: self._apply_doctor_names(names),
        )

    def _apply_doctor_names(self, names):
        self.all_doctors = list(names or [])
        try:
            self.doctor_name.configure(values=self.all_doctors)
        except Exception:
            pass

    def _refresh_doctors(self, show_list=False):
        self.reload_doctors()
        if show_list and self.all_doctors:
            self.doctor_name.after(10, self.doctor_name._show_all_on_focus)

    def on_doctor_select(self, event=None):
        name = self.doctor_name.get().strip().upper()
        if not name:
            self.doctor_phone.focus()
            return
        self.doctor_phone.focus()
        seq = int(getattr(self, "_doctor_lookup_seq", 0) or 0) + 1
        self._doctor_lookup_seq = seq
        from core.background_workers import db_path_from_conn, run_in_thread

        db_path = db_path_from_conn(self.conn)

        def _lookup():
            try:
                from core.sync_prefs import is_online_mode
                if is_online_mode():
                    from core.online_catalog import find_doctor_by_name
                    doc = find_doctor_by_name(name)
                    return (doc or {}).get("phone") or ""
            except Exception:
                pass
            if not db_path:
                return ""
            try:
                from core.db_utils import open_store_db
                conn = open_store_db(db_path, readonly=True, timeout=15.0)
                try:
                    row = conn.execute(
                        "SELECT phone FROM doctors WHERE UPPER(name)=? LIMIT 1",
                        (name,),
                    ).fetchone()
                    return (row[0] if row else "") or ""
                finally:
                    conn.close()
            except Exception:
                return ""

        def _apply(phone):
            if seq != getattr(self, "_doctor_lookup_seq", 0):
                return
            if not phone:
                return
            try:
                if self.doctor_name.get().strip().upper() != name:
                    return
                self.doctor_phone.delete(0, tk.END)
                self.doctor_phone.insert(0, phone)
            except Exception:
                pass

        run_in_thread(
            _lookup,
            name="BillingDoctorLookup",
            root=self.parent,
            on_success=_apply,
        )

    def _selected_medicine_requires_doctor(self, sel=None) -> bool:
        if sel is None:
            try:
                sel = self.medicine_combo.get_selected_medicine()
            except Exception:
                sel = None
        if not sel:
            return False
        try:
            from core.billing_layout_prefs import schedule_requires_doctor
            return bool(schedule_requires_doctor(sel.get("schedule")))
        except Exception:
            return bool(sel.get("schedule"))

    def on_doctor_phone_enter(self, event):
        if self._selected_medicine_requires_doctor():
            self.quantity.focus()
        else:
            self.medicine_combo.focus()

    def handle_medicine_focus(self):
        if self._selected_medicine_requires_doctor() and not self.doctor_name.get():
            return
        self.quantity.focus()

    # ── Customer helpers ──────────────────────────────────────────────────

    def _medicine_stock_qty(self, medicine_id, fallback=None) -> int:
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                # Prefer selected-row stock — avoids cold medicines() load on Add.
                if fallback is not None:
                    try:
                        return int(float(fallback or 0))
                    except (TypeError, ValueError):
                        pass
                from core.online_catalog import medicine_by_id
                m = medicine_by_id(medicine_id)
                if m is not None:
                    return int(float(m.get("stock_qty") or 0))
                return 0
        except Exception:
            pass
        try:
            self.cursor.execute(
                "SELECT COALESCE(stock_qty, 0) FROM medicines WHERE id=?", (medicine_id,))
            return int((self.cursor.fetchone() or [0])[0] or 0)
        except Exception:
            return int(float(fallback or 0)) if fallback is not None else 0

    def _medicine_row_info(self, medicine_id, sel=None) -> dict:
        sel = sel or {}
        # Online Add: use selected batch fields first (already loaded for the dropdown).
        if sel.get("type") or sel.get("unit") or sel.get("mrp") is not None or sel.get("rate") is not None:
            try:
                from core.sync_prefs import is_online_mode
                if is_online_mode():
                    return {
                        "type": sel.get("type") or "",
                        "unit": sel.get("unit") or "1",
                        "gst_percent": sel.get("gst_percent") or 0,
                        "location": sel.get("location") or "",
                        "mrp": sel.get("mrp"),
                        "rate": sel.get("rate"),
                    }
            except Exception:
                pass
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.online_catalog import medicine_by_id
                m = medicine_by_id(medicine_id) or {}
                return {
                    "type": m.get("type") or sel.get("type") or "",
                    "unit": m.get("unit") or sel.get("unit") or "1",
                    "gst_percent": m.get("gst_percent") if m.get("gst_percent") is not None else sel.get("gst_percent") or 0,
                    "location": m.get("location") or "",
                    "mrp": m.get("mrp") if m.get("mrp") is not None else sel.get("mrp"),
                    "rate": m.get("rate") if m.get("rate") is not None else sel.get("rate"),
                }
        except Exception:
            pass
        try:
            self.cursor.execute(
                "SELECT type, unit, gst_percent, location, COALESCE(mrp,0), COALESCE(rate,0) "
                "FROM medicines WHERE id=?",
                (medicine_id,))
            info = self.cursor.fetchone()
            if info:
                return {
                    "type": info[0] or "",
                    "unit": info[1] or "1",
                    "gst_percent": info[2] or 0,
                    "location": info[3] or "",
                    "mrp": info[4],
                    "rate": info[5],
                }
        except Exception:
            pass
        return {
            "type": sel.get("type") or "",
            "unit": sel.get("unit") or "1",
            "gst_percent": sel.get("gst_percent") or 0,
            "location": "",
            "mrp": sel.get("mrp"),
            "rate": sel.get("rate"),
        }

    def _refresh_customer_names(self, show_list=False):
        # Skip if names were loaded recently (startup prewarm / post-save refresh).
        import time

        now = time.time()
        last = float(getattr(self, "_customer_names_loaded_at", 0) or 0)
        if now - last < 90 and getattr(self.customer_name, "values", None):
            if show_list:
                try:
                    self.customer_name.after(10, self.customer_name._show_all_on_focus)
                except Exception:
                    pass
            return
        from core.background_workers import run_in_thread
        run_in_thread(
            lambda: get_customer_names(self.conn),
            name='BillingCustomersReload',
            root=self.parent,
            on_success=lambda names: self._apply_customer_names(names, show_list),
        )

    def _apply_customer_names(self, names, show_list=False):
        import time

        try:
            self._customer_names_loaded_at = time.time()
            self.customer_name.configure(values=names or [])
            if show_list:
                self.customer_name.after(10, self.customer_name._show_all_on_focus)
        except Exception:
            pass

    def _apply_default_village(self):
        default = get_default_village(self.conn)
        if default:
            self.customer_address.set(default)

    def _is_due_payment(self) -> bool:
        if not self._sales_payment_mode_enabled():
            return False
        return (self.payment_mode.get() or '').strip().lower() == 'due'

    def _is_cash_payment(self) -> bool:
        if not self._sales_payment_mode_enabled():
            return True
        mode = (self.payment_mode.get() or '').strip().lower()
        # Empty payment mode = treat as Cash (Enter on Online still saves/prints).
        return mode in ('cash', '')

    def _validate_payment_mode(self) -> bool:
        if not self._sales_payment_mode_enabled():
            return True
        mode = (self.payment_mode.get() or '').strip().lower()
        if mode in ('cash', 'due', ''):
            return True
        showwarning(
            "Payment Mode Required",
            "Please select Cash or Due before saving.",
            parent=self.parent,
        )
        try:
            self.payment_mode.focus(open_dropdown=True)
        except Exception:
            pass
        return False

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
            if hasattr(self, '_scroll_to_widget'):
                self._scroll_to_widget(self.cash_paid)
        except Exception:
            pass

    def _validate_cash_payment(self) -> bool:
        if self._is_due_payment():
            return True
        if not self._is_cash_payment():
            return self._validate_payment_mode()
        cash_s = (self.cash_paid.get() or '').strip()
        online_s = (self.online_paid.get() or '').strip()
        if not cash_s and not online_s:
            showwarning(
                "Payment Required",
                "Please enter amount in Cash or Online before saving.",
                parent=self.parent,
            )
            self._focus_cash_payment()
            return False
        try:
            total_paid = float(cash_s or 0) + float(online_s or 0)
        except ValueError:
            showwarning(
                "Invalid Payment",
                "Please enter valid numbers in Cash or Online.",
                parent=self.parent,
            )
            self._focus_cash_payment()
            return False
        if total_paid <= 0:
            showwarning(
                "Payment Required",
                "Cash or Online amount must be greater than zero.",
                parent=self.parent,
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
        self.calculate_total()

    def _online_enter(self, event=None):
        if not self._is_due_payment():
            self._run_sales_enter_action('cash_online_enter_action')
        return 'break'

    def _on_rounding_change(self, event=None):
        self._rounding_touched = True
        self.calculate_total()

    def _rounding_enter(self, event=None):
        if self._is_due_payment():
            self._run_sales_enter_action('due_rounding_enter_action')
            return 'break'
        self.cash_paid.focus_set()
        try:
            self.cash_paid.select_range(0, tk.END)
        except Exception:
            pass
        return 'break'

    def reload_villages(self, show_list=False):
        villages = village_names_for_ui(self.conn)
        self.customer_address.configure(values=villages)
        default = get_default_village(self.conn)
        if default and not self.customer_address.get().strip():
            self.customer_address.set(default)
        if show_list and villages:
            self.customer_address.after(10, self.customer_address._show_all_on_focus)

    def _customer_name_enter(self):
        """Enter: empty name → counter sale unless scheduled medicine needs customer."""
        name = self.customer_name.get().strip()
        if not name:
            if self._has_scheduled_medicine():
                showwarning(
                    "Missing Information",
                    "Scheduled medicine requires a customer name and doctor.\n"
                    "Please enter the customer name first.",
                    parent=self.parent,
                )
                try:
                    self.customer_name.focus()
                except Exception:
                    pass
                return
            self.customer_address.set('')
            self._customer_id = None
            self.previous_due = 0
            self.previous_credit = 0
            self.previous_due_var.set("0.00")
            self.customer_name.hide_list()
            self._focus_medicine_name()
            return
        # Apply known customer contact from catalog before deciding focus.
        try:
            self.on_customer_select()
        except Exception:
            pass
        default = get_default_village(self.conn)
        if default and not self.customer_address.get().strip():
            self.customer_address.set(default)
        phone = self.customer_phone.get().strip()
        addr = self.customer_address.get().strip()
        # Skip phone/address when already filled (pulled from server or typed earlier).
        if phone and addr:
            try:
                self.customer_name.hide_list()
            except Exception:
                pass
            try:
                self.doctor_name.entry.focus_set()
            except Exception:
                self._focus_medicine_name()
            return
        if not phone:
            try:
                self.customer_phone.focus_set()
                self.customer_phone.select_range(0, tk.END)
            except Exception:
                pass
            return
        try:
            self._focus_customer_address()
        except Exception:
            pass

    def _focus_customer_address(self):
        """Phone Enter → address, or skip to doctor when address already set."""
        default = get_default_village(self.conn)
        if default and not self.customer_address.get().strip():
            self.customer_address.set(default)
        if self.customer_address.get().strip():
            try:
                self.doctor_name.entry.focus_set()
            except Exception:
                pass
            return
        self.reload_villages(show_list=True)
        try:
            self.customer_address.entry.focus_set()
        except Exception:
            pass
        return 'break'

    def _address_enter(self):
        try:
            self.doctor_name.entry.focus_set()
        except Exception:
            pass

    def on_customer_select(self, event=None):
        name = self.customer_name.get().strip()
        if not name:
            return
        if is_counter_sale_name(name):
            self.customer_phone.delete(0, tk.END)
            self.customer_address.set('')
            self._set_previous_due(0, 0)
            self.calculate_total()
            return
        # Instant UI from cache; Online previous_due refresh runs in background.
        customer = get_customer_by_name(self.conn, name, force_refresh=False)
        if customer:
            self._apply_customer_row(customer, update_contact=True)
            self.calculate_total()
        self._update_sale_tab_label()
        self._schedule_customer_due_refresh(name, update_contact=True)

    def _normalize_customer_name_field(self):
        from core.name_utils import storage_name_from_entry
        raw = self.customer_name.get().strip()
        if not raw:
            return
        if is_counter_sale_name(raw):
            if raw.strip().upper() != COUNTER_SALE:
                self.customer_name.set(COUNTER_SALE)
            self._update_sale_tab_label()
            return
        short = storage_name_from_entry(raw)
        if short and short != raw.upper():
            self.customer_name.set(short)
        self._update_sale_tab_label()

    def _update_sale_tab_label(self):
        if not getattr(self, '_doc_tabs', None):
            return
        from core.name_utils import sale_tab_label
        default = f'Sale {self._active_tab_idx + 1}'
        label = sale_tab_label(self.customer_name.get(), default)
        if hasattr(self, '_refresh_tab_bar'):
            self._doc_tabs[self._active_tab_idx]['label'] = label
            self._refresh_tab_bar()

    def check_name_due(self, event):
        self._schedule_sale_tab_label_update()
        self.previous_due = 0
        self.previous_credit = 0
        self.previous_due_var.set("0.00")
        self._customer_id = None
        name = self.customer_name.get().strip()
        if not name:
            # Keep cached dropdown values — reloading all customers on every
            # backspace was freezing the Sales page on large stores.
            return
        pending = getattr(self, "_due_type_pending", None)
        if pending:
            try:
                self.parent.after_cancel(pending)
            except Exception:
                pass
        self._due_type_pending = self.parent.after(
            180, lambda n=name: self._lookup_customer_due_cached(n)
        )

    def _schedule_sale_tab_label_update(self):
        pending = getattr(self, "_tab_label_pending", None)
        if pending:
            try:
                self.parent.after_cancel(pending)
            except Exception:
                pass
        self._tab_label_pending = self.parent.after(120, self._run_sale_tab_label_update)

    def _run_sale_tab_label_update(self):
        self._tab_label_pending = None
        self._update_sale_tab_label()

    def _lookup_customer_due_cached(self, name):
        self._due_type_pending = None
        if self.customer_name.get().strip() != name:
            return
        from core.background_workers import run_in_thread
        seq = int(getattr(self, "_customer_due_seq", 0) or 0) + 1
        self._customer_due_seq = seq

        def _work():
            return get_customer_by_name(self.conn, name, force_refresh=False)

        def _apply(customer):
            if seq != getattr(self, "_customer_due_seq", 0):
                return
            if self.customer_name.get().strip() != name:
                return
            if customer:
                self._apply_customer_row(customer, update_contact=False)
            self.calculate_total()

        run_in_thread(
            _work,
            name="BillingCustomerDueType",
            root=self.parent,
            on_success=_apply,
        )

    def verify_customer_due(self, event):
        name = self.customer_name.get().strip()
        if not name:
            self.calculate_total()
            return
        customer = get_customer_by_name(self.conn, name, force_refresh=False)
        if customer:
            self._apply_customer_row(customer, update_contact=False)
        else:
            self.previous_due = 0
            self.previous_credit = 0
            self.previous_due_var.set("0.00")
        self.calculate_total()
        self._schedule_customer_due_refresh(name, update_contact=False)

    def _apply_customer_row(self, customer, *, update_contact: bool):
        if not customer:
            return
        try:
            self._customer_id = customer.get("id")
        except Exception:
            self._customer_id = customer["id"]
        if update_contact:
            self.customer_phone.delete(0, tk.END)
            self.customer_phone.insert(0, customer.get("phone") or "")
            addr = (customer.get("address") or "").strip()
            if addr:
                self.customer_address.set(addr)
            else:
                self._apply_default_village()
        self._set_previous_due(
            float(customer.get("total_due") or 0),
            float(customer.get("total_credit") or 0),
        )

    def _schedule_customer_due_refresh(self, name, *, update_contact: bool):
        """Online: refresh previous due from server without freezing the UI."""
        try:
            from core.sync_prefs import is_online_mode
            if not is_online_mode():
                return
        except Exception:
            return
        if is_counter_sale_name(name):
            return
        seq = int(getattr(self, "_customer_due_seq", 0) or 0) + 1
        self._customer_due_seq = seq
        from core.background_workers import run_in_thread

        def _work():
            return get_customer_by_name(self.conn, name, force_refresh=True)

        def _apply(customer):
            if seq != getattr(self, "_customer_due_seq", 0):
                return
            if self.customer_name.get().strip() != name:
                return
            if customer:
                self._apply_customer_row(customer, update_contact=update_contact)
                self.calculate_total()

        run_in_thread(
            _work,
            name="BillingCustomerDueRefresh",
            root=self.parent,
            on_success=_apply,
        )

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

    def open_quick_sale_medicine_dialog(self, event=None):
        from widgets.quick_sale_medicine_dialog import show_quick_sale_medicine_dialog

        show_quick_sale_medicine_dialog(self.parent, self.conn, self._append_quick_sale_row)
        return 'break'

    def _append_quick_sale_row(self, row):
        from core.quick_sale_medicine import prepare_quick_sale_row_for_display
        from core.margin_utils import validate_bill_discounts

        if (row.get('schedule') or '').strip():
            if not self.customer_name.get().strip():
                showwarning(
                    'Customer Required',
                    'Scheduled medicine requires a customer name.\n'
                    'Please enter the customer name first.',
                    parent=self.parent,
                )
                try:
                    self.customer_name.focus()
                except Exception:
                    pass
                return
            if not self.doctor_name.get().strip():
                try:
                    from core.billing_layout_prefs import schedule_requires_doctor
                    needs_doc = schedule_requires_doctor(row.get('schedule'))
                except Exception:
                    needs_doc = True
                if needs_doc:
                    showwarning(
                        'Doctor Required',
                        'H1 / X (and other schedules if enabled in Settings) require a doctor name.\n'
                        'Please select or enter the doctor first.',
                        parent=self.parent,
                    )
                    try:
                        self.doctor_name.entry.focus_set()
                    except Exception:
                        pass
                    return

        row = prepare_quick_sale_row_for_display(row)
        match = next(
            (
                m for m in self.selected_medicines
                if (m.get('quick_add') or not m.get('id'))
                and (m.get('name') or '').upper() == row['name']
                and (m.get('batch') or '').upper() == row['batch']
            ),
            None,
        )
        if match:
            test_med = dict(match)
            test_med['qty'] = int(match['qty']) + int(row['qty'])
            total_base = round(test_med['qty'] * match['rate'], 2)
            test_med['original_amount'] = total_base
            test_med['amount'] = total_base
            if not validate_bill_discounts(self.parent, [test_med]):
                return
            match['qty'] = test_med['qty']
            match['original_amount'] = total_base
            match['amount'] = total_base
            if row.get('schedule'):
                match['schedule'] = row['schedule']
            row = prepare_quick_sale_row_for_display(match)
            match.update(row)
        else:
            if not validate_bill_discounts(self.parent, [row]):
                return
            self.selected_medicines.append(row)
        self._sync_medicine_reserved_stock()
        self.update_medicine_tree()
        self.calculate_total()

    def _qty_in_bill(self, medicine_id):
        if not medicine_id:
            return 0
        return sum(m['qty'] for m in self.selected_medicines if m.get('id') == medicine_id)

    def _sync_medicine_reserved_stock(self):
        reserved = {}
        for m in self.selected_medicines:
            reserved[m['id']] = reserved.get(m['id'], 0) + m['qty']
        self.medicine_combo.set_reserved_stock(reserved)

    def on_medicine_select(self, event):
        sel = self.medicine_combo.get_selected_medicine()
        if sel:
            if self._selected_medicine_requires_doctor(sel) and not self.doctor_name.get():
                showwarning(
                    "Doctor Required",
                    "H1 / X (and other schedules if enabled in Settings) require a doctor name.",
                    parent=self.parent,
                )
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
        if self._selected_medicine_requires_doctor(sel) and not self.doctor_name.get():
            showwarning(
                "Doctor Required",
                "H1 / X (and other schedules if enabled in Settings) require a doctor name.",
                parent=self.parent,
            )
            return
        try:
            from core.batch_visibility import is_expired_as_of, medicine_existed_as_of
            bill_date = self.get_bill_date_value()
            if is_expired_as_of(sel['expiry'], bill_date):
                showerror("Expired Medicine",
                          f"{sel['name']} expired on {sel['expiry']}.", parent=self.parent)
                return
            from core.sale_availability import batch_existed_on

            # Online this used to be skipped (the engine's SQLite is empty there), so a
            # back-dated bill took batches that only came in later. The helper asks the
            # store Online and reads created_at + the first purchase offline.
            # wait=False: this is the Tk thread, so adding a line never waits for the
            # store. With no answer yet the line goes in and the answer is worked out
            # behind it; the save checks every line again (billing.py _persist_sale).
            if not batch_existed_on(self.cursor.connection, sel['id'], bill_date, wait=False):
                showerror(
                    "Not Available",
                    f"{sel['name']} was added after {bill_date}. "
                    "It cannot be sold on this bill date.",
                    parent=self.parent,
                )
                return
        except Exception:
            pass
        db_stock = self._medicine_stock_qty(sel.get('id'), fallback=sel.get('stock'))
        available = db_stock - self._qty_in_bill(sel['id'])
        if available <= 0:
            showerror("Out of Stock",
                      f"{sel['name']} is fully reserved in this bill.", parent=self.parent)
            return
        if qty > available:
            showerror("Insufficient Stock",
                      f"Only {int(available)} units available.", parent=self.parent)
            return

        info = self._medicine_row_info(sel.get('id'), sel)
        med_type    = info.get('type') or ''
        unit_value  = info.get('unit') or '1'
        gst_percent = info.get('gst_percent') or 0
        location    = self._fmt_location(info.get('location') or '')
        list_mrp    = float(info.get('mrp') if info.get('mrp') is not None else sel.get('mrp') or 0)
        purchase_rate = float(info.get('rate') if info.get('rate') is not None else sel.get('rate') or 0)

        if is_strip_count_type(med_type or '', unit_value):
            try:
                ups = resolve_tablets_per_stripe(
                    unit_value, name=sel.get('name') or '', med_type=med_type or '',
                )
                rate = list_mrp / ups
                # Persist corrected pack on Online unit=1 strip-MRP rows.
                if ups > 1 and str(unit_value).strip() in ('', '1', '1.0'):
                    unit_value = str(ups)
                    try:
                        from core.sync_prefs import is_online_mode
                        if is_online_mode():
                            from core.server_crud import upsert_medicine_online
                            from core.online_catalog import medicine_by_id, invalidate
                            mid = int(sel.get('id') or 0)
                            mp = medicine_by_id(mid) or {}
                            if mid and mp:
                                row = dict(mp)
                                row['id'] = mid
                                row['local_id'] = mid
                                row['unit'] = str(ups)
                                upsert_medicine_online(row)
                                invalidate('medicines')
                    except Exception:
                        pass
            except (ValueError, ZeroDivisionError):
                rate = list_mrp
        else:
            rate = sel['mrp']

        try:
            disc_input = float(self.medicine_discount.get() or 0)
        except ValueError:
            disc_input = 0
        if not self._item_discount_visible():
            disc_input = 0

        base = round(qty * rate, 2)
        from core.billing_layout_prefs import convert_item_discount_input_to_rupees

        med_disc_rs = convert_item_discount_input_to_rupees(disc_input, base)
        amount = round(base - med_disc_rs, 2)

        existing = next((m for m in self.selected_medicines if m['id'] == sel['id']), None)
        if existing:
            test_med = dict(existing)
            test_med['qty'] = existing['qty'] + qty
            total_base = round(test_med['qty'] * existing['rate'], 2)
            test_disc = round(existing.get('medicine_discount', 0) + med_disc_rs, 2)
            test_disc = min(test_disc, total_base)
            test_med['medicine_discount'] = test_disc
            enrich_medicine_margin_fields(
                test_med, list_mrp, purchase_rate, med_type, unit_value)
            if not validate_bill_discounts(self.parent, [test_med]):
                return
            existing['qty'] = test_med['qty']
            existing['original_amount'] = total_base
            existing['medicine_discount'] = test_disc
            existing['amount'] = round(total_base - test_disc, 2)
            enrich_medicine_margin_fields(
                existing, list_mrp, purchase_rate, med_type, unit_value)
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
                'gst_percent':      gst_percent,
                'location':         location,
                'unit':             unit_value,
            }
            enrich_medicine_margin_fields(
                med_row, list_mrp, purchase_rate, med_type, unit_value)
            if not validate_bill_discounts(self.parent, [med_row]):
                return
            self.selected_medicines.append(med_row)
        self._sync_medicine_reserved_stock()
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
        self.medicine_combo.selected_medicine = None
        self.quantity.delete(0, tk.END)
        self.medicine_discount.delete(0, tk.END)
        self.medicine_discount.insert(0, "0")

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

    def update_medicine_tree(self):
        for item in self.medicine_tree.get_children():
            self.medicine_tree.delete(item)
        for med in self.selected_medicines:
            if show_margin_column() or show_total_margin():
                enrich_medicine_margin_fields(
                    med,
                    med.get('list_mrp', med.get('mrp', 0)),
                    med.get('purchase_rate', 0),
                    med.get('type', ''),
                    med.get('unit', '1'),
                )
            self.medicine_tree.insert('', tk.END, values=self._tree_row_values(med))

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

        from core.billing_layout_prefs import (
            convert_item_discount_input_to_rupees,
            item_discount_is_percent,
        )
        disc_is_pct = item_discount_is_percent()
        ttk.Label(
            ff,
            text=f"Discount ({'%' if disc_is_pct else '₹'}):",
        ).grid(row=1, column=0, sticky=tk.W, padx=8, pady=6)
        disc_e = ttk.Entry(ff, width=14)
        disc_e.grid(row=1, column=1, padx=8, pady=6, sticky=tk.EW)
        try:
            idx = self.medicine_tree.index(sel[0])
            med_ref = self.selected_medicines[idx]
            stored_rs = float(med_ref.get('medicine_discount', 0) or 0)
            if disc_is_pct:
                base0 = round(float(med_ref.get('qty') or 0) * float(med_ref.get('rate') or 0), 2)
                pct = round((stored_rs / base0) * 100.0, 2) if base0 > 0 else 0.0
                disc_e.insert(0, str(pct))
            else:
                disc_e.insert(0, str(stored_rs))
        except (tk.TclError, IndexError, TypeError, ValueError):
            disc_e.insert(0, "0")

        def update():
            try:
                new_qty  = int(qty_e.get())
                disc_input = float(disc_e.get() or 0)
                try:
                    idx = self.medicine_tree.index(sel[0])
                except tk.TclError:
                    dlg.destroy(); return
                if new_qty == 0:
                    del self.selected_medicines[idx]
                else:
                    med = self.selected_medicines[idx]
                    db_stock = self._medicine_stock_qty(med.get('id'), fallback=med.get('stock'))
                    if new_qty > db_stock:
                        showerror("Insufficient Stock",
                                  f"Only {db_stock} units available in stock.",
                                  parent=self.parent)
                        return
                    base = round(new_qty * med['rate'], 2)
                    new_disc = convert_item_discount_input_to_rupees(disc_input, base)
                    med['qty']               = new_qty
                    med['medicine_discount'] = new_disc
                    med['original_amount']   = base
                    med['amount']            = round(base - min(new_disc, base), 2)
                    enrich_medicine_margin_fields(
                        med,
                        med.get('list_mrp', 0),
                        med.get('purchase_rate', 0),
                        med.get('type', ''),
                        med.get('unit', '1'),
                    )
                    try:
                        od = float(self.discount.get() or 0)
                    except ValueError:
                        od = 0.0
                    if not validate_bill_discounts(self.parent, self.selected_medicines, od):
                        return
                self._sync_medicine_reserved_stock()
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
            self._sync_medicine_reserved_stock()
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

    def _refresh_margin_summary_visibility(self):
        if not hasattr(self, '_total_margin_lbl'):
            return
        if show_total_margin():
            self._total_margin_lbl.grid(row=1, column=6, sticky=tk.W, padx=4, pady=2)
            self._total_margin_val.grid(row=1, column=7, sticky=tk.W, padx=4, pady=2)
        else:
            self._total_margin_lbl.grid_remove()
            self._total_margin_val.grid_remove()

    def _apply_location_column_visibility(self):
        visible = [c for c in get_visible_columns('billing', self._all_columns)
                   if c != 'Margin ₹']
        if not self._show_location_enabled():
            visible = [c for c in visible if c != 'Location']
        if not self._item_discount_visible():
            visible = [c for c in visible if c != 'Disc ₹']
        if show_margin_column():
            try:
                idx = visible.index('Disc ₹') + 1
            except ValueError:
                idx = len(visible)
            visible.insert(idx, 'Margin ₹')
        if not visible:
            visible = [c for c in self._all_columns if c != 'Location']
        self.medicine_tree.configure(displaycolumns=visible)
        self._refresh_item_discount_visibility()
        self._refresh_item_discount_label()
        try:
            self.medicine_tree.heading('Margin ₹', text=margin_column_heading())
        except Exception:
            pass
        self._refresh_margin_summary_visibility()
        self.update_medicine_tree()

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
