"""
ui/billing_nav.py
──────────────────
Arrow-key navigation mixin for BillingPage.
No UI building, no DB, no calculations.
"""
import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.scroll_manager import scroll_to_widget


class BillingNavMixin:

    def _scroll_to_widget(self, widget):
        if hasattr(self, '_inner_frame'):
            scroll_to_widget(self._inner_frame, widget)

    def _setup_arrow_nav(self):
        nav = [
            self.customer_name.entry,        # 0
            self.customer_phone,             # 1
            self.customer_address,           # 2
            self.doctor_name.entry,          # 3
            self.doctor_phone,               # 4
            self.medicine_combo.step1_entry, # 5
            self.quantity,                   # 6
            self.medicine_discount,          # 7
            self.discount,                   # 8
            self.rounding,                   # 9
            self.cash_paid,                  # 10
            self.online_paid,                # 11
            self.clear_btn,                  # 12
            self.generate_btn,               # 13
        ]
        n = len(nav)
        updown = {0, 1, 2, 4, 6, 7, 8, 9, 10, 11, 12, 13}

        def make_next(i):
            def handler(event):
                if event.keysym == 'Right':
                    try:
                        w = event.widget
                        if w.get() and w.index(tk.INSERT) < len(w.get()):
                            return None
                    except Exception:
                        pass
                target = nav[(i + 1) % n]
                target.focus()
                self._scroll_to_widget(target)
                return 'break'
            return handler

        def make_prev(i):
            def handler(event):
                if event.keysym == 'Left':
                    try:
                        w = event.widget
                        if w.get() and w.index(tk.INSERT) > 0:
                            return None
                    except Exception:
                        pass
                target = nav[(i - 1) % n]
                target.focus()
                self._scroll_to_widget(target)
                return 'break'
            return handler

        for i, w in enumerate(nav):
            if i in updown:
                w.bind('<Up>',   make_prev(i), add='+')
                w.bind('<Down>', make_next(i), add='+')
            w.bind('<Left>',  make_prev(i), add='+')
            w.bind('<Right>', make_next(i), add='+')

        nav_ids = set(id(w) for w in nav)
        self.parent.winfo_toplevel().bind(
            '<FocusIn>',
            lambda e: self._scroll_to_widget(e.widget) if id(e.widget) in nav_ids else None,
            add='+')

        self.customer_name.entry.bind(
            '<Escape>', lambda e: self.customer_name.hide_list(), add='+')
        self.doctor_name.entry.bind(
            '<Escape>', lambda e: self.doctor_name.hide_list(), add='+')
