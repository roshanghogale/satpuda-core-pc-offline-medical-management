"""Settings -> Sales & Billing."""
from __future__ import annotations

import os
import tkinter as tk

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.font_config import FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT
from core.themed_messagebox import showinfo, showwarning
from ui.settings.settings_tabs.appearance_scroll import AppearanceScrollPane
from core.settings_section_nav import wire_settings_section_nav, bindings_for_sectioned_tab

_NAV = [
    ("billing_layout", "Billing Layout & FY"),
    ("batch_picker", "Batch Picker"),
    ("sales_return", "Sales Return"),
    ("sales_bills", "Sales Bill Save"),
    ("upi_payment", "UPI Payment QR"),
    ("autosave", "Autosave"),
]


class SalesBillingTab:
    TAB_NAME = "Sales & Billing"

    def __init__(self, notebook, conn, parent_widget, host=None):
        self.conn = conn
        self._parent = parent_widget
        self._panels = {}
        self._nav_buttons = {}

        outer = host if host is not None else ttk.Frame(notebook)
        self.outer = outer
        if host is None and notebook is not None:
            notebook.add(outer, text=self.TAB_NAME)

        shell = ttk.Frame(outer)
        shell.pack(fill=tk.BOTH, expand=True)

        nav_outer = ttk.LabelFrame(shell, text="Sections")
        nav_outer.pack(side=tk.LEFT, fill=tk.Y, padx=(8, 4), pady=8)
        nav_scroll = ttk.Frame(nav_outer)
        nav_scroll.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        for sid, label in _NAV:
            btn = ttk.Button(
                nav_scroll, text=label, width=22,
                command=lambda k=sid: self._show_section(k),
            )
            btn.pack(fill=tk.X, pady=2)
            self._nav_buttons[sid] = btn

        right = ttk.Frame(shell)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 8), pady=8)
        self._scroller = AppearanceScrollPane(right)
        self._content_host = self._scroller.frame
        self._build_all_panels()
        self._show_section("billing_layout")
        wire_settings_section_nav(
            self, self._nav_buttons, [s[0] for s in _NAV], self._show_section)
        outer.after_idle(lambda: (self._scroller.bind_wheel_recursive(), self._scroller.refresh()))

    def get_keyboard_bindings(self):
        return bindings_for_sectioned_tab(self)

    def _panel(self, section_id):
        if section_id not in self._panels:
            frame = ttk.Frame(self._content_host)
            self._panels[section_id] = frame
        return self._panels[section_id]

    def show_section(self, section_id: str):
        """Public API for voice / open_settings navigation."""
        # Legacy voice/nav id
        if section_id == "sales_screen":
            section_id = "billing_layout"
        self._show_section(section_id)

    def _show_section(self, section_id):
        if section_id == "sales_screen":
            section_id = "billing_layout"
        for frame in self._panels.values():
            frame.pack_forget()
        frame = self._panel(section_id)
        frame.pack(fill=tk.BOTH, expand=True)
        for key, btn in self._nav_buttons.items():
            try:
                btn.configure(bootstyle="primary" if key == section_id else "secondary")
            except Exception:
                pass

    def _build_all_panels(self):
        self._build_billing_layout_panel()
        self._build_batch_picker_panel()
        self._build_sales_return_panel()
        self._build_sales_bills_panel()
        self._build_upi_payment_panel()
        self._build_autosave_panel()

    def _build_billing_layout_panel(self):
        frame = self._panel("billing_layout")
        from core.billing_layout_prefs import (
            ITEM_DISCOUNT_PERCENT,
            ITEM_DISCOUNT_RUPEES,
            MARGIN_DISPLAY_PERCENT,
            MARGIN_DISPLAY_RUPEES,
            item_discount_mode_label,
            load_billing_layout_prefs,
            margin_display_mode_label,
            save_billing_layout_prefs,
        )
        from core.history_prefs import current_fy_label, history_scope_label
        from core.sales_form_prefs import (
            POSITION_AFTER_BILL_DATE,
            POSITION_FIRST,
            payment_mode_position_label,
        )

        prefs = load_billing_layout_prefs(self.conn)

        fy = ttk.LabelFrame(frame, text="Financial Year (Sales / Purchase History)")
        fy.pack(fill=tk.X, padx=10, pady=10)
        ttk.Label(
            fy,
            text=f"Ongoing financial year: {current_fy_label()} "
                 f"(1 Apr – 31 Mar). History filters reset to this when you "
                 f"return to Sales / Purchase History.",
            wraplength=620,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(anchor=tk.W, padx=12, pady=(10, 6))
        self._history_scope_var = tk.StringVar(value=str(prefs.get("history_scope") or "current_fy"))
        ttk.Radiobutton(
            fy,
            text=history_scope_label("current_fy"),
            value="current_fy",
            variable=self._history_scope_var,
        ).pack(anchor=tk.W, padx=16, pady=2)
        ttk.Radiobutton(
            fy,
            text=history_scope_label("all"),
            value="all",
            variable=self._history_scope_var,
        ).pack(anchor=tk.W, padx=16, pady=(2, 10))

        pay = ttk.LabelFrame(frame, text="Cash / Due (Payment Mode) position")
        pay.pack(fill=tk.X, padx=10, pady=6)
        self._sales_payment_enabled_var = tk.BooleanVar(
            value=bool(prefs.get("payment_mode_enabled", True)))
        ttk.Checkbutton(
            pay,
            text="Show Payment Mode field (Cash / Due) on Sales page",
            variable=self._sales_payment_enabled_var,
            command=self._update_sales_payment_position_state,
        ).pack(anchor=tk.W, padx=12, pady=(10, 6))
        pos_row = ttk.Frame(pay)
        pos_row.pack(fill=tk.X, padx=12, pady=4)
        ttk.Label(pos_row, text="Payment field position:").pack(side=tk.LEFT)
        self._sales_payment_position_combo = ttk.Combobox(
            pos_row,
            values=(
                payment_mode_position_label(POSITION_FIRST),
                payment_mode_position_label(POSITION_AFTER_BILL_DATE),
            ),
            state="readonly",
            width=42,
        )
        self._sales_payment_position_combo.set(
            payment_mode_position_label(
                str(prefs.get("payment_mode_position") or POSITION_FIRST)))
        self._sales_payment_position_combo.pack(side=tk.LEFT, padx=8)
        self._update_sales_payment_position_state()

        disc = ttk.LabelFrame(frame, text="Discount per medicine")
        disc.pack(fill=tk.X, padx=10, pady=6)
        ttk.Label(
            disc,
            text="Show or hide the per-line discount field on Sales, and choose "
                 "whether it is entered in rupees or as a percentage of the line "
                 "amount. Stored bills always keep the discount in ₹.",
            wraplength=620,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(anchor=tk.W, padx=12, pady=(10, 6))
        self._item_discount_show_var = tk.BooleanVar(
            value=bool(prefs.get("billing_show_item_discount", True)))
        ttk.Checkbutton(
            disc,
            text="Show per-medicine discount field on Sales / Edit bill",
            variable=self._item_discount_show_var,
            command=self._update_item_discount_mode_state,
        ).pack(anchor=tk.W, padx=16, pady=2)
        ttk.Label(disc, text="Discount entry mode:").pack(anchor=tk.W, padx=16, pady=(6, 2))
        self._item_discount_mode_combo = ttk.Combobox(
            disc,
            values=(
                item_discount_mode_label(ITEM_DISCOUNT_RUPEES),
                item_discount_mode_label(ITEM_DISCOUNT_PERCENT),
            ),
            state="readonly",
            width=28,
        )
        self._item_discount_mode_combo.set(
            item_discount_mode_label(
                str(prefs.get("item_discount_mode") or ITEM_DISCOUNT_RUPEES)))
        self._item_discount_mode_combo.pack(anchor=tk.W, padx=16, pady=(0, 10))
        self._update_item_discount_mode_state()

        margin = ttk.LabelFrame(frame, text="Margin column (Sales screen only)")
        margin.pack(fill=tk.X, padx=10, pady=6)
        ttk.Label(
            margin,
            text="Margin is for the Sales screen while entering a bill only. "
                 "It is never printed on the GST invoice.",
            wraplength=620,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(anchor=tk.W, padx=12, pady=(10, 6))
        self._margin_col_var = tk.BooleanVar(
            value=bool(prefs.get("billing_show_margin_column", True)))
        ttk.Checkbutton(
            margin,
            text="Show margin column per medicine on Sales / Edit bill",
            variable=self._margin_col_var,
        ).pack(anchor=tk.W, padx=16, pady=2)
        ttk.Label(margin, text="Margin display:").pack(anchor=tk.W, padx=16, pady=(6, 2))
        self._margin_display_combo = ttk.Combobox(
            margin,
            values=(
                margin_display_mode_label(MARGIN_DISPLAY_RUPEES),
                margin_display_mode_label(MARGIN_DISPLAY_PERCENT),
            ),
            state="readonly",
            width=28,
        )
        self._margin_display_combo.set(
            margin_display_mode_label(
                str(prefs.get("billing_margin_display_mode") or MARGIN_DISPLAY_RUPEES)))
        self._margin_display_combo.pack(anchor=tk.W, padx=16, pady=(0, 6))
        ttk.Label(
            margin,
            text="Percentage shows margin as % of MRP line value (like Disc %).",
            wraplength=600,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(anchor=tk.W, padx=16, pady=(0, 4))
        self._total_margin_var = tk.BooleanVar(
            value=bool(prefs.get("billing_show_total_margin", True)))
        ttk.Checkbutton(
            margin,
            text="Show total margin in billing summary",
            variable=self._total_margin_var,
        ).pack(anchor=tk.W, padx=16, pady=2)
        self._margin_warn_var = tk.BooleanVar(
            value=bool(prefs.get("billing_margin_loss_warning", True)))
        ttk.Checkbutton(
            margin,
            text="Warn when discount is larger than margin",
            variable=self._margin_warn_var,
        ).pack(anchor=tk.W, padx=16, pady=(2, 10))

        doctor = ttk.LabelFrame(frame, text="Doctor name on Sales")
        doctor.pack(fill=tk.X, padx=10, pady=6)
        ttk.Label(
            doctor,
            text="H1 and X always require a doctor name. Uncheck to allow saving "
                 "other schedules (H, G, C, …) without a doctor.",
            wraplength=620,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(anchor=tk.W, padx=12, pady=(10, 6))
        self._require_doctor_other_var = tk.BooleanVar(
            value=bool(prefs.get("require_doctor_for_other_schedules", True)))
        ttk.Checkbutton(
            doctor,
            text="Require doctor name for schedules other than H1 and X",
            variable=self._require_doctor_other_var,
        ).pack(anchor=tk.W, padx=16, pady=(0, 10))

        ttk.Button(
            frame,
            text="Save Billing Layout & FY",
            command=self._save_billing_layout,
        ).pack(anchor=tk.W, padx=12, pady=12)

    def _update_sales_payment_position_state(self):
        state = "readonly" if self._sales_payment_enabled_var.get() else "disabled"
        try:
            self._sales_payment_position_combo.configure(state=state)
        except Exception:
            pass

    def _update_item_discount_mode_state(self):
        state = "readonly" if self._item_discount_show_var.get() else "disabled"
        try:
            self._item_discount_mode_combo.configure(state=state)
        except Exception:
            pass

    def _save_billing_layout(self):
        from core.billing_layout_prefs import (
            ITEM_DISCOUNT_PERCENT,
            ITEM_DISCOUNT_RUPEES,
            MARGIN_DISPLAY_PERCENT,
            MARGIN_DISPLAY_RUPEES,
            item_discount_mode_label,
            margin_display_mode_label,
            save_billing_layout_prefs,
        )
        from core.sales_form_prefs import (
            POSITION_AFTER_BILL_DATE,
            POSITION_FIRST,
            payment_mode_position_label,
        )
        label = (self._sales_payment_position_combo.get() or "").strip()
        if label == payment_mode_position_label(POSITION_AFTER_BILL_DATE):
            pos = POSITION_AFTER_BILL_DATE
        else:
            pos = POSITION_FIRST
        disc_label = (self._item_discount_mode_combo.get() or "").strip()
        if disc_label == item_discount_mode_label(ITEM_DISCOUNT_PERCENT):
            disc_mode = ITEM_DISCOUNT_PERCENT
        else:
            disc_mode = ITEM_DISCOUNT_RUPEES
        margin_label = (self._margin_display_combo.get() or "").strip()
        if margin_label == margin_display_mode_label(MARGIN_DISPLAY_PERCENT):
            margin_mode = MARGIN_DISPLAY_PERCENT
        else:
            margin_mode = MARGIN_DISPLAY_RUPEES
        save_billing_layout_prefs(
            {
                "history_scope": str(self._history_scope_var.get() or "current_fy"),
                "payment_mode_enabled": bool(self._sales_payment_enabled_var.get()),
                "payment_mode_position": pos,
                "item_discount_mode": disc_mode,
                "billing_show_item_discount": bool(self._item_discount_show_var.get()),
                "billing_show_margin_column": bool(self._margin_col_var.get()),
                "billing_show_total_margin": bool(self._total_margin_var.get()),
                "billing_margin_loss_warning": bool(self._margin_warn_var.get()),
                "billing_margin_display_mode": margin_mode,
                "require_doctor_for_other_schedules": bool(
                    self._require_doctor_other_var.get()
                ),
            },
            self.conn,
        )
        showinfo(
            "Billing Layout & FY",
            "Settings saved to this PC and server (when Online). "
            "Re-open Sales to apply layout changes.",
            parent=self._parent,
        )

    def _build_batch_picker_panel(self):
        frame = self._panel("batch_picker")
        sf = ttk.LabelFrame(frame, text="Medicine Batch Picker (Sales)")
        sf.pack(fill=tk.X, padx=10, pady=10)
        from core.sales_medicine_prefs import (
            load_batch_sort_order,
            save_batch_sort_order,
            batch_sort_label,
            batch_sort_from_label,
            BATCH_NEWEST_FIRST,
            BATCH_OLDEST_FIRST,
            load_show_zero_stock_in_sales,
            save_show_zero_stock_in_sales,
        )
        ttk.Label(
            sf,
            text="When a medicine has multiple batches, list order in the sales dropdown:",
            wraplength=560,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(anchor=tk.W, padx=12, pady=(10, 6))
        row = ttk.Frame(sf)
        row.pack(fill=tk.X, padx=12, pady=4)
        self._batch_sort_combo = ttk.Combobox(
            row,
            values=(batch_sort_label(BATCH_OLDEST_FIRST), batch_sort_label(BATCH_NEWEST_FIRST)),
            state="readonly",
            width=48,
        )
        self._batch_sort_combo.set(batch_sort_label(load_batch_sort_order()))
        self._batch_sort_combo.pack(side=tk.LEFT)
        self._show_zero_stock_var = tk.BooleanVar(value=load_show_zero_stock_in_sales())
        ttk.Checkbutton(
            sf,
            text="Show zero-stock medicines in every batch (Sales dropdown)",
            variable=self._show_zero_stock_var,
        ).pack(anchor=tk.W, padx=12, pady=8)
        ttk.Label(
            sf,
            text="Single-batch medicines are auto-selected (no picker shown).",
            foreground="#666",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(anchor=tk.W, padx=12, pady=(0, 4))

        def _save():
            save_batch_sort_order(batch_sort_from_label(self._batch_sort_combo.get()))
            save_show_zero_stock_in_sales(bool(self._show_zero_stock_var.get()))
            showinfo("Batch Picker", "Batch picker settings saved.", parent=self._parent)

        ttk.Button(sf, text="Save Batch Picker Settings", command=_save).pack(
            anchor=tk.W, padx=12, pady=12)

    def _build_sales_return_panel(self):
        frame = self._panel("sales_return")
        sf = ttk.LabelFrame(frame, text="Sales Return Lookup")
        sf.pack(fill=tk.X, padx=10, pady=10)
        from core.sales_return_prefs import (
            load_sales_return_lookup_days,
            save_sales_return_lookup_days,
            DEFAULT_LOOKUP_DAYS,
            MIN_LOOKUP_DAYS,
            MAX_LOOKUP_DAYS,
        )
        self._sales_return_days_var = tk.StringVar(value=str(load_sales_return_lookup_days()))
        ttk.Label(sf, text="Show sales bills from last (days):").pack(anchor=tk.W, padx=12, pady=(10, 4))
        ttk.Entry(sf, textvariable=self._sales_return_days_var, width=8).pack(anchor=tk.W, padx=12)
        ttk.Label(
            sf,
            text=f"({MIN_LOOKUP_DAYS}-{MAX_LOOKUP_DAYS}, default {DEFAULT_LOOKUP_DAYS})",
            foreground="#666",
        ).pack(anchor=tk.W, padx=12, pady=4)

        def _save():
            try:
                days = int((self._sales_return_days_var.get() or "").strip())
            except ValueError:
                showwarning("Invalid", "Enter a whole number of days.", parent=self._parent)
                return
            saved = save_sales_return_lookup_days(days)
            self._sales_return_days_var.set(str(saved))
            showinfo("Saved", f"Sales Return will search the last {saved} day(s).", parent=self._parent)

        ttk.Button(sf, text="Save Sales Return Settings", command=_save).pack(
            anchor=tk.W, padx=12, pady=12)

    def _build_sales_bills_panel(self):
        frame = self._panel("sales_bills")
        sf = ttk.LabelFrame(frame, text="Sales Bill Save")
        sf.pack(fill=tk.X, padx=10, pady=10)
        from core.bill_save_prefs import (
            load_sales_bill_save_dir,
            load_pdf_save_layout,
            save_sales_bill_save_dir,
            save_pdf_save_layout,
            pdf_save_layout_combo_values,
            pdf_save_layout_from_label,
            pdf_save_layout_label,
        )
        from core.bill_output import default_downloads_directory
        layout_row = ttk.Frame(sf)
        layout_row.pack(fill=tk.X, padx=12, pady=(10, 4))
        ttk.Label(
            layout_row,
            text="Bill layout for F5 save uses Print Sales 1 (Pharmacy Profile → Print Sales Buttons).",
            wraplength=520,
        ).pack(anchor=tk.W)
        ttk.Label(
            layout_row,
            text="Optional legacy PDF layout (only if Print Sales 1 is A5 and you need a different save shape):",
        ).pack(anchor=tk.W, pady=(6, 2))
        self._pdf_save_layout_combo = ttk.Combobox(
            layout_row,
            values=pdf_save_layout_combo_values(),
            state="readonly",
            width=48,
        )
        self._pdf_save_layout_combo.set(pdf_save_layout_label(load_pdf_save_layout()))
        self._pdf_save_layout_combo.pack(side=tk.LEFT, padx=8)
        path_row = ttk.Frame(sf)
        path_row.pack(fill=tk.X, padx=12, pady=4)
        ttk.Label(path_row, text="Save folder:").pack(side=tk.LEFT)
        self._sales_bill_dir_var = tk.StringVar(value=load_sales_bill_save_dir())
        ttk.Entry(path_row, textvariable=self._sales_bill_dir_var, width=52).pack(
            side=tk.LEFT, padx=8, fill=tk.X, expand=True)

        def _browse():
            from tkinter import filedialog
            initial = (self._sales_bill_dir_var.get() or "").strip() or default_downloads_directory()
            chosen = filedialog.askdirectory(
                title="Choose folder for saved sales bill PDFs",
                initialdir=initial,
                parent=self._parent,
            )
            if chosen:
                self._sales_bill_dir_var.set(chosen)

        ttk.Button(path_row, text="Browse...", command=_browse).pack(side=tk.LEFT)

        def _save():
            try:
                saved = save_sales_bill_save_dir((self._sales_bill_dir_var.get() or "").strip())
            except ValueError as exc:
                showwarning("Sales Bill Save", str(exc), parent=self._parent)
                return
            self._sales_bill_dir_var.set(saved)
            label = (self._pdf_save_layout_combo.get() or "").strip()
            save_pdf_save_layout(pdf_save_layout_from_label(label))
            showinfo("Sales Bill Save", "Settings saved.", parent=self._parent)

        ttk.Button(sf, text="Save Sales Bill Settings", command=_save).pack(
            anchor=tk.W, padx=12, pady=12)

    def _build_upi_payment_panel(self):
        frame = self._panel("upi_payment")
        sf = ttk.LabelFrame(frame, text="UPI Payment QR on Bill")
        sf.pack(fill=tk.X, padx=10, pady=10)
        from core.upi_prefs import (
            load_upi_id,
            load_upi_qr_amount_mode,
            load_upi_qr_enabled,
            upi_amount_mode_label,
            AMOUNT_TOTAL,
            AMOUNT_TOTAL_PLUS_PREV_DUE,
            save_upi_id,
            save_upi_qr_enabled,
            save_upi_qr_amount_mode,
            upi_amount_mode_from_label,
        )
        self._upi_qr_enabled_var = tk.BooleanVar(value=load_upi_qr_enabled())
        ttk.Checkbutton(sf, text="Show UPI QR on bill", variable=self._upi_qr_enabled_var).pack(
            anchor=tk.W, padx=12, pady=(10, 6))
        self._upi_id_var = tk.StringVar(value=load_upi_id())
        ttk.Entry(sf, textvariable=self._upi_id_var, width=42).pack(anchor=tk.W, padx=12, pady=4)
        self._upi_amount_combo = ttk.Combobox(
            sf,
            values=(
                upi_amount_mode_label(AMOUNT_TOTAL),
                upi_amount_mode_label(AMOUNT_TOTAL_PLUS_PREV_DUE),
            ),
            state="readonly",
            width=36,
        )
        self._upi_amount_combo.set(upi_amount_mode_label(load_upi_qr_amount_mode()))
        self._upi_amount_combo.pack(anchor=tk.W, padx=12, pady=4)

        def _save():
            if self._upi_qr_enabled_var.get() and not (self._upi_id_var.get() or "").strip():
                showwarning("UPI ID Required", "Enter your UPI ID first.", parent=self._parent)
                return
            save_upi_qr_enabled(bool(self._upi_qr_enabled_var.get()))
            save_upi_id((self._upi_id_var.get() or "").strip())
            save_upi_qr_amount_mode(upi_amount_mode_from_label(self._upi_amount_combo.get()))
            showinfo("UPI Settings", "UPI settings saved.", parent=self._parent)

        ttk.Button(sf, text="Save UPI Settings", command=_save).pack(anchor=tk.W, padx=12, pady=12)

    def _build_autosave_panel(self):
        frame = self._panel("autosave")
        sf = ttk.LabelFrame(frame, text="Sales & Purchase Autosave")
        sf.pack(fill=tk.X, padx=10, pady=10)
        from core.autosave_prefs import (
            load_autosave_enabled,
            load_autosave_interval_seconds,
            save_autosave_enabled,
            save_autosave_interval_seconds,
            DEFAULT_INTERVAL_SECONDS,
        )
        self._autosave_enabled_var = tk.BooleanVar(value=load_autosave_enabled())
        ttk.Checkbutton(
            sf,
            text="Auto-save in-progress sales and purchases",
            variable=self._autosave_enabled_var,
        ).pack(anchor=tk.W, padx=12, pady=(10, 6))
        self._autosave_interval_var = tk.StringVar(value=str(load_autosave_interval_seconds()))
        ttk.Entry(sf, textvariable=self._autosave_interval_var, width=10).pack(
            anchor=tk.W, padx=12, pady=4)
        ttk.Label(
            sf,
            text=f"Interval in seconds (30-3600, default {DEFAULT_INTERVAL_SECONDS})",
            foreground="#666",
        ).pack(anchor=tk.W, padx=12, pady=4)

        def _save():
            save_autosave_enabled(bool(self._autosave_enabled_var.get()))
            try:
                secs = int((self._autosave_interval_var.get() or "").strip())
            except ValueError:
                showwarning("Autosave", "Enter a whole number of seconds.", parent=self._parent)
                return
            save_autosave_interval_seconds(secs)
            self._autosave_interval_var.set(str(secs))
            showinfo("Autosave", f"Autosave settings saved ({secs}s).", parent=self._parent)

        ttk.Button(sf, text="Save Autosave Settings", command=_save).pack(
            anchor=tk.W, padx=12, pady=12)
