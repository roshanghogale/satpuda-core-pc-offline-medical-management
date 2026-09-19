# Suppress warnings at startup
import os, sys, shutil, warnings, threading, queue
os.environ['PYTHONWARNINGS'] = 'ignore'
# Server sync init: quieter logs + less aggressive keepalive (must be before grpc import).
os.environ.setdefault('GRPC_VERBOSITY', 'ERROR')
os.environ.setdefault('GRPC_TRACE', '')
os.environ.setdefault('GRPC_ARG_KEEPALIVE_TIME_MS', '120000')
os.environ.setdefault('GRPC_ARG_KEEPALIVE_TIMEOUT_MS', '20000')
os.environ.setdefault('GRPC_ARG_HTTP2_MAX_PINGS_WITHOUT_DATA', '0')
os.environ.setdefault('GRPC_ARG_KEEPALIVE_PERMIT_WITHOUT_CALLS', '0')
os.environ.setdefault('GRPC_ARG_HTTP2_MIN_RECV_PING_INTERVAL_WITHOUT_DATA_MS', '60000')
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message=".*Task policy set failed.*")

from core.frozen_bootstrap import prepare_frozen_runtime  # noqa: F401 — used by restart/selftest paths
from core.ssl_utils import configure_ssl_certificates

configure_ssl_certificates()
from core.log_policy import configure_silent_logs
configure_silent_logs()
try:
    import core.keyboard_registry as _kb
    _kb.SHORTCUT_LOG_ALWAYS = False
    _kb.DEBUG_KEYBOARD = False
except Exception:
    pass


from core.frozen_appdata_setup import setup_frozen_appdata


def _setup_exe_environment():
    """Prepare AppData and ensure the frozen runtime is configured once."""
    if not getattr(sys, "frozen", False):
        return
    setup_frozen_appdata(chdir=True)
    try:
        from core.window_icon import _ensure_cached_ico

        _ensure_cached_ico()
    except Exception:
        pass


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
    from core.license_manager import check_expiry, expiry_block_reason, _is_online_mode
    if not check_expiry():
        return
    # A LOST LICENCE FILE IS NOT AN EXPIRY.
    #
    # It has its own sentence and its own remedy -- one internet connection --
    # and it must not open the three-factor activation dialog, which asks a
    # shopkeeper for a device key that would not help him.
    if expiry_block_reason() == "needs_internet":
        from core.license_seal import NEEDS_INTERNET_MESSAGE
        # NOT A DEAD END ANY MORE.
        #
        # This used to be one message box and sys.exit(0): the shop was told to
        # connect the internet, pressed OK, and the app closed. If the internet
        # was already on, or the licence arrived a moment later, there was
        # nothing to press and nothing changed -- reopening ran the same check
        # and closed again. Now the same screen can look again, and can connect
        # the PC to its shop with the shop's SC- key, which is the one thing
        # that fetches the licence when this computer was never paired.
        from widgets.activation_dialog import show_license_recovery_dialog
        if show_license_recovery_dialog(NEEDS_INTERNET_MESSAGE):
            if not check_expiry():
                return
        sys.exit(0)
    if _is_online_mode():
        # Same server expiry as Android: do not ask for credentials again.
        # Admin extends expiry_date; user reopens the app.
        #
        # Recoverable in the same way as the licence-not-found case above: the
        # administrator extending the date on the server is a change this screen
        # can see for itself, so "Look again" reopens the shop without the
        # shopkeeper having to restart anything.
        from widgets.activation_dialog import show_license_recovery_dialog
        if show_license_recovery_dialog(
            "Store license expired or access disabled.\n\n"
            "Contact the administrator to extend the expiry date.\n"
            "You do not need to re-activate — press Look again once it is "
            "extended.",
            title="Access blocked",
        ):
            if not check_expiry():
                return
        sys.exit(0)
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
        self.root._app_instance = self
        self.root._main_app = self
        try:
            from core.page_refresh import register_app
            register_app(self)
        except Exception:
            pass
        try:
            from core.sync_v3.data_change_bus import get_bus

            get_bus().set_tk_root(self.root)
        except Exception:
            pass
        try:
            from core.themed_messagebox import install_messagebox_patch
            install_messagebox_patch()
        except Exception:
            pass
        self._update_window_title()
        set_window_icon(self.root)
        self.root.geometry("1200x800")
        try:
            self.root.withdraw()
        except Exception:
            pass

        self._prompt_initial_store_if_needed()
        self._init_database()
        if not self._run_app_login_gate():
            try:
                self.root.destroy()
            except Exception:
                pass
            sys.exit(0)
        self._master_medicine_ready = False

        self._billing_page  = None
        self._purchase_page = None
        self._sales_history_page = None
        self._purchase_history_page = None
        self._inventory_page = None
        self._settings_page = None
        self._payment_page = None
        self._general_products_page = None
        self._sales_return_page = None
        self._purchase_return_page = None
        self._stock_disposal_page = None
        self._reorder_page = None
        self._tutor_chat_window = None
        self._tutor_nav_btn = None
        self._home_built = False
        self._returns_shell_built = False
        self._settings_notebook_bound = False
        self._returns_outer = None
        self._returns_bindings = None
        self._returns_show = None
        self._returns_last_kind = 'sales'
        self._pages_prewarmed = False
        self._pages_display_warmed = False
        self._server_refresh_job = None
        self._server_pending_cols = set()
        self._startup_alerts_cache = None
        self._present_startup_alerts = None
        self._startup_alerts_shown = False
        self._startup_alerts_scheduled = False
        self._post_alert_loading = None
        self._tutor_chat_input_active = False

        self.root._startup_prewarm = False

        self._build_nav(show_home=False)
        # Delay optional import prewarm so first launch interaction stays responsive.
        self.root.after(15000, self._start_delayed_import_prewarm)
        # Lazy startup: build only home initially; pages/data load on first open.
        self._startup_alerts_cache = None
        self._present_startup_alerts = None
        self.show_welcome()
        self.root.after(1200, self._ensure_tutor_float)

        try:
            self.root.state('zoomed')
            self.root.deiconify()
        except Exception:
            pass

        self.root._input_ctrl = self.input_ctrl
        self.root._main_app   = self

        try:
            self.root.update_idletasks()
        except Exception:
            pass

        self._schedule_startup_alerts()
        self._startup_alerts_shown = False

        self.root.after(80, self._deferred_startup_tasks)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _start_post_alert_loading(self, alert_window=None):
        """Full-screen progress (~6s) for background startup work."""
        if getattr(self, "_post_alert_loading", None) is not None:
            return
        from core.splash_screen import PostAlertLoadingOverlay
        self._post_alert_loading = PostAlertLoadingOverlay(
            self.root,
            duration_ms=6000,
            on_done=self._finish_post_alert_loading,
            alert_window=alert_window,
        )

    def _start_startup_progress(self):
        """Progress overlay when alerts are skipped/snoozed — still loads in background."""
        self._start_post_alert_loading(alert_window=None)

    def _stop_post_alert_loading(self):
        loading = getattr(self, "_post_alert_loading", None)
        if loading is not None:
            loading.destroy()
            self._post_alert_loading = None

    def _finish_post_alert_loading(self):
        self._post_alert_loading = None
        try:
            from core.keyboard_registry import KeyboardRegistry
            KeyboardRegistry.finish_modal_session(defer_refresh=True, blur=False)
        except Exception:
            pass
        self._ensure_tutor_float()

    def _pump_startup_ui(self):
        try:
            self.root.update()
        except Exception:
            try:
                self.root.update_idletasks()
            except Exception:
                pass

    def _prewarm_heavy_imports(self):
        """Import heavy page modules in background so first navigation is faster."""
        import importlib
        for name in (
            'ui.billing.billing',
            'ui.purchase.purchase',
            'ui.inventory.inventory',
            'ui.sales.sales_history',
            'ui.purchase.purchase_history',
            'core.printer_manager',
            'core.gemini_bill_parser',
            'core.purchase_bill_image_parser',
        ):
            try:
                importlib.import_module(name)
            except Exception:
                pass

    def _deferred_startup_tasks(self):
        """Non-blocking startup: master DB, sync, module prewarm."""
        self._prepare_master_medicine_background()
        self.root.after(2500, self._start_server_sync_if_online)
        self.root.after(10000, self._start_backup)
        if not getattr(self, '_startup_alerts_shown', False):
            self._schedule_startup_alerts()
        self._schedule_update_check()

    def _start_delayed_import_prewarm(self):
        threading.Thread(
            target=self._prewarm_heavy_imports,
            daemon=True,
            name='SatpudaModulePrewarm',
        ).start()

    def _prewarm_all_pages_at_startup(self):
        """Build every main page before the app is shown."""
        import importlib

        steps = (
            ('billing', '_billing_page', 'Sales', 'ui.billing', 'BillingPage'),
            ('purchase', '_purchase_page', 'Purchase', 'ui.purchase', 'PurchasePage'),
            ('inventory', '_inventory_page', 'Inventory', 'ui.inventory', 'InventoryPage'),
            ('sales_history', '_sales_history_page', 'Sales History',
             'ui.sales.sales_history', 'SalesHistoryPage'),
            ('purchase_history', '_purchase_history_page', 'Purchase History',
             'ui.purchase.purchase_history', 'PurchaseHistoryPage'),
        )
        for key, attr, label, module_name, class_name in steps:
            if getattr(self, attr, None) is not None:
                continue
            self._pump_startup_ui()
            try:
                mod = importlib.import_module(module_name)
                cls = getattr(mod, class_name)
                container = self._page_cache.show(key)
                page = cls(container, self.conn)
                page.parent = container
                setattr(self, attr, page)
            except Exception:
                pass
        self._page_cache.hide_all()
        self._pages_prewarmed = True

    def _any_prewarm_data_still_loading(self):
        for page, flag in (
            (self._sales_history_page, '_sales_loading'),
            (self._inventory_page, '_inventory_loading'),
            (self._purchase_history_page, '_purchase_loading'),
        ):
            if page is not None and getattr(page, flag, False):
                return True
        return False

    def _pump_ui_until(self, predicate, timeout=90):
        import time
        end = time.time() + timeout
        while time.time() < end:
            if predicate():
                return True
            try:
                self.root.update()
            except Exception:
                break
            time.sleep(0.02)
        return False

    def _warm_prewarmed_page_data(self):
        """DB fetch in background threads; UI apply + tree fill on main thread at startup."""
        import queue
        import threading
        import time

        jobs = []

        if self._billing_page is not None:
            def _fetch_billing():
                from core.customer_service import get_customer_names, get_all_doctor_names
                from core.village_service import village_names_for_ui
                return (
                    get_customer_names(self.conn),
                    get_all_doctor_names(self.conn),
                    village_names_for_ui(self.conn),
                )

            jobs.append(
                ('Sales data', _fetch_billing, self._billing_page._apply_billing_reference_data, None),
            )

        if self._sales_history_page is not None:
            page = self._sales_history_page

            def _apply_sales(rows):
                page._set_sales_loading(True, 'Loading sales history…')
                page._apply_sales_rows(rows)

            jobs.append(('Sales History', page._fetch_sales_rows, _apply_sales, '_sales_loading'))

        if self._inventory_page is not None:
            page = self._inventory_page

            def _apply_inventory(payload):
                page._set_inventory_loading(True, 'Loading inventory…')
                page._apply_inventory_rows(payload)

            jobs.append(('Inventory', page._fetch_inventory_rows, _apply_inventory, '_inventory_loading'))

        if self._purchase_history_page is not None:
            page = self._purchase_history_page

            def _apply_purchases(payload):
                page._purchase_loading = True
                page._apply_purchase_rows(payload)

            jobs.append(
                ('Purchase History', page._fetch_purchase_rows, _apply_purchases, '_purchase_loading'),
            )

        for label, fetch_fn, apply_fn, loading_attr in jobs:
            self._pump_startup_ui()
            result_q = queue.Queue()

            def _worker(fn=fetch_fn):
                try:
                    result_q.put(('ok', fn()))
                except Exception as exc:
                    result_q.put(('err', exc))

            threading.Thread(target=_worker, daemon=True, name=f'Prewarm{label}').start()
            status, payload = 'err', None
            while True:
                try:
                    status, payload = result_q.get_nowait()
                    break
                except queue.Empty:
                    try:
                        self.root.update()
                    except Exception:
                        break
                    time.sleep(0.02)
            if status != 'ok':
                continue
            try:
                apply_fn(payload)
            except Exception:
                continue
            if loading_attr:
                page_ref = {
                    '_sales_loading': self._sales_history_page,
                    '_inventory_loading': self._inventory_page,
                    '_purchase_loading': self._purchase_history_page,
                }.get(loading_attr)
                if page_ref is not None:
                    self._pump_ui_until(
                        lambda p=page_ref, a=loading_attr: not getattr(p, a, False),
                        timeout=120,
                    )
            else:
                try:
                    self.root.update_idletasks()
                except Exception:
                    pass

    def _warm_home_at_startup(self):
        ctx = getattr(self, '_home_warm', None)
        if not ctx:
            return
        try:
            from ui.shared.home_page import warm_home_dashboard_during_splash
            warm_home_dashboard_during_splash(
                ctx['main_frame'],
                self.conn,
                ctx['inner'],
                ctx['stat_value_labels'],
                ctx['banner_frame'],
                db_path=ctx.get('db_path') or self.db_path,
                pump_fn=lambda: self.root.update(),
            )
        except Exception:
            pass

    def _wire_all_prewarmed_pages(self):
        from core.keyboard_registry import KeyboardRegistry
        for page in (
            self._billing_page,
            self._purchase_page,
            self._inventory_page,
            self._sales_history_page,
            self._purchase_history_page,
        ):
            if page is None:
                continue
            inner = getattr(page, '_inner_frame', None)
            bindings = getattr(inner, '_keyboard_bindings', None) if inner else None
            if inner is None:
                continue
            if bindings is None and hasattr(page, '_register_keyboard'):
                try:
                    page._register_keyboard()
                    bindings = getattr(inner, '_keyboard_bindings', None)
                except Exception:
                    pass
            if bindings:
                KeyboardRegistry.register_page(inner, bindings)
        inner_home = getattr(self, '_home_inner_frame', None)
        if inner_home is not None:
            bindings = getattr(self, '_home_keyboard_bindings', None) or getattr(
                inner_home, '_keyboard_bindings', None)
            if bindings:
                KeyboardRegistry.register_page(inner_home, bindings)

    def _warm_widget_tree(self, widget):
        try:
            from widgets.two_step_medicine_combo import TwoStepMedicineCombo
            if isinstance(widget, TwoStepMedicineCombo):
                if widget.step1_tree is None:
                    widget._init_trees()
                widget._bind_global_return()
                widget._bind_ancestor_unmap()
        except Exception:
            pass
        try:
            for child in widget.winfo_children():
                self._warm_widget_tree(child)
        except Exception:
            pass

    def _finish_page_scroll_region(self, page):
        inner = getattr(page, '_inner_frame', None)
        if inner is None:
            return
        try:
            inner.update_idletasks()
            canvas = getattr(inner, '_canvas', None)
            if canvas is not None:
                canvas.configure(scrollregion=canvas.bbox('all'))
        except Exception:
            pass

    def _finish_page_display_warm(self, page):
        if page is None:
            return
        container = getattr(page, 'parent', None)
        if container is not None:
            self._warm_widget_tree(container)
        if hasattr(page, '_setup_arrow_nav'):
            try:
                page._setup_arrow_nav()
            except Exception:
                pass
        if page is self._billing_page:
            try:
                page._rebind_mousewheel()
                page._apply_location_column_visibility()
                page._refresh_margin_summary_visibility()
                page.reload_villages()
            except Exception:
                pass
        elif page is self._purchase_page:
            try:
                page._rebind_mousewheel()
                page.refresh_layout_dropdowns()
            except Exception:
                pass
        self._finish_page_scroll_region(page)

    def _display_warm_all_pages_at_startup(self):
        """Show each page once before deiconify so first user click is instant."""
        steps = (
            ('billing', '_billing_page', 'Sales'),
            ('purchase', '_purchase_page', 'Purchase'),
            ('inventory', '_inventory_page', 'Inventory'),
            ('sales_history', '_sales_history_page', 'Sales History'),
            ('purchase_history', '_purchase_history_page', 'Purchase History'),
        )
        for key, attr, label in steps:
            page = getattr(self, attr, None)
            if page is None:
                continue
            self._pump_startup_ui()
            self._page_cache.show(key)
            self._finish_page_display_warm(page)
            try:
                self.root.update()
            except Exception:
                pass
        if self._home_built:
            self._page_cache.show('home')
            inner = getattr(self, '_home_inner_frame', None)
            if inner is not None:
                try:
                    inner.update_idletasks()
                    canvas = getattr(inner, '_canvas', None)
                    if canvas is not None:
                        canvas.configure(scrollregion=canvas.bbox('all'))
                except Exception:
                    pass
            try:
                self.root.update()
            except Exception:
                pass
        self._pages_display_warmed = True

    def _fast_activate_page(self, page):
        self._activate_cached_page(page, reload_if_empty=False)

    def _activate_cached_page(self, page, *, reload_if_empty=True):
        """Wire scroll/keyboard and refresh layout when showing a cached page."""
        if page is None:
            return
        self._wire_scroll_page(page)
        inner = getattr(page, '_inner_frame', None)

        def _refresh():
            if inner is not None:
                try:
                    from core.scroll_manager import refresh_scroll_region, bind_scroll_descendants
                    inner.update_idletasks()
                    canvas = getattr(inner, '_canvas', None)
                    if canvas is not None:
                        canvas.update_idletasks()
                        win_id = getattr(inner, '_canvas_win_id', None)
                        if win_id is not None:
                            w = max(canvas.winfo_width(), 200)
                            if w < 100:
                                w = max(inner.winfo_reqwidth(), 400)
                            canvas.itemconfig(win_id, width=w)
                    refresh_scroll_region(inner)
                    bind_scroll_descendants(inner, force=True)
                except Exception:
                    pass
            self._finish_page_scroll_region(page)
            if hasattr(page, 'on_page_shown'):
                try:
                    page.on_page_shown()
                except Exception:
                    pass
            if reload_if_empty and hasattr(page, 'ensure_data_loaded'):
                try:
                    page.ensure_data_loaded()
                except Exception:
                    pass

        try:
            parent = getattr(page, 'parent', None) or self.root
            parent.after_idle(_refresh)
        except Exception:
            _refresh()

    def _flush_startup_idle_work(self):
        self._pump_ui_until(
            lambda: not self._any_prewarm_data_still_loading(),
            timeout=180,
        )
        import time
        for _ in range(24):
            try:
                self.root.update()
            except Exception:
                break
            time.sleep(0.01)

    def _load_startup_alerts_at_startup(self):
        """Load alert data while the main window is still hidden."""
        import queue
        import time
        from core.startup_alerts import collect_startup_alerts

        result = queue.Queue()

        def _worker():
            try:
                result.put(collect_startup_alerts(self.conn, self.db_path))
            except Exception:
                result.put([])

        threading.Thread(target=_worker, daemon=True, name='StartupAlertsPrefetch').start()
        while True:
            try:
                return result.get_nowait()
            except queue.Empty:
                self._pump_startup_ui()
                time.sleep(0.02)

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
        from core.sync_prefs import is_online_mode

        if is_online_mode():
            # Server-only: no persistent store SQLite. Ephemeral :memory: shell
            # keeps legacy UI that still expects a connection object.
            try:
                from core.online_migrate import ensure_online_server_only_ready

                gate = ensure_online_server_only_ready(auto_wipe_empty=True)
                if gate.get("needs_migrate") and gate.get("has_data"):
                    self._run_online_migrate_gate(gate.get("db_path") or "")
            except Exception as exc:
                print(f"[online] migrate gate: {exc}")
            self.conn = sqlite3.connect(":memory:", check_same_thread=False)
            self.cursor = self.conn.cursor()
            self.db_path = ""
            db_initialise(self.conn)
            # The settings table is part of this throwaway shell, so thresholds
            # and reorder defaults would reset every launch. Refill them from
            # the durable mirror, which Offline writes to as well.
            try:
                from core.settings_mirror import apply_to

                apply_to(self.conn)
            except Exception:
                pass
            try:
                reload_slots_for_active_store()
            except Exception:
                pass
            return

        db_path = get_active_db_path()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn   = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA busy_timeout=30000')
        self.cursor = self.conn.cursor()
        self.db_path = db_path
        db_initialise(self.conn)
        # Keep the mirror in step with the real table, so a later switch to
        # Online carries this store's own settings instead of the defaults.
        try:
            from core.settings_mirror import capture_from

            capture_from(self.conn)
        except Exception:
            pass
        reload_slots_for_active_store()
        try:
            from core.backup_manager import sync_backup_config_to_active_store
            sync_backup_config_to_active_store()
        except Exception:
            pass

    def _run_online_migrate_gate(self, db_path: str) -> None:
        """Modal: Push local DB to server then delete, or delete and use server only."""
        from core.themed_messagebox import showerror, showinfo
        from core.online_migrate import push_local_then_wipe, wipe_local_store
        import threading
        import tkinter as tk
        from tkinter import ttk

        # Root is withdrawn during startup — must show it or the dialog is invisible
        # and wait_window blocks forever (app looks like it "never started").
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.update_idletasks()
            self.root.update()
        except Exception:
            pass

        result = {"choice": None}
        dlg = tk.Toplevel(self.root)
        dlg.title("Local data found — Online is server-only")
        dlg.transient(self.root)
        dlg.resizable(False, False)
        try:
            dlg.attributes("-topmost", True)
        except Exception:
            pass
        try:
            dlg.grab_set()
        except Exception:
            pass
        frm = ttk.Frame(dlg, padding=16)
        frm.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            frm,
            text=(
                "This PC still has a local store database.\n\n"
                "Online mode uses the server only. Choose:\n"
                "• Push to Server — upload local data, then delete the local DB\n"
                "• Delete Local — discard local data and use server data only"
            ),
            wraplength=420,
            justify=tk.LEFT,
        ).pack(anchor=tk.W)
        btns = ttk.Frame(frm)
        btns.pack(fill=tk.X, pady=(14, 0))

        def _pick(choice):
            result["choice"] = choice
            try:
                dlg.grab_release()
            except Exception:
                pass
            dlg.destroy()

        ttk.Button(btns, text="Push to Server", command=lambda: _pick("push")).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(btns, text="Delete Local", command=lambda: _pick("wipe")).pack(
            side=tk.LEFT, padx=4
        )
        dlg.update_idletasks()
        try:
            x = self.root.winfo_rootx() + 80
            y = self.root.winfo_rooty() + 80
            dlg.geometry(f"+{max(40, x)}+{max(40, y)}")
        except Exception:
            dlg.geometry("+120+120")
        dlg.lift()
        try:
            dlg.focus_force()
        except Exception:
            pass
        self.root.wait_window(dlg)

        choice = result["choice"]
        if choice not in ("push", "wipe"):
            showerror(
                "Online mode",
                "You must Push or Delete local data to continue in Online mode.",
                parent=self.root,
            )
            self.root.destroy()
            sys.exit(0)

        # Delete Local is file-only — run on UI thread so status isn't stuck on
        # "Starting…" and it finishes in under a second when the DB isn't locked.
        if choice == "wipe":
            try:
                self.root.config(cursor="watch")
                self.root.update_idletasks()
            except Exception:
                pass
            try:
                wipe_local_store(db_path=db_path, progress_cb=None)
            except Exception as exc:
                showerror("Online migrate", str(exc), parent=self.root)
                self.root.destroy()
                sys.exit(0)
            finally:
                try:
                    self.root.config(cursor="")
                except Exception:
                    pass
            try:
                showinfo(
                    "Online mode",
                    "Local database removed. Continuing with server-only Online mode.",
                    parent=self.root,
                )
            except Exception:
                pass
            try:
                self.root.withdraw()
            except Exception:
                pass
            return

        prog = tk.Toplevel(self.root)
        prog.title("Please wait…")
        prog.transient(self.root)
        try:
            prog.attributes("-topmost", True)
        except Exception:
            pass
        try:
            prog.grab_set()
        except Exception:
            pass
        status = tk.StringVar(value="Pushing local data to server…")
        ttk.Label(prog, textvariable=status, wraplength=400).pack(padx=20, pady=16)
        bar = ttk.Progressbar(prog, mode="indeterminate", length=360)
        bar.pack(padx=20, pady=(0, 16))
        bar.start(12)
        done = {"ok": False, "err": None, "res": None}
        prog.update_idletasks()

        def put(msg):
            def _set(m=msg):
                try:
                    status.set(m)
                    prog.update_idletasks()
                except Exception:
                    pass
            try:
                self.root.after(0, _set)
            except Exception:
                pass

        def work():
            try:
                done["res"] = push_local_then_wipe(db_path=db_path, progress_cb=put)
                done["ok"] = True
            except Exception as exc:
                done["err"] = str(exc)
            finally:
                try:
                    self.root.after(0, prog.destroy)
                except Exception:
                    pass

        threading.Thread(target=work, daemon=True, name="OnlineMigratePush").start()
        self.root.wait_window(prog)
        if done["err"] or not done["ok"]:
            showerror("Online migrate", done["err"] or "Migrate failed", parent=self.root)
            self.root.destroy()
            sys.exit(0)
        try:
            showinfo(
                "Online mode",
                "Local database removed. Continuing with server-only Online mode.",
                parent=self.root,
            )
        except Exception:
            pass
        try:
            self.root.withdraw()
        except Exception:
            pass

    def _run_app_login_gate(self) -> bool:
        """If App Login is enabled in Pharmacy Profile, require credentials before UI."""
        try:
            from core.login_prefs import is_login_enabled
            if not is_login_enabled():
                return True
        except Exception:
            return True
        try:
            # Briefly show root so the login dialog can parent correctly.
            self.root.deiconify()
            self.root.update_idletasks()
        except Exception:
            pass
        try:
            from widgets.app_login_dialog import show_app_login_dialog
            ok = show_app_login_dialog(self.root)
        except Exception:
            ok = True
        if ok:
            try:
                self.root.withdraw()
            except Exception:
                pass
        return bool(ok)

    def _update_window_title(self):
        try:
            from core.store_manager import get_active_display_name, has_registry
            store = get_active_display_name()
            if has_registry() and store:
                self.root.title(f"Satpuda Core Private Limited — {store}")
            else:
                self.root.title(
                    "Satpuda Core Private Limited — Medical Management Software")
        except Exception:
            self.root.title(
                "Satpuda Core Private Limited — Medical Management Software")

    # kept for backward compat (settings delete-all calls this)
    def create_tables(self):
        db_initialise(self.conn)

    # ── Navigation bar ────────────────────────────────────────────────────

    def _build_nav(self, *, show_home=True):
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
            ("Payment",          self.open_payment),
            ("Settings",         self.open_settings),
        ]
        for text, cmd in nav_items:
            try:
                btn = ttk.Button(nav_frame, text=text, width=16,
                                 bootstyle="outline-primary", style='Nav.TButton',
                                 command=lambda c=cmd, t=text: self.nav_click(c, t))
            except Exception:
                btn = ttk.Button(nav_frame, text=text, width=16, style='Nav.TButton',
                                 command=lambda c=cmd, t=text: self.nav_click(c, t))
            btn.pack(side=tk.LEFT, padx=4, pady=8)
            self.nav_buttons[text] = btn

        # Lightweight sync visibility hidden — keep vars for optional restore.
        self._sync_status_var = tk.StringVar(value="Sync: —")
        self._sync_status_label = None
        # (Previously: nav-bar Sync status label + click details. Hidden per request.)

        # Voice loads after home screen + master DB — keeps startup responsive.
        self._voice_nav_frame = nav_frame
        self._voice_init_pending = False
        self.root.after(600, self._schedule_voice_controls_deferred)
        # Help AI FAB — shown when startup overlay finishes (_finish_post_alert_loading).
        self.root.after(2500, self._ensure_tutor_float)

        self.active_nav = None
        ttk.Separator(self.root, orient='horizontal').pack(fill=tk.X, padx=10, pady=5)

        self.main_frame = ttk.Frame(self.root)
        self.main_frame.configure(takefocus=1)
        self.main_frame.pack(side="left", fill="both", expand=True, padx=10, pady=10)

        from core.page_cache import MainPageCache
        self._page_cache = MainPageCache(self.main_frame)

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
        if show_home:
            self.show_welcome()

    def _ensure_tutor_float(self):
        """Create Help AI and show the floating ? button."""
        self._init_tutor_float()
        tutor = getattr(self, '_tutor_chat_window', None)
        if tutor is not None:
            tutor.set_fab_allowed(True)

    def nav_click(self, command, text):
        self.active_nav = text
        self._style_nav_button(text)
        command()
        tutor = getattr(self, "_tutor_chat_window", None)
        if tutor is not None and tutor.is_visible():
            tutor.refresh_context()

    # ── App Tutor (Help AI) ───────────────────────────────────────────────

    def _init_tutor_float(self):
        try:
            from core.build_features import is_gemini_supported
            from core.gemini_tutor_config import is_tutor_enabled
            if not is_gemini_supported() or not is_tutor_enabled():
                return
            from widgets.tutor_chat_window import TutorChatWindow
            if getattr(self, "_tutor_chat_window", None) is None:
                self._tutor_chat_window = TutorChatWindow(self)
            else:
                self._tutor_chat_window.apply_settings()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Help AI init failed: %s", exc, exc_info=True)

    def _sync_tutor_ui(self):
        """Refresh floating Help AI after settings change."""
        try:
            from core.gemini_tutor_config import is_tutor_enabled
            tutor = getattr(self, "_tutor_chat_window", None)
            if tutor is not None:
                tutor.apply_settings()
                if is_tutor_enabled():
                    tutor.set_fab_allowed(True)
            elif is_tutor_enabled():
                self._ensure_tutor_float()
        except Exception:
            pass

    def _open_tutor_chat(self):
        from widgets.tutor_chat_window import open_tutor_chat
        open_tutor_chat(self)

    # ── Satpuda voice assistant ───────────────────────────────────────────

    def _init_voice_controls_deferred(self):
        nav = getattr(self, "_voice_nav_frame", None)
        if nav is None:
            return
        from core.voice.assistant_config import load_voice_assistant_enabled
        if not load_voice_assistant_enabled():
            return
        if getattr(self, "_voice_assistant", None) is not None:
            return
        loading = getattr(self.root, "_master_loading", False)
        if loading and not self._master_medicine_ready:
            self._voice_init_pending = True
            self.root.bind("<<MasterMedicineReady>>", self._on_master_ready_for_voice, add="+")
            return
        self._build_voice_controls(nav)

    def _schedule_voice_controls_deferred(self):
        self._init_voice_controls_deferred()
        if getattr(self, "_voice_init_pending", False):
            # Fallback if master-ready event never fires (non-medical / quick load).
            self.root.after(5000, self._init_voice_if_still_pending)

    def _on_master_ready_for_voice(self, _event=None):
        if not getattr(self, "_voice_init_pending", False):
            return
        self._voice_init_pending = False
        nav = getattr(self, "_voice_nav_frame", None)
        if nav is not None:
            self.root.after(2500, lambda: self._build_voice_controls(nav))

    def _init_voice_if_still_pending(self):
        if not getattr(self, "_voice_init_pending", False):
            return
        if getattr(self, "_voice_assistant", None) is not None:
            self._voice_init_pending = False
            return
        if getattr(self.root, "_master_loading", False):
            self.root.after(3000, self._init_voice_if_still_pending)
            return
        self._voice_init_pending = False
        nav = getattr(self, "_voice_nav_frame", None)
        if nav is not None:
            self._build_voice_controls(nav)

    def _build_voice_controls(self, nav_frame):
        self._voice_dialog = None
        self._voice_overlay = None
        self._voice_assistant = None
        self._voice_nav_btn = None

        from core.voice.assistant import SatpudaVoiceAssistant
        self._voice_overlay = None
        self._voice_assistant = SatpudaVoiceAssistant(
            self,
            on_status=self._on_voice_status,
            on_result=self._on_voice_result,
            on_state=self._on_voice_state,
            on_audio_level=self._on_voice_audio_level,
            on_mic_changed=self._update_voice_nav_button,
            on_heard=self._on_voice_heard,
            on_live_heard=self._on_voice_live_heard,
        )
        # Whisper model loads when the voice dialog opens (not at app startup).
        self._update_voice_nav_button()

    def _voice_overlay_ref(self):
        return getattr(self, "_voice_overlay", None)

    def _ensure_voice_overlay(self):
        if self._voice_overlay is None:
            from widgets.satpuda_float_overlay import SatpudaFloatOverlay
            self._voice_overlay = SatpudaFloatOverlay(self.root)
        return self._voice_overlay

    def _update_voice_nav_button(self):
        btn = getattr(self, "_voice_nav_btn", None)
        assistant = getattr(self, "_voice_assistant", None)
        if btn is None:
            return
        enabled = assistant is not None and assistant.enabled
        standby = enabled and assistant.in_standby
        actively_listening = enabled and not standby
        overlay = self._voice_overlay_ref()
        if actively_listening:
            self._ensure_voice_overlay().show()
        elif overlay is not None:
            overlay.hide()
        try:
            if standby:
                btn.configure(text="Standby", bootstyle="warning")
            elif enabled:
                btn.configure(text="Listening", bootstyle="success")
            else:
                btn.configure(text="Satpuda", bootstyle="outline-secondary")
        except Exception:
            try:
                if standby:
                    btn.configure(text="Standby")
                else:
                    btn.configure(text="Listening" if enabled else "Satpuda")
            except Exception:
                pass
        self._sync_voice_dialog_mic()

    def _open_voice_dialog(self):
        from widgets.satpuda_voice_dialog import open_satpuda_voice_dialog
        open_satpuda_voice_dialog(self)

    def _voice_dialog_ref(self):
        dlg = getattr(self, "_voice_dialog", None)
        if dlg is None:
            return None
        try:
            if dlg.win.winfo_exists():
                return dlg
        except Exception:
            pass
        return None

    def _on_voice_state(self, state: str, detail: str = ""):
        overlay = self._voice_overlay_ref()
        if overlay is not None:
            overlay.on_state(state, detail)
        dlg = self._voice_dialog_ref()
        if dlg is not None:
            dlg.on_state(state, detail)

    def _sync_voice_dialog_mic(self):
        dlg = self._voice_dialog_ref()
        if dlg is not None and hasattr(dlg, "sync_mic_from_assistant"):
            dlg.sync_mic_from_assistant()

    def _on_voice_audio_level(self, level: float):
        overlay = self._voice_overlay_ref()
        if overlay is not None:
            overlay.on_level(level)
        dlg = self._voice_dialog_ref()
        if dlg is not None:
            dlg.on_audio_level(level)

    def _on_voice_status(self, message: str):
        overlay = self._voice_overlay_ref()
        if overlay is not None:
            overlay.on_status(message)
        dlg = self._voice_dialog_ref()
        if dlg is not None:
            dlg.on_status(message)

    def _reload_voice_settings(self):
        from core.voice.command_parser import reload_voice_parser_config
        reload_voice_parser_config()
        assistant = getattr(self, "_voice_assistant", None)
        if assistant is not None:
            assistant.reload_voice_settings()
        overlay = self._voice_overlay_ref()
        if overlay is not None:
            overlay.refresh_assistant_name()
        dlg = self._voice_dialog_ref()
        if dlg is not None:
            dlg.refresh_assistant_name()

    def _reload_voice_assistant_name(self):
        self._reload_voice_settings()

    def _on_voice_live_heard(self, text: str):
        overlay = self._voice_overlay_ref()
        if overlay is not None:
            overlay.on_live_heard(text)
        dlg = self._voice_dialog_ref()
        if dlg is not None:
            dlg.on_live_heard(text)

    def _on_voice_heard(self, text: str):
        overlay = self._voice_overlay_ref()
        if overlay is not None:
            overlay.on_heard(text)
        dlg = self._voice_dialog_ref()
        if dlg is not None:
            dlg.on_heard(text)

    def _on_voice_result(self, recognized: str, action: str, detail: str = "", spoken_source: str = ""):
        assistant = getattr(self, "_voice_assistant", None)
        in_standby = assistant is not None and assistant.enabled and assistant.in_standby
        overlay = self._voice_overlay_ref()
        if overlay is not None:
            if in_standby:
                overlay.hide()
            elif action == "Working":
                overlay.on_working(recognized, detail or "Working…")
            elif action.startswith("Opened"):
                overlay.on_success(recognized, action)
            elif action.startswith("Action"):
                overlay.on_success(recognized, detail or action)
            elif action in ("No action", "Failed"):
                msg = detail or action
                overlay.on_error(recognized, msg)
        dlg = self._voice_dialog_ref()
        if dlg is not None:
            dlg.on_result(recognized, action, detail)
        if action in ("Failed", "No action"):
            try:
                from core.voice.tts import speak_navigation_feedback
                speak_navigation_feedback(action, detail, spoken_source or recognized)
            except Exception:
                pass
        if in_standby:
            self._update_voice_nav_button()

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
            '7': _nav(self.open_payment, 'Payment'),
            '8': _nav(self.open_settings, 'Settings'),
        })
        tab_map = {
            1: lambda: self.open_settings('Pharmacy Profile'),
            2: lambda: self.open_settings('Contacts'),
            3: lambda: self.open_settings('Shelf Management'),
            4: lambda: self.open_settings('Appearance'),
            5: lambda: self.open_settings('Layout & Lists'),
            6: lambda: self.open_settings('Import'),
            7: lambda: self.open_settings('Alert & Monitoring'),
            8: lambda: self.open_settings('Management'),
            9: lambda: self.open_settings('Payment'),
            0: lambda: self.open_settings('Ledger'),
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
                self.root.after_idle(
                    lambda: KeyboardRegistry.set_nav_mode(True)
                )
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
                step1_tree = getattr(child, 'step1_tree', None)
                step2_tree = getattr(child, 'step2_tree', None)
                if target in (child.step1_entry, step1_tree, step2_tree):
                    if getattr(child, 'step2_visible', False):
                        child.hide_step2()
                    elif getattr(child, 'step1_visible', False):
                        child.hide_step1()
                    else:
                        from core.keyboard_registry import KeyboardRegistry
                        KeyboardRegistry.blur_to_nav()
                    return True
            if hasattr(child, 'entry') and hasattr(child, 'list_visible'):
                listbox = getattr(child, 'listbox', None)
                if child.entry is target or listbox is target:
                    if hasattr(child, 'dismiss'):
                        child.dismiss(blur=True)
                    else:
                        child.hide_list()
                        from core.keyboard_registry import KeyboardRegistry
                        KeyboardRegistry.blur_to_nav()
                    return True
            if self._close_dropdown_in_frame(child, target): return True
        return False

    # ── Page management ───────────────────────────────────────────────────

    def clear_main_frame(self):
        """Hide all cached pages (legacy name — does not destroy widgets)."""
        self._page_cache.hide_all()

    def invalidate_page_cache(self):
        """Destroy every cached page shell (e.g. after a full data reset)."""
        self._page_cache.invalidate_all()
        self._billing_page = None
        self._purchase_page = None
        self._sales_history_page = None
        self._purchase_history_page = None
        self._inventory_page = None
        self._settings_page = None
        self._payment_page = None
        self._general_products_page = None
        self._sales_return_page = None
        self._purchase_return_page = None
        self._stock_disposal_page = None
        self._reorder_page = None
        self._home_built = False
        self._returns_shell_built = False
        self._settings_notebook_bound = False
        self._returns_outer = None
        self._returns_bindings = None
        self._returns_show = None
        self._home_inner_frame = None
        self._home_keyboard_bindings = None

    def _style_nav_button(self, text):
        for btn in self.nav_buttons.values():
            try:
                btn.configure(bootstyle="outline-primary")
            except Exception:
                btn.configure(style="Nav.TButton")
        if text is not None:
            try:
                self.nav_buttons[text].configure(bootstyle="primary")
            except Exception:
                pass

    def _wire_scroll_page(self, page):
        inner = getattr(page, '_inner_frame', None)
        if inner is None:
            return
        canvas = getattr(inner, '_canvas', None)
        self.input_ctrl.set_active_canvas(canvas)
        self.input_ctrl.set_active_frame(inner)
        from core.keyboard_registry import KeyboardRegistry
        bindings = getattr(inner, '_keyboard_bindings', None)
        if bindings is None:
            page._register_keyboard()
            bindings = getattr(inner, '_keyboard_bindings', None)
        if bindings:
            KeyboardRegistry.activate_page(inner, bindings)

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

    def _ensure_settings_page(self):
        container = self._page_cache.show('settings')
        if self._settings_page is None:
            from ui.settings import SettingsPage
            self._settings_page = SettingsPage(container, self.conn)
            self._settings_page.parent = container
            if not self._settings_notebook_bound:
                self._settings_page._notebook.bind(
                    '<<NotebookTabChanged>>',
                    lambda e: (self._update_settings_canvas(self._settings_page),
                               self._settings_page.refresh_keyboard_bindings()),
                )
                self._settings_notebook_bound = True
        return self._settings_page

    def show_welcome(self):
        self.active_nav = '🏠 Home'
        self._style_nav_button('🏠 Home')
        container = self._page_cache.show('home')
        if not self._home_built:
            from ui.shared.home_page import build_home
            build_home(
                main_frame=container,
                conn=self.conn,
                nav_click_fn=self.nav_click,
                open_billing_fn=lambda: self.nav_click(self.open_billing, 'Sales'),
                open_purchase_fn=lambda: self.nav_click(self.open_purchase, 'Purchase'),
                open_inventory_fn=lambda: self.nav_click(self.open_inventory, 'Inventory'),
                open_contacts_fn=lambda: self.nav_click(self.open_contacts, 'Settings'),
                open_ledger_fn=lambda: self.nav_click(lambda: self.open_settings('Ledger'), 'Settings'),
                open_alerts_fn=lambda: self.nav_click(self.open_alerts, 'Settings'),
                open_general_products_fn=self.open_general_products,
                input_ctrl=self.input_ctrl,
                register_canvas_fn=self._register_canvas,
                db_path=self.db_path,
            )
            self._home_built = True
        else:
            from ui.shared.home_page import refresh_home_dashboard
            refresh_home_dashboard(container, self.conn)
        from core.keyboard_registry import KeyboardRegistry
        inner = getattr(self, '_home_inner_frame', None)
        if inner is not None:
            self._register_canvas(inner)
            bindings = getattr(self, '_home_keyboard_bindings', None) or getattr(
                inner, '_keyboard_bindings', None)
            if bindings:
                KeyboardRegistry.register_page(inner, bindings)
                KeyboardRegistry.activate_page(inner, bindings)
        KeyboardRegistry.set_nav_mode(True)

    def open_general_products(self):
        """Standalone general product rates — home quick access only (not in top nav)."""
        self._style_nav_button(None)
        self.active_nav = None
        container = self._page_cache.show('general_products')
        if self._general_products_page is None:
            from ui.general_products import GeneralProductsPage
            self._general_products_page = GeneralProductsPage(container, self.conn)
        page = self._general_products_page
        inner = getattr(page, '_inner_frame', None)
        if inner is not None:
            container.after(50, lambda: self._register_canvas(inner))

    def open_billing(self):
        self._page_cache.show('billing')
        if self._billing_page is None:
            from ui.billing import BillingPage
            container = self._page_cache.container('billing')
            self._billing_page = BillingPage(container, self.conn)
            self._billing_page.parent = container
        self._activate_cached_page(self._billing_page, reload_if_empty=False)
        try:
            self._billing_page._rebind_mousewheel()
            self._billing_page._apply_location_column_visibility()
            self._billing_page._refresh_margin_summary_visibility()
            self._billing_page.reload_villages()
        except Exception:
            pass
        self.root.after(50, self._billing_page._focus_first_field)

    def open_purchase(self):
        self._page_cache.show('purchase')
        if self._purchase_page is None:
            from ui.purchase import PurchasePage
            container = self._page_cache.container('purchase')
            self._purchase_page = PurchasePage(container, self.conn)
            self._purchase_page.parent = container
        self._activate_cached_page(self._purchase_page, reload_if_empty=False)
        try:
            self._purchase_page._rebind_mousewheel()
            self._purchase_page.refresh_layout_dropdowns()
        except Exception:
            pass
        self.root.after(50, self._purchase_page._focus_supplier_name)

    def open_sales_history(self):
        self._page_cache.show('sales_history')
        if self._sales_history_page is None:
            from ui.sales.sales_history import SalesHistoryPage
            container = self._page_cache.container('sales_history')
            self._sales_history_page = SalesHistoryPage(container, self.conn)
            self._sales_history_page.parent = container
        self._activate_cached_page(self._sales_history_page, reload_if_empty=True)

    def open_purchase_history(self):
        self._page_cache.show('purchase_history')
        if self._purchase_history_page is None:
            from ui.purchase.purchase_history import PurchaseHistoryPage
            container = self._page_cache.container('purchase_history')
            self._purchase_history_page = PurchaseHistoryPage(container, self.conn)
            self._purchase_history_page.parent = container
        self._activate_cached_page(self._purchase_history_page, reload_if_empty=True)

    def open_inventory(self):
        self._page_cache.show('inventory')
        if self._inventory_page is None:
            from ui.inventory import InventoryPage
            container = self._page_cache.container('inventory')
            self._inventory_page = InventoryPage(container, self.conn)
            self._inventory_page.parent = container
        self._activate_cached_page(self._inventory_page, reload_if_empty=True)

    def open_reorder(self, prefill=None, bulk=False):
        """Open Settings -> Reorder tab."""
        if bulk:
            prefill = {"bulk": True}
        self.active_nav = 'Settings'
        self._style_nav_button('Settings')
        page = self._ensure_settings_page()
        page.open_reorder(prefill=prefill, bulk=bulk)
        self.root.update_idletasks()
        self._update_settings_canvas(page)
        page.refresh_keyboard_bindings()

    def open_stock_disposal(self, prefill=None, bulk=False, include_expired=True, include_near_expiry=True):
        if bulk:
            from core.stock_disposal_service import start_bulk_return_sequence
            from core.themed_messagebox import showinfo
            if not start_bulk_return_sequence(
                self, include_expired=include_expired, include_near_expiry=include_near_expiry,
            ):
                showinfo(
                    "Return by Purchase",
                    "No near-expiry or expired medicines with stock to return.",
                    parent=self.root,
                )
            return
        self.active_nav = 'Returns'
        self.open_returns(kind='disposal', prefill=prefill)

    def open_payment(self, which=None):
        """Top-nav Payment page (supplier / customer)."""
        self.active_nav = 'Payment'
        self._style_nav_button('Payment')
        outer = self._page_cache.show('payment')
        if which not in ("supplier", "customer"):
            which = None
        if self._payment_page is None:
            from ui.settings.settings_tabs.payment_combined_tab import PaymentCombinedTab

            self._payment_page = PaymentCombinedTab(None, self.conn, host=outer)

            def _refresh():
                from core.keyboard_registry import KeyboardRegistry

                KeyboardRegistry.register_page(
                    outer, self._payment_page.get_keyboard_bindings()
                )

            self._payment_page._keyboard_refresh = _refresh
            _refresh()
        if which:
            self._payment_page._show(which)
        else:
            refresh = getattr(self._payment_page, "_keyboard_refresh", None)
            if callable(refresh):
                refresh()

    def open_contacts(self, subtab="Customers"):
        """Customers / doctors / suppliers — Settings → Contacts only."""
        page = self._ensure_settings_page()
        self.active_nav = 'Settings'
        page.open_contacts(subtab)
        self._update_settings_canvas(page)
        page.refresh_keyboard_bindings()

    def open_returns(self, kind=None, prefill=None):
        self.active_nav = 'Returns'
        outer = self._page_cache.show('returns')

        if not self._returns_shell_built:
            btn_bar = ttk.Frame(outer)
            btn_bar.pack(fill=tk.X, padx=10, pady=(8, 0))
            container = ttk.Frame(outer)
            container.pack(fill=tk.BOTH, expand=True)
            sales_holder = ttk.Frame(container)
            purchase_holder = ttk.Frame(container)
            disposal_holder = ttk.Frame(container)

            from core.keyboard_registry import KeyboardRegistry, PageBindings

            def _show(selected_kind, return_prefill=None):
                self._returns_last_kind = selected_kind
                sales_holder.pack_forget()
                purchase_holder.pack_forget()
                disposal_holder.pack_forget()
                pf = return_prefill if isinstance(return_prefill, dict) else {}
                if selected_kind == 'sales':
                    if self._sales_return_page is None:
                        from ui.returns.sales_return import SalesReturnPage
                        self._sales_return_page = SalesReturnPage(sales_holder, self.conn)
                    page = self._sales_return_page
                    sales_holder.pack(fill=tk.BOTH, expand=True)
                    try:
                        sales_btn.configure(bootstyle="primary")
                        pur_btn.configure(bootstyle="outline-secondary")
                        disp_btn.configure(bootstyle="outline-secondary")
                    except Exception:
                        pass
                elif selected_kind == 'purchase':
                    is_bulk = bool(pf.get("purchase_groups") or pf.get("writeoff_lines"))
                    if is_bulk or getattr(self, "_purchase_return_is_bulk", False):
                        for child in purchase_holder.winfo_children():
                            child.destroy()
                        self._purchase_return_page = None
                    if is_bulk:
                        from ui.returns.purchase_return import BulkPurchaseReturnPage
                        self._purchase_return_page = BulkPurchaseReturnPage(
                            purchase_holder, self.conn, pf)
                        self._purchase_return_is_bulk = True
                    else:
                        self._purchase_return_is_bulk = False
                        if self._purchase_return_page is None:
                            from ui.returns.purchase_return import PurchaseReturnPage
                            self._purchase_return_page = PurchaseReturnPage(
                                purchase_holder, self.conn)
                    page = self._purchase_return_page
                    purchase_holder.pack(fill=tk.BOTH, expand=True)
                    try:
                        pur_btn.configure(bootstyle="primary")
                        sales_btn.configure(bootstyle="outline-secondary")
                        disp_btn.configure(bootstyle="outline-secondary")
                    except Exception:
                        pass
                else:
                    for child in disposal_holder.winfo_children():
                        child.destroy()
                    from ui.returns.stock_disposal_page import StockDisposalPage
                    self._stock_disposal_page = StockDisposalPage(
                        disposal_holder, self.conn, prefill=pf,
                    )
                    page = self._stock_disposal_page
                    disposal_holder.pack(fill=tk.BOTH, expand=True)
                    try:
                        disp_btn.configure(bootstyle="primary")
                        sales_btn.configure(bootstyle="outline-secondary")
                        pur_btn.configure(bootstyle="outline-secondary")
                    except Exception:
                        pass
                inner = getattr(page, '_inner_frame', None)
                if inner is not None:
                    self._register_canvas(inner)
                    if hasattr(page, 'get_keyboard_bindings'):
                        kb = page.get_keyboard_bindings()
                        inner._keyboard_bindings = kb
                        KeyboardRegistry.register_page(inner, kb)
                KeyboardRegistry.register_page(outer, self._returns_bindings)
                if inner is not None and hasattr(page, 'get_keyboard_bindings'):
                    KeyboardRegistry.activate_page(inner, page.get_keyboard_bindings())

            self._returns_show = _show
            returns_bindings = PageBindings(
                page_id='returns',
                sub_keys={
                    's': lambda: _show('sales'),
                    'p': lambda: _show('purchase'),
                    'w': lambda: _show('disposal'),
                },
            )
            self._returns_bindings = returns_bindings
            outer._keyboard_bindings = returns_bindings
            self._returns_outer = outer

            try:
                sales_btn = ttk.Button(
                    btn_bar, text="🧾 Sales Return", command=lambda: _show('sales'),
                    bootstyle="primary", width=20)
                pur_btn = ttk.Button(
                    btn_bar, text="📦 Purchase Return", command=lambda: _show('purchase'),
                    bootstyle="outline-secondary", width=20)
                disp_btn = ttk.Button(
                    btn_bar, text="⚠ Return / Write-off", command=lambda: _show('disposal'),
                    bootstyle="outline-secondary", width=22)
            except Exception:
                sales_btn = ttk.Button(
                    btn_bar, text="🧾 Sales Return", command=lambda: _show('sales'), width=20)
                pur_btn = ttk.Button(
                    btn_bar, text="📦 Purchase Return", command=lambda: _show('purchase'), width=20)
                disp_btn = ttk.Button(
                    btn_bar, text="⚠ Return / Write-off", command=lambda: _show('disposal'), width=22)
            sales_btn.pack(side=tk.LEFT, padx=6, pady=4)
            pur_btn.pack(side=tk.LEFT, padx=6, pady=4)
            disp_btn.pack(side=tk.LEFT, padx=6, pady=4)
            self._returns_shell_built = True
            _show(kind or 'sales', prefill if kind in ('disposal', 'purchase') else None)
        else:
            if kind:
                self._returns_show(kind, prefill if kind in ('disposal', 'purchase') else None)
            else:
                self._returns_show(getattr(self, '_returns_last_kind', 'sales'))
            from core.keyboard_registry import KeyboardRegistry
            KeyboardRegistry.register_page(self._returns_outer, self._returns_bindings)

    def open_settings(
        self,
        select_tab=None,
        contacts_sub=None,
        management_sub=None,
        payment_sub=None,
        ledger_sub=None,
        import_sub=None,
        pharmacy_sub=None,
        layout_sub=None,
        sales_billing_sub=None,
        alerts_sub=None,
        reorder_sub=None,
    ):
        if select_tab == "Payment" or payment_sub:
            self.open_payment(payment_sub or "supplier")
            return
        page = self._ensure_settings_page()
        self.active_nav = 'Settings'
        if select_tab:
            page._select_tab(select_tab)
        if contacts_sub:
            page.open_contacts(contacts_sub)
        elif management_sub:
            page.open_management(management_sub)
        elif import_sub:
            page.open_import(import_sub)
        elif payment_sub:
            page.open_payment(payment_sub)
        elif ledger_sub:
            page.open_ledger(ledger_sub)
        elif pharmacy_sub and hasattr(page, '_pharmacy'):
            page._select_tab('Pharmacy Profile')
            page._pharmacy.select_section(pharmacy_sub)
        elif layout_sub and hasattr(page, '_layout'):
            page._layout.open_section(layout_sub)
        elif sales_billing_sub:
            page.open_sales_billing(sales_billing_sub)
        elif alerts_sub:
            page.open_alerts(alerts_sub)
        elif reorder_sub:
            page.open_reorder_settings(reorder_sub)
        elif select_tab in ('Alert & Monitoring', 'Alerts'):
            page.open_alerts()
        elif select_tab == 'Reorder':
            page.open_reorder_settings('pending')
        elif select_tab == 'Sales & Billing':
            page.open_sales_billing()
        elif not select_tab:
            page._select_tab('Pharmacy Profile')
        self.root.update_idletasks()
        self._update_settings_canvas(page)
        page.refresh_keyboard_bindings()

    def open_alerts(self):
        """Open Settings → Alert & Monitoring from home quick action."""
        page = self._ensure_settings_page()
        self.active_nav = 'Settings'
        page.open_alerts()
        self.root.update_idletasks()
        self._update_settings_canvas(page)
        page.refresh_keyboard_bindings()

    def _update_settings_canvas(self, settings_page):
        try:
            tab_id     = settings_page._notebook.select()
            tab_widget = settings_page._notebook.nametowidget(tab_id)
            tab_text   = settings_page._notebook.tab(tab_id, 'text')
            if tab_text in ('Appearance', 'Layout & Lists'):
                settings_page._layout.sync_input_canvas()
            elif tab_text in ('Management', 'Data & System'):
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

    def _prepare_master_medicine_background(self):
        """Prepare master medicines DB in background — no startup modal."""
        mode = load_app_mode()
        self.root._master_mode = mode
        self.root._master_loading = (mode == 'medical')
        self.root._master_ready = (mode != 'medical')
        self._master_medicine_ready = (mode != 'medical')
        if mode != 'medical':
            try:
                ensure_mode_master_state(mode)
            except Exception:
                pass
            return

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

        threading.Thread(target=_worker, daemon=True, name='MasterMedicinePrep').start()

        def _poll():
            try:
                while True:
                    item = q.get_nowait()
                    if item and item[0] == "done":
                        _ok, _count, _msg = item[1], item[2], item[3]
                        self.root._master_loading = False
                        self.root._master_ready = bool(_ok)
                        self._master_medicine_ready = bool(_ok)
                        self.root.event_generate("<<MasterMedicineReady>>", when="tail")
                        return
            except queue.Empty:
                pass
            self.root.after(200, _poll)

        _poll()

    def _prepare_master_medicine_on_startup(self):
        """Legacy name — redirects to background preparation."""
        self._prepare_master_medicine_background()

    # ── Backup ────────────────────────────────────────────────────────────

    def _start_backup(self):
        try:
            from core.sync_coordinator import should_run_drive_backup
            if not should_run_drive_backup():
                return  # Online mode — Server sync already started earlier
            from core.backup_manager import is_auto_backup_enabled, run_backup_on_open
            if not is_auto_backup_enabled():
                return
            run_backup_on_open(on_error=self._show_backup_error)
            self.root.after(60 * 60 * 1000, self._schedule_backup)
        except Exception:
            pass

    def _start_server_sync_if_online(self):
        """Start Online server sync (legacy method name kept for callers)."""
        try:
            from core.build_features import is_server_sync_supported
            from core.sync_prefs import is_online_mode
            from core.sync_coordinator import stop_online_sync

            # Offline = local SQLite is source of truth. Never leave the server
            # poller writing the store DB (including after a failed online attempt).
            if not is_server_sync_supported() or not is_online_mode():
                try:
                    stop_online_sync()
                except Exception:
                    pass
                self._server_sync_started = False
                return
            self._start_server_sync()
        except Exception:
            pass

    def _start_server_sync(self):
        """Start Online Satpuda Core Server live hints (server-only Online)."""
        if getattr(self, '_server_sync_started', False):
            return
        try:
            from core.sync_prefs import is_online_mode
            from core.sync_coordinator import start_online_sync
            if not is_online_mode():
                return
            self._server_sync_started = True

            def _start_listeners():
                try:
                    started = start_online_sync(
                        None,
                        on_change=self._on_server_data_changed,
                        db_path=None,
                        adopt_server_head=True,
                        hints_only=True,
                    )
                    if started:
                        try:
                            from core.sync_status import note_last_sync
                            note_last_sync('online')
                        except Exception:
                            pass
                    try:
                        self.root.after(0, self._refresh_sync_status)
                    except Exception:
                        pass
                except Exception:
                    pass

            threading.Thread(
                target=_start_listeners, daemon=True, name='ServerSyncStart',
            ).start()

            try:
                from core.online_guard import (
                    start_connectivity_monitor,
                    set_reconnect_handler,
                    register_status_listener,
                    refresh_status,
                )

                def _on_reconnect():
                    def _work():
                        try:
                            from core.sync_coordinator import start_online_sync

                            start_online_sync(
                                None,
                                on_change=self._on_server_data_changed,
                                db_path=None,
                                adopt_server_head=True,
                                hints_only=True,
                            )
                        except Exception:
                            pass
                        try:
                            self.root.after(0, self._refresh_sync_status)
                        except Exception:
                            pass

                    threading.Thread(
                        target=_work, daemon=True, name='ServerReconnectRefetch',
                    ).start()

                set_reconnect_handler(_on_reconnect)
                register_status_listener(
                    lambda text: self.root.after(0, lambda t=text: self._set_online_status(t))
                )
                start_connectivity_monitor()
                refresh_status()
            except Exception:
                pass
        except Exception as exc:
            try:
                from core.themed_messagebox import showwarning
                showwarning('Server Sync', str(exc), parent=self.root)
            except Exception:
                pass

    def _set_online_status(self, text: str):
        try:
            var = getattr(self, '_sync_status_var', None)
            if var is not None and text:
                var.set(text)
        except Exception:
            pass

    def _refresh_sync_status(self):
        try:
            from core.sync_prefs import is_online_mode
            var = getattr(self, '_sync_status_var', None)
            if var is not None:
                if is_online_mode():
                    from core.online_guard import status_label
                    var.set(status_label())
                else:
                    from core.sync_status import format_status_line
                    var.set(format_status_line())
        except Exception:
            pass

    def _poll_sync_status(self):
        self._refresh_sync_status()
        try:
            self.root.after(5000, self._poll_sync_status)
        except Exception:
            pass

    def _show_sync_status_details(self):
        try:
            from core.sync_status import snapshot
            from core.themed_messagebox import showinfo
            snap = snapshot()
            lines = [
                f"Last sync: {snap.get('last_sync_at') or 'never'} ({snap.get('last_sync_kind') or '-'})",
                f"Pending pushes: {snap.get('pending_count', 0)}",
            ]
            for p in snap.get('pending') or []:
                lines.append(f"  • {p}")
            lines.append(f"Conflict skips: {snap.get('skip_count', 0)}")
            for s in snap.get('skips') or []:
                lines.append(f"  • {s}")
            if snap.get('errors'):
                lines.append("Errors:")
                for e in snap['errors']:
                    lines.append(f"  • {e}")
            showinfo("Sync status", "\n".join(lines), parent=self.root)
        except Exception as exc:
            try:
                from core.themed_messagebox import showwarning
                showwarning("Sync status", str(exc), parent=self.root)
            except Exception:
                pass

    def _on_server_data_changed(self, collection: str):
        """Refresh open pages when remote Server data arrives (debounced).

        Online UI list pages subscribe to store_live_refresh directly — do not
        also emit DataChangeBus / queue_sync_refresh for those collections
        (avoids triple rebuilds per hint).
        """
        try:
            from core.sync_status import note_last_sync
            note_last_sync(f'pull:{collection}')
            self._refresh_sync_status()
        except Exception:
            pass

        online = False
        try:
            from core.sync_prefs import is_online_mode
            online = bool(is_online_mode())
        except Exception:
            online = False

        # Offline / Sync V3 still uses DataChangeBus. Online live lists use
        # store_live_refresh only (single refresh trigger per change).
        if not online:
            try:
                from core.sync_v3.data_change_bus import emit

                emit(collection or "all", local=False)
            except Exception:
                pass

        self._server_pending_cols.add(collection)
        try:
            if self._server_refresh_job is not None:
                self.root.after_cancel(self._server_refresh_job)
        except Exception:
            pass
        try:
            # Short debounce keeps Online sync near-instant without full flicker storms.
            self._server_refresh_job = self.root.after(400, self._flush_server_refresh)
        except Exception:
            self._flush_server_refresh()

    def _flush_server_refresh(self):
        self._server_refresh_job = None
        cols = self._server_pending_cols
        self._server_pending_cols = set()
        if not cols:
            return
        nav = getattr(self, 'active_nav', '')
        online = False
        try:
            from core.sync_prefs import is_online_mode
            online = bool(is_online_mode())
        except Exception:
            online = False

        def _refresh():
            try:
                # Always refresh cached pages for changed collections — do not
                # require the user to already be looking at that page (stale cache).
                inventory_cols = {'medicines', 'stock_operations'}
                sales_hist_cols = {'sales', 'customer_payments', 'sales_returns'}
                purchase_hist_cols = {
                    'purchases', 'supplier_payments', 'purchase_returns', 'suppliers',
                }
                billing_cols = {'medicines', 'customers'}
                purchase_entry_cols = {'suppliers', 'medicines'}
                refresh_all = bool(cols & {'all', 'server'})

                # Online: inventory / sales history / purchase history already
                # refresh via store_live_refresh — skip duplicate queue_sync_refresh.
                if not online:
                    if refresh_all or ((cols & inventory_cols) and self._inventory_page):
                        if self._inventory_page:
                            if hasattr(self._inventory_page, 'queue_sync_refresh'):
                                self._inventory_page.queue_sync_refresh()
                            elif hasattr(self._inventory_page, 'load_inventory'):
                                self._inventory_page.load_inventory()
                    if refresh_all or ((cols & sales_hist_cols) and self._sales_history_page):
                        if self._sales_history_page:
                            if hasattr(self._sales_history_page, 'queue_sync_refresh'):
                                self._sales_history_page.queue_sync_refresh()
                            elif hasattr(self._sales_history_page, 'load_sales_history'):
                                self._sales_history_page.load_sales_history()
                    if refresh_all or ((cols & purchase_hist_cols) and self._purchase_history_page):
                        if self._purchase_history_page:
                            if hasattr(self._purchase_history_page, 'queue_sync_refresh'):
                                self._purchase_history_page.queue_sync_refresh()
                            elif hasattr(self._purchase_history_page, 'load_purchase_history'):
                                self._purchase_history_page.load_purchase_history()
                if nav == 'Sales' and (refresh_all or (cols & billing_cols)) and self._billing_page:
                    combo = getattr(self._billing_page, 'medicine_combo', None)
                    if combo is not None and hasattr(combo, 'load_medicine_names'):
                        combo.load_medicine_names()
                if nav == 'Purchase' and (refresh_all or (cols & purchase_entry_cols)) and self._purchase_page:
                    if hasattr(self._purchase_page, 'refresh_layout_dropdowns'):
                        self._purchase_page.refresh_layout_dropdowns()

                if self._home_built and hasattr(self, '_refresh_home_stats'):
                    if refresh_all or (cols & (sales_hist_cols | purchase_hist_cols | inventory_cols)):
                        self._refresh_home_stats()
            except Exception:
                pass

        try:
            self.root.after(0, _refresh)
        except Exception:
            _refresh()

    def _schedule_backup(self):
        try:
            from core.sync_coordinator import should_run_drive_backup
            if not should_run_drive_backup():
                return
            from core.backup_manager import is_auto_backup_enabled, run_backup_silently
            if not is_auto_backup_enabled():
                return
            run_backup_silently(on_error=self._show_backup_error)
            self.root.after(60 * 60 * 1000, self._schedule_backup)
        except Exception:
            pass

    def _on_close(self):
        try:
            self._stop_post_alert_loading()
        except Exception:
            pass
        try:
            dlg = self._voice_dialog_ref()
            if dlg is not None:
                dlg.close()
            if getattr(self, '_voice_assistant', None):
                self._voice_assistant.disable()
            overlay = self._voice_overlay_ref()
            if overlay is not None:
                overlay.hide()
        except Exception:
            pass
        try:
            from core.sync_coordinator import should_run_drive_backup, stop_online_sync
            stop_online_sync()
            from core.backup_manager import is_auto_backup_enabled, run_backup_now
            if should_run_drive_backup() and is_auto_backup_enabled():
                run_backup_now()
        except Exception:
            pass
        # Teardown can race with pending after()/widget cmds (ttkbootstrap).
        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        except Exception:
            pass

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
        if getattr(self, "_startup_alerts_scheduled", False):
            return
        self._startup_alerts_scheduled = True

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
        self._startup_alerts_scheduled = False
        self._startup_alerts_shown = True
        try:
            from core.startup_alerts_prefs import should_show_startup_alerts
            if not should_show_startup_alerts():
                from core.keyboard_registry import KeyboardRegistry
                KeyboardRegistry.set_nav_mode(True)
                present = getattr(self, '_present_startup_alerts', None)
                if callable(present) and hasattr(present, 'dismiss_hidden'):
                    present.dismiss_hidden()
                self._present_startup_alerts = None
                self._start_startup_progress()
                return
            present = getattr(self, '_present_startup_alerts', None)
            if callable(present):
                self._present_startup_alerts = None
                present()
                return
            cache = getattr(self, '_startup_alerts_cache', None)
            if cache is not None:
                from core.startup_alerts import show_startup_alerts_from_cache
                show_startup_alerts_from_cache(
                    self.root, cache,
                )
                self._startup_alerts_cache = None
                return
            from core.startup_alerts import show_startup_alerts
            show_startup_alerts(self.root, self.conn, db_path=self.db_path)
        except Exception:
            from core.keyboard_registry import KeyboardRegistry
            KeyboardRegistry.set_nav_mode(True)

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
