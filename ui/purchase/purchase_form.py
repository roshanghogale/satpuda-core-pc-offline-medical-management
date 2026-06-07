"""
ui/purchase/purchase_form.py
────────────────────────────
UI building mixin for PurchasePage.
No calculations. No DB calls.
"""
import tkinter as tk
from datetime import datetime
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.font_config import *
from core.layout_config import (
    PURCHASE_ROWS, get_type_measure_unit, is_strip_count_type,
)
from core.column_config import apply_column_visibility, all_column_names
from core.scroll_manager import make_scrollable
from widgets.searchable_combo import SearchableCombo


TYPE_UNITS = {
    'syrup': 'ml', 'injection': 'ml', 'liquid': 'ml', 'liniment': 'ml',
    'ointment': 'gm', 'powder': 'gm', 'gel': 'gm', 'granules': 'gm',
    'vaccine': 'ml', 'injection - vial': 'Vial',
}


class PurchaseFormMixin:

    def _normalize_expiry_text(self, text: str) -> str:
        raw = ''.join(ch for ch in (text or '') if ch.isdigit())
        if not raw:
            return ''
        if len(raw) == 1:
            return raw
        if len(raw) == 2:
            return f"{raw}/"
        if len(raw) <= 4:
            return f"{raw[:2]}/{raw[2:]}"
        return f"{raw[:2]}/{raw[2:4]}"

    def _on_expiry_key_release(self, event=None):
        entry = getattr(self, 'expiry_date', None)
        if not entry or not entry.winfo_exists():
            return
        original = entry.get()
        formatted = self._normalize_expiry_text(original)
        if formatted != original:
            pos = entry.index(tk.INSERT)
            entry.delete(0, tk.END)
            entry.insert(0, formatted)
            if len(formatted) == 3 and '/' in formatted and pos >= 2:
                pos += 1
            entry.icursor(min(pos, len(formatted)))

    def _on_expiry_focus_out(self, event=None):
        entry = getattr(self, 'expiry_date', None)
        if not entry or not entry.winfo_exists():
            return
        formatted = self._normalize_expiry_text(entry.get())
        entry.delete(0, tk.END)
        entry.insert(0, formatted)

    # ── top-level builder ─────────────────────────────────────────────────

    def _build_interface(self):
        main_frame = make_scrollable(self.parent)
        self._inner_frame = main_frame
        self._page_canvas = getattr(main_frame, '_canvas', None)
        main_frame.configure(padding=(10, 10))

        top = ttk.Frame(main_frame)
        top.pack(fill=tk.X, pady=2)

        self._build_supplier_panel(top)
        self._build_medicine_panel(top)
        self._build_items_tree(main_frame)
        self._build_summary(main_frame)

    # ── supplier panel ────────────────────────────────────────────────────

    def _build_supplier_panel(self, parent):
        sf = ttk.LabelFrame(parent, text="Supplier Information")
        sf.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))

        ttk.Label(sf, text="Supplier Name:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.supplier_name = SearchableCombo(sf, width=25)
        self.supplier_name.grid(row=0, column=1, padx=5, pady=2)
        self.supplier_name.bind('<<ComboboxSelected>>', self.load_supplier_details)
        self.supplier_name.entry.bind('<FocusIn>', lambda e: self.load_suppliers(), add='+')
        self.supplier_name.next_focus_widget = lambda: self.supplier_address.focus()

        for row, (lbl, attr) in enumerate([
            ("Address:",    'supplier_address'),
            ("Phone:",      'supplier_phone'),
            ("GSTIN:",      'supplier_gstin'),
            ("DL Numbers:", 'supplier_dl'),
        ], start=1):
            ttk.Label(sf, text=lbl).grid(row=row, column=0, sticky=tk.W, padx=5, pady=2)
            e = ttk.Entry(sf, width=25)
            e.grid(row=row, column=1, padx=5, pady=2)
            setattr(self, attr, e)

        ttk.Label(sf, text="Purchase Date:").grid(row=5, column=0, sticky=tk.W, padx=5, pady=2)
        self.purchase_date = ttk.Entry(sf, width=25)
        self.purchase_date.grid(row=5, column=1, padx=5, pady=2)
        self.purchase_date.insert(0, datetime.now().strftime('%Y-%m-%d'))
        ttk.Label(sf, text="(YYYY-MM-DD)", font=(FONT_FAMILY, 7)).grid(
            row=5, column=1, sticky=tk.E, padx=5)

        ttk.Label(sf, text="Bill Number:").grid(row=6, column=0, sticky=tk.W, padx=5, pady=2)
        self.bill_number = ttk.Entry(sf, width=25)
        self.bill_number.grid(row=6, column=1, padx=5, pady=2)

        from core.focus_chain import wire_entry_filter_chain
        wire_entry_filter_chain(
            self.supplier_address,
            self.supplier_phone,
            self.supplier_gstin,
            self.supplier_dl,
            self.purchase_date,
            self.bill_number,
            last_action=lambda: self.medicine_name.entry.focus_set(),
        )

        self.load_suppliers()

    # ── medicine panel ────────────────────────────────────────────────────

    def _build_medicine_panel(self, parent):
        mf = ttk.LabelFrame(parent, text="Medicine Details")
        mf.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        mf.grid_columnconfigure(1, weight=1)
        mf.grid_columnconfigure(3, weight=1)

        ttk.Label(mf, text="Medicine Name:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.medicine_name = SearchableCombo(mf, width=30)
        self.medicine_name.grid(row=0, column=1, sticky=tk.EW, padx=5, pady=5)
        self.medicine_name.next_focus_widget = lambda: self.medicine_type.focus()
        self.medicine_name.bind('<<ComboboxSelected>>', self.on_medicine_selected)
        self.medicine_name.entry.bind('<FocusIn>', lambda e: self._reload_medicine_names(), add='+')
        self.medicine_name.configure(values=[])
        self.medicine_master_status_var = tk.StringVar(value="")
        ttk.Label(
            mf,
            textvariable=self.medicine_master_status_var,
            font=(FONT_FAMILY, 8),
            foreground="#8a6d3b",
        ).grid(row=1, column=1, sticky=tk.W, padx=5, pady=(0, 4))

        ttk.Label(mf, text="Type:").grid(row=0, column=2, sticky=tk.W, padx=5, pady=5)
        self.medicine_type = SearchableCombo(mf, width=20)
        self.medicine_type.configure(values=self._med_types)
        self.medicine_type.grid(row=0, column=3, sticky=tk.EW, padx=5, pady=5)
        self.medicine_type.bind('<<ComboboxSelected>>', self.on_type_change)
        self.medicine_type.next_focus_widget = lambda: self.focus_qty_field()

        self.qty_frame = ttk.LabelFrame(mf, text="Quantity Details")
        self.qty_frame.grid(row=2, column=0, columnspan=4, sticky=tk.EW, padx=5, pady=5)
        self._create_tablet_qty_fields()

        # Fields: (row, col, label, attr, width, next_attr)
        fields = [
            (3, 0, "HSN Code:",    'hsn_code',    20, 'gst_value'),
            (3, 2, "GST %:",       'gst_value',   15, 'mrp'),
            (4, 0, "MRP:",         'mrp',         20, 'rate'),
            (4, 2, "Rate (per strip/unit):", 'rate', 15, 'manufacturer'),
            (5, 0, "Manufacturer:",'manufacturer',30, 'batch_no'),
            (5, 2, "Batch No:",    'batch_no',    15, 'expiry_date'),
            (6, 0, "Expiry (MM/YY):", 'expiry_date', 20, 'schedule'),
            (7, 0, "Content/Drug:", 'content_drug', 30, 'item_discount'),
        ]
        for row, col, lbl, attr, width, next_attr in fields:
            ttk.Label(mf, text=lbl).grid(row=row, column=col, sticky=tk.W, padx=5, pady=5)
            e = ttk.Entry(mf, width=width)
            e.grid(row=row, column=col+1, sticky=tk.EW, padx=5, pady=5)
            e.bind('<Return>', lambda ev, n=next_attr: getattr(self, n).focus())
            setattr(self, attr, e)
        self.expiry_date.bind('<KeyRelease>', self._on_expiry_key_release, add='+')
        self.expiry_date.bind('<FocusOut>', self._on_expiry_focus_out, add='+')

        ttk.Label(mf, text="Schedule:").grid(row=6, column=2, sticky=tk.W, padx=5, pady=5)
        self.schedule = SearchableCombo(mf, values=[s for s in self._schedules if s],
                                        width=15, listbox_height=8)
        self.schedule.grid(row=6, column=3, sticky=tk.EW, padx=5, pady=5)
        self.schedule.next_focus_widget = lambda: self.content_drug.focus()

        ttk.Label(mf, text="Discount %:").grid(row=8, column=0, sticky=tk.W, padx=5, pady=5)
        self.item_discount = ttk.Entry(mf, width=10)
        self.item_discount.grid(row=8, column=1, sticky=tk.EW, padx=5, pady=5)
        self.item_discount.insert(0, "0")
        self.item_discount.bind('<Return>', lambda e: self.add_medicine())

        self.add_btn = ttk.Button(mf, text="Add Medicine", command=self.add_medicine)
        self.add_btn.grid(row=8, column=2, columnspan=2, padx=10, pady=5)

    # ── qty fields ────────────────────────────────────────────────────────

    def _create_tablet_qty_fields(self):
        for w in self.qty_frame.winfo_children():
            w.destroy()
        ttk.Label(self.qty_frame, text="Strips (Qty):").grid(row=0, column=0, padx=5, pady=5)
        self.stripes = ttk.Entry(self.qty_frame, width=10)
        self.stripes.grid(row=0, column=1, padx=5, pady=5)
        self.stripes.bind('<Return>', lambda e: self.tablets_per_stripe.focus())

        ttk.Label(self.qty_frame, text="Tablets/Strip:").grid(row=0, column=2, padx=5, pady=5)
        self.tablets_per_stripe = ttk.Entry(self.qty_frame, width=10)
        self.tablets_per_stripe.grid(row=0, column=3, padx=5, pady=5)
        self.tablets_per_stripe.bind('<Return>', lambda e: self.free_stripes.focus())

        ttk.Label(self.qty_frame, text="Free Strips:").grid(row=0, column=4, padx=5, pady=5)
        self.free_stripes = ttk.Entry(self.qty_frame, width=10)
        self.free_stripes.grid(row=0, column=5, padx=5, pady=5)
        self.free_stripes.insert(0, "0")
        self.free_stripes.bind('<Return>', lambda e: self.hsn_code.focus())
        self._bind_qty_nav()

    def _create_other_qty_fields(self):
        for w in self.qty_frame.winfo_children():
            w.destroy()
        med_type = self.medicine_type.get().lower()
        is_vial  = (med_type == 'injection - vial')

        ttk.Label(self.qty_frame, text="Pack Size (e.g. 500ml):").grid(row=0, column=0, padx=5, pady=5)
        self.quantity = ttk.Entry(self.qty_frame, width=12)
        self.quantity.grid(row=0, column=1, padx=5, pady=5)
        self.quantity.bind('<Return>', lambda e: self.units.focus())

        if med_type == 'vaccine':
            self.vaccine_unit_var = tk.StringVar(value='ml')
            ttk.Combobox(self.qty_frame, textvariable=self.vaccine_unit_var,
                         values=['ml', 'Doses'], width=7, state='readonly'
                         ).grid(row=0, column=2, padx=(0, 10), pady=5)

        ttk.Label(self.qty_frame,
                  text="Vials (Qty)" if is_vial else "Units (Qty)"
                  ).grid(row=0, column=3, padx=5, pady=5)
        self.units = ttk.Entry(self.qty_frame, width=10)
        self.units.grid(row=0, column=4, padx=5, pady=5)
        self.units.bind('<Return>', lambda e: self.free_items.focus())

        ttk.Label(self.qty_frame,
                  text="Free Vials" if is_vial else "Free Units"
                  ).grid(row=0, column=5, padx=5, pady=5)
        self.free_items = ttk.Entry(self.qty_frame, width=10)
        self.free_items.grid(row=0, column=6, padx=5, pady=5)
        self.free_items.insert(0, "0")
        self.free_items.bind('<Return>', lambda e: self.hsn_code.focus())
        self._bind_qty_nav()

    def _uses_strip_qty(self, med_type=None):
        med_type = med_type or self.medicine_type.get()
        return is_strip_count_type(med_type, self._sched_unit.get(med_type, ''))

    def on_type_change(self, event=None):
        med_type = self.medicine_type.get()
        if self._uses_strip_qty(med_type):
            self._create_tablet_qty_fields()
            if med_type.lower() == 'bolus':
                self.tablets_per_stripe.delete(0, tk.END)
                self.tablets_per_stripe.insert(0, "1")
        else:
            self._create_other_qty_fields()
        self._apply_type_qty_default(med_type)

    def _apply_type_qty_default(self, med_type):
        default = self._type_qty.get(med_type, 0)
        if not default:
            return
        val = str(default)
        try:
            if self._uses_strip_qty(med_type):
                if hasattr(self, 'stripes') and self.stripes.winfo_exists():
                    if not self.stripes.get().strip():
                        self.stripes.delete(0, tk.END); self.stripes.insert(0, val)
            else:
                if hasattr(self, 'units') and self.units.winfo_exists():
                    if not self.units.get().strip():
                        self.units.delete(0, tk.END); self.units.insert(0, val)
                unit_label = get_type_measure_unit(med_type)
                if unit_label and hasattr(self, 'quantity') and self.quantity.winfo_exists():
                    if not self.quantity.get().strip():
                        self.quantity.delete(0, tk.END); self.quantity.insert(0, unit_label)
        except Exception:
            pass

    def focus_qty_field(self):
        med_type = self.medicine_type.get()
        if self._uses_strip_qty(med_type):
            if not (hasattr(self, 'stripes') and self.stripes.winfo_exists()):
                self._create_tablet_qty_fields()
            self.stripes.focus()
        else:
            if not (hasattr(self, 'quantity') and self.quantity.winfo_exists()):
                self._create_other_qty_fields()
            self.quantity.focus()

    def _get_auto_unit(self):
        med_type = self.medicine_type.get()
        if med_type.lower() == 'vaccine' and hasattr(self, 'vaccine_unit_var'):
            return self.vaccine_unit_var.get()
        if self._uses_strip_qty(med_type):
            return ''
        layout_unit = get_type_measure_unit(med_type)
        if layout_unit:
            return layout_unit
        return TYPE_UNITS.get(med_type.lower(), '')

    # ── items treeview ────────────────────────────────────────────────────

    def _build_items_tree(self, parent):
        items_frame = ttk.LabelFrame(parent, text="Purchase Items")
        items_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 2))
        self.items_frame = items_frame

        self._all_columns = tuple(all_column_names('purchase'))
        widths = {'Medicine':120,'Type':70,'Batch':70,'Expiry':65,'Qty':50,
                  'Pack':48,'HSN':52,'Free':45,'Rate':65,'Disc%':45,'GST%':45,
                  'Taxable':70,'GST Amt':65,'Amount':75}
        self.items_tree = ttk.Treeview(items_frame, columns=self._all_columns,
                                       show='headings', height=PURCHASE_ROWS,
                                       style='Large.Treeview')
        for col in self._all_columns:
            self.items_tree.heading(col, text=col)
            self.items_tree.column(col, width=widths.get(col, 70))
        apply_column_visibility(self.items_tree, 'purchase', self._all_columns)

        sb = ttk.Scrollbar(items_frame, orient=tk.VERTICAL, command=self.items_tree.yview)
        self.items_tree.configure(yscrollcommand=sb.set)
        self.items_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self.context_menu = tk.Menu(self.parent, tearoff=0)
        if not getattr(self, '_items_tree_keys_wired', False):
            from core.tree_action_menu import setup_tree_actions
            setup_tree_actions(
                self.parent,
                self.items_tree,
                [
                    ("Edit Item", self.edit_selected_item),
                    ("Remove Item", self.remove_selected_item),
                ],
                on_double=self.edit_selected_item,
                on_delete=lambda e: self.remove_selected_item(),
                escape_to=self.medicine_name.entry,
            )
            self._items_tree_keys_wired = True

    # ── summary / payment section ─────────────────────────────────────────

    def _build_summary(self, parent):
        tf = ttk.LabelFrame(parent, text="Purchase Summary")
        tf.pack(fill=tk.X, pady=5)

        # ── Row 0: Subtotal | CGST | SGST | Total Amount | Total Due | [Clear] ──
        display_row0 = [
            ("Subtotal:",      'subtotal_var',     None),
            ("CGST:",          'cgst_var',         None),
            ("SGST:",          'sgst_var',         None),
            ("Total Amount:",  'total_amount_var', 'bold'),
        ]
        for col, (lbl, attr, weight) in enumerate(display_row0):
            ttk.Label(tf, text=lbl).grid(row=0, column=col*2, sticky=tk.W, padx=4, pady=2)
            var = tk.StringVar(value="0.00")
            setattr(self, attr, var)
            kw = {'font': (FONT_FAMILY, FONT_SIZE_LABELS, weight or 'normal')}
            ttk.Label(tf, textvariable=var, **kw).grid(
                row=0, column=col*2+1, sticky=tk.W, padx=4, pady=2)

        ttk.Label(tf, text="Total Due:",
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(
            row=0, column=8, sticky=tk.W, padx=8, pady=2)
        self.total_due_var = tk.StringVar(value="0.00")
        self.due_label = ttk.Label(tf, textvariable=self.total_due_var,
                                   font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold'))
        self.due_label.grid(row=0, column=9, sticky=tk.W, padx=4, pady=2)

        action_col = ttk.Frame(tf)
        action_col.grid(row=0, column=10, rowspan=4, padx=10, pady=2, sticky=tk.N)
        self.clear_btn = ttk.Button(action_col, text="Clear", command=self.clear_form)
        self.clear_btn.pack(fill=tk.X, pady=(0, 4))
        self.save_btn = ttk.Button(action_col, text="Save Purchase (F5)", command=self.save_purchase)
        try:
            self.save_btn.configure(bootstyle='primary')
        except Exception:
            pass
        self.save_btn.pack(fill=tk.X, pady=(0, 4))
        self.import_bill_btn = ttk.Button(
            action_col, text="Import Purchase (Shift+F2)",
            command=self.open_import_purchase_bill,
        )
        self.import_bill_btn.pack(fill=tk.X)

        # ── Row 1: Need to Pay | Final Amount | Current Credit ────────────
        ttk.Label(tf, text="Need to Pay:",
                  font=(FONT_FAMILY, FONT_SIZE_LABELS)).grid(
            row=1, column=0, sticky=tk.W, padx=4, pady=2)
        self.need_to_pay_var = tk.StringVar(value="0.00")
        ttk.Label(tf, textvariable=self.need_to_pay_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS)).grid(
            row=1, column=1, sticky=tk.W, padx=4, pady=2)

        ttk.Label(tf, text="Final Amount:",
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(row=1, column=6, sticky=tk.W, padx=8, pady=2)
        self.final_amount_var = tk.StringVar(value="0.00")
        ttk.Label(tf, textvariable=self.final_amount_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(row=1, column=7, sticky=tk.W, padx=4, pady=2)

        ttk.Label(tf, text="Current Credit:",
                  font=(FONT_FAMILY, FONT_SIZE_LABELS)).grid(
            row=1, column=8, sticky=tk.W, padx=8, pady=2)
        self.current_credit_var = tk.StringVar(value="0.00")
        ttk.Label(tf, textvariable=self.current_credit_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS)).grid(row=1, column=9, sticky=tk.W, padx=4, pady=2)

        # ── Row 2: Overall Disc % | Overall Disc ₹ | Rounding | Prev Due | Prev Credit | Amount Paid | [Save] ──
        ttk.Label(tf, text="Overall Disc %:").grid(row=2, column=0, sticky=tk.W, padx=4, pady=2)
        self.overall_discount_pct = ttk.Entry(tf, width=7)
        self.overall_discount_pct.grid(row=2, column=1, padx=4, pady=2)
        self.overall_discount_pct.insert(0, "0")
        self.overall_discount_pct.bind('<FocusIn>', lambda e: e.widget.select_range(0, tk.END))

        ttk.Label(tf, text="Overall Disc ₹:").grid(row=2, column=2, sticky=tk.W, padx=4, pady=2)
        self.overall_discount = ttk.Entry(tf, width=8)
        self.overall_discount.grid(row=2, column=3, padx=4, pady=2)
        self.overall_discount.insert(0, "0")
        self.overall_discount.bind('<FocusIn>', lambda e: e.widget.select_range(0, tk.END))

        ttk.Label(tf, text="Rounding:").grid(row=2, column=4, sticky=tk.W, padx=4, pady=2)
        self.rounding_entry = ttk.Entry(tf, width=8)
        self.rounding_entry.grid(row=2, column=5, padx=4, pady=2)
        self.rounding_entry.insert(0, "0.00")
        self.rounding_entry.bind('<FocusIn>', lambda e: e.widget.select_range(0, tk.END))

        # Previous Due — read-only display
        ttk.Label(tf, text="Prev Due:").grid(row=2, column=6, sticky=tk.W, padx=4, pady=2)
        self.previous_due_var = tk.StringVar(value="0.00")
        ttk.Label(tf, textvariable=self.previous_due_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS),
                  width=8, anchor='w').grid(
            row=2, column=7, padx=4, pady=2)

        # Previous Credit — read-only display
        ttk.Label(tf, text="Prev Credit:").grid(row=2, column=8, sticky=tk.W, padx=4, pady=2)
        self.previous_credit_var = tk.StringVar(value="0.00")
        ttk.Label(tf, textvariable=self.previous_credit_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS),
                  width=8, anchor='w').grid(
            row=2, column=9, padx=4, pady=2)

        ttk.Label(tf, text="Amount Paid:").grid(row=3, column=0, sticky=tk.W, padx=4, pady=2)
        self.amount_paid = ttk.Entry(tf, width=10)
        self.amount_paid.grid(row=3, column=1, padx=4, pady=2)
        self.amount_paid.insert(0, "0.00")
        self.amount_paid.bind('<FocusIn>', lambda e: e.widget.select_range(0, tk.END))

        self.import_bill_info_var = tk.StringVar(value="")
        ttk.Label(
            tf,
            textvariable=self.import_bill_info_var,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=920,
            justify=tk.LEFT,
        ).grid(row=4, column=0, columnspan=12, sticky=tk.W, padx=6, pady=(0, 4))

        # Trigger recalculation on any input change
        for w in (self.overall_discount_pct, self.overall_discount,
                  self.rounding_entry, self.amount_paid):
            w.bind('<KeyRelease>', self.calculate_total)
        self.overall_discount_pct.bind(
            '<FocusOut>',
            lambda e: (self.sync_overall_discount_fields('pct'), self.calculate_total()),
        )
        self.overall_discount.bind(
            '<FocusOut>',
            lambda e: (self.sync_overall_discount_fields('rupees'), self.calculate_total()),
        )

    # ── tree helpers ──────────────────────────────────────────────────────

    def show_context_menu(self, event):
        if self.items_tree.selection():
            self.context_menu.post(event.x_root, event.y_root)

    def _format_unit_display(self, item):
        qv = str(item.get('quantity_value', '') or '').strip()
        if qv and any(sep in qv for sep in ('*', 'x', 'X', '×')):
            return qv
        if is_strip_count_type(item['type'], self._sched_unit.get(item['type'], '')):
            tps = item.get('tablets_per_stripe', 1)
            try:
                return f"{int(float(tps))}'S"
            except (ValueError, TypeError):
                return str(tps)
        return qv

    def _purchase_row_values(self, item):
        disc = item.get("discount_display")
        if not disc:
            if float(item.get("discount_pct", 0) or 0) > 0:
                disc = f"{item.get('discount_pct', 0):.1f}%"
            elif float(item.get("disc_column_value", 0) or 0) > 0:
                disc = f"₹{float(item.get('disc_column_value', 0)):.2f}"
            else:
                disc = "0"
        pack_val = (item.get("pack") or "").strip()
        if not pack_val:
            pack_val = self._format_unit_display(item)
        return {
            "Medicine": item.get("name", ""),
            "Type": item.get("type", ""),
            "Batch": item.get("batch", ""),
            "Expiry": item.get("expiry", ""),
            "Qty": f"{float(item.get('qty', 0) or 0):.1f}",
            "Pack": pack_val,
            "HSN": item.get("hsn_code", "") or "",
            "Free": f"{float(item.get('free_qty', 0) or 0):.1f}",
            "Rate": f"{float(item.get('rate', 0) or 0):.2f}",
            "Disc%": disc,
            "GST%": f"{float(item.get('gst_pct', 0) or 0):.1f}%",
            "Taxable": f"{float(item.get('taxable', 0) or 0):.2f}",
            "GST Amt": f"{float(item.get('gst_amt', 0) or 0):.2f}",
            "Amount": f"{float(item.get('item_amount', 0) or 0):.2f}",
        }

    def update_items_tree(self):
        for row in self.items_tree.get_children():
            self.items_tree.delete(row)
        for item in self.purchase_items:
            row = self._purchase_row_values(item)
            self.items_tree.insert(
                '', tk.END,
                values=tuple(row.get(col, "") for col in self._all_columns),
            )
        n = len(self.purchase_items)
        if n > PURCHASE_ROWS:
            hint = f"Purchase Items ({n} items — scroll down to see all)"
        elif n:
            hint = f"Purchase Items ({n} item{'s' if n != 1 else ''})"
        else:
            hint = "Purchase Items"
        if hasattr(self, 'items_frame'):
            self.items_frame.configure(text=hint)
