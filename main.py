# Suppress warnings at startup
import os, sys, shutil, warnings
os.environ['PYTHONWARNINGS'] = 'ignore'
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message=".*Task policy set failed.*")


def _setup_exe_environment():
    if not getattr(sys, 'frozen', False):
        return
    app_data = os.path.join(
        os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'VeterinaryApp')
    os.makedirs(app_data, exist_ok=True)
    base_dir = sys._MEIPASS
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)
    # Copy all config files to AppData on first run (or if missing)
    for fname in ('theme_config.txt', 'layout_config.txt', 'font_size.txt',
                  'expiry.dat', 'backup_creds.dat', 'sample_import.json',
                  'backup_config.dat', 'backup_slots.dat'):
        dst = os.path.join(app_data, fname)
        # Always overwrite expiry.dat from bundled version (demo enforcement)
        if fname == 'expiry.dat' or not os.path.exists(dst):
            # Try config/ subfolder first, then root of _MEIPASS
            src_config = os.path.join(base_dir, 'config', fname)
            src_root   = os.path.join(base_dir, fname)
            src = src_config if os.path.exists(src_config) else src_root
            if os.path.exists(src):
                shutil.copy2(src, dst)
            elif fname in ('theme_config.txt', 'font_size.txt', 'layout_config.txt'):
                defaults = {'theme_config.txt': 'superhero',
                            'font_size.txt': '10', 'layout_config.txt': '{}'}
                open(dst, 'w').write(defaults.get(fname, ''))
    os.chdir(app_data)

_setup_exe_environment()

# ── License / expiry checks ───────────────────────────────────────────────────
def _run_license_check():
    from core.license_manager import needs_activation, prepare_device_key
    if needs_activation():
        prepare_device_key()
        from widgets.activation_dialog import show_activation_dialog
        activated = [False]
        def _on(): activated[0] = True
        show_activation_dialog(_on)
        if not activated[0]:
            sys.exit(0)

def _run_expiry_check():
    from core.license_manager import check_expiry
    if check_expiry():
        from widgets.activation_dialog import show_activation_dialog
        activated = [False]
        def _on(): activated[0] = True
        show_activation_dialog(_on)
        if not activated[0]:
            sys.exit(0)

_run_license_check()
_run_expiry_check()

# ── Imports ───────────────────────────────────────────────────────────────────
import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

import sqlite3
from core.app_setup import (
    AVAILABLE_THEMES, load_theme, save_theme,
    create_window, set_window_icon, restart_app,
)
from core.db_setup import initialise as db_initialise
from core.input_controller import GlobalInputController


class VeterinaryManagementSystem:

    def __init__(self):
        self.available_themes = AVAILABLE_THEMES
        self.current_theme    = load_theme()

        self.root = create_window(self.current_theme)
        self.root.title("Satpuda Core — Billing. Management. Simplified.")
        set_window_icon(self.root)
        self.root.geometry("1200x800")
        self.root.state('zoomed')

        self._init_database()

        self._billing_page  = None
        self._purchase_page = None

        self._build_nav()

        self.root._input_ctrl = self.input_ctrl
        self.root._main_app   = self

        self._start_backup()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        # Auto-import medicines master in background if not yet done
        self.root.after(2000, self._auto_import_medicines_master)

    # ── Database ──────────────────────────────────────────────────────────

    def _init_database(self):
        if getattr(sys, 'frozen', False):
            db_path = os.path.join(os.path.dirname(sys.executable), 'veterinary.db')
        else:
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'veterinary.db')
        self.conn   = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()
        self.db_path = db_path
        db_initialise(self.conn)

    # kept for backward compat (settings delete-all calls this)
    def create_tables(self):
        db_initialise(self.conn)

    # ── Navigation bar ────────────────────────────────────────────────────

    def _build_nav(self):
        nav_frame = ttk.Frame(self.root)
        nav_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        self.nav_buttons = {}
        nav_items = [
            ("🏠 Home",          self.show_welcome),
            ("Sales",            self.open_billing),
            ("Purchase",         self.open_purchase),
            ("Inventory",        self.open_inventory),
            ("Sales History",    self.open_sales_history),
            ("Purchase History", self.open_purchase_history),
            ("Customers",        self.open_customers),
            ("Returns",          self.open_returns),
            ("Settings",         self.open_settings),
        ]
        for text, cmd in nav_items:
            try:
                btn = ttk.Button(nav_frame, text=text, width=18,
                                 bootstyle="outline-primary", style='Nav.TButton',
                                 command=lambda c=cmd, t=text: self.nav_click(c, t))
            except Exception:
                btn = ttk.Button(nav_frame, text=text, width=18, style='Nav.TButton',
                                 command=lambda c=cmd, t=text: self.nav_click(c, t))
            btn.pack(side=tk.LEFT, padx=5, pady=8)
            self.nav_buttons[text] = btn

        self.active_nav = None
        ttk.Separator(self.root, orient='horizontal').pack(fill=tk.X, padx=10, pady=5)

        self.main_frame = ttk.Frame(self.root)
        self.main_frame.pack(side="left", fill="both", expand=True, padx=10, pady=10)

        self.input_ctrl = GlobalInputController(self.root, self.main_frame)

        self._bind_page_keys()
        self.show_welcome()

    def nav_click(self, command, text):
        for btn in self.nav_buttons.values():
            try: btn.configure(bootstyle="outline-primary")
            except: btn.configure(style="Nav.TButton")
        try: self.nav_buttons[text].configure(bootstyle="primary")
        except: pass
        self.active_nav = text
        command()

    # ── Keyboard shortcuts ────────────────────────────────────────────────

    def _bind_page_keys(self):
        page_map = {
            '`': ('🏠 Home',          self.show_welcome),
            '1': ('Sales',            self.open_billing),
            '2': ('Purchase',         self.open_purchase),
            '3': ('Inventory',        self.open_inventory),
            '4': ('Sales History',    self.open_sales_history),
            '5': ('Purchase History', self.open_purchase_history),
            '6': ('Customers',        self.open_customers),
            '7': ('Settings',         self.open_settings),
            '8': ('Returns',          self.open_returns),
        }
        INPUT_CLASSES = ('Entry', 'TEntry', 'TCombobox', 'Text', 'Listbox')

        def _focused():
            try: return self.root.focus_get()
            except: return None

        def _input_focused():
            w = _focused()
            try: return w and w.winfo_class() in INPUT_CLASSES
            except: return False

        def _on_escape(event):
            w = _focused()
            if w is None: return None
            try:
                if w.winfo_class() not in INPUT_CLASSES: return None
            except: return None
            if self._try_close_dropdown(w): return 'break'
            try: self.main_frame.focus_set()
            except: self.root.focus_set()
            return 'break'

        self.root.bind('<Escape>', _on_escape, add='+')

        def make_handler(nav_text, cmd):
            def handler(event):
                if _input_focused(): return None
                self.nav_click(cmd, nav_text); return 'break'
            return handler

        for key, (nav_text, cmd) in page_map.items():
            self.root.bind(key, make_handler(nav_text, cmd))

    def _try_close_dropdown(self, widget):
        if widget is None: return False
        try:
            if widget.winfo_class() == 'TCombobox':
                popdown = widget.tk.call('ttk::combobox::PopdownWindow', widget)
                if popdown and int(widget.tk.call('winfo', 'ismapped', popdown)):
                    widget.event_generate('<Escape>'); return True
                return False
        except Exception: pass
        return self._close_dropdown_in_frame(self.main_frame, widget)

    def _close_dropdown_in_frame(self, frame, target):
        try: children = frame.winfo_children()
        except: return False
        for child in children:
            if hasattr(child, 'step1_entry') and hasattr(child, 'step1_visible'):
                if child.step1_entry is target:
                    if getattr(child, 'step2_visible', False): child.hide_step2(); return True
                    if getattr(child, 'step1_visible', False): child.hide_step1(); return True
                    return False
            if hasattr(child, 'entry') and hasattr(child, 'list_visible'):
                if child.entry is target:
                    if getattr(child, 'list_visible', False): child.hide_list(); return True
                    return False
            if self._close_dropdown_in_frame(child, target): return True
        return False

    # ── Page management ───────────────────────────────────────────────────

    def _hide_persistent_pages(self):
        if self._billing_page and self._billing_page.parent.winfo_exists():
            self._billing_page.parent.pack_forget()
        if self._purchase_page and self._purchase_page.parent.winfo_exists():
            self._purchase_page.parent.pack_forget()

    def clear_main_frame(self):
        self._hide_persistent_pages()
        for w in self.main_frame.winfo_children():
            if self._billing_page  and w is self._billing_page.parent:  continue
            if self._purchase_page and w is self._purchase_page.parent: continue
            w.destroy()

    def _register_canvas(self, inner_frame):
        canvas = getattr(inner_frame, '_canvas', None)
        self.input_ctrl.set_active_canvas(canvas)
        self.input_ctrl.set_active_frame(inner_frame)

    # ── Pages ─────────────────────────────────────────────────────────────

    def show_welcome(self):
        self.clear_main_frame()
        for btn in self.nav_buttons.values():
            try: btn.configure(bootstyle="outline-primary")
            except: pass
        try: self.nav_buttons["🏠 Home"].configure(bootstyle="primary")
        except: pass
        self.active_nav = "🏠 Home"

        from ui.shared.home_page import build_home
        build_home(
            main_frame       = self.main_frame,
            conn             = self.conn,
            nav_click_fn     = self.nav_click,
            open_billing_fn  = lambda: self.nav_click(self.open_billing,  'Sales'),
            open_purchase_fn = lambda: self.nav_click(self.open_purchase, 'Purchase'),
            open_inventory_fn= lambda: self.nav_click(self.open_inventory,'Inventory'),
            open_customers_fn= lambda: self.nav_click(self.open_customers,'Customers'),
            open_ledger_fn   = lambda: self.nav_click(lambda: self.open_settings('Ledger'), 'Settings'),
            input_ctrl       = self.input_ctrl,
            register_canvas_fn = self._register_canvas,
        )

    def open_billing(self):
        self.clear_main_frame()
        from ui.billing import BillingPage
        if self._billing_page is None or not self._billing_page.parent.winfo_exists():
            container = ttk.Frame(self.main_frame)
            container.pack(fill=tk.BOTH, expand=True)
            self._billing_page = BillingPage(container, self.conn)
            self._billing_page.parent = container
        else:
            self._billing_page.parent.pack(fill=tk.BOTH, expand=True)
            self._billing_page._rebind_mousewheel()
            self._billing_page.reload_doctors()
            from core.customer_service import get_customer_names
            self._billing_page.customer_name.configure(values=get_customer_names(self.conn))
            self._billing_page._apply_location_column_visibility()
            self.root.after(50, self._billing_page.customer_name.focus)
        self._register_canvas(self._billing_page._inner_frame)

    def open_purchase(self):
        self.clear_main_frame()
        from ui.purchase import PurchasePage
        if self._purchase_page is None or not self._purchase_page.parent.winfo_exists():
            container = ttk.Frame(self.main_frame)
            container.pack(fill=tk.BOTH, expand=True)
            self._purchase_page = PurchasePage(container, self.conn)
            self._purchase_page.parent = container
        else:
            self._purchase_page.parent.pack(fill=tk.BOTH, expand=True)
            self._purchase_page._rebind_mousewheel()
            self.root.after(50, self._purchase_page.supplier_name.focus)
        self._register_canvas(self._purchase_page._inner_frame)

    def open_sales_history(self):
        self.clear_main_frame()
        from ui.sales.sales_history import SalesHistoryPage
        self._register_canvas(SalesHistoryPage(self.main_frame, self.conn)._inner_frame)

    def open_purchase_history(self):
        self.clear_main_frame()
        from ui.purchase.purchase_history import PurchaseHistoryPage
        self._register_canvas(PurchaseHistoryPage(self.main_frame, self.conn)._inner_frame)

    def open_inventory(self):
        self.clear_main_frame()
        from ui.inventory import InventoryPage
        self._register_canvas(InventoryPage(self.main_frame, self.conn)._inner_frame)

    def open_customers(self):
        self.clear_main_frame()
        from ui.shared.customers import CustomersPage
        self._register_canvas(CustomersPage(self.main_frame, self.conn)._inner_frame)

    def open_returns(self):
        self.clear_main_frame()
        outer = ttk.Frame(self.main_frame)
        outer.pack(fill=tk.BOTH, expand=True)
        btn_bar = ttk.Frame(outer)
        btn_bar.pack(fill=tk.X, padx=10, pady=(8, 0))
        container = ttk.Frame(outer)
        container.pack(fill=tk.BOTH, expand=True)

        def _show(kind):
            for w in container.winfo_children(): w.destroy()
            if kind == 'sales':
                from ui.returns.sales_return import SalesReturnPage
                page = SalesReturnPage(container, self.conn)
                try: sales_btn.configure(bootstyle="primary"); pur_btn.configure(bootstyle="outline-secondary")
                except: pass
            else:
                from ui.returns.purchase_return import PurchaseReturnPage
                page = PurchaseReturnPage(container, self.conn)
                try: pur_btn.configure(bootstyle="primary"); sales_btn.configure(bootstyle="outline-secondary")
                except: pass
            self._register_canvas(page._inner_frame)

        try:
            sales_btn = ttk.Button(btn_bar, text="🧾 Sales Return",    command=lambda: _show('sales'),    bootstyle="primary",           width=20)
            pur_btn   = ttk.Button(btn_bar, text="📦 Purchase Return", command=lambda: _show('purchase'), bootstyle="outline-secondary", width=20)
        except Exception:
            sales_btn = ttk.Button(btn_bar, text="🧾 Sales Return",    command=lambda: _show('sales'),    width=20)
            pur_btn   = ttk.Button(btn_bar, text="📦 Purchase Return", command=lambda: _show('purchase'), width=20)
        sales_btn.pack(side=tk.LEFT, padx=6, pady=4)
        pur_btn.pack(side=tk.LEFT, padx=6, pady=4)
        _show('sales')

    def open_settings(self, select_tab=None):
        self.clear_main_frame()
        from ui.settings import SettingsPage
        page = SettingsPage(self.main_frame, self.conn)
        if select_tab:
            # Select the tab whose text matches select_tab
            nb = page._notebook
            for i in range(nb.index('end')):
                if nb.tab(i, 'text') == select_tab:
                    nb.select(i)
                    break
        self._update_settings_canvas(page)
        page._notebook.bind('<<NotebookTabChanged>>',
                            lambda e: self._update_settings_canvas(page))

    def _update_settings_canvas(self, settings_page):
        try:
            tab_id     = settings_page._notebook.select()
            tab_widget = settings_page._notebook.nametowidget(tab_id)
            canvas     = self._find_tab_canvas(tab_widget)
            self.input_ctrl.set_active_canvas(canvas)
            self.input_ctrl.set_active_frame(tab_widget)
        except Exception:
            self.input_ctrl.set_active_canvas(None)
            self.input_ctrl.set_active_frame(None)

    def _find_tab_canvas(self, widget):
        for child in widget.winfo_children():
            if isinstance(child, tk.Canvas): return child
            result = self._find_tab_canvas(child)
            if result is not None: return result
        return None

    # ── Medicines master auto-import ─────────────────────────────────────

    def _auto_import_medicines_master(self):
        """If medicines_master table is empty, import from bundled Excel in background."""
        import threading
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='medicines_master'")
            if cur.fetchone():
                cur.execute("SELECT COUNT(*) FROM medicines_master")
                if cur.fetchone()[0] > 0:
                    return  # already populated
        except Exception:
            return

        def _run():
            try:
                import re, sqlite3 as _sq
                import openpyxl

                # Locate bundled Excel
                if getattr(sys, 'frozen', False):
                    xlsx = os.path.join(sys._MEIPASS, 'assets',
                                        'medicines_master_with_cdsco.xlsx')
                else:
                    xlsx = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        'assets', 'medicines_master_with_cdsco.xlsx')

                if not os.path.exists(xlsx):
                    return

                _TYPE_KW = [
                    ('Tablet','Tablet'),('Capsule','Capsule'),('Syrup','Syrup'),
                    ('Injection','Injection'),('Ointment','Ointment'),
                    ('Cream','Ointment'),('Gel','Gel'),('Drops','Syrup'),
                    ('Powder','Powder'),('Spray','Syrup'),('Inhaler','Injection'),
                    ('Solution','Syrup'),('Suspension','Syrup'),
                    ('Liniment','Liniment'),('Bolus','Bolus'),('Vaccine','Vaccine'),
                ]
                def _dtype(name, form):
                    text = f"{name} {form}".lower()
                    for kw, t in _TYPE_KW:
                        if kw.lower() in text: return t
                    return 'Other'

                wb = openpyxl.load_workbook(xlsx, read_only=True)
                rows = list(wb.active.iter_rows(values_only=True))
                wb.close()

                # Use a separate connection for the background thread
                conn2 = _sq.connect(self.db_path)
                cur2  = conn2.cursor()
                cur2.execute("DROP TABLE IF EXISTS medicines_master")
                cur2.execute("""
                    CREATE TABLE medicines_master (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL, manufacturer TEXT,
                        mrp REAL, content_drug TEXT,
                        med_type TEXT, pack_size TEXT
                    )""")
                cur2.execute("CREATE INDEX IF NOT EXISTS idx_mm_name "
                             "ON medicines_master(name COLLATE NOCASE)")

                batch = []
                for r in rows[1:]:
                    name = str(r[0]).strip() if r[0] else ''
                    if not name or name.lower() == 'none':
                        continue
                    mfg  = str(r[1]).strip()  if r[1]  else ''
                    try:   mrp = float(r[10]) if r[10] is not None else 0.0
                    except: mrp = 0.0
                    salt = str(r[11]).strip() if r[11] else ''
                    salt = re.sub(r'\s*\+\s*nan\s*', '', salt).strip().strip('+').strip()
                    form = str(r[16]).strip() if r[16] else ''
                    pack = str(r[17]).strip() if r[17] else ''
                    batch.append((name, mfg, mrp, salt, _dtype(name, form), pack))
                    if len(batch) >= 5000:
                        cur2.executemany(
                            "INSERT INTO medicines_master "
                            "(name,manufacturer,mrp,content_drug,med_type,pack_size) "
                            "VALUES (?,?,?,?,?,?)", batch)
                        batch.clear()
                if batch:
                    cur2.executemany(
                        "INSERT INTO medicines_master "
                        "(name,manufacturer,mrp,content_drug,med_type,pack_size) "
                        "VALUES (?,?,?,?,?,?)", batch)
                conn2.commit()
                conn2.close()
            except Exception as e:
                print(f"[medicines_master] auto-import failed: {e}")

        threading.Thread(target=_run, daemon=True).start()

    # ── Backup ────────────────────────────────────────────────────────────

    def _start_backup(self):
        try:
            from core.backup_manager import run_backup_on_open
            run_backup_on_open(on_error=self._show_backup_error)
        except Exception: pass
        self.root.after(60 * 60 * 1000, self._schedule_backup)

    def _schedule_backup(self):
        try:
            from core.backup_manager import run_backup_silently
            run_backup_silently(on_error=self._show_backup_error)
        except Exception: pass
        self.root.after(60 * 60 * 1000, self._schedule_backup)

    def _on_close(self):
        try:
            from core.backup_manager import run_backup_now
            run_backup_now()
        except Exception: pass
        self.root.destroy()

    def _show_backup_error(self, message):
        try:
            from tkinter import messagebox
            self.root.after(0, lambda: messagebox.showwarning(
                "Backup Connection Error",
                f"Automatic backup could not connect to Google Drive.\n\n"
                f"Reason: {message}\n\nYour data is safe locally. Backup will retry in 1 hour."))
        except Exception: pass

    # ── Theme (called from settings) ──────────────────────────────────────

    def change_theme(self, theme_name):
        if theme_name in self.available_themes:
            save_theme(theme_name)
            from tkinter import messagebox
            messagebox.showinfo("Theme Changed", "Application will restart with the new theme.")
            restart_app(self.root)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = VeterinaryManagementSystem()
    app.run()
