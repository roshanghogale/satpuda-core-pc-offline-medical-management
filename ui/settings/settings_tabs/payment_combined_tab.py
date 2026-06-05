"""Supplier + Customer payment on one Settings tab (ledger-style toggle)."""

import tkinter as tk

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from ui.settings.settings_tabs.payment_tab import PaymentTab
from ui.settings.settings_tabs.customer_payment_tab import CustomerPaymentTab


class PaymentCombinedTab:
    def __init__(self, notebook, conn):
        self.conn = conn
        self._active = None
        outer = ttk.Frame(notebook)
        notebook.add(outer, text="Payment")

        btn_bar = ttk.Frame(outer)
        btn_bar.pack(fill=tk.X, padx=10, pady=10)
        try:
            self._btn_supplier = ttk.Button(
                btn_bar,
                text="Supplier Payment",
                command=lambda: self._show("supplier"),
                bootstyle="primary",
                width=22,
            )
            self._btn_customer = ttk.Button(
                btn_bar,
                text="Customer Payment",
                command=lambda: self._show("customer"),
                bootstyle="success",
                width=22,
            )
        except Exception:
            self._btn_supplier = ttk.Button(
                btn_bar, text="Supplier Payment",
                command=lambda: self._show("supplier"), width=22,
            )
            self._btn_customer = ttk.Button(
                btn_bar, text="Customer Payment",
                command=lambda: self._show("customer"), width=22,
            )
        self._btn_supplier.pack(side=tk.LEFT, padx=8)
        self._btn_customer.pack(side=tk.LEFT, padx=8)

        self._host = ttk.Frame(outer)
        self._host.pack(fill=tk.BOTH, expand=True)
        self._supplier_host = ttk.Frame(self._host)
        self._customer_host = ttk.Frame(self._host)

        self._supplier = PaymentTab(conn, parent=self._supplier_host)
        self._customer = CustomerPaymentTab(conn, parent=self._customer_host)
        self._show("supplier")

    def _show(self, which: str):
        self._supplier_host.pack_forget()
        self._customer_host.pack_forget()
        if which == "customer":
            self._customer_host.pack(fill=tk.BOTH, expand=True)
            self._active = self._customer
        else:
            self._supplier_host.pack(fill=tk.BOTH, expand=True)
            self._active = self._supplier
        try:
            self._btn_supplier.configure(
                bootstyle="primary" if which == "supplier" else "secondary"
            )
            self._btn_customer.configure(
                bootstyle="success" if which == "customer" else "secondary"
            )
        except Exception:
            pass
