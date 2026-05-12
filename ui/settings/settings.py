import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from ui.settings.settings_tabs.pharmacy_tab   import PharmacyTab
from ui.settings.settings_tabs.doctors_tab    import DoctorsTab
from ui.settings.settings_tabs.suppliers_tab  import SuppliersTab
from ui.settings.settings_tabs.layout_tab     import LayoutTab
from ui.settings.settings_tabs.database_tab   import DatabaseTab
from ui.settings.settings_tabs.payment_tab    import PaymentTab
from ui.settings.settings_tabs.ledger_tab     import LedgerTab
from ui.settings.settings_tabs.misc_tabs      import ThresholdsTab, ShortcutsTab
from ui.settings.settings_tabs.customer_payment_tab import CustomerPaymentTab


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
        self._doctors    = DoctorsTab(notebook, conn)
        self._suppliers  = SuppliersTab(notebook, conn)

        # Shelf Management — delegates to existing ShelfManagementPage
        shelf_frame = ttk.Frame(notebook)
        notebook.add(shelf_frame, text="Shelf Management")
        from ui.shared.shelf_management import ShelfManagementPage
        ShelfManagementPage(shelf_frame, conn)

        self._layout     = LayoutTab(notebook, root)

        # Import Data tab
        import_frame = ttk.Frame(notebook)
        notebook.add(import_frame, text="📥 Import Data")
        self._build_import_tab(import_frame)

        # Import from Mobile tab
        mobile_frame = ttk.Frame(notebook)
        notebook.add(mobile_frame, text="📱 Import from Mobile")
        from ui.shared.import_from_mobile import ImportFromMobilePage
        ImportFromMobilePage(mobile_frame, conn)

        self._thresholds = ThresholdsTab(notebook, conn)
        self._database   = DatabaseTab(notebook, conn, parent)
        self._payment    = PaymentTab(notebook, conn)
        self._cust_pay   = CustomerPaymentTab(notebook, conn)
        self._ledger     = LedgerTab(notebook, conn)
        self._shortcuts  = ShortcutsTab(notebook)

        # Ctrl+Tab navigation
        parent.after(200, lambda: self._setup_notebook_nav(notebook))

    # ── Import Data tab (thin wrapper — delegates to ImportPurchasesPage) ─

    def _build_import_tab(self, frame):
        from ui.shared.import_purchases import ImportPurchasesPage
        import tkinter as tk
        try:
            import ttkbootstrap as ttk
        except ImportError:
            from tkinter import ttk

        web_bar = ttk.Frame(frame)
        web_bar.pack(fill=tk.X, padx=10, pady=(10, 4))
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

    def _open_web_purchase(self):
        import sys, os, webbrowser, shutil, json
        if getattr(sys, 'frozen', False):
            src = os.path.join(sys._MEIPASS, 'web_app', 'index.html')
            dst_dir = os.path.join(
                os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
                'VeterinaryApp', 'web_app')
            os.makedirs(dst_dir, exist_ok=True)
            dst = os.path.join(dst_dir, 'index.html')
            try:
                shutil.copy2(src, dst)
                # also copy assets folder
                src_assets = os.path.join(sys._MEIPASS, 'web_app', 'assets')
                dst_assets = os.path.join(dst_dir, 'assets')
                if os.path.isdir(src_assets):
                    if os.path.exists(dst_assets):
                        shutil.rmtree(dst_assets)
                    shutil.copytree(src_assets, dst_assets)
            except Exception as e:
                from tkinter import messagebox
                messagebox.showerror('Web App Error', f'Could not extract web app:\n{e}')
                return
            index = dst
        else:
            index = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                'web_app', 'index.html')
        if not os.path.exists(index):
            from tkinter import messagebox
            messagebox.showerror('Not Found', f'Web app not found at:\n{index}')
            return
        from core.layout_config import load_layout, _DEFAULT_SCHEDULES, _DEFAULT_MED_TYPES
        layout    = load_layout()
        schedules = layout.get('schedules', list(_DEFAULT_SCHEDULES))
        med_types = layout.get('med_types',  list(_DEFAULT_MED_TYPES))
        med_names = []
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='medicines_master'")
            if cur.fetchone():
                cur.execute("SELECT name FROM medicines_master ORDER BY name COLLATE NOCASE")
                med_names = [r[0] for r in cur.fetchall()]
        except Exception:
            pass
        # Write localStorage data into launcher.html and open index.html directly.
        # Browsers block file:// -> file:// redirects, so we write the data into
        # a launcher that uses a same-origin iframe trick — but the simplest reliable
        # approach is to write the script into a copy of index.html itself.
        launcher = os.path.join(os.path.dirname(index), 'launcher.html')
        inject = (
            f"<script>\n"
            f"localStorage.setItem('vet_schedules', JSON.stringify({json.dumps(schedules)}));\n"
            f"localStorage.setItem('vet_med_types', JSON.stringify({json.dumps(med_types)}));\n"
            f"localStorage.setItem('vet_medicine_names', JSON.stringify({json.dumps(med_names)}));\n"
            f"</script>\n"
        )
        try:
            with open(index, 'r', encoding='utf-8') as f:
                original = f.read()
            # Inject before </head> or at top if no </head>
            if '</head>' in original:
                patched = original.replace('</head>', inject + '</head>', 1)
            else:
                patched = inject + original
            with open(launcher, 'w', encoding='utf-8') as f:
                f.write(patched)
            webbrowser.open(f'file:///{launcher.replace(os.sep, "/")}')
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror('Web App Error', f'Could not open web app:\n{e}')

    # ── Keyboard navigation ───────────────────────────────────────────────

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
