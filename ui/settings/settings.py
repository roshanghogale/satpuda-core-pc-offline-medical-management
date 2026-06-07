import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from ui.settings.settings_tabs.pharmacy_tab   import PharmacyTab
from ui.settings.settings_tabs.contacts_tab   import ContactsTab
from ui.settings.settings_tabs.layout_tab     import LayoutTab
from ui.settings.settings_tabs.database_tab   import DatabaseTab
from ui.settings.settings_tabs.payment_combined_tab import PaymentCombinedTab
from ui.settings.settings_tabs.ledger_tab     import LedgerTab
from ui.settings.settings_tabs.misc_tabs      import ShortcutsTab
from ui.settings.settings_tabs.import_tab     import ImportTab


class SettingsPage:
    def __init__(self, parent, conn):
        self.conn   = conn
        self.parent = parent

        notebook = ttk.Notebook(parent)
        notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self._notebook = notebook

        root = parent.winfo_toplevel()

        # ── Tabs (each owns its own UI + logic) ───────────────────────────
        self._pharmacy   = PharmacyTab(notebook, conn)
        self._contacts   = ContactsTab(notebook, conn)

        # Shelf Management — delegates to existing ShelfManagementPage
        shelf_frame = ttk.Frame(notebook)
        notebook.add(shelf_frame, text="Shelf Management")
        from ui.shared.shelf_management import ShelfManagementPage
        ShelfManagementPage(shelf_frame, conn)

        self._layout     = LayoutTab(notebook, root, conn=conn)
        self._import     = ImportTab(notebook, conn, parent)
        self._database   = DatabaseTab(notebook, conn, parent)
        self._payment    = PaymentCombinedTab(notebook, conn)
        self._ledger     = LedgerTab(notebook, conn)
        self._shortcuts  = ShortcutsTab(notebook)

        for tab in (self._pharmacy, self._contacts, self._layout, self._import, self._database,
                    self._payment, self._ledger):
            tab._keyboard_refresh = self.refresh_keyboard_bindings

        from core.settings_section_nav import bind_settings_page_keys
        parent.after(100, lambda: bind_settings_page_keys(self))

        # Ctrl+Tab navigation
        parent.after(200, lambda: self._setup_notebook_nav(notebook))
        self._tab_objects = {
            'Pharmacy Profile': self._pharmacy,
            'Contacts': self._contacts,
            'Shelf Management': None,
            'Appearance': self._layout,
            'Import': self._import,
            'Management': self._database,
            'Payment': self._payment,
            'Ledger': self._ledger,
            '⌨ Shortcuts': self._shortcuts,
        }

    def refresh_keyboard_bindings(self):
        from core.keyboard_registry import KeyboardRegistry, PageBindings
        try:
            tab_id = self._notebook.select()
            tab_text = self._notebook.tab(tab_id, 'text')
            tab_widget = self._notebook.nametowidget(tab_id)
        except Exception:
            return
        obj = self._tab_objects.get(tab_text)
        if obj and hasattr(obj, 'get_keyboard_bindings'):
            bindings = obj.get_keyboard_bindings()
            KeyboardRegistry.register_page(tab_widget, bindings)
        elif tab_text == 'Appearance' and hasattr(self._layout, 'get_keyboard_bindings'):
            bindings = self._layout.get_keyboard_bindings()
            KeyboardRegistry.register_page(tab_widget, bindings)
        else:
            bindings = PageBindings(page_id='settings')
            KeyboardRegistry.register_page(tab_widget, bindings)
        KeyboardRegistry.clear_sidebar_nav_mode()

    def _current_tab_object(self):
        try:
            tab_text = self._notebook.tab(self._notebook.select(), 'text')
            return self._tab_objects.get(tab_text)
        except Exception:
            return None

    def focus_active_tab_sidebar(self) -> bool:
        """F4 — focus section sidebar on the active settings tab."""
        obj = self._current_tab_object()
        if obj is None:
            return False
        buttons = getattr(obj, '_section_buttons', None)
        if not buttons:
            return False
        focus_fn = getattr(obj, '_focus_sidebar', None)
        if callable(focus_fn):
            focus_fn()
            return True
        from core.settings_section_nav import focus_settings_sidebar
        focus_settings_sidebar(obj)
        return True

    # ── Keyboard navigation ───────────────────────────────────────────────

    def open_contacts(self, subtab: str = "Customers"):
        """Open Settings → Contacts → section (Doctors / Customers / Suppliers)."""
        nb = self._notebook
        for i in range(nb.index("end")):
            if nb.tab(i, "text") == "Contacts":
                nb.select(i)
                break
        if hasattr(self, "_contacts"):
            self._contacts.select_subtab(subtab)

    def open_management(self, section: str = "updates"):
        """Open Settings → Management → section (updates / export / backup / admin / danger)."""
        nb = self._notebook
        for i in range(nb.index("end")):
            if nb.tab(i, "text") == "Management":
                nb.select(i)
                break
        if hasattr(self, "_database"):
            self._database.show_section(section)

    def open_import(self, section: str = "purchase_bill"):
        """Open Settings → Import → section."""
        nb = self._notebook
        for i in range(nb.index("end")):
            if nb.tab(i, "text") == "Import":
                nb.select(i)
                break
        if hasattr(self, "_import"):
            self._import.show_section(section)

    def _setup_notebook_nav(self, notebook):
        def _next(e):
            notebook.select((notebook.index('current') + 1) % notebook.index('end'))
            return 'break'
        def _prev(e):
            notebook.select((notebook.index('current') - 1) % notebook.index('end'))
            return 'break'
        root = self.parent.winfo_toplevel()
        root.bind('<Control-Tab>',       _next, add='+')
        root.bind('<Control-Shift-Tab>', _prev, add='+')

    # ── Public helpers kept for backward compat (main.py export buttons) ─

    def export_sales(self):
        self._database.export_sales()

    def export_purchases(self):
        self._database.export_purchases()

    def export_inventory(self):
        self._database.export_inventory()

    def export_all(self):
        self._database.export_all()
