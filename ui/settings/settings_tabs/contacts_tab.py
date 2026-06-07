"""Settings → Contacts: Doctors, Customers, and Suppliers with section navigation."""
import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from ui.settings.settings_tabs.doctors_tab import DoctorsTab
from ui.settings.settings_tabs.suppliers_tab import SuppliersTab
from ui.shared.customers import CustomersPage
from ui.settings.settings_tabs.appearance_scroll import AppearanceScrollPane
from core.settings_section_nav import wire_settings_section_nav, bindings_for_sectioned_tab


_NAV_SECTIONS = [
    ('doctors',   'Doctors'),
    ('customers', 'Customers'),
    ('suppliers', 'Suppliers'),
]


class ContactsTab:
    TAB_NAME = 'Contacts'

    def __init__(self, notebook, conn):
        self.conn = conn
        self._panels = {}
        self._nav_buttons = {}

        outer = ttk.Frame(notebook)
        self.outer = outer
        notebook.add(outer, text=self.TAB_NAME)

        shell = ttk.Frame(outer)
        shell.pack(fill=tk.BOTH, expand=True)

        nav_outer = ttk.LabelFrame(shell, text="Sections")
        nav_outer.pack(side=tk.LEFT, fill=tk.Y, padx=(8, 4), pady=8)
        nav_scroll = ttk.Frame(nav_outer)
        nav_scroll.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        for section_id, label in _NAV_SECTIONS:
            btn = ttk.Button(
                nav_scroll, text=label, width=22,
                command=lambda k=section_id: self._show_section(k),
            )
            btn.pack(fill=tk.X, pady=2)
            self._nav_buttons[section_id] = btn

        right_col = ttk.Frame(shell)
        right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 8), pady=8)
        self._scroller = AppearanceScrollPane(right_col)
        self._content_host = self._scroller.frame

        self._build_doctors_panel()
        self._build_customers_panel()
        self._build_suppliers_panel()
        self._active_section = 'doctors'
        self._show_section('doctors')
        wire_settings_section_nav(
            self, self._nav_buttons, [s[0] for s in _NAV_SECTIONS], self._show_section)

    def get_keyboard_bindings(self):
        def _first():
            if self._active_section == 'customers' and hasattr(self, 'customers'):
                self.customers.search_entry.focus()
            elif self._active_section == 'suppliers' and hasattr(self, 'suppliers'):
                self.suppliers.supplier_name.focus_set()
            elif hasattr(self, 'doctors'):
                self.doctors.doctor_name.focus_set()

        return bindings_for_sectioned_tab(
            self, first_focus=_first, f2_target=self._f2_target)

    def _f2_target(self):
        from core.focus_chain import focus_tree
        if self._active_section == 'customers':
            return focus_tree(self.customers.tree)
        if self._active_section == 'suppliers':
            return focus_tree(self.suppliers.tree)
        if hasattr(self, 'doctors'):
            return focus_tree(self.doctors.tree)
        return False

    def _panel(self, section_id):
        wrapper = ttk.Frame(self._content_host)
        self._panels[section_id] = wrapper
        return wrapper

    def _show_section(self, section_id):
        if section_id not in self._panels:
            return
        self._active_section = section_id
        for frame in self._panels.values():
            frame.pack_forget()
        panel = self._panels[section_id]
        panel.pack(fill=tk.BOTH, expand=True)

        def _after_show():
            self._scroller.bind_wheel_recursive()
            self._scroller.refresh()
            self._scroller.scroll_to_top()

        panel.after_idle(_after_show)
        refresh = getattr(self, '_keyboard_refresh', None)
        if callable(refresh):
            refresh()
        for key, btn in self._nav_buttons.items():
            try:
                btn.configure(bootstyle='primary' if key == section_id else 'secondary')
            except Exception:
                pass

    def select_subtab(self, name: str):
        """Switch section — Doctors, Customers, or Suppliers."""
        mapping = {
            'Doctors': 'doctors',
            'Customers': 'customers',
            'Suppliers': 'suppliers',
        }
        self._show_section(mapping.get(name, 'doctors'))

    def _build_doctors_panel(self):
        frame = self._panel('doctors')
        self.doctors = DoctorsTab(frame, self.conn, embedded=True)

    def _build_customers_panel(self):
        frame = self._panel('customers')
        self.customers = CustomersPage(frame, self.conn, embedded=True)

    def _build_suppliers_panel(self):
        frame = self._panel('suppliers')
        self.suppliers = SuppliersTab(frame, self.conn, embedded=True)
