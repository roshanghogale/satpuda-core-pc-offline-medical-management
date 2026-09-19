"""
ui/billing_nav.py
──────────────────
Arrow-key navigation mixin for BillingPage.
"""
import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.scroll_manager import scroll_to_widget
from core.focus_chain import wire_focus_ring


class BillingNavMixin:

    def _bill_date_entry(self):
        if hasattr(self.bill_date, 'entry'):
            return self.bill_date.entry
        return self.bill_date

    def _scroll_to_widget(self, widget):
        if hasattr(self, '_inner_frame'):
            scroll_to_widget(self._inner_frame, widget)

    def _setup_arrow_nav(self):
        if getattr(self, '_arrow_nav_ready', False):
            return
        nav = []
        if getattr(self, '_sales_payment_mode_enabled', lambda: True)() and not getattr(
            self, '_sales_payment_after_bill_date', lambda: False
        )():
            nav.append(self.payment_mode)
        nav.extend([
            self.customer_name.entry,
            self.customer_phone,
            self.customer_address.entry,
            self.doctor_name.entry,
            self.doctor_phone,
            self._bill_date_entry(),
        ])
        if getattr(self, '_sales_payment_mode_enabled', lambda: True)() and getattr(
            self, '_sales_payment_after_bill_date', lambda: False
        )():
            nav.append(self.payment_mode)
        nav.extend([
            self.medicine_combo.step1_entry,
            self.quantity,
            self.medicine_discount,
            self.add_medicine_btn,
            self.discount_pct,
            self.discount,
            self.rounding,
            self.cash_paid,
            self.online_paid,
            self.generate_btn,
            self.clear_btn,
            self.print_sales_1_btn,
            self.print_sales_2_btn,
        ])
        wire_focus_ring(nav, scroll_to=self._scroll_to_widget, updown_indices=list(range(1, len(nav))))
        self._wire_action_button_returns()

        from core.tree_action_menu import setup_tree_actions
        setup_tree_actions(
            self.parent,
            self.medicine_tree,
            [
                ("Edit Quantity", self.edit_quantity),
                ("Delete Line", self._delete_selected_medicine),
            ],
            on_double=self.edit_quantity,
            on_delete=lambda e: self._delete_selected_medicine(),
            escape_to=self.medicine_combo.step1_entry,
        )

        self._bind_end_to_payment()
        self._arrow_nav_ready = True

    def _wire_action_button_returns(self):
        """Enter activates Save / Clear / Print buttons in the action row."""
        for btn in (
            self.generate_btn,
            self.clear_btn,
            self.print_sales_1_btn,
            self.print_sales_2_btn,
        ):
            def _activate(_event=None, b=btn):
                b.invoke()
                return 'break'
            btn.bind('<Return>', _activate, add='+')
            btn.bind('<KP_Enter>', _activate, add='+')

    def _focus_overall_discount(self, event=None):
        try:
            self.discount_pct.focus_set()
            self.discount_pct.select_range(0, tk.END)
            self._scroll_to_widget(self.discount_pct)
        except Exception:
            pass
        return 'break'

    def _focus_payment_field(self, event=None):
        try:
            self.cash_paid.focus_set()
            self.cash_paid.select_range(0, tk.END)
            self._scroll_to_widget(self.cash_paid)
        except Exception:
            pass
        return 'break'

    def _bind_end_to_payment(self):
        skip = {self.cash_paid, self.online_paid}

        def handler(event):
            return self._focus_payment_field()

        for w in [
            self.customer_name.entry, self.customer_phone, self.customer_address.entry,
            self.doctor_name.entry, self.doctor_phone, self._bill_date_entry(),
            self.medicine_combo.step1_entry, self.quantity, self.medicine_discount,
            self.add_medicine_btn, self.discount_pct, self.discount, self.rounding,
            self.generate_btn, self.clear_btn, self.print_sales_1_btn, self.print_sales_2_btn,
        ]:
            if w in skip:
                continue
            try:
                if w.winfo_exists():
                    w.bind('<End>', handler, add='+')
            except Exception:
                pass
