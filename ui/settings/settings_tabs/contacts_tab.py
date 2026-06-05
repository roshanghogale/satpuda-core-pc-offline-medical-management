"""Settings → Contacts: Doctors, Customers, and Suppliers in one tab."""
import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from ui.settings.settings_tabs.doctors_tab import DoctorsTab
from ui.settings.settings_tabs.suppliers_tab import SuppliersTab
from ui.shared.customers import CustomersPage


class ContactsTab:
    def __init__(self, notebook, conn):
        outer = ttk.Frame(notebook)
        notebook.add(outer, text="Contacts")

        inner = ttk.Notebook(outer)
        inner.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        doctors_frame = ttk.Frame(inner)
        inner.add(doctors_frame, text="Doctors")
        self.doctors = DoctorsTab(doctors_frame, conn)

        customers_frame = ttk.Frame(inner)
        inner.add(customers_frame, text="Customers")
        self.customers = CustomersPage(customers_frame, conn)

        suppliers_frame = ttk.Frame(inner)
        inner.add(suppliers_frame, text="Suppliers")
        self.suppliers = SuppliersTab(suppliers_frame, conn)

        self._inner_notebook = inner

    def select_subtab(self, name: str):
        """Switch inner tab to Doctors, Customers, or Suppliers."""
        for i in range(self._inner_notebook.index("end")):
            if self._inner_notebook.tab(i, "text") == name:
                self._inner_notebook.select(i)
                break
