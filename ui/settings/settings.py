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
from core.scroll_manager import make_scrollable


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

        # Import (purchase bills + mobile JSON)
        import_outer = ttk.Frame(notebook)
        notebook.add(import_outer, text="Import")
        self._build_import_tab(import_outer)

        self._database   = DatabaseTab(notebook, conn, parent)
        self._payment    = PaymentCombinedTab(notebook, conn)
        self._ledger     = LedgerTab(notebook, conn)
        self._shortcuts  = ShortcutsTab(notebook)

        # Ctrl+Tab navigation
        parent.after(200, lambda: self._setup_notebook_nav(notebook))

    # ── Import tab (purchase bills + mobile) ──────────────────────────────

    def _build_import_tab(self, outer):
        from ui.shared.import_purchases import ImportPurchasesPage
        from ui.shared.import_from_mobile import ImportFromMobilePage

        frame = make_scrollable(outer)

        bill_bar = ttk.LabelFrame(frame, text="Import Purchase Bill")
        bill_bar.pack(fill=tk.X, padx=10, pady=(10, 4))
        ttk.Button(
            bill_bar,
            text="Import Purchase Bill",
            command=self._open_import_purchase_bill,
        ).pack(side=tk.LEFT, padx=8, pady=8)
        ttk.Label(bill_bar,
            text="Pick a PDF, CSV or Excel bill — the Purchase page opens with rows filled in for you to verify and save.",
            foreground='gray',
        ).pack(side=tk.LEFT, padx=8, pady=8)

        web_bar = ttk.Frame(frame)
        web_bar.pack(fill=tk.X, padx=10, pady=(4, 4))
        from core.font_config import FONT_FAMILY, FONT_SIZE_LABELS, FONT_SIZE_SUPPORTING_TEXT
        ttk.Label(web_bar, text="Web Purchase Entry:",
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(web_bar, text="🌐 Open Web Purchase Entry",
                   command=self._open_web_purchase).pack(side=tk.LEFT)
        from core.alert_colors import get_muted_color
        ttk.Label(web_bar, text="Enter purchases in the browser, copy JSON, paste below.",
                  font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
                  foreground=get_muted_color()).pack(side=tk.LEFT, padx=12)
        ttk.Separator(frame, orient='horizontal').pack(fill=tk.X, padx=10, pady=4)
        ImportPurchasesPage(frame, self.conn)

        ttk.Separator(frame, orient='horizontal').pack(fill=tk.X, padx=10, pady=12)
        mobile_lf = ttk.LabelFrame(frame, text="Import from Mobile")
        mobile_lf.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))
        ImportFromMobilePage(mobile_lf, self.conn)

    def _open_import_purchase_bill(self):
        root = self.parent.winfo_toplevel()
        app = getattr(root, '_main_app', None)
        purchase_page = None
        if app is not None and hasattr(app, 'open_purchase'):
            try:
                if hasattr(app, 'nav_click') and 'Purchase' in getattr(app, 'nav_buttons', {}):
                    app.nav_click(app.open_purchase, 'Purchase')
                else:
                    app.open_purchase()
                purchase_page = getattr(app, '_purchase_page', None)
            except Exception as e:
                from tkinter import messagebox
                messagebox.showerror(
                    "Import Purchase Bill",
                    f"Could not open the Purchase page:\n{e}",
                    parent=root,
                )
                return

        if purchase_page is None:
            from tkinter import messagebox
            messagebox.showerror(
                "Import Purchase Bill",
                "Purchase page is not available. Open Purchase once, then try again.",
                parent=root,
            )
            return

        from core.purchase_import_flow import import_purchase_bill_direct
        import_purchase_bill_direct(root, purchase_page)

    def _open_web_purchase(self):
        import sys
        import os
        import webbrowser
        import shutil
        from tkinter import messagebox
        from core.web_purchase_server import (
            start_web_purchase_server, stop_web_purchase_server,
            get_api_base_url, write_runtime_catalog,
        )

        web_root = None
        if getattr(sys, 'frozen', False):
            dst_dir = os.path.join(
                os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
                'VeterinaryApp', 'web_app',
            )
            os.makedirs(dst_dir, exist_ok=True)
            src = os.path.join(sys._MEIPASS, 'web_app', 'index.html')
            dst = os.path.join(dst_dir, 'index.html')
            try:
                shutil.copy2(src, dst)
                src_meds = os.path.join(sys._MEIPASS, 'web_app', 'medicines.json')
                if os.path.isfile(src_meds):
                    shutil.copy2(src_meds, os.path.join(dst_dir, 'medicines.json'))
                src_assets = os.path.join(sys._MEIPASS, 'web_app', 'assets')
                dst_assets = os.path.join(dst_dir, 'assets')
                if os.path.isdir(src_assets):
                    if os.path.exists(dst_assets):
                        shutil.rmtree(dst_assets)
                    shutil.copytree(src_assets, dst_assets)
            except Exception as e:
                messagebox.showerror('Web App Error', f'Could not extract web app:\n{e}')
                return
            web_root = dst_dir
        else:
            web_root = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                'web_app',
            )

        index = os.path.join(web_root, 'index.html')
        if not os.path.isfile(index):
            messagebox.showerror('Not Found', f'Web app not found at:\n{index}')
            return

        try:
            write_runtime_catalog(web_root, self.conn)
            stop_web_purchase_server()
            start_web_purchase_server(self.conn, web_root=web_root)
            url = get_api_base_url() + '/'
            webbrowser.open(url)
        except Exception as e:
            messagebox.showerror('Web App Error', f'Could not start web purchase server:\n{e}')

    # ── Keyboard navigation ───────────────────────────────────────────────

    def open_contacts(self, subtab: str = "Customers"):
        """Open Settings → Contacts → subtab (Doctors / Customers / Suppliers)."""
        nb = self._notebook
        for i in range(nb.index("end")):
            if nb.tab(i, "text") == "Contacts":
                nb.select(i)
                break
        if hasattr(self, "_contacts"):
            self._contacts.select_subtab(subtab)

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
