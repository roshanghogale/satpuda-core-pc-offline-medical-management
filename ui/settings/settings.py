import tkinter as tk

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from ui.settings.settings_tabs.pharmacy_tab import PharmacyTab
from ui.settings.settings_tabs.contacts_tab import ContactsTab
from ui.settings.settings_tabs.layout_tab import LayoutTab
from ui.settings.settings_tabs.database_tab import DatabaseTab
from ui.settings.settings_tabs.payment_combined_tab import PaymentCombinedTab
from ui.settings.settings_tabs.ledger_tab import LedgerTab
from ui.settings.settings_tabs.misc_tabs import ShortcutsTab


class _LazyTabHost:
    """Placeholder notebook tab that builds its real content on first visit."""

    def __init__(self, notebook, tab_name: str, factory):
        self._notebook = notebook
        self.tab_name = tab_name
        self._factory = factory
        self._built = False
        self._instance = None
        self.outer = ttk.Frame(notebook)
        notebook.add(self.outer, text=tab_name)
        self._placeholder = ttk.Label(
            self.outer,
            text=f"Loading {tab_name}…",
            anchor="center",
        )
        self._placeholder.pack(expand=True)

    def ensure_loaded(self):
        if self._built:
            return self._instance
        self._placeholder.destroy()
        self._instance = self._factory(self.outer)
        self._built = True
        if hasattr(self._instance, "_keyboard_refresh"):
            pass
        return self._instance

    def __getattr__(self, name):
        if self._built and self._instance is not None:
            return getattr(self._instance, name)
        raise AttributeError(name)


class SettingsPage:
    _LAZY_TABS = frozenset({
        "Contacts",
        "Shelf Management",
        "Sales & Billing",
        "Import",
        "Alert & Monitoring",
        "Data & System",
        "Management",
        "Payment",
        "Ledger",
        "Reorder",
    })

    def __init__(self, parent, conn):
        self.conn = conn
        self.parent = parent

        notebook = ttk.Notebook(parent)
        notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self._notebook = notebook
        self._lazy_hosts: dict[str, _LazyTabHost] = {}

        root = parent.winfo_toplevel()

        self._pharmacy = PharmacyTab(notebook, conn)
        self._contacts_host = self._add_lazy_tab(
            "Contacts",
            lambda parent: self._build_contacts_tab(parent),
        )
        self._shelf_host = self._add_lazy_tab(
            "Shelf Management",
            lambda parent: self._build_shelf_tab(parent),
        )

        self._layout = LayoutTab(notebook, root, conn=conn)

        self._sales_host = self._add_lazy_tab(
            "Sales & Billing",
            lambda parent: self._build_sales_tab(parent),
        )
        self._import_host = self._add_lazy_tab(
            "Import",
            lambda parent: self._build_import_tab(parent),
        )
        self._alerts_host = self._add_lazy_tab(
            "Alert & Monitoring",
            lambda parent: self._build_alerts_tab(parent),
        )
        self._database_host = self._add_lazy_tab(
            DatabaseTab.TAB_NAME,
            lambda parent: self._build_database_tab(parent),
        )
        self._payment_host = self._add_lazy_tab(
            "Payment",
            lambda parent: self._build_payment_tab(parent),
        )
        self._ledger_host = self._add_lazy_tab(
            "Ledger",
            lambda parent: self._build_ledger_tab(parent),
        )
        self._reorder_host = self._add_lazy_tab(
            "Reorder",
            lambda parent: self._build_reorder_tab(parent),
        )
        self._shortcuts = ShortcutsTab(notebook)

        self._pharmacy._keyboard_refresh = self.refresh_keyboard_bindings
        self._layout._keyboard_refresh = self.refresh_keyboard_bindings
        self._shortcuts._keyboard_refresh = self.refresh_keyboard_bindings

        notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed, add="+")

        from core.settings_section_nav import bind_settings_page_keys
        parent.after(100, lambda: bind_settings_page_keys(self))
        parent.after(200, lambda: self._setup_notebook_nav(notebook))

        self._tab_objects = {
            "Pharmacy Profile": self._pharmacy,
            "Contacts": self._contacts_host,
            "Shelf Management": self._shelf_host,
            "Appearance": self._layout,
            "Layout & Lists": self._layout,
            "Sales & Billing": self._sales_host,
            "Import": self._import_host,
            "Alert & Monitoring": self._alerts_host,
            "Data & System": self._database_host,
            "Management": self._database_host,
            "Payment": self._payment_host,
            "Ledger": self._ledger_host,
            "Reorder": self._reorder_host,
            "⌨ Shortcuts": self._shortcuts,
        }

    def _add_lazy_tab(self, tab_name: str, factory) -> _LazyTabHost:
        host = _LazyTabHost(self._notebook, tab_name, factory)
        self._lazy_hosts[tab_name] = host
        return host

    def _build_contacts_tab(self, parent):
        tab = ContactsTab(None, self.conn, host=parent)
        tab._keyboard_refresh = self.refresh_keyboard_bindings
        return tab

    def _build_shelf_tab(self, parent):
        from ui.shared.shelf_management import ShelfManagementPage
        return ShelfManagementPage(parent, self.conn)

    def _build_sales_tab(self, parent):
        from ui.settings.settings_tabs.sales_billing_tab import SalesBillingTab
        tab = SalesBillingTab(None, self.conn, self.parent, host=parent)
        tab._keyboard_refresh = self.refresh_keyboard_bindings
        return tab

    def _build_import_tab(self, parent):
        from ui.settings.settings_tabs.import_tab import ImportTab
        tab = ImportTab(None, self.conn, self.parent, host=parent)
        tab._keyboard_refresh = self.refresh_keyboard_bindings
        return tab

    def _build_alerts_tab(self, parent):
        from ui.settings.settings_tabs.alert_monitoring_tab import AlertMonitoringTab
        tab = AlertMonitoringTab(None, self.conn, self.parent, host=parent)
        tab._keyboard_refresh = self.refresh_keyboard_bindings
        return tab

    def _build_database_tab(self, parent):
        tab = DatabaseTab(None, self.conn, self.parent, host=parent)
        tab._keyboard_refresh = self.refresh_keyboard_bindings
        return tab

    def _build_payment_tab(self, parent):
        tab = PaymentCombinedTab(None, self.conn, host=parent)
        tab._keyboard_refresh = self.refresh_keyboard_bindings
        return tab

    def _build_ledger_tab(self, parent):
        tab = LedgerTab(None, self.conn, host=parent)
        tab._keyboard_refresh = self.refresh_keyboard_bindings
        return tab

    def _build_reorder_tab(self, parent):
        from ui.settings.settings_tabs.reorder_combined_tab import ReorderCombinedTab
        tab = ReorderCombinedTab(None, self.conn, self.parent, host=parent)
        tab._keyboard_refresh = self.refresh_keyboard_bindings
        return tab

    @property
    def _contacts(self):
        return self._contacts_host.ensure_loaded()

    @property
    def _payment(self):
        return self._payment_host.ensure_loaded()

    @property
    def _ledger(self):
        return self._ledger_host.ensure_loaded()

    @property
    def _sales(self):
        return self._sales_host.ensure_loaded()

    @property
    def _import(self):
        return self._import_host.ensure_loaded()

    @property
    def _alerts(self):
        return self._alerts_host.ensure_loaded()

    @property
    def _database(self):
        return self._database_host.ensure_loaded()

    @property
    def _reorder(self):
        return self._reorder_host.ensure_loaded()

    def _on_tab_changed(self, _event=None):
        try:
            tab_text = self._notebook.tab(self._notebook.select(), "text")
        except Exception:
            return
        if tab_text in ("Appearance", "Layout & Lists"):
            self._layout.on_top_level_tab_selected(tab_text)
        host = self._lazy_hosts.get(tab_text)
        if host is not None:
            host.ensure_loaded()
        self.refresh_keyboard_bindings()

    def _ensure_lazy(self, tab_name: str):
        host = self._lazy_hosts.get(tab_name)
        if host is not None:
            host.ensure_loaded()

    def refresh_keyboard_bindings(self):
        from core.keyboard_registry import KeyboardRegistry, PageBindings
        try:
            tab_id = self._notebook.select()
            tab_text = self._notebook.tab(tab_id, "text")
            tab_widget = self._notebook.nametowidget(tab_id)
        except Exception:
            return
        obj = self._tab_objects.get(tab_text)
        if isinstance(obj, _LazyTabHost):
            obj = obj.ensure_loaded()
        if obj and hasattr(obj, "get_keyboard_bindings"):
            bindings = obj.get_keyboard_bindings()
            KeyboardRegistry.register_page(tab_widget, bindings)
        elif tab_text in ("Appearance", "Layout & Lists") and hasattr(self._layout, "get_keyboard_bindings"):
            bindings = self._layout.get_keyboard_bindings()
            KeyboardRegistry.register_page(tab_widget, bindings)
        else:
            bindings = PageBindings(page_id="settings")
            KeyboardRegistry.register_page(tab_widget, bindings)
        KeyboardRegistry.clear_sidebar_nav_mode()

    def _current_tab_object(self):
        try:
            tab_text = self._notebook.tab(self._notebook.select(), "text")
            obj = self._tab_objects.get(tab_text)
            if isinstance(obj, _LazyTabHost):
                return obj.ensure_loaded()
            return obj
        except Exception:
            return None

    def focus_active_tab_sidebar(self) -> bool:
        obj = self._current_tab_object()
        if obj is None:
            return False
        buttons = getattr(obj, "_section_buttons", None)
        if not buttons:
            return False
        focus_fn = getattr(obj, "_focus_sidebar", None)
        if callable(focus_fn):
            focus_fn()
            return True
        from core.settings_section_nav import focus_settings_sidebar
        focus_settings_sidebar(obj)
        return True

    def _select_tab(self, tab_name: str) -> bool:
        nb = self._notebook
        for i in range(nb.index("end")):
            if nb.tab(i, "text") == tab_name:
                nb.select(i)
                self._ensure_lazy(tab_name)
                return True
        aliases = {
            "Management": DatabaseTab.TAB_NAME,
            "Appearance": LayoutTab.TAB_NAME,
        }
        alt = aliases.get(tab_name)
        if alt:
            return self._select_tab(alt)
        return False

    def open_contacts(self, subtab: str = "Customers"):
        self._select_tab("Contacts")
        if hasattr(self, "_contacts"):
            self._contacts.select_subtab(subtab)

    def open_management(self, section: str = "updates"):
        self._select_tab(DatabaseTab.TAB_NAME)
        self._database.show_section(section)

    def open_import(self, section: str = "purchase_bill"):
        self._select_tab("Import")
        self._import.show_section(section)

    def open_payment(self, which: str = "supplier"):
        self._select_tab("Payment")
        if hasattr(self._payment, "_show"):
            self._payment._show(which)

    def open_ledger(self, which: str = "supplier"):
        self._select_tab("Ledger")
        if hasattr(self._ledger, "_show"):
            self._ledger._show(which)

    def open_alerts(self, section: str | None = None):
        self._select_tab("Alert & Monitoring")
        if section and hasattr(self._alerts, "show_section"):
            self._alerts.show_section(section)
        if hasattr(self._alerts, "refresh"):
            self._alerts.refresh()

    def open_sales_billing(self, section: str = "billing_layout"):
        self._select_tab("Sales & Billing")
        if hasattr(self._sales, "show_section"):
            self._sales.show_section(section)

    def open_reorder_settings(self, which: str = "pending"):
        self._select_tab("Reorder")
        if hasattr(self._reorder, "_show"):
            self._reorder._show(which)

    def open_reorder(self, prefill=None, bulk: bool = False):
        self._select_tab("Reorder")
        if bulk:
            self._reorder.open_with_prefill({"bulk": True})
        elif prefill:
            self._reorder.open_with_prefill(prefill)
        else:
            self._reorder._show("pending")

    def _setup_notebook_nav(self, notebook):
        def _next(_e):
            notebook.select((notebook.index("current") + 1) % notebook.index("end"))
            return "break"

        def _prev(_e):
            notebook.select((notebook.index("current") - 1) % notebook.index("end"))
            return "break"

        root = self.parent.winfo_toplevel()
        root.bind("<Control-Tab>", _next, add="+")
        root.bind("<Control-Shift-Tab>", _prev, add="+")

    def export_sales(self):
        self._database.export_sales()

    def export_purchases(self):
        self._database.export_purchases()

    def export_inventory(self):
        self._database.export_inventory()

    def export_all(self):
        self._database.export_all()
