# Suppress warnings at startup
import os, sys, shutil, warnings, threading, queue
os.environ['PYTHONWARNINGS'] = 'ignore'
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message=".*Task policy set failed.*")

from core.frozen_bootstrap import prepare_frozen_runtime


def _setup_exe_environment():
    """Prepare AppData and ensure the frozen runtime is configured once."""
    if not getattr(sys, "frozen", False):
        return

    prepare_frozen_runtime()

    app_data = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "VeterinaryApp"
    )
    os.makedirs(app_data, exist_ok=True)
    base_dir = sys._MEIPASS
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)
    # Seed AppData from the EXE bundle only when a file is missing (first install).
    # Never overwrite existing files — updates must preserve activation, expiry,
    # backup_config, theme, and all other per-store settings.
    for fname in (
        "theme_config.txt",
        "layout_config.txt",
        "font_size.txt",
        "expiry.dat",
        "backup_creds.dat",
        "sample_import.json",
        "backup_config.dat",
        "backup_slots.dat",
        "app_mode.txt",
        "activation.dat",
    ):
        dst = os.path.join(app_data, fname)
        if os.path.exists(dst):
            continue
        src_config = os.path.join(base_dir, "config", fname)
        src_root = os.path.join(base_dir, fname)
        src = src_config if os.path.exists(src_config) else src_root
        if os.path.exists(src):
            shutil.copy2(src, dst)
        elif fname in ("theme_config.txt", "font_size.txt", "layout_config.txt"):
            defaults = {
                "theme_config.txt": "superhero",
                "font_size.txt": "10",
                "layout_config.txt": "{}",
            }
            open(dst, "w").write(defaults.get(fname, ""))
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

# ── Selftest for restart correctness ─────────────────────────────────────
# Usage (manual): run SatpudaCore.exe --selftest-restart
# First run relaunches; second run verifies sqlite3 import succeeds.
_SELFTEST_RESTART = "--selftest-restart" in sys.argv
_SELFTEST_RESTARTED = "--selftest-restarted" in sys.argv
if _SELFTEST_RESTART and not _SELFTEST_RESTARTED:
    try:
        sys.argv.append("--selftest-restarted")
        from core.frozen_bootstrap import relaunch_executable
        relaunch_executable()
    except Exception:
        try:
            open("selftest_error.txt", "w", encoding="utf-8").write("selftest relaunch failed")
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

if not (_SELFTEST_RESTART and _SELFTEST_RESTARTED):
    _run_license_check()
    _run_expiry_check()
    try:
        from core.store_manager import ensure_registry_on_startup
        ensure_registry_on_startup()
        from core.backup_manager import sync_backup_config_to_active_store
        sync_backup_config_to_active_store()
    except Exception:
        pass

# ── Imports ───────────────────────────────────────────────────────────────────
import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

import sqlite3
if _SELFTEST_RESTARTED:
    # If we reached here, sqlite3 extension module was imported successfully.
    try:
        open("selftest_ok.txt", "w", encoding="utf-8").write("sqlite3 import ok after restart")
    except Exception:
        pass
    sys.exit(0)
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
        try:
            from core.themed_messagebox import install_messagebox_patch
            install_messagebox_patch()
        except Exception:
            pass
        self._update_window_title()
        set_window_icon(self.root)
        self.root.geometry("1200x800")
        self.root.state('zoomed')

        self._prompt_initial_store_if_needed()
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

    def _prompt_initial_store_if_needed(self):
        """Already-activated upgrade: move legacy veterinary.db into named store folder."""
        try:
            from core.store_manager import (
                has_registry, get_legacy_db_path, setup_initial_store_on_activation,
            )
            from core.license_manager import is_activated
            from tkinter import simpledialog
            from core.themed_messagebox import showerror
        except Exception:
            return

        from core.store_manager import ensure_registry_on_startup
        ensure_registry_on_startup()
        if has_registry() or not is_activated():
            return
        if not os.path.isfile(get_legacy_db_path()):
            return

        default = ''
        try:
            from core.backup_manager import get_backup_config_status
            default = get_backup_config_status().get('store_name', '') or ''
        except Exception:
            pass

        name = simpledialog.askstring(
            "Initial Store Setup",
            "Enter your store name.\n\n"
            "Your existing database will be moved into this store.\n"
            "This name is also used for the Google Drive backup folder.",
            initialvalue=default,
            parent=self.root,
        )
        if not (name or '').strip():
            showerror(
                "Initial Store Setup",
                "A store name is required to continue.",
                parent=self.root,
            )
            self.root.destroy()
            sys.exit(0)
        try:
            setup_initial_store_on_activation(name.strip())
        except Exception as e:
            showerror("Initial Store Setup", str(e), parent=self.root)
            self.root.destroy()
            sys.exit(0)

    def _init_database(self):
        from core.store_manager import get_active_db_path
        from core.backup_manager import reload_slots_for_active_store
        db_path = get_active_db_path()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn   = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()
        self.db_path = db_path
        db_initialise(self.conn)
        reload_slots_for_active_store()
        try:
            from core.backup_manager import sync_backup_config_to_active_store
            sync_backup_config_to_active_store()
        except Exception:
            pass

    def _update_window_title(self):
        try:
            from core.store_manager import get_active_display_name, has_registry
            store = get_active_display_name()
            if has_registry() and store:
                self.root.title(f"Satpuda Core — {store}")
            else:
                self.root.title("Satpuda Core — Billing. Management. Simplified.")
        except Exception:
            self.root.title("Satpuda Core — Billing. Management. Simplified.")

    # kept for backward compat (settings delete-all calls this)
    def create_tables(self):
        db_initialise(self.conn)

    # ── Navigation bar ────────────────────────────────────────────────────

    def _build_nav(self):
        nav_frame = ttk.Frame(self.root)
        nav_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        self.nav_frame = nav_frame
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
        self.main_frame.configure(takefocus=1)
        self.main_frame.pack(side="left", fill="both", expand=True, padx=10, pady=10)

        self.input_ctrl = GlobalInputController(self.root, self.main_frame)
        from core.keyboard_registry import KeyboardRegistry
        self._configure_keyboard_navigation(KeyboardRegistry)
        KeyboardRegistry.install(self.root, self)
        KeyboardRegistry.register_app_handlers(
            on_ctrl_p=self._global_print_last_bill,
            on_ctrl_e=self._global_export_page,
        )
        KeyboardRegistry.wire_shell(self.root, self.main_frame, self.nav_frame)
        self.root._main_app = self

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

    def _configure_keyboard_navigation(self, KeyboardRegistry):
        """Single source of truth for page digit navigation (bind_all via registry)."""

        def _nav(cmd, text):
            def handler(event=None):
                self.nav_click(cmd, text)
                return 'break'
            return handler

        KeyboardRegistry.configure_navigation({
            '`': _nav(self.show_welcome, '🏠 Home'),
            '0': _nav(self.show_welcome, '🏠 Home'),
            '1': _nav(self.open_billing, 'Sales'),
            '2': _nav(self.open_purchase, 'Purchase'),
            '3': _nav(self.open_inventory, 'Inventory'),
            '4': _nav(self.open_sales_history, 'Sales History'),
            '5': _nav(self.open_purchase_history, 'Purchase History'),
            '6': _nav(self.open_returns, 'Returns'),
            '7': _nav(self.open_settings, 'Settings'),
        })
        tab_map = {
            1: lambda: self.open_settings('Pharmacy Profile'),
            2: lambda: self.open_settings('Contacts'),
            3: lambda: self.open_settings('Shelf Management'),
            4: lambda: self.open_settings('Appearance'),
            5: lambda: self.open_settings('Import'),
            6: lambda: self.open_settings('Management'),
            7: lambda: self.open_settings('Payment'),
            8: lambda: self.open_settings('Ledger'),
            9: lambda: self.open_settings('⌨ Shortcuts'),
        }
        KeyboardRegistry.set_settings_tab_map(tab_map)

    def _global_print_last_bill(self, event=None):
        """Ctrl+P on any page — print last saved sales bill."""
        if self._billing_page and hasattr(self._billing_page, '_print_last_bill'):
            self._billing_page._print_last_bill()
            return 'break'
        from core.themed_messagebox import showinfo
        showinfo('No Bill', 'Save a sales bill first, or open Sales.', parent=self.root)
        return 'break'

    def _global_export_page(self, event=None):
        """Ctrl+E fallback when the active page has no export handler."""
        nav = getattr(self, 'active_nav', None)
        if nav == '🏠 Home':
            hb = getattr(self, '_home_keyboard_bindings', None)
            if hb and hb.on_ctrl_e:
                hb.on_ctrl_e()
                return 'break'
        page_exports = {
            'Inventory': ('_inventory_page', '_export_menu'),
            'Sales History': ('_sales_history_page', '_export_menu'),
            'Purchase History': ('_purchase_history_page', '_export_menu'),
        }
        spec = page_exports.get(nav)
        if spec:
            attr, method = spec
            page = getattr(self, attr, None)
            if page and hasattr(page, method):
                getattr(page, method)()
                return 'break'
        return None

    def _bind_page_keys(self):
        from core.keyboard_registry import KeyboardRegistry

        def _on_escape(event):
            from core.dialog_escape import close_active_dialog
            from core.keyboard_registry import KeyboardRegistry
            if close_active_dialog(self.root):
                self.root.after_idle(KeyboardRegistry.finish_modal_session)
                return 'break'
            if KeyboardRegistry.clear_sidebar_nav_mode():
                return 'break'
            from core.keyboard_registry import is_treeview_focused
            w = None
            try:
                w = self.root.focus_get()
            except Exception:
                pass
            if w is not None:
                try:
                    cls = w.winfo_class()
                except Exception:
                    cls = ''
                if cls == 'Treeview':
                    return None  # input_controller handles tree escape
                if cls in ('Entry', 'TEntry', 'TCombobox', 'Text', 'Listbox'):
                    if self._try_close_dropdown(w):
                        return 'break'
            b = KeyboardRegistry.active()
            if b and b.on_escape_extra:
                try:
                    if b.on_escape_extra() == 'break':
                        return 'break'
                except Exception:
                    pass
            KeyboardRegistry.finish_modal_session()
            return 'break'

        self.root.bind('<Escape>', _on_escape, add='+')

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
        from core.keyboard_registry import KeyboardRegistry
        bindings = getattr(inner_frame, '_keyboard_bindings', None)
        if bindings is None and self._purchase_page and getattr(self._purchase_page, '_inner_frame', None) is inner_frame:
            self._purchase_page._register_keyboard()
            bindings = getattr(inner_frame, '_keyboard_bindings', None)
        if bindings is None and self._billing_page and getattr(self._billing_page, '_inner_frame', None) is inner_frame:
            self._billing_page._register_keyboard()
            bindings = getattr(inner_frame, '_keyboard_bindings', None)
        if bindings is None:
            bindings = KeyboardRegistry.make_bindings('page')
        KeyboardRegistry.register_page(inner_frame, bindings)

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
            self.root.after(50, self._billing_page._focus_customer_name)
        self._billing_page._register_keyboard()
        self._register_canvas(self._billing_page._inner_frame)
        self.root.after(120, self._billing_page._register_keyboard)

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
            self.root.after(50, self._purchase_page._focus_supplier_name)
        self._purchase_page._register_keyboard()
        self._register_canvas(self._purchase_page._inner_frame)
        self.root.after(120, self._purchase_page._register_keyboard)

    def open_sales_history(self):
        self.clear_main_frame()
        from ui.sales.sales_history import SalesHistoryPage
        page = SalesHistoryPage(self.main_frame, self.conn)
        self._sales_history_page = page
        page._register_keyboard()
        self._register_canvas(page._inner_frame)
        self.root.after(120, page._register_keyboard)

    def open_purchase_history(self):
        self.clear_main_frame()
        from ui.purchase.purchase_history import PurchaseHistoryPage
        page = PurchaseHistoryPage(self.main_frame, self.conn)
        self._purchase_history_page = page
        page._register_keyboard()
        self._register_canvas(page._inner_frame)
        self.root.after(120, page._register_keyboard)

    def open_inventory(self):
        self.clear_main_frame()
        from ui.inventory import InventoryPage
        page = InventoryPage(self.main_frame, self.conn)
        self._inventory_page = page
        page._register_keyboard()
        self._register_canvas(page._inner_frame)
        self.root.after(120, page._register_keyboard)

    def open_contacts(self, subtab="Customers"):
        """Customers / doctors / suppliers — Settings → Contacts only."""
        self.clear_main_frame()
        from ui.settings import SettingsPage
        page = SettingsPage(self.main_frame, self.conn)
        self._settings_page = page
        self.active_nav = 'Settings'
        page.open_contacts(subtab)
        self._update_settings_canvas(page)
        page._notebook.bind(
            "<<NotebookTabChanged>>",
            lambda e: (self._update_settings_canvas(page),
                       page.refresh_keyboard_bindings()),
        )
        page.refresh_keyboard_bindings()

    def open_returns(self):
        self.active_nav = 'Returns'
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
            KeyboardRegistry.register_page(outer, returns_bindings)

        self._returns_show = _show

        from core.keyboard_registry import KeyboardRegistry, PageBindings
        returns_bindings = PageBindings(
            page_id='returns',
            sub_keys={
                's': lambda: _show('sales'),
                'p': lambda: _show('purchase'),
            },
        )
        outer._keyboard_bindings = returns_bindings

        try:
            sales_btn = ttk.Button(btn_bar, text="🧾 Sales Return",    command=lambda: _show('sales'),    bootstyle="primary",           width=20)
            pur_btn   = ttk.Button(btn_bar, text="📦 Purchase Return", command=lambda: _show('purchase'), bootstyle="outline-secondary", width=20)
        except Exception:
            sales_btn = ttk.Button(btn_bar, text="🧾 Sales Return",    command=lambda: _show('sales'),    width=20)
            pur_btn   = ttk.Button(btn_bar, text="📦 Purchase Return", command=lambda: _show('purchase'), width=20)
        sales_btn.pack(side=tk.LEFT, padx=6, pady=4)
        pur_btn.pack(side=tk.LEFT, padx=6, pady=4)
        for btn in self.nav_buttons.values():
            try: btn.configure(bootstyle="outline-primary")
            except: btn.configure(style="Nav.TButton")
        try: self.nav_buttons['Returns'].configure(bootstyle="primary")
        except: pass
        _show('sales')
        KeyboardRegistry.register_page(outer, returns_bindings)

    def open_settings(self, select_tab=None, contacts_sub=None, management_sub=None):
        self.clear_main_frame()
        from ui.settings import SettingsPage
        page = SettingsPage(self.main_frame, self.conn)
        self._settings_page = page
        self.active_nav = 'Settings'
        if select_tab:
            nb = page._notebook
            for i in range(nb.index('end')):
                if nb.tab(i, 'text') == select_tab:
                    nb.select(i)
                    break
        if contacts_sub:
            page.open_contacts(contacts_sub)
        if management_sub:
            page.open_management(management_sub)
        self._update_settings_canvas(page)
        page._notebook.bind('<<NotebookTabChanged>>',
                            lambda e: (self._update_settings_canvas(page),
                                       page.refresh_keyboard_bindings()))
        page.refresh_keyboard_bindings()

    def _update_settings_canvas(self, settings_page):
        try:
            tab_id     = settings_page._notebook.select()
            tab_widget = settings_page._notebook.nametowidget(tab_id)
            tab_text   = settings_page._notebook.tab(tab_id, 'text')
            if tab_text == 'Appearance':
                settings_page._layout.sync_input_canvas()
            elif tab_text == 'Management':
                settings_page._database.sync_input_canvas()
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
        finally:
            from core.keyboard_registry import KeyboardRegistry
            self.root.after_idle(KeyboardRegistry.finish_modal_session)

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
                        "Open Settings → Management → App Updates to install now?",
                        parent=self.root,
                    ):
                        self.open_settings("Management", management_sub='updates')

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
