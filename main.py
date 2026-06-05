# Suppress warnings at startup
import os, sys, shutil, warnings, threading, queue
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
    # Seed AppData from the EXE bundle only when a file is missing (first install).
    # Never overwrite existing files — updates must preserve activation, expiry,
    # backup_config, theme, and all other per-store settings.
    for fname in ('theme_config.txt', 'layout_config.txt', 'font_size.txt',
                  'expiry.dat', 'backup_creds.dat', 'sample_import.json',
                  'backup_config.dat', 'backup_slots.dat', 'app_mode.txt',
                  'activation.dat'):
        dst = os.path.join(app_data, fname)
        if os.path.exists(dst):
            continue
        src_config = os.path.join(base_dir, 'config', fname)
        src_root   = os.path.join(base_dir, fname)
        src = src_config if os.path.exists(src_config) else src_root
        if os.path.exists(src):
            shutil.copy2(src, dst)
        elif fname in ('theme_config.txt', 'font_size.txt', 'layout_config.txt'):
            defaults = {'theme_config.txt': 'superhero',
                        'font_size.txt': '10', 'layout_config.txt': '{}'}
            open(dst, 'w').write(defaults.get(fname, ''))
    try:
        from core.backup_manager import seed_bundled_backup_files
        seed_bundled_backup_files()
    except Exception:
        pass
    try:
        from core.window_icon import _ensure_cached_ico
        _ensure_cached_ico()
    except Exception:
        pass
    os.chdir(app_data)

_setup_exe_environment()

try:
    from core.window_icon import init_process_app_id
    init_process_app_id()
except Exception:
    pass

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
    create_window, set_window_icon, restart_app, load_app_mode,
)
from core.db_setup import initialise as db_initialise
from core.input_controller import GlobalInputController
from core.master_medicine_service import (
    ensure_mode_master_state,
    sync_master_with_inventory_db_path,
)


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
        self._master_medicine_ready = False

        self._billing_page  = None
        self._purchase_page = None

        self._prepare_master_medicine_on_startup()
        self._build_nav()

        self.root._input_ctrl = self.input_ctrl
        self.root._main_app   = self

        self._start_backup()
        self._schedule_startup_alerts()
        self._schedule_update_check()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        # Startup already prepared/cleared master DB by mode.

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
            '6': ('Settings',         self.open_settings),
            '7': ('Returns',          self.open_returns),
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
        if not (self._purchase_page and getattr(self._purchase_page, '_inner_frame', None) is inner_frame):
            self.input_ctrl.set_f2_handler(None)

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
            open_contacts_fn = lambda: self.nav_click(self.open_contacts, 'Settings'),
            open_ledger_fn   = lambda: self.nav_click(lambda: self.open_settings('Ledger'), 'Settings'),
            open_general_products_fn=self.open_general_products,
            input_ctrl       = self.input_ctrl,
            register_canvas_fn = self._register_canvas,
        )

    def open_general_products(self):
        """Standalone general product rates — home quick access only (not in top nav)."""
        self.clear_main_frame()
        for btn in self.nav_buttons.values():
            try:
                btn.configure(bootstyle='outline-primary')
            except Exception:
                pass
        self.active_nav = None

        from ui.general_products import GeneralProductsPage
        container = ttk.Frame(self.main_frame)
        container.pack(fill=tk.BOTH, expand=True)
        page = GeneralProductsPage(container, self.conn)
        inner = getattr(page, '_inner_frame', None)
        if inner is not None:
            container.after(50, lambda: self._register_canvas(inner))

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
        self.input_ctrl.set_f2_handler(self._purchase_page._f2_import_bill)

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

    def open_contacts(self, subtab="Customers"):
        """Customers / doctors / suppliers — Settings → Contacts only."""
        self.clear_main_frame()
        from ui.settings import SettingsPage
        page = SettingsPage(self.main_frame, self.conn)
        page.open_contacts(subtab)
        self._update_settings_canvas(page)
        page._notebook.bind(
            "<<NotebookTabChanged>>",
            lambda e: self._update_settings_canvas(page),
        )

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

    def open_settings(self, select_tab=None, contacts_sub=None):
        self.clear_main_frame()
        from ui.settings import SettingsPage
        page = SettingsPage(self.main_frame, self.conn)
        if select_tab:
            nb = page._notebook
            for i in range(nb.index('end')):
                if nb.tab(i, 'text') == select_tab:
                    nb.select(i)
                    break
        if contacts_sub:
            page.open_contacts(contacts_sub)
        self._update_settings_canvas(page)
        page._notebook.bind('<<NotebookTabChanged>>',
                            lambda e: self._update_settings_canvas(page))

    def _update_settings_canvas(self, settings_page):
        try:
            tab_id     = settings_page._notebook.select()
            tab_widget = settings_page._notebook.nametowidget(tab_id)
            tab_text   = settings_page._notebook.tab(tab_id, 'text')
            if tab_text == 'Appearance':
                settings_page._layout.sync_input_canvas()
            else:
                canvas = self._find_tab_canvas(tab_widget)
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

    # ── Master medicines DB startup preparation ───────────────────────────

    def _prepare_master_medicine_on_startup(self):
        mode = load_app_mode()
        self.root._master_mode = mode
        self.root._master_loading = (mode == 'medical')
        self.root._master_ready = (mode != 'medical')
        self._master_medicine_ready = (mode != 'medical')
        if mode != 'medical':
            # Veterinary mode keeps master db cleared.
            try:
                ensure_mode_master_state(mode)
            except Exception:
                pass
            return

        progress_win = tk.Toplevel(self.root)
        progress_win.title("Preparing Medicines")
        progress_win.geometry("460x140")
        progress_win.resizable(False, False)
        progress_win.transient(self.root)
        # Do not grab focus globally; a stale grab can freeze all app inputs.
        progress_win.attributes('-topmost', True)
        try:
            progress_win.configure(padx=16, pady=14)
        except Exception:
            pass
        ttk.Label(
            progress_win,
            text="Loading medical master medicines. Please wait...",
        ).pack(anchor='w', pady=(0, 8))
        status_var = tk.StringVar(value="Starting...")
        ttk.Label(progress_win, textvariable=status_var).pack(anchor='w', pady=(0, 8))
        pb = ttk.Progressbar(progress_win, mode='determinate', maximum=100)
        pb.pack(fill=tk.X)
        q = queue.Queue()

        def _emit(percent, message):
            try:
                q.put((int(percent), str(message)))
            except Exception:
                pass

        def _worker():
            ok, count, msg = ensure_mode_master_state('medical', progress_cb=_emit)
            if ok:
                try:
                    _emit(99, "Syncing inventory medicines to master DB...")
                    sync_master_with_inventory_db_path(self.db_path)
                except Exception:
                    pass
            q.put(("done", ok, count, msg))

        threading.Thread(target=_worker, daemon=True).start()

        def _poll():
            try:
                while True:
                    item = q.get_nowait()
                    if item and item[0] == "done":
                        _ok, _count, _msg = item[1], item[2], item[3]
                        pb['value'] = 100
                        status_var.set(
                            f"Ready ({_count:,})" if _ok else f"Master load issue: {_msg}"
                        )
                        self.root._master_loading = False
                        self.root._master_ready = bool(_ok)
                        self._master_medicine_ready = bool(_ok)
                        self.root.after(250, lambda: self._close_master_progress(progress_win))
                        self.root.event_generate("<<MasterMedicineReady>>", when="tail")
                        return
                    pct, msg = item
                    pb['value'] = max(0, min(100, int(pct)))
                    status_var.set(msg)
            except queue.Empty:
                pass
            self.root.after(80, _poll)

        _poll()
        # Non-blocking startup: keep app responsive while master db prepares.

    def _close_master_progress(self, progress_win):
        try:
            progress_win.destroy()
        except Exception:
            pass

    # ── Backup ────────────────────────────────────────────────────────────

    def _start_backup(self):
        try:
            from core.backup_manager import is_auto_backup_enabled, run_backup_on_open
            if not is_auto_backup_enabled():
                return
            run_backup_on_open(on_error=self._show_backup_error)
            self.root.after(60 * 60 * 1000, self._schedule_backup)
        except Exception:
            pass

    def _schedule_backup(self):
        try:
            from core.backup_manager import is_auto_backup_enabled, run_backup_silently
            if not is_auto_backup_enabled():
                return
            run_backup_silently(on_error=self._show_backup_error)
            self.root.after(60 * 60 * 1000, self._schedule_backup)
        except Exception:
            pass

    def _on_close(self):
        try:
            from core.backup_manager import is_auto_backup_enabled, run_backup_now
            if is_auto_backup_enabled():
                run_backup_now()
        except Exception:
            pass
        self.root.destroy()

    def _show_backup_error(self, message):
        try:
            from core.themed_messagebox import showwarning
            self.root.after(0, lambda: showwarning(
                "Backup Connection Error",
                f"Automatic backup could not connect to Google Drive.\n\n"
                f"Reason: {message}\n\nYour data is safe locally. Backup will retry in 1 hour.",
                parent=self.root))
        except Exception: pass

    def _schedule_startup_alerts(self):
        """Wait until the maximized main window is mapped (fixes invisible dialogs in EXE)."""
        def _try():
            try:
                self.root.update_idletasks()
                if self.root.winfo_width() < 200:
                    self.root.after(300, _try)
                    return
            except Exception:
                pass
            self._show_startup_alerts()

        self.root.after(800, _try)

    def _show_startup_alerts(self):
        try:
            from core.startup_alerts import show_startup_alerts
            show_startup_alerts(self.root, self.conn)
        except Exception:
            pass

    def _schedule_update_check(self):
        """Once per day, silently check GitHub Releases and prompt if newer."""
        self.root.after(6000, self._run_update_check)

    def _run_update_check(self):
        def _run():
            try:
                from core.github_updater import (
                    should_auto_check_today,
                    check_for_update,
                    format_release_summary,
                )
                if not should_auto_check_today():
                    return
                info = check_for_update()
                if not info.available or not info.has_download:
                    return

                def _prompt():
                    from core.themed_messagebox import askyesno
                    notes = format_release_summary(info)
                    if askyesno(
                        "Update Available",
                        f"A new version is available.\n\n{notes}\n\n"
                        "Open Settings → Database → Management to install now?",
                        parent=self.root,
                    ):
                        self.open_settings("Database")

                self.root.after(0, _prompt)
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()

    # ── Theme (called from settings) ──────────────────────────────────────

    def change_theme(self, theme_name):
        if theme_name in self.available_themes:
            save_theme(theme_name)
            from core.themed_messagebox import showinfo
            showinfo("Theme Changed", "Application will restart with the new theme.",
                     parent=self.root)
            restart_app(self.root)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = VeterinaryManagementSystem()
    app.run()
