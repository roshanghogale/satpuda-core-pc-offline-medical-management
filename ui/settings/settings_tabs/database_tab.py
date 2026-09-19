import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno
import threading
import os
import json
from datetime import date
from core.font_config import *
from core.alert_colors import get_alert_color
from core.scroll_manager import open_dialog
from ui.settings.settings_tabs.appearance_scroll import AppearanceScrollPane
from core.settings_section_nav import wire_settings_section_nav, bindings_for_sectioned_tab
from ui.settings.settings_tabs.updates_tab import UpdatesTab


def _restart_app(root=None):
    from core.app_setup import restart_app
    restart_app(root)


_NAV_SECTIONS = [
    ('stores',  'Stores & Startup Alerts'),
    ('export',  'Export Data'),
    ('maintenance', 'Data Maintenance'),
    ('backup',  'Google Drive Backup'),
    ('updates', 'App Updates'),
    ('my_assist', 'My Assist'),
    ('admin',   'Administrator'),
    ('danger',  'Danger Zone'),
]


def _nav_sections():
    from core.build_features import is_voice_supported

    if is_voice_supported():
        return list(_NAV_SECTIONS)
    return [s for s in _NAV_SECTIONS if s[0] != 'my_assist']


class DatabaseTab:
    TAB_NAME = "Data & System"

    def __init__(self, notebook, conn, parent_widget, host=None):
        self.conn = conn
        self.cursor = conn.cursor()
        self._parent = parent_widget
        self._panels = {}
        self._nav_buttons = {}
        self._active_section = None

        outer = host if host is not None else ttk.Frame(notebook)
        self.outer = outer
        if host is None and notebook is not None:
            notebook.add(outer, text=self.TAB_NAME)

        shell = ttk.Frame(outer)
        shell.pack(fill=tk.BOTH, expand=True)

        nav_outer = ttk.LabelFrame(shell, text="Sections")
        nav_outer.pack(side=tk.LEFT, fill=tk.Y, padx=(8, 4), pady=8)
        nav_scroll = ttk.Frame(nav_outer)
        nav_scroll.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        for section_id, label in _nav_sections():
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

        self._build_all_panels()
        self._show_section('stores')
        wire_settings_section_nav(
            self, self._nav_buttons, [s[0] for s in _nav_sections()], self._show_section)

    def get_keyboard_bindings(self):
        return bindings_for_sectioned_tab(self)

    def _panel(self, section_id):
        wrapper = ttk.Frame(self._content_host)
        self._panels[section_id] = wrapper
        return wrapper

    def sync_input_canvas(self):
        app = getattr(self._parent.winfo_toplevel(), '_main_app', None)
        if not app or not hasattr(app, 'input_ctrl'):
            return
        app.input_ctrl.set_active_canvas(self._scroller.canvas)

    def show_section(self, section_id):
        """Public: switch to a Management subsection (e.g. from update prompt)."""
        self._show_section(section_id)

    def _show_section(self, section_id):
        if section_id not in self._panels:
            return
        for frame in self._panels.values():
            frame.pack_forget()
        panel = self._panels[section_id]
        panel.pack(side=tk.TOP, fill=tk.X, anchor='n')
        self._active_section = section_id

        def _after_show():
            self._scroller.bind_wheel_recursive()
            self._scroller.refresh()
            self._scroller.scroll_to_top()

        panel.after_idle(_after_show)
        self.sync_input_canvas()
        if section_id == 'my_assist' and hasattr(self, '_assist_name_var'):
            from core.voice.assistant_config import (
                load_assistant_name,
                load_voice_language,
                load_voice_tts_enabled,
            )
            self._assist_name_var.set(load_assistant_name())
            self._assist_lang_var.set(load_voice_language())
            if hasattr(self, '_assist_tts_var'):
                self._assist_tts_var.set(load_voice_tts_enabled())
            self._refresh_my_assist_reference()
        for key, btn in self._nav_buttons.items():
            try:
                btn.configure(bootstyle='primary' if key == section_id else 'secondary')
            except Exception:
                pass

    def _build_all_panels(self):
        self._build_stores_panel()
        self._build_updates_panel()
        from core.build_features import is_voice_supported
        if is_voice_supported():
            self._build_my_assist_panel()
        self._build_export_panel()
        self._build_maintenance_panel()
        self._build_backup_panel()
        self._build_admin_panel()
        self._build_danger_panel()

    def _build_stores_panel(self):
        frame = self._panel('stores')
        sf = ttk.LabelFrame(frame, text="Store Management")
        sf.pack(fill=tk.X, padx=10, pady=10)

        self._stores_info_var = tk.StringVar(value="")
        ttk.Label(
            sf, textvariable=self._stores_info_var,
            wraplength=560, justify=tk.LEFT,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(anchor=tk.W, padx=12, pady=(8, 4))

        list_frame = ttk.Frame(sf)
        list_frame.pack(fill=tk.X, padx=12, pady=4)
        self._stores_listbox = tk.Listbox(
            list_frame, height=6, font=(FONT_FAMILY, FONT_SIZE_LABELS),
            selectbackground='#2563eb', selectforeground='white',
            activestyle='none', exportselection=False,
        )
        self._stores_listbox.pack(side=tk.LEFT, fill=tk.X, expand=True)
        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self._stores_listbox.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._stores_listbox.config(yscrollcommand=sb.set)
        self._store_row_keys = []

        btn_row = ttk.Frame(sf)
        btn_row.pack(fill=tk.X, padx=12, pady=(8, 12))
        ttk.Button(btn_row, text="Switch to Selected Store",
                   command=self._switch_selected_store).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_row, text="Create New Store",
                   command=self._create_new_store).pack(side=tk.LEFT, padx=6)
        ttk.Button(btn_row, text="Restore Active Store from Drive",
                   command=self._restore_active_from_drive).pack(side=tk.LEFT, padx=6)
        ttk.Button(btn_row, text="Refresh",
                   command=self._refresh_stores_panel).pack(side=tk.LEFT, padx=6)

        alerts_row = ttk.Frame(sf)
        alerts_row.pack(fill=tk.X, padx=12, pady=(0, 12))
        from core.startup_alerts_prefs import load_startup_alerts_enabled, save_startup_alerts_enabled
        self._startup_alerts_var = tk.BooleanVar(value=load_startup_alerts_enabled())
        ttk.Checkbutton(
            alerts_row,
            text="Show daily startup alerts (low stock, expiry, customer due)",
            variable=self._startup_alerts_var,
            command=self._save_startup_alerts_pref,
        ).pack(anchor=tk.W)

        from core.history_prefs import (
            HISTORY_SCOPE_ALL,
            HISTORY_SCOPE_CURRENT_FY,
            load_history_scope,
            save_history_scope,
        )
        history_row = ttk.Frame(sf)
        history_row.pack(fill=tk.X, padx=12, pady=(0, 12))
        self._history_scope_var = tk.StringVar(value=load_history_scope())
        ttk.Label(history_row, text="Sales / Purchase history default:").pack(side=tk.LEFT)
        ttk.Radiobutton(
            history_row,
            text="Current financial year only",
            value=HISTORY_SCOPE_CURRENT_FY,
            variable=self._history_scope_var,
            command=self._save_history_scope_pref,
        ).pack(side=tk.LEFT, padx=(8, 4))
        ttk.Radiobutton(
            history_row,
            text="All dates",
            value=HISTORY_SCOPE_ALL,
            variable=self._history_scope_var,
            command=self._save_history_scope_pref,
        ).pack(side=tk.LEFT, padx=4)

        self._refresh_stores_panel()

    def _build_autosave_panel(self):
        frame = self._panel('autosave')
        sf = ttk.LabelFrame(frame, text="Sales & Purchase Autosave")
        sf.pack(fill=tk.X, padx=10, pady=10)

        from core.autosave_prefs import (
            load_autosave_enabled,
            load_autosave_interval_seconds,
            DEFAULT_INTERVAL_SECONDS,
        )
        self._autosave_enabled_var = tk.BooleanVar(value=load_autosave_enabled())
        ttk.Checkbutton(
            sf,
            text="Auto-save in-progress sales and purchases (safety backup — form stays open)",
            variable=self._autosave_enabled_var,
        ).pack(anchor=tk.W, padx=12, pady=(10, 6))

        interval_row = ttk.Frame(sf)
        interval_row.pack(fill=tk.X, padx=12, pady=4)
        ttk.Label(interval_row, text="Autosave every (seconds):").pack(side=tk.LEFT)
        self._autosave_interval_var = tk.StringVar(
            value=str(load_autosave_interval_seconds()),
        )
        ttk.Entry(interval_row, textvariable=self._autosave_interval_var, width=10).pack(
            side=tk.LEFT, padx=8,
        )
        ttk.Label(
            interval_row,
            text=f"(30–3600, default {DEFAULT_INTERVAL_SECONDS})",
        ).pack(side=tk.LEFT)

        ttk.Button(sf, text="Save Autosave Settings", command=self._save_autosave_settings).pack(
            anchor=tk.W, padx=12, pady=(8, 12),
        )

    def _build_sales_screen_panel(self):
        frame = self._panel('sales_screen')
        sf = ttk.LabelFrame(frame, text="Sales Screen Layout")
        sf.pack(fill=tk.X, padx=10, pady=10)

        from core.sales_form_prefs import (
            load_payment_mode_enabled,
            load_payment_mode_position,
            POSITION_FIRST,
            POSITION_AFTER_BILL_DATE,
            payment_mode_position_label,
        )
        self._sales_payment_enabled_var = tk.BooleanVar(value=load_payment_mode_enabled())
        ttk.Checkbutton(
            sf,
            text="Show Payment Mode field (Cash / Due) on Sales page",
            variable=self._sales_payment_enabled_var,
            command=self._update_sales_payment_position_state,
        ).pack(anchor=tk.W, padx=12, pady=(10, 6))

        pos_row = ttk.Frame(sf)
        pos_row.pack(fill=tk.X, padx=12, pady=4)
        ttk.Label(pos_row, text="Payment field position:").pack(side=tk.LEFT)
        self._sales_payment_position_combo = ttk.Combobox(
            pos_row,
            values=(
                payment_mode_position_label(POSITION_FIRST),
                payment_mode_position_label(POSITION_AFTER_BILL_DATE),
            ),
            state='readonly',
            width=42,
        )
        current_pos = load_payment_mode_position()
        self._sales_payment_position_combo.set(payment_mode_position_label(current_pos))
        self._sales_payment_position_combo.pack(side=tk.LEFT, padx=8)
        self._update_sales_payment_position_state()

        ttk.Label(
            sf,
            text="Re-open the Sales page after saving to apply layout changes.",
            foreground="#666",
        ).pack(anchor=tk.W, padx=12, pady=(4, 4))
        ttk.Button(sf, text="Save Sales Screen Settings", command=self._save_sales_screen_settings).pack(
            anchor=tk.W, padx=12, pady=(4, 12),
        )

    def _build_sales_return_panel(self):
        frame = self._panel('sales_return')
        sf = ttk.LabelFrame(frame, text="Sales Return Lookup")
        sf.pack(fill=tk.X, padx=10, pady=10)

        from core.sales_return_prefs import (
            load_sales_return_lookup_days,
            DEFAULT_LOOKUP_DAYS,
            MIN_LOOKUP_DAYS,
            MAX_LOOKUP_DAYS,
        )

        ttk.Label(
            sf,
            text=(
                "When searching by medicine on the Sales Return page, only bills from "
                "this many recent days are listed."
            ),
            wraplength=560,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=12, pady=(10, 8))

        days_row = ttk.Frame(sf)
        days_row.pack(fill=tk.X, padx=12, pady=4)
        ttk.Label(days_row, text="Show sales bills from last (days):").pack(side=tk.LEFT)
        self._sales_return_days_var = tk.StringVar(
            value=str(load_sales_return_lookup_days()),
        )
        ttk.Entry(days_row, textvariable=self._sales_return_days_var, width=8).pack(
            side=tk.LEFT, padx=8,
        )
        ttk.Label(
            days_row,
            text=f"({MIN_LOOKUP_DAYS}–{MAX_LOOKUP_DAYS}, default {DEFAULT_LOOKUP_DAYS})",
        ).pack(side=tk.LEFT)

        ttk.Button(sf, text="Save Sales Return Settings", command=self._save_sales_return_settings).pack(
            anchor=tk.W, padx=12, pady=(8, 12),
        )

    def _save_sales_return_settings(self):
        from core.sales_return_prefs import save_sales_return_lookup_days
        try:
            days = int((self._sales_return_days_var.get() or '').strip())
        except ValueError:
            showwarning("Invalid", "Enter a whole number of days.", parent=self._parent)
            return
        saved = save_sales_return_lookup_days(days)
        self._sales_return_days_var.set(str(saved))
        showinfo(
            "Saved",
            f"Sales Return will search bills from the last {saved} day(s).",
            parent=self._parent,
        )

    def _build_sales_bills_panel(self):
        frame = self._panel('sales_bills')
        sf = ttk.LabelFrame(frame, text="Sales Bill Save")
        sf.pack(fill=tk.X, padx=10, pady=10)

        from core.bill_save_prefs import (
            load_sales_bill_save_dir,
            load_pdf_save_layout,
            pdf_save_layout_combo_values,
            pdf_save_layout_label,
        )
        from core.bill_output import default_downloads_directory

        ttk.Label(
            sf,
            text=(
                "When you save a sale (F5), PDF is written to the folder below.\n"
                "Page: A5 portrait (2 copies or bottom half) or A6 horizontal (1 copy). "
                "Bills are upright (not rotated). "
                "Uses Edge, Chrome, Brave, or another Chromium browser on this PC."
            ),
            wraplength=560,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=12, pady=(10, 8))

        layout_row = ttk.Frame(sf)
        layout_row.pack(fill=tk.X, padx=12, pady=4)
        ttk.Label(layout_row, text="PDF layout:").pack(side=tk.LEFT)
        self._pdf_save_layout_combo = ttk.Combobox(
            layout_row,
            values=pdf_save_layout_combo_values(),
            state='readonly',
            width=48,
        )
        current_layout = load_pdf_save_layout()
        self._pdf_save_layout_combo.set(pdf_save_layout_label(current_layout))
        self._pdf_save_layout_combo.pack(side=tk.LEFT, padx=8)

        path_row = ttk.Frame(sf)
        path_row.pack(fill=tk.X, padx=12, pady=4)
        ttk.Label(path_row, text="Save folder:").pack(side=tk.LEFT)
        self._sales_bill_dir_var = tk.StringVar(value=load_sales_bill_save_dir())
        ttk.Entry(path_row, textvariable=self._sales_bill_dir_var, width=52).pack(
            side=tk.LEFT, padx=8, fill=tk.X, expand=True,
        )
        ttk.Button(path_row, text="Browse…", command=self._browse_sales_bill_dir).pack(side=tk.LEFT)

        default_dl = default_downloads_directory()
        ttk.Label(
            sf,
            text=f"Leave folder empty to use Downloads ({default_dl})",
            foreground="#666",
        ).pack(anchor=tk.W, padx=12, pady=(4, 4))
        ttk.Button(sf, text="Save Sales Bill Settings", command=self._save_sales_bill_settings).pack(
            anchor=tk.W, padx=12, pady=(4, 12),
        )

    def _build_upi_payment_panel(self):
        frame = self._panel('upi_payment')
        sf = ttk.LabelFrame(frame, text="UPI Payment QR on Bill")
        sf.pack(fill=tk.X, padx=10, pady=10)

        from core.upi_prefs import (
            load_upi_id,
            load_upi_qr_amount_mode,
            load_upi_qr_enabled,
            upi_amount_mode_label,
            AMOUNT_TOTAL,
            AMOUNT_TOTAL_PLUS_PREV_DUE,
        )

        ttk.Label(
            sf,
            text=(
                "When enabled, saved PDFs and printed bills show a UPI QR code. "
                "Customer scans to pay the amount directly to your UPI."
            ),
            wraplength=560,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=12, pady=(10, 8))

        self._upi_qr_enabled_var = tk.BooleanVar(value=load_upi_qr_enabled())
        ttk.Checkbutton(
            sf,
            text="Show UPI QR on bill (print & PDF save)",
            variable=self._upi_qr_enabled_var,
        ).pack(anchor=tk.W, padx=12, pady=(0, 8))

        row = ttk.Frame(sf)
        row.pack(fill=tk.X, padx=12, pady=4)
        ttk.Label(row, text="UPI ID (VPA):").pack(side=tk.LEFT)
        self._upi_id_var = tk.StringVar(value=load_upi_id())
        ttk.Entry(row, textvariable=self._upi_id_var, width=42).pack(
            side=tk.LEFT, padx=8, fill=tk.X, expand=True,
        )

        amt_row = ttk.Frame(sf)
        amt_row.pack(fill=tk.X, padx=12, pady=4)
        ttk.Label(amt_row, text="QR amount:").pack(side=tk.LEFT)
        self._upi_amount_combo = ttk.Combobox(
            amt_row,
            values=(
                upi_amount_mode_label(AMOUNT_TOTAL),
                upi_amount_mode_label(AMOUNT_TOTAL_PLUS_PREV_DUE),
            ),
            state='readonly',
            width=36,
        )
        self._upi_amount_combo.set(upi_amount_mode_label(load_upi_qr_amount_mode()))
        self._upi_amount_combo.pack(side=tk.LEFT, padx=8)

        ttk.Label(
            sf,
            text=(
                "Use your real UPI ID from your bank app (e.g. name@oksbi). "
                "Do not use a placeholder — wrong ID causes 'Unable to scan QR' in GPay."
            ),
            foreground="#666",
            wraplength=520,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=12, pady=(4, 4))
        ttk.Button(sf, text="Save UPI Settings", command=self._save_upi_settings).pack(
            anchor=tk.W, padx=12, pady=(4, 12),
        )

    def _save_upi_settings(self):
        from core.upi_prefs import (
            save_upi_id,
            save_upi_qr_amount_mode,
            save_upi_qr_enabled,
            upi_amount_mode_from_label,
        )
        enabled = bool(self._upi_qr_enabled_var.get())
        upi = (self._upi_id_var.get() or '').strip()
        if enabled and not upi:
            showwarning(
                "UPI ID Required",
                "Enter your UPI ID before enabling the QR on bills.",
                parent=self._parent,
            )
            return
        if upi and '@' not in upi:
            showwarning(
                "UPI ID",
                "UPI ID usually looks like name@bank (e.g. shop@oksbi).",
                parent=self._parent,
            )
        save_upi_qr_enabled(enabled)
        save_upi_id(upi)
        save_upi_qr_amount_mode(upi_amount_mode_from_label(self._upi_amount_combo.get()))
        showinfo("UPI Settings", "UPI payment QR settings saved.", parent=self._parent)

    def _save_startup_alerts_pref(self):
        from core.startup_alerts_prefs import save_startup_alerts_enabled
        enabled = bool(self._startup_alerts_var.get())
        save_startup_alerts_enabled(enabled)

    def _save_history_scope_pref(self):
        from core.history_prefs import save_history_scope
        save_history_scope(str(self._history_scope_var.get()))
        if not enabled:
            showinfo(
                "Startup Alerts",
                "Daily startup alerts are turned off. "
                "You can re-enable them here anytime, or use Skip Today on the alert dialog.",
                parent=self._parent,
            )

    def _save_autosave_settings(self):
        from core.autosave_prefs import save_autosave_enabled, save_autosave_interval_seconds
        save_autosave_enabled(bool(self._autosave_enabled_var.get()))
        try:
            secs = int((self._autosave_interval_var.get() or '').strip())
        except ValueError:
            showwarning(
                "Autosave",
                "Please enter a whole number of seconds (30–3600).",
                parent=self._parent,
            )
            return
        save_autosave_interval_seconds(secs)
        self._autosave_interval_var.set(str(secs))
        showinfo("Autosave", f"Autosave settings saved ({secs} seconds).", parent=self._parent)

    def _save_sales_screen_settings(self):
        from core.billing_layout_prefs import save_billing_layout_prefs
        from core.sales_form_prefs import (
            POSITION_FIRST,
            POSITION_AFTER_BILL_DATE,
            payment_mode_position_label,
        )
        label = (self._sales_payment_position_combo.get() or '').strip()
        if label == payment_mode_position_label(POSITION_AFTER_BILL_DATE):
            pos = POSITION_AFTER_BILL_DATE
        else:
            pos = POSITION_FIRST
        save_billing_layout_prefs(
            {
                "payment_mode_enabled": bool(self._sales_payment_enabled_var.get()),
                "payment_mode_position": pos,
            },
            getattr(self, "conn", None),
        )
        showinfo(
            "Sales Screen",
            "Sales screen settings saved.\nRe-open the Sales page to apply layout changes.",
            parent=self._parent,
        )

    def _browse_sales_bill_dir(self):
        from tkinter import filedialog
        initial = (self._sales_bill_dir_var.get() or '').strip()
        if not initial or not os.path.isdir(initial):
            from core.bill_output import default_downloads_directory
            initial = default_downloads_directory()
        chosen = filedialog.askdirectory(
            title="Choose folder for saved sales bill PDFs",
            initialdir=initial,
            parent=self._parent,
        )
        if chosen:
            self._sales_bill_dir_var.set(chosen)

    def _save_sales_bill_settings(self):
        from core.bill_save_prefs import (
            save_sales_bill_save_dir,
            save_pdf_save_layout,
            pdf_save_layout_from_label,
        )
        raw = (self._sales_bill_dir_var.get() or '').strip()
        try:
            saved = save_sales_bill_save_dir(raw)
        except ValueError as exc:
            showwarning("Sales Bill Save", str(exc), parent=self._parent)
            return
        self._sales_bill_dir_var.set(saved)

        label = (self._pdf_save_layout_combo.get() or '').strip()
        save_pdf_save_layout(pdf_save_layout_from_label(label))

        if saved:
            folder_msg = f"Folder: {saved}"
        else:
            from core.bill_output import default_downloads_directory
            folder_msg = f"Folder: Downloads ({default_downloads_directory()})"
        showinfo(
            "Sales Bill Save",
            f"Settings saved.\n{folder_msg}\nLayout: {label}",
            parent=self._parent,
        )

    def _save_sales_bill_dir_settings(self):
        self._save_sales_bill_settings()

    def _update_sales_payment_position_state(self):
        enabled = bool(self._sales_payment_enabled_var.get())
        try:
            self._sales_payment_position_combo.configure(
                state='readonly' if enabled else 'disabled',
            )
        except Exception:
            pass

    def _refresh_stores_panel(self):
        try:
            from core.store_manager import (
                list_stores, get_active_store_key, is_satellite_device,
                get_active_display_name, display_name_key,
            )
            if is_satellite_device():
                name = get_active_display_name()
                self._stores_info_var.set(
                    f"This device is linked to one store only: {name}\n"
                    f"Drive folder: {display_name_key(name)}\n"
                    "Store switching and creation are disabled on this device."
                )
                self._stores_listbox.delete(0, tk.END)
                if name:
                    self._stores_listbox.insert(tk.END, f"* {name}")
                return

            active = get_active_store_key()
            stores = list_stores()
            self._stores_listbox.delete(0, tk.END)
            self._store_row_keys = []
            active_index = None
            for s in stores:
                name = s.get('display_name', '')
                is_active = s.get('store_key') == active
                label = f"  {name}" if not is_active else f"▶  {name}   [ACTIVE]"
                self._stores_listbox.insert(tk.END, label)
                idx = self._stores_listbox.size() - 1
                self._store_row_keys.append(s.get('store_key'))
                if is_active:
                    active_index = idx
                    self._stores_listbox.itemconfig(
                        idx, bg='#dbeafe', fg='#1e3a8a',
                        selectbackground='#2563eb', selectforeground='white',
                    )
                else:
                    self._stores_listbox.itemconfig(
                        idx, bg='#f8fafc', fg='#334155',
                        selectbackground='#2563eb', selectforeground='white',
                    )

            if active_index is not None:
                self._stores_listbox.selection_set(active_index)
                self._stores_listbox.see(active_index)

            count = len(stores)
            self._stores_info_var.set(
                f"{count} store(s) on this device. Each has its own database and Drive folder.\n"
                "The active store is highlighted in blue with ▶ and [ACTIVE]. "
                "Switching stores restarts the app."
            )
            # Never block the Data & System UI on Server (offline hangs ~60s).
            def _publish_async():
                try:
                    from core.store_link import publish_pc_store_registry
                    publish_pc_store_registry()
                except Exception:
                    pass
            threading.Thread(
                target=_publish_async, daemon=True, name='PublishStoreRegistry',
            ).start()
        except Exception as e:
            self._stores_info_var.set(f"Store list unavailable: {e}")

    def _selected_store_key(self):
        from core.store_manager import is_satellite_device
        if is_satellite_device():
            return None
        sel = self._stores_listbox.curselection()
        if not sel:
            return None
        idx = sel[0]
        if 0 <= idx < len(self._store_row_keys):
            return self._store_row_keys[idx]
        return None

    def _switch_selected_store(self):
        from core.store_manager import set_active_store, get_active_store_key, is_satellite_device
        if is_satellite_device():
            showwarning("Store Management", "This device is linked to one store only.", parent=self._parent)
            return
        key = self._selected_store_key()
        if not key:
            showwarning("Store Management", "Select a store from the list.", parent=self._parent)
            return
        if key == get_active_store_key():
            showinfo("Store Management", "This store is already active.", parent=self._parent)
            return
        if not askyesno(
            "Switch Store",
            "Switching stores will restart the app.\n"
            "Unsaved work on the current page may be lost.\n\nContinue?",
            parent=self._parent,
        ):
            return
        if set_active_store(key):
            from core.backup_manager import reload_slots_for_active_store
            reload_slots_for_active_store()
            root = self._parent.winfo_toplevel()
            _restart_app(root)

    def _create_new_store(self):
        from core.store_manager import create_store, is_satellite_device, names_match, list_stores
        if is_satellite_device():
            showwarning("Store Management", "Cannot create stores on a single-store device.", parent=self._parent)
            return

        dlg = open_dialog(self._parent, "Create New Store", width=420, height=200, resizable=False)
        body = dlg.content
        ttk.Label(body, text="Store name (used for local data and Drive backup folder):",
                  wraplength=380).pack(anchor=tk.W, padx=12, pady=(16, 6))
        name_var = tk.StringVar()
        ttk.Entry(body, textvariable=name_var, width=40).pack(padx=12, pady=4)

        def _save():
            name = name_var.get().strip()
            if not name:
                showerror("Create Store", "Store name is required.", parent=dlg)
                return
            for s in list_stores():
                if names_match(s.get('display_name', ''), name):
                    showerror("Create Store", f'Store "{name}" already exists.', parent=dlg)
                    return
            try:
                entry = create_store(
                    name, device_role='admin', empty_db=True,
                    migrate_legacy=False, activate=False,
                )
                from core.store_manager import ensure_registry_on_startup
                ensure_registry_on_startup()
                self._refresh_stores_panel()
                dlg.destroy()
                if askyesno(
                    "Create Store",
                    f'Store "{name}" created.\n\nSwitch to it now? (restarts the app)',
                    parent=self._parent,
                ):
                    from core.store_manager import set_active_store
                    from core.backup_manager import reload_slots_for_active_store
                    set_active_store(entry['store_key'])
                    reload_slots_for_active_store()
                    _restart_app(self._parent.winfo_toplevel())
            except Exception as e:
                showerror("Create Store", str(e), parent=dlg)

        ttk.Button(dlg.footer, text="Create", command=_save).pack(side=tk.LEFT, padx=6)
        ttk.Button(dlg.footer, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT, padx=6)

    def _restore_active_from_drive(self):
        self._sync_from_drive(status_setter=self._stores_info_var.set)

    def _sync_from_drive(
        self,
        *,
        status_setter=None,
        auto_restart: bool = True,
        voice: bool = False,
        on_complete=None,
    ):
        from core.store_manager import get_active_store, get_active_display_name, has_registry

        def _report(ok: bool, msg: str):
            if callable(on_complete):
                on_complete(ok, msg)

        if not has_registry():
            msg = "No store is configured on this device."
            if voice:
                from core.voice.tts import speak_action_result
                speak_action_result(msg)
            else:
                showwarning("Sync from Drive", msg, parent=self._parent)
            _report(False, msg)
            return

        store = get_active_store()
        name = (store or {}).get('display_name') or get_active_display_name()
        if not name:
            msg = "No active store."
            if voice:
                from core.voice.tts import speak_action_result
                speak_action_result(msg)
            else:
                showwarning("Sync from Drive", msg, parent=self._parent)
            _report(False, msg)
            return

        selected_file_id = None
        selected_label = "latest usable backup"

        if not voice:
            pick = self._choose_drive_backup(name)
            if pick is None:
                _report(False, "Sync cancelled.")
                return
            selected_file_id, selected_label = pick
            if not askyesno(
                "Sync from Drive",
                f'Replace the local database for "{name}" with this Drive backup?\n\n'
                f"{selected_label}\n\n"
                "Unsaved changes on this device will be lost. "
                "The app will restart after a successful sync.\n"
                "Old backups are upgraded automatically to the current database format.",
                parent=self._parent,
            ):
                _report(False, "Sync cancelled.")
                return
        else:
            from core.voice.tts import speak_action_result
            speak_action_result(f"Syncing from Google Drive for {name}.")

        if status_setter:
            status_setter("Syncing from Drive...")
        else:
            self._backup_status_var.set("Syncing from Drive...")
        self._parent.update_idletasks()

        root = self._parent.winfo_toplevel()
        app = getattr(root, '_main_app', None)
        conn = getattr(app, 'conn', None) if app else None

        def _run():
            try:
                from core.backup_manager import sync_active_store_from_drive
                from core.sync_coordinator import stop_online_sync

                try:
                    stop_online_sync()
                except Exception:
                    pass

                from core.sync_prefs import is_online_mode

                if is_online_mode():
                    # An Online store keeps its data on the server and runs on an
                    # in-memory SQLite shell, so the plain restore wrote a local
                    # file the app never opens -- the shop pressed Sync from Drive,
                    # was told "4410 sales", and saw no change. Restoring then
                    # PUSHING is the only sequence that reaches an Online store.
                    from core.backup_manager import restore_latest_backup_to_store
                    from core.online_migrate import drive_restore_then_push_wipe
                    from core.store_manager import (
                        get_active_display_name,
                        get_active_store_key,
                        get_store_db_path,
                    )

                    _store_key = get_active_store_key()

                    def _restore_to_local() -> str:
                        ok_r, result = restore_latest_backup_to_store(
                            get_active_display_name(),
                            _store_key,
                            close_conn=conn,
                            file_id=selected_file_id,
                        )
                        if not ok_r:
                            raise RuntimeError(str(result))
                        return get_store_db_path(_store_key)

                    res = drive_restore_then_push_wipe(
                        _restore_to_local,
                        progress_cb=(status_setter if callable(status_setter) else None),
                    ) or {}
                    ok = bool(res.get("ok"))
                    msg = str(
                        res.get("message")
                        or res.get("error")
                        or f"Restored and pushed {res.get('pushed', 0):,} record(s) to the server."
                    )
                else:
                    ok, msg = sync_active_store_from_drive(
                        close_conn=conn, file_id=selected_file_id,
                    )
            except Exception as e:
                ok, msg = False, str(e)

            def _done():
                if ok:
                    if voice:
                        from core.voice.tts import speak_action_result
                        speak_action_result(f"Sync complete. Restarting now.")
                    else:
                        showinfo("Sync from Drive", f"Sync complete.\n\n{msg}", parent=self._parent)
                    _restart_app(root)
                    _report(True, msg or "Sync complete.")
                else:
                    if voice:
                        from core.voice.tts import speak_action_result
                        speak_action_result(msg or "Sync from Drive failed.")
                    else:
                        showerror("Sync from Drive", msg, parent=self._parent)
                    if status_setter:
                        status_setter("")
                    else:
                        self._backup_status_var.set("")
                    _report(False, msg or "Sync from Drive failed.")
                self._refresh_stores_panel()
                self._refresh_backup_info()

            self._parent.after(0, _done)

        threading.Thread(target=_run, daemon=True).start()

    def _choose_drive_backup(self, store_name: str):
        """Show dropdown of all Drive backups. Returns (file_id, label) or None if cancelled."""
        dlg = open_dialog(
            self._parent, "Select Drive Backup",
            width=560, height=240, resizable=False,
        )
        body = dlg.content
        ttk.Label(
            body,
            text=f'Select which backup to restore for "{store_name}".\n'
                 'Includes .db.gz and older plain .db files. Old databases are upgraded automatically.',
            wraplength=520,
            justify=tk.LEFT,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(anchor=tk.W, padx=12, pady=(10, 6))

        status = tk.StringVar(value="Loading backup list from Google Drive...")
        ttk.Label(body, textvariable=status, font=(FONT_FAMILY, FONT_SIZE_LABELS)).pack(
            anchor=tk.W, padx=12, pady=(0, 6),
        )

        choice_var = tk.StringVar(value="")
        combo = ttk.Combobox(body, textvariable=choice_var, state='disabled', width=72)
        combo.pack(fill=tk.X, padx=12, pady=(0, 8))

        backups = []
        result = {'value': None}

        def _on_loaded(ok, payload):
            try:
                if not dlg.winfo_exists():
                    return
            except Exception:
                return
            if not ok:
                status.set(str(payload) or "Failed to list backups.")
                combo.configure(state='disabled')
                return
            backups.clear()
            backups.extend(payload or [])
            labels = [b.get('label') or b.get('name') or '' for b in backups]
            combo.configure(values=labels, state='readonly')
            if labels:
                choice_var.set(labels[0])
                status.set(f"{len(labels)} backup(s) found — newest first.")
            else:
                status.set("No backups found.")

        def _load():
            try:
                from core.backup_manager import list_drive_backups
                ok, payload = list_drive_backups(store_name)
            except Exception as e:
                ok, payload = False, str(e)
            self._parent.after(0, lambda: _on_loaded(ok, payload))

        def _confirm():
            label = (choice_var.get() or '').strip()
            if not label or not backups:
                showwarning("Sync from Drive", "Select a backup file first.", parent=dlg)
                return
            match = next(
                (b for b in backups if (b.get('label') or b.get('name')) == label),
                None,
            )
            if not match:
                showwarning("Sync from Drive", "Selected backup is invalid.", parent=dlg)
                return
            result['value'] = (match.get('id'), label)
            dlg.destroy()

        def _cancel():
            result['value'] = None
            dlg.destroy()

        ttk.Button(dlg.footer, text="Restore Selected", command=_confirm).pack(side=tk.LEFT, padx=6)
        ttk.Button(dlg.footer, text="Cancel", command=_cancel).pack(side=tk.LEFT, padx=6)

        threading.Thread(target=_load, daemon=True).start()
        dlg.wait_window()
        return result['value']

    def _build_updates_panel(self):
        frame = self._panel('updates')
        mgmt = ttk.LabelFrame(frame, text="App Updates")
        mgmt.pack(fill=tk.X, padx=10, pady=10)
        ttk.Label(
            mgmt,
            text="Check for app updates from GitHub Releases. Install Update opens "
                 "Satpuda Core Installer if it is already on this PC; otherwise it "
                 "downloads the installer once to Local\\Programs\\Satpuda Core. "
                 "Your database, activation, and backups stay on this PC.",
            wraplength=560,
            justify=tk.LEFT,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(anchor=tk.W, padx=12, pady=(8, 4))
        self._updates_tab = UpdatesTab.embed(mgmt, self._parent)

    def _build_export_panel(self):
        from core.export_prefs import load_default_export_format, save_default_export_format

        frame = self._panel('export')
        ef = ttk.LabelFrame(frame, text="Export Data")
        ef.pack(fill=tk.X, padx=10, pady=10)
        ttk.Label(
            ef,
            text="Export sales, purchases, and inventory. Choose a default file format below — "
                 "used by Satpuda voice and pre-selected when you export manually.",
            wraplength=560,
            justify=tk.LEFT,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(anchor=tk.W, padx=12, pady=(8, 6))

        fmt_row = ttk.Frame(ef)
        fmt_row.pack(anchor=tk.W, padx=12, pady=(0, 8))
        ttk.Label(fmt_row, text="Default format:").pack(side=tk.LEFT, padx=(0, 8))
        self._export_fmt_var = tk.StringVar(value=load_default_export_format())

        def _on_fmt_change(*_):
            save_default_export_format(self._export_fmt_var.get())

        for text, val in (("CSV", "csv"), ("Excel (.xlsx)", "xlsx"), ("PDF (HTML)", "pdf")):
            ttk.Radiobutton(
                fmt_row, text=text, variable=self._export_fmt_var, value=val,
                command=_on_fmt_change,
            ).pack(side=tk.LEFT, padx=6)

        br = ttk.Frame(ef)
        br.pack(pady=8)
        ttk.Button(br, text="Export Sales",     command=self.export_sales).pack(side=tk.LEFT, padx=8)
        ttk.Button(br, text="Export Purchases", command=self.export_purchases).pack(side=tk.LEFT, padx=8)
        ttk.Button(br, text="Export Inventory", command=self.export_inventory).pack(side=tk.LEFT, padx=8)
        ttk.Button(br, text="Export All",       command=self.export_all).pack(side=tk.LEFT, padx=8)

    def _build_maintenance_panel(self):
        frame = self._panel('maintenance')
        mf = ttk.LabelFrame(frame, text="Data Maintenance")
        mf.pack(fill=tk.X, padx=10, pady=10)
        ttk.Label(
            mf,
            text="Fix legacy medicine names stored with mixed upper/lower case. "
                 "All names are converted to UPPERCASE so the same product is never "
                 "treated as two different medicines. This also runs automatically on app startup.",
            wraplength=560,
            justify=tk.LEFT,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(anchor=tk.W, padx=12, pady=(8, 6))
        ttk.Button(
            mf,
            text="Normalize Medicine Names (Uppercase)",
            command=self._normalize_medicine_names,
        ).pack(anchor=tk.W, padx=12, pady=(0, 12))

    def _normalize_medicine_names(self):
        from core.name_utils import normalize_medicine_names_in_db

        if not askyesno(
            "Normalize Medicine Names",
            "Convert all medicine names in this store to UPPERCASE?\n\n"
            "Existing batches are updated in place. Purchase and sales history are kept.",
            parent=self._parent,
        ):
            return

        def _worker(put):
            put("Normalizing medicine names…")
            return normalize_medicine_names_in_db(self.conn)

        def _done(updated):
            try:
                from core.page_refresh import refresh_open_pages
                refresh_open_pages(inventory=True, purchase=True, home=True)
            except Exception:
                pass
            if updated:
                showinfo(
                    "Done",
                    f"Updated {updated:,} medicine batch row(s) to uppercase.",
                    parent=self._parent,
                )
            else:
                showinfo(
                    "Done",
                    "All medicine names are already uppercase — nothing to change.",
                    parent=self._parent,
                )

        from core.background_workers import run_with_progress

        run_with_progress(
            self._parent,
            "Normalize Medicine Names",
            _worker,
            on_complete=_done,
            on_error=lambda exc: showerror(
                "Error",
                f"Could not normalize medicine names:\n{exc}",
                parent=self._parent,
            ),
        )

    def _build_backup_panel(self):
        frame = self._panel('backup')
        bf = ttk.LabelFrame(frame, text="Google Drive Backup")
        bf.pack(fill=tk.X, padx=10, pady=10)
        self._backup_status_var = tk.StringVar(value="")
        ttk.Label(
            bf,
            text="Upload local data to Drive with Backup Now (works in Online or Offline mode), "
                 "or Sync from Drive to pick any backup file (.db.gz or older .db) and restore it. "
                 "Old backups are upgraded to the current database format automatically.",
            wraplength=560,
            justify=tk.LEFT,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(padx=10, pady=(8, 4), anchor='w')
        try:
            from core.backup_manager import is_auto_backup_enabled
            auto_on = is_auto_backup_enabled()
        except Exception:
            auto_on = False
        self._auto_backup_var = tk.BooleanVar(value=auto_on)
        ttk.Checkbutton(
            bf,
            text="Automatic backup on open, close, and every hour",
            variable=self._auto_backup_var,
            command=self._save_auto_backup_pref,
        ).pack(anchor=tk.W, padx=10, pady=(0, 6))
        ttk.Label(bf, textvariable=self._backup_status_var,
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).pack(pady=(0, 4))
        try:
            from core.sync_prefs import get_sync_mode, mode_label
            ttk.Label(
                bf,
                text=f"Sync mode: {mode_label(get_sync_mode())}",
                font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
                foreground='gray',
            ).pack(anchor=tk.W, padx=10, pady=(0, 6))
        except Exception:
            pass
        backup_btn_row = ttk.Frame(bf)
        backup_btn_row.pack(pady=(0, 8))
        ttk.Button(
            backup_btn_row, text="Backup Now",
            command=self._manual_backup,
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            backup_btn_row, text="Sync from Drive",
            command=self._sync_from_drive,
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            backup_btn_row, text="Push to Server",
            command=self._push_to_server,
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            backup_btn_row, text="Push All Stores",
            command=self._push_all_stores_to_server,
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            backup_btn_row, text="Pull from Server",
            command=self._pull_from_server,
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            backup_btn_row, text="Verify Server Sync",
            command=self._verify_server_sync,
        ).pack(side=tk.LEFT)

        self._backup_info_var = tk.StringVar(value="")
        ttk.Label(
            bf,
            textvariable=self._backup_info_var,
            justify=tk.LEFT,
            wraplength=560,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(padx=10, pady=(0, 10), anchor='w')
        self._refresh_backup_info()

    def _build_my_assist_panel(self):
        from core.voice.assistant_config import (
            get_assistant_display_name,
            get_voice_language_label,
            load_assistant_name,
            load_voice_language,
            load_voice_tts_enabled,
            load_voice_auto_start_mic,
            load_voice_wake_only,
            save_assistant_name,
            save_voice_language,
        )
        from core.voice.command_reference import build_my_assist_reference

        frame = self._panel('my_assist')
        outer = ttk.LabelFrame(frame, text="My Assist — Voice Assistant")
        outer.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(
            outer,
            text="Set your wake word and command language. English is the default.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=620,
        ).pack(anchor=tk.W, padx=10, pady=(8, 10))

        name_row = ttk.Frame(outer)
        name_row.pack(fill=tk.X, padx=10, pady=4)
        ttk.Label(name_row, text="Assistant name:", width=16).pack(side=tk.LEFT)
        self._assist_name_var = tk.StringVar(value=load_assistant_name())
        name_entry = ttk.Entry(name_row, textvariable=self._assist_name_var, width=28)
        name_entry.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(
            name_row,
            text="(wake word — English letters only)",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(side=tk.LEFT)

        lang_row = ttk.Frame(outer)
        lang_row.pack(fill=tk.X, padx=10, pady=(10, 4))
        ttk.Label(lang_row, text="Language:", width=16).pack(side=tk.LEFT)
        self._assist_lang_var = tk.StringVar(value=load_voice_language())
        lang_frame = ttk.Frame(lang_row)
        lang_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        for code, label in (("en", "English"), ("mr", "Marathi")):
            ttk.Radiobutton(
                lang_frame,
                text=label,
                value=code,
                variable=self._assist_lang_var,
                command=self._refresh_my_assist_reference,
            ).pack(side=tk.LEFT, padx=(0, 16))

        auto_row = ttk.Frame(outer)
        auto_row.pack(fill=tk.X, padx=10, pady=(6, 4))
        self._assist_auto_mic_var = tk.BooleanVar(value=load_voice_auto_start_mic())
        ttk.Checkbutton(
            auto_row,
            text="Start microphone on app open (standby — say Hey Satpuda to listen)",
            variable=self._assist_auto_mic_var,
        ).pack(side=tk.LEFT, anchor=tk.W)

        mic_row = ttk.Frame(outer)
        mic_row.pack(fill=tk.X, padx=10, pady=(6, 4))
        ttk.Label(mic_row, text="Microphone:", width=16).pack(side=tk.LEFT)
        self._assist_mic_var = tk.StringVar(value="")
        ttk.Entry(
            mic_row,
            textvariable=self._assist_mic_var,
            state="readonly",
            width=34,
            font=(FONT_FAMILY, FONT_SIZE_DEFAULT),
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            mic_row,
            text="Select…",
            width=10,
            command=self._open_assist_mic_picker,
        ).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(
            mic_row,
            text="Refresh",
            width=8,
            command=self._refresh_assist_mic_label,
        ).pack(side=tk.LEFT)
        self._refresh_assist_mic_label()

        wake_row = ttk.Frame(outer)
        wake_row.pack(fill=tk.X, padx=10, pady=(4, 4))
        self._assist_wake_only_var = tk.BooleanVar(value=load_voice_wake_only())
        ttk.Checkbutton(
            wake_row,
            text="After Turn Mic On, stay on standby until Hey Satpuda (not immediate listening)",
            variable=self._assist_wake_only_var,
        ).pack(side=tk.LEFT, anchor=tk.W)

        tts_row = ttk.Frame(outer)
        tts_row.pack(fill=tk.X, padx=10, pady=(6, 4))
        self._assist_tts_var = tk.BooleanVar(value=load_voice_tts_enabled())
        ttk.Checkbutton(
            tts_row,
            text="Speak navigation confirmations (Opening Inventory, Please say it again, …)",
            variable=self._assist_tts_var,
        ).pack(side=tk.LEFT)
        ttk.Button(
            tts_row,
            text="Test voice",
            width=12,
            command=self._test_assist_tts,
        ).pack(side=tk.RIGHT)

        tutor_row = ttk.Frame(outer)
        tutor_row.pack(fill=tk.X, padx=10, pady=(6, 4))
        try:
            from core.build_features import is_gemini_supported
            from core.gemini_tutor_config import (
                is_tutor_enabled, load_tutor_window_height, set_tutor_enabled,
            )
            if is_gemini_supported():
                self._tutor_enabled_var = tk.BooleanVar(value=is_tutor_enabled())
                ttk.Checkbutton(
                    tutor_row,
                    text="Enable Satpuda AI (floating bottom-right) — Gemini read-only Q&A; uses your Import key",
                    variable=self._tutor_enabled_var,
                    command=self._on_tutor_enabled_changed,
                ).pack(side=tk.LEFT, anchor=tk.W)
                size_row = ttk.Frame(outer)
                size_row.pack(fill=tk.X, padx=10, pady=(2, 6))
                ttk.Label(size_row, text="Help AI window height:").pack(side=tk.LEFT)
                self._tutor_height_var = tk.IntVar(value=load_tutor_window_height())
                ttk.Spinbox(
                    size_row,
                    from_=420,
                    to=900,
                    increment=20,
                    textvariable=self._tutor_height_var,
                    width=8,
                ).pack(side=tk.LEFT, padx=(8, 6))
                ttk.Label(
                    size_row,
                    text="Smaller number = shorter window",
                    font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
                ).pack(side=tk.LEFT)
        except Exception:
            pass

        self._assist_hint_var = tk.StringVar()
        ttk.Label(
            outer,
            textvariable=self._assist_hint_var,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=620,
        ).pack(anchor=tk.W, padx=10, pady=(6, 8))

        ref_frame = ttk.LabelFrame(outer, text="Voice command reference")
        ref_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))
        ref_wrap = ttk.Frame(ref_frame)
        ref_wrap.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self._assist_ref_text = tk.Text(
            ref_wrap,
            height=22,
            wrap=tk.WORD,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            state=tk.DISABLED,
        )
        ref_scroll = ttk.Scrollbar(ref_wrap, orient=tk.VERTICAL, command=self._assist_ref_text.yview)
        self._assist_ref_text.configure(yscrollcommand=ref_scroll.set)
        self._assist_ref_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ref_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        btn_row = ttk.Frame(outer)
        btn_row.pack(fill=tk.X, padx=10, pady=(0, 10))
        ttk.Button(btn_row, text="Save", command=self._save_my_assist_settings, width=14).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(
            btn_row,
            text="Refresh reference",
            command=self._refresh_my_assist_reference,
            width=16,
        ).pack(side=tk.LEFT)

        self._refresh_my_assist_reference()

    def _main_app(self):
        root = self._parent.winfo_toplevel()
        return getattr(root, '_main_app', None) or getattr(root, '_app_instance', None)

    def _voice_assistant(self):
        app = self._main_app()
        return getattr(app, '_voice_assistant', None) if app else None

    def _refresh_assist_mic_label(self):
        from core.voice.mic_devices import invalidate_device_cache, list_input_devices, resolve_device, load_saved_device
        if not hasattr(self, '_assist_mic_var'):
            return
        invalidate_device_cache()
        devices = list_input_devices(timeout=12.0)
        if not devices:
            self._assist_mic_var.set("No microphone found — check Windows Sound settings")
            return
        pick = resolve_device(load_saved_device())
        self._assist_mic_var.set((pick or devices[0]).get("label", "Microphone"))

    def _open_assist_mic_picker(self):
        from widgets.satpuda_voice_dialog import open_mic_picker_dialog
        root = self._parent.winfo_toplevel()
        open_mic_picker_dialog(
            root,
            assistant=self._voice_assistant(),
            on_selected=lambda _dev: self._refresh_assist_mic_label(),
        )

    def _refresh_my_assist_reference(self):
        from core.voice.assistant_config import get_assistant_display_name, get_voice_language_label
        from core.voice.command_reference import build_my_assist_reference

        lang = self._assist_lang_var.get() if hasattr(self, '_assist_lang_var') else 'en'
        name = get_assistant_display_name()
        if hasattr(self, '_assist_hint_var'):
            if lang == 'mr':
                self._assist_hint_var.set(
                    f'Always say "{name}" in English, then English screen name + Marathi action. '
                    f'Example: "{name} Purchase ugaad" or "{name} Sales dakhau". '
                    f'(Mic uses English recognition so Satpuda is not misheard.)'
                )
            else:
                self._assist_hint_var.set(
                    f'Always say "{name}" before every command, e.g. "{name} open purchase" '
                    f'or "{name} show supplier ledger". '
                    f'English commands only ({get_voice_language_label("en")}).'
                )
        if not hasattr(self, '_assist_ref_text'):
            return
        text = build_my_assist_reference(lang)
        self._assist_ref_text.configure(state=tk.NORMAL)
        self._assist_ref_text.delete('1.0', tk.END)
        self._assist_ref_text.insert('1.0', text)
        self._assist_ref_text.configure(state=tk.DISABLED)

    def _on_tutor_enabled_changed(self):
        from core.gemini_tutor_config import set_tutor_enabled
        set_tutor_enabled(bool(self._tutor_enabled_var.get()))
        try:
            app = getattr(self._parent.winfo_toplevel(), '_main_app', None)
            if app is not None and hasattr(app, '_sync_tutor_ui'):
                app._sync_tutor_ui()
        except Exception:
            pass

    def _test_assist_tts(self):
        from core.voice.tts import test_speak
        from core.themed_messagebox import showerror, showinfo

        if test_speak("Satpuda is ready. Opening Inventory."):
            showinfo("My Assist", "Voice test played. If you heard nothing, check Windows volume.", parent=self._parent)
        else:
            showerror(
                "My Assist",
                "Voice test failed. Install pywin32 and check that a Windows voice (e.g. Zira) is installed.",
                parent=self._parent,
            )

    def _save_my_assist_settings(self):
        from core.voice.assistant_config import (
            save_assistant_name,
            save_voice_language,
            save_voice_tts_enabled,
            save_voice_auto_start_mic,
            save_voice_wake_only,
        )
        from core.gemini_tutor_config import save_tutor_window_height

        ok_name, name_result = save_assistant_name(self._assist_name_var.get())
        if not ok_name:
            showerror("My Assist", name_result, parent=self._parent)
            return
        ok_lang, lang_result = save_voice_language(self._assist_lang_var.get())
        if not ok_lang:
            showerror("My Assist", lang_result, parent=self._parent)
            return
        ok_tts, tts_result = save_voice_tts_enabled(bool(self._assist_tts_var.get()))
        if not ok_tts:
            showerror("My Assist", tts_result, parent=self._parent)
            return
        ok_wake, wake_result = save_voice_wake_only(bool(self._assist_wake_only_var.get()))
        if not ok_wake:
            showerror("My Assist", wake_result, parent=self._parent)
            return
        ok_auto, auto_result = save_voice_auto_start_mic(bool(self._assist_auto_mic_var.get()))
        if not ok_auto:
            showerror("My Assist", auto_result, parent=self._parent)
            return
        tutor_height = None
        if hasattr(self, '_tutor_height_var'):
            try:
                tutor_height = save_tutor_window_height(self._tutor_height_var.get())
                self._tutor_height_var.set(tutor_height)
            except Exception as exc:
                showerror("My Assist", f"Invalid Help AI height: {exc}", parent=self._parent)
                return
        self._assist_name_var.set(name_result)
        self._assist_lang_var.set(lang_result)
        self._refresh_my_assist_reference()
        app = getattr(self._parent.winfo_toplevel(), '_main_app', None)
        if app is not None and hasattr(app, '_reload_voice_settings'):
            app._reload_voice_settings()
        if app is not None and hasattr(app, '_sync_tutor_ui'):
            app._sync_tutor_ui()
        tts_note = "on" if self._assist_tts_var.get() else "off"
        wake_note = "on" if self._assist_wake_only_var.get() else "off"
        auto_note = "on" if self._assist_auto_mic_var.get() else "off"
        showinfo(
            "My Assist",
            f"Saved. Wake word: {name_result.title()} — Language: "
            f"{('Marathi' if lang_result == 'mr' else 'English')} — "
            f"Auto-start mic: {auto_note} — Standby after mic on: {wake_note} — "
            f"Spoken navigation: {tts_note}"
            f"{f' — Help AI height: {tutor_height}px' if tutor_height else ''}.",
            parent=self._parent,
        )

    def _build_admin_panel(self):
        frame = self._panel('admin')
        admin = ttk.LabelFrame(frame, text="Administrator")
        admin.pack(fill=tk.X, padx=10, pady=10)
        ttk.Label(
            admin,
            text="Restricted tools: Drive backup, expiry.dat editor. Voice: see My Assist section.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(anchor=tk.W, padx=10, pady=(8, 4))
        ttk.Button(admin, text="Administrator Login", command=self._admin_login).pack(
            anchor=tk.W, padx=10, pady=(0, 10)
        )

        master = ttk.LabelFrame(frame, text="Global Master Medicines")
        master.pack(fill=tk.X, padx=10, pady=(0, 10))
        ttk.Label(
            master,
            text=(
                "Server holds one global catalog. Normal Push/Pull never touches it. "
                "Download replaces the local snapshot; Push stock enriches the server from inventory."
            ),
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=560,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=10, pady=(8, 6))
        self._master_status_var = tk.StringVar(value="")
        try:
            from core.master_medicine_service import master_row_count

            self._master_status_var.set(f"Local master rows: {master_row_count():,}")
        except Exception:
            self._master_status_var.set("")
        ttk.Label(master, textvariable=self._master_status_var).pack(anchor=tk.W, padx=10, pady=(0, 6))
        mrow = ttk.Frame(master)
        mrow.pack(anchor=tk.W, padx=10, pady=(0, 10))
        ttk.Button(
            mrow, text="Download Master (replace local)",
            command=self._download_master_medicines,
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            mrow, text="Push Stock → Master",
            command=self._push_stock_to_master,
        ).pack(side=tk.LEFT)

    def _build_danger_panel(self):
        frame = self._panel('danger')
        wf = ttk.LabelFrame(frame, text="Danger Zone")
        wf.pack(fill=tk.X, padx=10, pady=10)
        ttk.Label(wf, text="Delete All Tables",
                  font=(FONT_FAMILY, FONT_SIZE_SECTION_TITLE, 'bold'),
                  foreground=get_alert_color('danger')).pack(pady=5)
        ttk.Label(wf, text="This will permanently delete ALL data from the local database.").pack(pady=2)
        ttk.Label(wf, text="This action cannot be undone!",
                  foreground=get_alert_color('danger')).pack(pady=2)
        ttk.Button(wf, text="DELETE ALL TABLES", command=self.delete_all_tables).pack(pady=10)

    def _save_auto_backup_pref(self):
        try:
            from core.backup_manager import set_auto_backup_enabled
            set_auto_backup_enabled(bool(self._auto_backup_var.get()))
            self._refresh_backup_info()
        except Exception as e:
            showerror("Backup Settings", f"Could not save preference: {e}", parent=self._parent)

    def _manual_backup(self):
        self._backup_status_var.set("Backing up...")
        self._parent.update_idletasks()

        def _run():
            try:
                from core.backup_manager import (
                    run_backup_now,
                    last_backup_log_message,
                    sync_backup_config_to_active_store,
                )
                sync_backup_config_to_active_store()
                # run_backup_now now reports what it did. The log-line reading
                # below is kept only as a fallback for the case where a build
                # has logging turned off (core/log_policy.py) AND the result is
                # somehow empty; it never gets to contradict the real answer.
                res = run_backup_now(manual=True)
                if isinstance(res, dict) and res.get("message"):
                    msg = str(res["message"])
                    self._parent.after(
                        0, lambda m=msg: self._backup_status_var.set(m)
                    )
                    return
                last = last_backup_log_message()
                if "Backup OK" in last:
                    msg = f"Backup successful! {last.split('Backup OK', 1)[-1].strip()}"
                elif "no internet" in last.lower():
                    msg = "No internet connection."
                elif "backup_config.dat missing" in last.lower():
                    msg = "Backup not configured."
                elif "backup_creds.dat missing" in last.lower():
                    msg = "Backup credentials missing/invalid."
                elif "Backup Drive error" in last:
                    if "disabled_client" in last.lower():
                        msg = (
                            "Google OAuth client is disabled. Run generate_oauth_token.py, "
                            "rebuild the EXE, then delete %LOCALAPPDATA%\\VeterinaryApp\\backup_creds.dat "
                            "and restart."
                        )
                    elif "404" in last or "not found" in last.lower():
                        msg = "Drive folder not found. Check the folder ID in Administrator settings."
                    elif "403" in last:
                        msg = "No access to Drive folder. Share it with the backup Google account."
                    elif "timed out" in last.lower() or "10054" in last:
                        msg = (
                            "Drive upload timed out or connection dropped. "
                            "Try again on a stable connection; pendrive backup may still have worked."
                        )
                    else:
                        msg = "Drive backup failed. See backup_log.txt for details."
                elif "Backup failed" in last:
                    msg = "Backup failed. Check backup_log.txt."
                else:
                    msg = "Backup finished. Check backup_log.txt if unsure."
            except Exception as e:
                msg = f"Error: {e}"
            self._parent.after(0, lambda: self._backup_status_var.set(msg))

        threading.Thread(target=_run, daemon=True).start()

    def _pull_from_server(self):
        from core.themed_messagebox import askyesno, showerror, showinfo
        from core.sync_prefs import is_online_mode

        if not is_online_mode():
            showerror(
                "Pull from Server",
                "Switch Sync Mode to Online first, then pull from the server.",
                parent=self._parent,
            )
            return
        if not askyesno(
            "Pull from Server — disaster recovery",
            "FULL REPLACE: clear this PC's active store and download from Satpuda Core Server?\n\n"
            "Normal day-to-day sync is automatic (revision SyncEngine). "
            "Use this only for disaster recovery or when this PC is badly out of date.\n\n"
            "Local medicines, bills, customers and related rows are cleared first. "
            "Theme/settings on this PC are kept.\n\n"
            "Use Push to Server first if this PC has newer bills you still need online.",
            parent=self._parent,
        ):
            return

        self._backup_status_var.set("Downloading from server…")
        self._parent.update_idletasks()

        def _run():
            msg = ""
            ok = False
            try:
                from core import server_live as live

                def _progress(line: str) -> None:
                    self._parent.after(
                        0,
                        lambda m=line: self._backup_status_var.set(m),
                    )

                # Pull replaces the local database with what comes back. If
                # this created the store first, it created an EMPTY one and
                # then wiped the local store with nothing.
                live.ensure_active_store_on_server(create_if_new=True)
                pulled = live.sync_down_all(
                    self.conn,
                    progress_cb=_progress,
                    incremental=False,
                    replace_local=True,
                )
                try:
                    self.conn.commit()
                except Exception:
                    pass
                msg = (
                    f"Replaced local store with {pulled:,} record(s) from Satpuda Core Server."
                )
                ok = True
            except Exception as exc:
                msg = f"Pull failed: {exc}"
            self._parent.after(
                0,
                lambda: self._backup_status_var.set(
                    "Pull finished" if ok else "Pull failed"
                ),
            )
            if ok:
                self._parent.after(
                    0,
                    lambda: showinfo("Pull from Server", msg, parent=self._parent),
                )
            else:
                self._parent.after(
                    0,
                    lambda: showerror("Pull from Server", msg, parent=self._parent),
                )

        threading.Thread(target=_run, daemon=True).start()

    def _push_to_server(self):
        from core.themed_messagebox import askyesno, showerror, showinfo

        if not askyesno(
            "Push to Server",
            "Upload the ACTIVE store only to Satpuda Core Server?\n\n"
            "Each store has its own server partition (store_pk) — data does not "
            "mix with other stores.\n\n"
            "Use “Push All Stores” if you need every store on this PC uploaded.",
            parent=self._parent,
        ):
            return

        self._backup_status_var.set("Uploading active store…")
        self._parent.update_idletasks()

        def _run():
            msg = ""
            try:
                from core import server_sync

                def _progress(line: str) -> None:
                    self._parent.after(
                        0,
                        lambda t=line: self._backup_status_var.set(t),
                    )

                count = server_sync.push_active_store_to_server(
                    self.conn, progress_cb=_progress
                )
                msg = f"Uploaded {count:,} record(s) for the active store."
            except Exception as exc:
                msg = f"Server upload failed: {exc}"
            self._parent.after(0, lambda: self._backup_status_var.set(msg.split("\n")[0]))
            if msg.startswith("Uploaded"):
                self._parent.after(
                    0,
                    lambda: showinfo("Push to Server", msg, parent=self._parent),
                )
            elif msg:
                self._parent.after(
                    0,
                    lambda: showerror("Push to Server", msg, parent=self._parent),
                )

        threading.Thread(target=_run, daemon=True).start()

    def _push_all_stores_to_server(self):
        from core.themed_messagebox import askyesno, showerror, showinfo

        if not askyesno(
            "Push All Stores",
            "Upload EVERY local store on this PC?\n\n"
            "Each store is paired separately and written only under that store’s "
            "store_pk on the server. Data is not merged across stores.",
            parent=self._parent,
        ):
            return

        self._backup_status_var.set("Uploading all stores…")
        self._parent.update_idletasks()

        def _run():
            msg = ""
            try:
                from core import server_sync

                def _progress(line: str) -> None:
                    self._parent.after(
                        0,
                        lambda t=line: self._backup_status_var.set(t),
                    )

                count, messages = server_sync.push_all_local_stores_to_server(
                    progress_cb=_progress,
                )
                detail = "\n".join(messages)
                msg = f"Uploaded {count:,} record(s) to server.\n\n{detail}"
            except Exception as exc:
                msg = f"Server upload failed: {exc}"
            self._parent.after(0, lambda: self._backup_status_var.set(msg.split("\n")[0]))
            if "Uploaded" in msg and "FAILED" not in msg:
                self._parent.after(
                    0,
                    lambda: showinfo("Push All Stores", msg, parent=self._parent),
                )
            elif "Uploaded" in msg:
                self._parent.after(
                    0,
                    lambda: showinfo("Push All Stores (partial)", msg, parent=self._parent),
                )
            elif msg:
                self._parent.after(
                    0,
                    lambda: showerror("Push All Stores", msg, parent=self._parent),
                )

        threading.Thread(target=_run, daemon=True).start()

    def _verify_server_sync(self):
        from core.themed_messagebox import showerror, showinfo

        self._backup_status_var.set("Verifying server sync…")
        self._parent.update_idletasks()

        def _run():
            msg = ""
            try:
                from core import server_api as api
                from core import server_sync
                from core.store_manager import get_active_store_key, list_stores

                api.health()
                active = get_active_store_key()
                store = next((s for s in list_stores() if s.get("store_key") == active), None)
                if not store:
                    raise RuntimeError("No active local store")
                # Resolved from the store's own SC- key. This used to open with
                # api.admin_login() -- the vendor administrator, from a password
                # compiled into the build -- on a diagnostic button.
                remote = server_sync._remote_store_for(store)
                token = server_sync._pair_for_store(remote, store.get("store_key") or "")
                lines = [
                    f"Local: {server_sync._display_name(store)}",
                    f"Server: {remote.get('store_name')} ({remote.get('android_key')})",
                    "",
                    f"{'Collection':<22} {'Local':>8} {'Server':>8}",
                ]
                cols = [
                    "customers", "suppliers", "medicines", "doctors", "sales",
                    "purchases", "customer_payments", "supplier_payments",
                    "sales_returns", "purchase_returns",
                ]
                mismatch = False
                for col in cols:
                    try:
                        cur = self.conn.cursor()
                        cur.execute(f"SELECT COUNT(*) FROM {col}")
                        local_n = int(cur.fetchone()[0] or 0)
                    except Exception:
                        local_n = 0
                    try:
                        remote_docs, _meta = api.pull_collection(
                            token, col, include_deleted=False, limit=20000,
                        )
                        remote_n = len(remote_docs)
                    except Exception as exc:
                        lines.append(f"{col:<22} {local_n:>8} {'ERR':>8}  ({exc})")
                        mismatch = True
                        continue
                    mark = "" if local_n == remote_n else " *"
                    if mark:
                        mismatch = True
                    lines.append(f"{col:<22} {local_n:>8} {remote_n:>8}{mark}")
                msg = "\n".join(lines)
                if mismatch:
                    msg += "\n\n* counts differ (re-run Sync to Server if needed)"
                else:
                    msg += "\n\nCounts match for active store."
            except Exception as exc:
                msg = f"Verify failed: {exc}"
            self._parent.after(0, lambda: self._backup_status_var.set("Verify finished"))
            if msg.startswith("Verify failed"):
                self._parent.after(
                    0,
                    lambda: showerror("Verify Server Sync", msg, parent=self._parent),
                )
            else:
                self._parent.after(
                    0,
                    lambda: showinfo("Verify Server Sync", msg, parent=self._parent),
                )

        threading.Thread(target=_run, daemon=True).start()

    def _verify_server_sync(self):
        """Legacy alias — verification now targets the server."""
        self._verify_server_sync()

    def _refresh_backup_info(self):
        try:
            from core.backup_manager import get_backup_config_status, is_auto_backup_enabled
            auto_on = is_auto_backup_enabled()
            auto_line = (
                "Automatic backup is ON (open, hourly, close)."
                if auto_on else
                "Automatic backup is OFF — faster start/close. Use Backup Now when needed."
            )
            st = get_backup_config_status()
            if st.get('configured'):
                name = st.get('store_name', '')
                fid = st.get('folder_id', '')
                short_id = fid[:8] + '…' + fid[-4:] if len(fid) > 16 else fid
                self._backup_info_var.set(
                    f"Backup is configured for this installation.\n"
                    f"Store: {name}\n"
                    f"Drive folder: {short_id}\n\n"
                    "Backups upload to Store_<name> inside that Drive folder.\n"
                    "Use Backup Now to upload, or Sync from Drive to choose any backup "
                    "(.db.gz or older .db) and restore it. Old backups are upgraded automatically.\n"
                    "Use Administrator Login to change store name or folder ID.\n"
                    f"{auto_line}"
                )
            elif st.get('folder_id') and not st.get('creds_ok'):
                self._backup_info_var.set(
                    "Drive folder is set but OAuth credentials are missing or invalid.\n"
                    "Rebuild the EXE with valid config/backup_creds.dat, or delete\n"
                    "%LOCALAPPDATA%\\VeterinaryApp\\backup_creds.dat and restart the app."
                )
            else:
                self._backup_info_var.set(
                    "Backup is not configured on this PC.\n"
                    "Use Administrator Login to set the store name and Drive folder ID,\n"
                    "or embed settings before building the EXE (store_backup.build)."
                )
        except Exception:
            self._backup_info_var.set("Backup status unavailable.")

    def _download_master_medicines(self):
        from core.themed_messagebox import askyesno, showerror, showinfo

        if not askyesno(
            "Download Master",
            "Replace the local master medicine catalog with the global server catalog?\n\n"
            "This does not change inventory, sales, or purchases.",
            parent=self._parent,
        ):
            return
        self._master_status_var.set("Downloading master…")

        def _run():
            try:
                from core.master_medicine_cloud import download_master_replace_local
                from core.master_medicine_service import master_row_count

                ok, msg, _n = download_master_replace_local()
                status = f"Local master rows: {master_row_count():,}"
                self._parent.after(0, lambda: self._master_status_var.set(status))
                if ok:
                    self._parent.after(
                        0, lambda: showinfo("Download Master", msg, parent=self._parent)
                    )
                else:
                    self._parent.after(
                        0, lambda: showerror("Download Master", msg, parent=self._parent)
                    )
            except Exception as exc:
                self._parent.after(
                    0,
                    lambda: showerror("Download Master", str(exc), parent=self._parent),
                )

        threading.Thread(target=_run, daemon=True).start()

    def _push_stock_to_master(self):
        from core.themed_messagebox import askyesno, showerror, showinfo

        if not askyesno(
            "Push Stock → Master",
            "Enrich the global server master from this store's inventory "
            "(schedule, HSN, GST, manufacturer, etc.), then refresh the local snapshot?",
            parent=self._parent,
        ):
            return
        self._master_status_var.set("Pushing stock into master…")

        def _run():
            try:
                from core.master_medicine_cloud import push_stock_into_master
                from core.master_medicine_service import master_row_count

                ok, msg, _summary = push_stock_into_master(self.conn)
                status = f"Local master rows: {master_row_count():,}"
                self._parent.after(0, lambda: self._master_status_var.set(status))
                if ok:
                    self._parent.after(
                        0, lambda: showinfo("Push Stock → Master", msg, parent=self._parent)
                    )
                else:
                    self._parent.after(
                        0, lambda: showerror("Push Stock → Master", msg, parent=self._parent)
                    )
            except Exception as exc:
                self._parent.after(
                    0,
                    lambda: showerror("Push Stock → Master", str(exc), parent=self._parent),
                )

        threading.Thread(target=_run, daemon=True).start()

    def _admin_login(self):
        dlg = open_dialog(self._parent, "Administrator Login", width=360, height=220, resizable=False)
        body = dlg.content
        ttk.Label(body, text="Username").pack(pady=(18, 4))
        user_var = tk.StringVar()
        user_e = ttk.Entry(body, textvariable=user_var, width=32)
        user_e.pack()
        ttk.Label(body, text="Password").pack(pady=(10, 4))
        pass_var = tk.StringVar()
        pass_e = ttk.Entry(body, textvariable=pass_var, show='*', width=32)
        pass_e.pack()
        user_e.focus_set()

        def _submit():
            from core.license_manager import _MASTER_USERNAME, _MASTER_PASSWORD
            u = user_var.get().strip()
            p = pass_var.get()
            ok = (u == _MASTER_USERNAME and p == _MASTER_PASSWORD) or (
                u == "satpudacore" and p == "satpudacore"
            )
            if not ok:
                showerror("Administrator", "Invalid username or password.", parent=dlg)
                pass_e.delete(0, tk.END)
                pass_e.focus_set()
                return
            dlg.destroy()
            self._open_admin_panel()

        user_e.bind('<Return>', lambda e: pass_e.focus_set())
        pass_e.bind('<Return>', lambda e: _submit())
        ttk.Button(dlg.footer, text="Login", command=_submit).pack(side=tk.LEFT, padx=6)
        ttk.Button(dlg.footer, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT, padx=6)

    def _vendor_admin_sign_in(self):
        """Sign in to the Satpuda SERVER as the vendor administrator.

        Not the same thing as the Administrator button that opened this panel:
        that one is a local gate on this PC. This one is Satpuda's own account
        on the server, and it is needed for the few actions that touch the whole
        account -- publishing a store that has never been on the server, listing
        the account's stores, rotating a pairing key.

        It used to be unnecessary, because the username and password were
        compiled into every build and five code paths used them by themselves.
        Now they are typed here, exchanged for a token that lives in memory for
        a few minutes, and never written to disk.
        """
        from core.themed_messagebox import showerror, showinfo

        dlg = open_dialog(
            self._parent, "Satpuda Server Sign-in", width=380, height=240,
            resizable=False,
        )
        body = dlg.content
        ttk.Label(
            body,
            text="Satpuda administrator username and password (not stored).",
            wraplength=340,
        ).pack(pady=(14, 8), padx=12, anchor=tk.W)
        ttk.Label(body, text="Username").pack(anchor=tk.W, padx=12)
        user_var = tk.StringVar()
        user_e = ttk.Entry(body, textvariable=user_var, width=32)
        user_e.pack(padx=12, fill=tk.X)
        ttk.Label(body, text="Password").pack(anchor=tk.W, padx=12, pady=(8, 0))
        pass_var = tk.StringVar()
        pass_e = ttk.Entry(body, textvariable=pass_var, show="*", width=32)
        pass_e.pack(padx=12, fill=tk.X)
        user_e.focus_set()

        def _submit():
            from core import admin_session

            try:
                admin_session.sign_in(user_var.get().strip(), pass_var.get())
            except Exception as exc:
                showerror("Satpuda Server Sign-in", str(exc), parent=dlg)
                pass_var.set("")
                pass_e.focus_set()
                return
            pass_var.set("")
            dlg.destroy()
            showinfo(
                "Satpuda Server Sign-in",
                "Signed in. Administrator actions will work for the next few "
                "minutes on this computer only.",
                parent=self._parent,
            )

        user_e.bind("<Return>", lambda e: pass_e.focus_set())
        pass_e.bind("<Return>", lambda e: _submit())
        ttk.Button(dlg.footer, text="Sign in", command=_submit).pack(side=tk.LEFT, padx=6)
        ttk.Button(dlg.footer, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT, padx=6)

    def _open_admin_panel(self):
        dlg = open_dialog(self._parent, "Administrator Tools", width=480, height=280, resizable=False)
        body = dlg.content
        ttk.Label(
            body,
            text="Choose a tool. Changes are saved on this PC and kept after app updates.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=420,
        ).pack(anchor=tk.W, padx=12, pady=(16, 12))

        btn_row = ttk.Frame(body)
        btn_row.pack(fill=tk.X, padx=12, pady=4)
        ttk.Button(
            btn_row,
            text="Satpuda server sign-in (administrator)",
            command=lambda: (dlg.destroy(), self._vendor_admin_sign_in()),
        ).pack(fill=tk.X, pady=4)
        ttk.Button(
            btn_row,
            text="Drive Backup Settings",
            command=lambda: (dlg.destroy(), self._open_backup_config_editor()),
        ).pack(fill=tk.X, pady=4)
        ttk.Button(
            btn_row,
            text="Expiry File Editor",
            command=lambda: (dlg.destroy(), self._open_expiry_editor()),
        ).pack(fill=tk.X, pady=4)
        ttk.Button(
            btn_row,
            text="My Assist Settings",
            command=lambda: (dlg.destroy(), self._show_section('my_assist')),
        ).pack(fill=tk.X, pady=4)
        ttk.Button(
            btn_row,
            text="Sync Mode (Offline / Online)",
            command=lambda: (dlg.destroy(), self._open_sync_mode_editor()),
        ).pack(fill=tk.X, pady=4)
        ttk.Button(
            btn_row,
            text="Android Store Connection Key",
            command=lambda: (dlg.destroy(), self._open_android_key_viewer()),
        ).pack(fill=tk.X, pady=4)
        ttk.Button(
            btn_row,
            text="Server Connection Info",
            command=lambda: (dlg.destroy(), self._open_server_creds_editor()),
        ).pack(fill=tk.X, pady=4)

        ttk.Button(dlg.footer, text="Close", command=dlg.destroy).pack(side=tk.LEFT, padx=6)

    def _open_assistant_name_editor(self):
        from core.voice.assistant_config import (
            DEFAULT_ASSISTANT_NAME,
            get_assistant_display_name,
            load_assistant_name,
            save_assistant_name,
        )

        dlg = open_dialog(
            self._parent,
            "Administrator - Voice Assistant Name",
            width=520,
            height=280,
            resizable=False,
        )
        body = dlg.content
        ttk.Label(
            body,
            text="Wake word spoken before each voice command.\n"
                 "Use a short English word (default: Satpuda). Changes apply immediately.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=460,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=12, pady=(16, 10))

        row = ttk.Frame(body)
        row.pack(fill=tk.X, padx=12, pady=4)
        ttk.Label(row, text="Assistant name:", width=16).pack(side=tk.LEFT)
        name_var = tk.StringVar(value=load_assistant_name())
        entry = ttk.Entry(row, textvariable=name_var, width=28)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        entry.focus_set()
        entry.select_range(0, tk.END)

        hint_var = tk.StringVar(
            value=f'Example: say "{get_assistant_display_name()}" then "open purchase"',
        )
        ttk.Label(
            body,
            textvariable=hint_var,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=460,
        ).pack(anchor=tk.W, padx=12, pady=(8, 4))

        def _save():
            ok, result = save_assistant_name(name_var.get())
            if not ok:
                showerror("Voice Assistant", result, parent=dlg)
                return
            from core.voice.command_parser import reload_wake_config
            reload_wake_config()
            try:
                app = self._parent.winfo_toplevel()._app_instance
                if app is not None:
                    app._reload_voice_settings()
            except Exception:
                pass
            showinfo(
                "Voice Assistant",
                f'Wake word saved as "{result.capitalize()}".\n'
                f'Say "{result}" then your command.',
                parent=dlg,
            )
            dlg.destroy()

        def _reset():
            name_var.set(DEFAULT_ASSISTANT_NAME)

        entry.bind("<Return>", lambda _e: _save())
        ttk.Button(dlg.footer, text="Save", command=_save).pack(side=tk.LEFT, padx=6)
        ttk.Button(dlg.footer, text=f"Reset to {DEFAULT_ASSISTANT_NAME.capitalize()}", command=_reset).pack(
            side=tk.LEFT, padx=6,
        )
        ttk.Button(dlg.footer, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT, padx=6)

    def _open_backup_config_editor(self):
        from core.backup_manager import get_backup_config_status, write_backup_config

        st = get_backup_config_status()
        dlg = open_dialog(
            self._parent, "Administrator - Drive Backup Settings",
            width=620, height=360, resizable=True,
        )
        top = dlg.content
        top.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        ttk.Label(
            top,
            text="Set which Google Drive folder receives this store's backups.\n"
                 "A subfolder Store_<store_name> is created automatically inside the folder ID.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=560,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 12))

        name_row = ttk.Frame(top)
        name_row.pack(fill=tk.X, pady=4)
        ttk.Label(name_row, text="Store Name:", width=16).pack(side=tk.LEFT)
        store_var = tk.StringVar(value=st.get('store_name', ''))
        ttk.Entry(name_row, textvariable=store_var, width=42).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(
            top,
            text=("This name only decides which Drive subfolder backups are written "
                  "to. It does not rename the store on this PC or on the server."),
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=560,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 4))

        folder_row = ttk.Frame(top)
        folder_row.pack(fill=tk.X, pady=4)
        ttk.Label(folder_row, text="Drive Folder ID:", width=16).pack(side=tk.LEFT)
        folder_var = tk.StringVar(value=st.get('folder_id', ''))
        ttk.Entry(folder_row, textvariable=folder_var, width=42).pack(side=tk.LEFT, fill=tk.X, expand=True)

        creds_label = "OAuth credentials: OK" if st.get('creds_ok') else (
            "OAuth credentials: missing or invalid (rebuild EXE with backup_creds.dat)"
        )
        ttk.Label(
            top, text=creds_label,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            foreground='green' if st.get('creds_ok') else get_alert_color('warning'),
        ).pack(anchor=tk.W, pady=(12, 4))

        from core.license_manager import _appdata_dir
        path_label = os.path.join(_appdata_dir(), 'backup_config.dat')
        ttk.Label(
            top,
            text=f"Saved to:\n{path_label}",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=560,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(4, 8))

        def _save():
            store_name = store_var.get().strip()
            folder_id = folder_var.get().strip()
            if not store_name:
                showerror("Backup Settings", "Store name is required.", parent=dlg)
                return
            if not folder_id:
                showerror("Backup Settings", "Drive folder ID is required.", parent=dlg)
                return
            try:
                from core.store_manager import display_name_key
                write_backup_config(folder_id, store_name)
                # This screen names a DRIVE FOLDER. It used to also call
                # update_active_store_display_name(store_name), which rewrites
                # the store_key, moves stores/<key>/ and leaves every file that
                # carries the store's identity -- server_session_<key>.json,
                # store_key_<key>.txt, the catalog snapshot, the bootstrap
                # marker, the watermark bucket -- behind under the old key. The
                # server is never told. On the next launch nothing matched and
                # the PC quietly paired itself to a brand-new empty store while
                # the shop's real ledger sat on the server untouched: the "store
                # opened with no data" report. The field is prefilled from
                # backup_config.dat, so pressing Save without editing anything
                # was enough to trigger it. The desktop build dropped this in
                # the same way; renaming a store is a deliberate act and belongs
                # behind its own confirmation, not here.
                import sys
                if not getattr(sys, 'frozen', False):
                    try:
                        from core.backup_manager import write_bundled_backup_config
                        write_bundled_backup_config(folder_id, store_name)
                    except Exception:
                        pass
                self._refresh_backup_info()
                self._refresh_stores_panel()
                showinfo(
                    "Backup Settings",
                    f"Backup settings saved.\n\n"
                    f"Store: {store_name}\n"
                    f"Drive subfolder: {display_name_key(store_name)}\n\n"
                    "If this folder already exists on Drive, backups will use it.",
                    parent=dlg,
                )
            except Exception as e:
                showerror("Backup Settings", f"Failed to save backup settings:\n{e}", parent=dlg)

        ttk.Button(dlg.footer, text="Save", command=_save).pack(side=tk.LEFT, padx=6)
        ttk.Button(dlg.footer, text="Close", command=dlg.destroy).pack(side=tk.LEFT, padx=6)

    def _open_android_key_viewer(self):
        from core.sync_prefs import is_online_mode, mode_label, get_sync_mode
        from core.store_link import get_local_android_key, regenerate_android_key
        from core.store_manager import get_active_display_name
        from core.app_setup import load_app_mode
        from core.themed_messagebox import showinfo, showerror, askyesno

        if not is_online_mode():
            showinfo(
                "Android Connection Key",
                f"Sync mode is {mode_label(get_sync_mode())}.\n\n"
                "Switch to Online (Server) in Sync Mode first. The Android key "
                "pairs your phone to this store on Satpuda Core Server.",
                parent=self._parent,
            )
            return

        dlg = open_dialog(
            self._parent, "Android Store Connection Key",
            width=560, height=340, resizable=False,
        )
        body = dlg.content
        store_name = get_active_display_name() or 'Default'
        key_var = tk.StringVar(value=get_local_android_key() or '(not generated yet)')

        ttk.Label(
            body,
            text="Use this key when activating Satpuda Core on Android.\n"
                 "Enter the same store name and this SC- key on the phone to pair "
                 "with Satpuda Core Server.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=500,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=12, pady=(16, 12))

        row = ttk.Frame(body)
        row.pack(fill=tk.X, padx=12, pady=8)
        ttk.Label(row, text="Store:", width=10).pack(side=tk.LEFT)
        ttk.Label(row, text=store_name).pack(side=tk.LEFT)

        key_row = ttk.Frame(body)
        key_row.pack(fill=tk.X, padx=12, pady=8)
        ttk.Label(key_row, text="Key:", width=10).pack(side=tk.LEFT)
        ttk.Entry(key_row, textvariable=key_var, width=36, state='readonly').pack(
            side=tk.LEFT, fill=tk.X, expand=True)

        def _ensure():
            from core.background_workers import run_with_progress
            from core.sync_coordinator import ensure_online_store_link

            def _worker(put):
                put("Pairing store / generating Android key…")
                return ensure_online_store_link()

            def _done(k):
                if k:
                    key_var.set(k)
                else:
                    showerror(
                        "Android Key",
                        "Could not pair with server. Check internet connection.",
                        parent=dlg,
                    )

            run_with_progress(
                dlg,
                "Android Connection Key",
                _worker,
                on_complete=_done,
                on_error=lambda exc: showerror("Android Key", str(exc), parent=dlg),
            )

        def _regenerate():
            if not askyesno(
                "Regenerate Key",
                "This invalidates the old Android key. Devices using the old key "
                "must be re-activated. Continue?",
                parent=dlg,
            ):
                return

            from core.background_workers import run_with_progress

            def _worker(put):
                put("Regenerating Android key…")
                return regenerate_android_key(store_name, load_app_mode())

            def _done(k):
                key_var.set(k)
                showinfo("Android Key", f"New key: {k}", parent=dlg)

            run_with_progress(
                dlg,
                "Regenerate Android Key",
                _worker,
                on_complete=_done,
                on_error=lambda exc: showerror("Android Key", str(exc), parent=dlg),
            )

        def _copy():
            try:
                k = key_var.get().strip()
                if k and not k.startswith('('):
                    self._parent.clipboard_clear()
                    self._parent.clipboard_append(k)
                    showinfo("Copied", "Android key copied to clipboard.", parent=dlg)
            except Exception as exc:
                showerror("Copy failed", str(exc), parent=dlg)

        btn_row = ttk.Frame(body)
        btn_row.pack(fill=tk.X, padx=12, pady=12)
        ttk.Button(btn_row, text="Generate / Refresh", command=_ensure).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_row, text="Regenerate New Key", command=_regenerate).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_row, text="Copy Key", command=_copy).pack(side=tk.LEFT, padx=4)

        if not get_local_android_key():
            dlg.after(200, _ensure)

        ttk.Button(dlg.footer, text="Close", command=dlg.destroy).pack(side=tk.LEFT, padx=6)

    def _open_sync_mode_editor(self):
        from core.sync_prefs import (
            MODE_OFFLINE, MODE_ONLINE, get_sync_mode, set_sync_mode, mode_label,
        )
        from core.themed_messagebox import showinfo, showerror

        dlg = open_dialog(
            self._parent, "Administrator - Sync Mode",
            width=560, height=380, resizable=False,
        )
        body = dlg.content
        ttk.Label(
            body,
            text="Choose how this device syncs with other Satpuda Core devices.\n"
                 "Only administrators can change this setting.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=500,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=12, pady=(16, 12))

        mode_var = tk.StringVar(value=get_sync_mode())

        ttk.Radiobutton(
            body, text=mode_label(MODE_OFFLINE), value=MODE_OFFLINE, variable=mode_var,
        ).pack(anchor=tk.W, padx=20, pady=4)
        ttk.Label(
            body,
            text="  Local SQLite is source of truth. Google Drive backup/restore by store name.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT), foreground='gray',
        ).pack(anchor=tk.W, padx=36)

        ttk.Radiobutton(
            body, text=mode_label(MODE_ONLINE), value=MODE_ONLINE, variable=mode_var,
        ).pack(anchor=tk.W, padx=20, pady=(12, 4))
        ttk.Label(
            body,
            text="  Server-first: history/inventory/master lists load from "
                 "Satpuda Core Server. Switching Online does NOT download "
                 "the full database into SQLite — only pairs the store and "
                 "starts live update hints.",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT), foreground='gray',
            wraplength=460,
        ).pack(anchor=tk.W, padx=36)

        def _save():
            try:
                from core.sync_prefs import MODE_ONLINE as _ON, is_online_mode
                from core.sync_coordinator import stop_online_sync

                previous_online = is_online_mode()
                chosen = mode_var.get()
                set_sync_mode(chosen)

                tip = ""
                if chosen == _ON:
                    tip = (
                        "\n\nOnline behaviour (server-first):\n"
                        "• Sales/purchase history & inventory screens read the server\n"
                        "• Live WebSocket hints refresh those lists (no full SQLite replace)\n"
                        "• Each save still pushes to the server\n"
                        "• Pull from Server = recovery tool only — not used on mode switch\n"
                        "• No internet? Switch Offline to work locally"
                    )

                # Offline ← Online: stop sync quickly on UI thread
                if chosen != _ON and previous_online:
                    try:
                        stop_online_sync()
                    except Exception:
                        pass
                    # The app MUST restart here.
                    #
                    # Online runs on sqlite3.connect(":memory:") -- there is no
                    # local store file behind it. Flipping the preference does not
                    # swap that connection, so without a restart the shop kept
                    # billing into memory and every one of those bills was gone
                    # the moment the app closed.
                    showinfo(
                        "Sync Mode",
                        f"Saved: {mode_label(chosen)}\n\n"
                        "Online server sync stopped.\n\n"
                        "The app will now restart so it opens the local store "
                        "file. Do not enter any bills until it comes back.",
                        parent=dlg,
                    )
                    dlg.destroy()
                    try:
                        _restart_app(self._parent.winfo_toplevel())
                    except Exception:
                        _restart_app()
                    return

                # Offline → Online: heavy bootstrap on background thread + progress UI
                if chosen == _ON:
                    from core.background_workers import db_path_from_conn, run_with_progress
                    from core.online_switch_job import run_online_switch

                    db_path = db_path_from_conn(self.conn)
                    on_change = getattr(self._parent, '_on_server_data_changed', None)

                    def worker(put):
                        return run_online_switch(
                            db_path,
                            progress_cb=put,
                            on_change=on_change,
                        )

                    def on_done(result):
                        result = result or {}
                        extra = ""
                        if result.get("bootstrap_ran") and result.get("bootstrap_message"):
                            extra += f"\n\nBootstrap: {result.get('bootstrap_message')}"
                        if result.get("error"):
                            extra += f"\n\nNote: {result.get('error')}"
                        extra += (
                            "\n\nConnected — live hints started. "
                            "History/inventory read from the server (no full SQLite download)."
                            if result.get("server_sync_started")
                            else "\n\nCould not start live sync yet. Check internet / server, then reopen the store."
                        )
                        showinfo(
                            "Sync Mode",
                            f"Saved: {mode_label(chosen)}{extra}{tip}",
                            parent=self._parent,
                        )
                        try:
                            dlg.destroy()
                        except Exception:
                            pass

                    def on_error(exc):
                        showerror("Sync Mode", str(exc), parent=self._parent)

                    run_with_progress(
                        self._parent,
                        "Switching to Online…",
                        worker,
                        on_complete=on_done,
                        on_error=on_error,
                    )
                    return

                showinfo(
                    "Sync Mode",
                    f"Saved: {mode_label(chosen)}",
                    parent=dlg,
                )
                dlg.destroy()
            except Exception as exc:
                showerror("Sync Mode", str(exc), parent=dlg)

        ttk.Button(dlg.footer, text="Save", command=_save).pack(side=tk.LEFT, padx=6)
        ttk.Button(dlg.footer, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT, padx=6)

    def _open_server_creds_editor(self):
        """Show Satpuda Core Server connection info (legacy Server editor replaced)."""
        from core import server_api as api
        from core.store_manager import get_active_store_key
        from core.themed_messagebox import showinfo

        dlg = open_dialog(
            self._parent, "Administrator - Server Connection",
            width=560, height=280, resizable=False,
        )
        body = dlg.content
        session = api.load_session(get_active_store_key() or "Store_Default")
        reachable = api.health_ok(timeout=3.0)
        ttk.Label(
            body,
            text=(
                f"API: {api.api_base()}\n"
                f"Reachable: {'Yes' if reachable else 'No'}\n"
                f"Store ID: {session.get('store_id') or '(not paired yet)'}\n"
                f"Pairing key: {session.get('android_key') or '(none)'}\n\n"
                "Online mode syncs to Satpuda Core Server."
            ),
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            wraplength=520,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=12, pady=(16, 12))

        def _test():
            ok = api.health_ok(timeout=5.0)
            showinfo(
                "Server",
                f"{'Server is reachable.' if ok else 'Cannot reach server.'}\n{api.api_base()}",
                parent=dlg,
            )

        ttk.Button(dlg.footer, text="Test Connection", command=_test).pack(side=tk.LEFT, padx=6)
        ttk.Button(dlg.footer, text="Close", command=dlg.destroy).pack(side=tk.LEFT, padx=6)

    def _expiry_paths(self):
        from core.license_manager import all_expiry_paths, expiry_write_paths
        return all_expiry_paths(), expiry_write_paths()

    def _read_expiry_payload_from_path(self, path):
        from core.license_manager import _read_expiry_payload_from_path
        return _read_expiry_payload_from_path(path)

    def _write_expiry_payload_to_path(self, path, payload):
        from core.license_manager import _write_expiry_payload
        _write_expiry_payload(payload)

    def _open_expiry_editor(self):
        from core.background_workers import run_with_progress
        from core.license_manager import get_activation_date, get_expiry_state
        from core.sync_prefs import is_online_mode

        def _worker(put):
            put("Fetching expiry from server…")
            return get_expiry_state(force_server=True)

        def _open(state):
            self._open_expiry_editor_with_state(state)

        run_with_progress(
            self._parent,
            "Expiry Editor",
            _worker,
            on_complete=_open,
            on_error=lambda exc: showerror(
                "Expiry Editor",
                f"Could not load expiry:\n{exc}",
                parent=self._parent,
            ),
        )

    def _open_expiry_editor_with_state(self, state):
        from core.license_manager import get_activation_date
        from core.sync_prefs import is_online_mode

        read_paths, write_paths = self._expiry_paths()
        state = state or {}
        online = bool(state.get("online", is_online_mode()))
        server = state.get("server") if isinstance(state.get("server"), dict) else None
        merged = {
            "enabled": bool(state.get("enabled", True)),
            "expiry_date": str(state.get("expiry_date") or date.today())[:10],
        }
        expiry_cfg = {
            "apply_expiry_check": bool(state.get("apply_expiry_check", True)),
        }

        dlg = open_dialog(self._parent, "Administrator - Expiry File Editor", width=620, height=500, resizable=True)
        top = dlg.content
        top.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        src_map = {
            "server": "server (Online — fetched now)",
            "cache": "server cache (Online unreachable)",
            "local": "local AppData (Offline)",
        }
        src = src_map.get(str(state.get("source") or ""), "local AppData")
        act_day = str(state.get("activation_date") or get_activation_date() or "")[:10]
        ttk.Label(
            top,
            text=f"Source: {src}",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(anchor=tk.W, pady=(0, 8))
        if isinstance(server, dict) and server.get('is_active') is False:
            ttk.Label(
                top,
                text="Server access is OFF for this store (admin panel). Devices cannot sync until re-enabled.",
                foreground="#b00020",
            ).pack(anchor=tk.W, pady=(0, 8))

        act_row = ttk.Frame(top)
        act_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(act_row, text="Activation Date (YYYY-MM-DD):").pack(side=tk.LEFT)
        activation_var = tk.StringVar(value=act_day or str(date.today()))
        ttk.Entry(act_row, textvariable=activation_var, width=22).pack(side=tk.LEFT, padx=8)
        ttk.Label(
            top,
            text="For old stores: type the real activation date here (separate from expiry).",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).pack(anchor=tk.W, pady=(0, 8))

        apply_var = tk.BooleanVar(value=bool(expiry_cfg.get('apply_expiry_check', True)))
        ttk.Checkbutton(
            top,
            text="Apply expiry check (master switch — when off, expiry is ignored)",
            variable=apply_var,
        ).pack(anchor=tk.W, pady=(0, 8))

        enabled_var = tk.BooleanVar(value=bool(merged.get('enabled', True)))
        ttk.Checkbutton(top, text="Expiry enabled", variable=enabled_var).pack(anchor=tk.W, pady=(0, 8))

        row = ttk.Frame(top)
        row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(row, text="Expiry Date (YYYY-MM-DD):").pack(side=tk.LEFT)
        expiry_var = tk.StringVar(value=str(merged.get('expiry_date', '') or ''))
        ttk.Entry(row, textvariable=expiry_var, width=22).pack(side=tk.LEFT, padx=8)

        ttk.Label(
            top,
            text="Online: activation + expiry save to the server. Offline: saved locally only.",
        ).pack(anchor=tk.W, pady=(0, 4))
        ttk.Label(
            top,
            text="Local mirror path:",
        ).pack(anchor=tk.W)
        path_text = tk.Text(top, height=5, wrap='word')
        path_text.pack(fill=tk.X, pady=(2, 8))
        path_lines = []
        for p in write_paths:
            tag = " (exists)" if os.path.exists(p) else " (will create)"
            path_lines.append(p + tag)
        for p in read_paths:
            if p not in write_paths and os.path.exists(p):
                path_lines.append(p + " (read-only legacy)")
        path_text.insert('1.0', "\n".join(path_lines))
        path_text.configure(state='disabled')

        raw_text = tk.Text(top, height=8, wrap='word')
        raw_text.pack(fill=tk.BOTH, expand=True)
        raw_text.insert('1.0', json.dumps(merged, indent=2))

        def _save():
            payload = {
                'enabled': bool(enabled_var.get()),
                'expiry_date': (expiry_var.get() or '').strip(),
            }
            act = (activation_var.get() or '').strip()
            try:
                date.fromisoformat(payload['expiry_date'])
            except Exception:
                showerror("Expiry Editor", "Invalid expiry date. Use YYYY-MM-DD.", parent=dlg)
                return
            if act:
                try:
                    date.fromisoformat(act)
                except Exception:
                    showerror(
                        "Expiry Editor",
                        "Invalid activation date. Use YYYY-MM-DD.",
                        parent=dlg,
                    )
                    return

            from tkinter.simpledialog import askstring

            from core.background_workers import run_with_progress
            from core.license_manager import save_expiry_settings
            from core.sync_prefs import is_online_mode

            apply_check = bool(apply_var.get())

            # THE EXPIRY CANNOT BE SET FROM THIS COMPUTER ALONE ANY MORE.
            #
            # It is written on the Satpuda server and signed there, and this
            # screen is only the place the request is typed. So it needs the
            # vendor's administrator username and password -- asked for now,
            # sent once, never stored -- and it needs the internet. Without
            # either, nothing is changed at all: there is deliberately no
            # local-only path left, because a shop that can write its own expiry
            # file is a shop that sets its own expiry.
            admin_user = askstring(
                "Satpuda administrator",
                "Administrator username:",
                parent=dlg,
            )
            if not (admin_user or '').strip():
                return
            admin_pass = askstring(
                "Satpuda administrator",
                "Administrator password:",
                parent=dlg,
                show='*',
            )
            if not (admin_pass or ''):
                return

            def _worker(put):
                put("Saving expiry on the Satpuda server…")
                return save_expiry_settings(
                    enabled=payload['enabled'],
                    expiry_date=payload['expiry_date'],
                    apply_expiry_check=apply_check,
                    activation_date=act or None,
                    admin_username=(admin_user or '').strip(),
                    admin_password=admin_pass or '',
                    push_remote=True,
                )

            def _done(result):
                if result.get('ok') is False:
                    showerror(
                        "Expiry Editor",
                        result.get('error')
                        or "Server update failed. Expiry was not changed.",
                        parent=dlg,
                    )
                    return
                combined = {
                    'activation_date': act or result.get('activation_date'),
                    'expiry_config': {'apply_expiry_check': apply_check},
                    'expiry_dat': result.get('local') or payload,
                    'online': bool(is_online_mode()),
                    'server': result.get('server'),
                }
                raw_text.delete('1.0', tk.END)
                raw_text.insert('1.0', json.dumps(combined, indent=2))
                showinfo(
                    "Expiry Editor",
                    "Expiry saved on the Satpuda server and signed for this "
                    "computer.",
                    parent=dlg,
                )

            run_with_progress(
                dlg,
                "Saving Expiry",
                _worker,
                on_complete=_done,
                on_error=lambda e: showerror(
                    "Expiry Editor",
                    f"Failed to save expiry file:\n{e}",
                    parent=dlg,
                ),
            )

        ttk.Button(dlg.footer, text="Save & Sync", command=_save).pack(side=tk.LEFT, padx=6)
        ttk.Button(dlg.footer, text="Close", command=dlg.destroy).pack(side=tk.LEFT, padx=6)

    def _run_export_job(self, title, worker, on_ready):
        from core.background_workers import run_with_progress
        run_with_progress(
            self._parent,
            title,
            worker,
            on_complete=on_ready,
            on_error=lambda exc: showerror('Export Error', str(exc), parent=self._parent),
        )

    def export_sales(self):
        def _worker(put):
            import sqlite3
            from core.background_workers import db_path_from_conn
            path = db_path_from_conn(self.conn)
            conn = sqlite3.connect(path, check_same_thread=False) if path else self.conn
            own = conn is not self.conn
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM sales")
                if cur.fetchone()[0] == 0:
                    return None
                put('Loading sales lines…')
                cur.execute("""
                    SELECT s.bill_no, s.bill_date, c.name, COALESCE(c.phone,''),
                           COALESCE(s.doctor_name,''), m.name, COALESCE(m.type,''),
                           COALESCE(si.qty,0), COALESCE(si.rate,0), COALESCE(si.amount,0),
                           COALESCE(m.batch_no,''), COALESCE(m.expiry_date,''),
                           s.total_amount, COALESCE(s.discount,0),
                           COALESCE(s.cash_paid,0), COALESCE(s.online_paid,0),
                           COALESCE(s.amount_paid,0), COALESCE(s.due_amount,0),
                           COALESCE(s.total_due,0)
                    FROM sales s
                    JOIN customers c   ON s.customer_id  = c.id
                    JOIN sales_items si ON si.sale_id     = s.id
                    JOIN medicines m   ON si.medicine_id  = m.id
                    ORDER BY s.bill_date DESC, s.bill_no, m.name
                """)
                rows = cur.fetchall()
                headers = ['Bill No','Date','Customer','Phone','Doctor',
                           'Medicine','Type','Qty','Rate','Amount',
                           'Batch No','Expiry Date','Bill Total','Discount',
                           'Cash Paid','Online Paid','Amount Paid','Due Amount','Total Due']
                return rows, headers, f'Sales Export ({len(rows)} rows)', 'sales_export'
            finally:
                if own:
                    conn.close()

        def _ready(payload):
            if not payload:
                showinfo('Nothing to Export', 'No sales records found.', parent=self._parent)
                return
            rows, headers, title, key = payload
            from core.export_manager import export_data
            export_data(self._parent, title, headers, rows, key)

        self._run_export_job('Export Sales', _worker, _ready)

    def export_purchases(self):
        def _worker(put):
            import sqlite3
            from core.background_workers import db_path_from_conn
            path = db_path_from_conn(self.conn)
            conn = sqlite3.connect(path, check_same_thread=False) if path else self.conn
            own = conn is not self.conn
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM purchases")
                if cur.fetchone()[0] == 0:
                    return None
                put('Loading purchase lines…')
                cur.execute("""
                    SELECT p.purchase_no, COALESCE(p.bill_number,''), p.purchase_date,
                           s.name, COALESCE(s.phone,''), m.name, COALESCE(pi.type,''),
                           COALESCE(pi.qty,0), COALESCE(pi.free_qty,0),
                           COALESCE(pi.rate,0), COALESCE(pi.mrp,0),
                           COALESCE(pi.batch_no,''), COALESCE(pi.expiry_date,''),
                           p.total_amount, COALESCE(p.amount_paid,0),
                           COALESCE(p.due_amount,0), COALESCE(p.total_due,0)
                    FROM purchases p
                    JOIN suppliers s       ON p.supplier_id   = s.id
                    JOIN purchase_items pi ON pi.purchase_id  = p.id
                    JOIN medicines m       ON pi.medicine_id  = m.id
                    ORDER BY p.purchase_date DESC, p.purchase_no, m.name
                """)
                rows = cur.fetchall()
                headers = ['Purchase No','Bill Number','Date','Supplier','Phone',
                           'Medicine','Type','Qty','Free Qty','Rate','MRP',
                           'Batch No','Expiry Date','Total Amount','Amount Paid',
                           'Due Amount','Total Due']
                return rows, headers, f'Purchases Export ({len(rows)} rows)', 'purchases_export'
            finally:
                if own:
                    conn.close()

        def _ready(payload):
            if not payload:
                showinfo('Nothing to Export', 'No purchase records found.', parent=self._parent)
                return
            rows, headers, title, key = payload
            from core.export_manager import export_data
            export_data(self._parent, title, headers, rows, key)

        self._run_export_job('Export Purchases', _worker, _ready)

    def export_inventory(self):
        def _worker(put):
            import sqlite3
            from core.background_workers import db_path_from_conn
            path = db_path_from_conn(self.conn)
            conn = sqlite3.connect(path, check_same_thread=False) if path else self.conn
            own = conn is not self.conn
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM medicines")
                if cur.fetchone()[0] == 0:
                    return None
                put('Loading inventory…')
                cur.execute("""
                    SELECT m.name, m.type, COALESCE(m.batch_no,''), COALESCE(m.expiry_date,''),
                           COALESCE(m.stock_qty,0), COALESCE(m.unit,''),
                           COALESCE(m.mrp,0), COALESCE(m.rate,0), COALESCE(m.gst_percent,0),
                           COALESCE(m.hsn_code,''), COALESCE(m.manufacturer,''),
                           COALESCE(m.schedule,''), COALESCE(m.content_drug,''),
                           COALESCE(m.location,''), m.created_at
                    FROM medicines m ORDER BY m.name, m.batch_no
                """)
                rows = cur.fetchall()
                headers = ['Name','Type','Batch No','Expiry Date','Stock Qty','Unit',
                           'MRP','Rate','GST%','HSN Code','Manufacturer',
                           'Schedule','Content/Drug','Location','Created At']
                return rows, headers, f'Inventory Export ({len(rows)} medicines)', 'inventory_export'
            finally:
                if own:
                    conn.close()

        def _ready(payload):
            if not payload:
                showinfo('Nothing to Export', 'No medicines found.', parent=self._parent)
                return
            rows, headers, title, key = payload
            from core.export_manager import export_data
            export_data(self._parent, title, headers, rows, key)

        self._run_export_job('Export Inventory', _worker, _ready)

    def export_all(self):
        def _worker(put):
            import sqlite3
            from core.background_workers import db_path_from_conn
            path = db_path_from_conn(self.conn)
            conn = sqlite3.connect(path, check_same_thread=False) if path else self.conn
            own = conn is not self.conn
            sections = []
            try:
                cur = conn.cursor()
                put('Loading sales…')
                cur.execute("""
                    SELECT s.bill_no, s.bill_date, c.name, COALESCE(c.phone,''),
                           COALESCE(s.doctor_name,''), m.name, COALESCE(m.type,''),
                           COALESCE(si.qty,0), COALESCE(si.rate,0), COALESCE(si.amount,0),
                           COALESCE(m.batch_no,''), COALESCE(m.expiry_date,''),
                           s.total_amount, COALESCE(s.discount,0),
                           COALESCE(s.cash_paid,0), COALESCE(s.online_paid,0),
                           COALESCE(s.amount_paid,0), COALESCE(s.due_amount,0),
                           COALESCE(s.total_due,0)
                    FROM sales s
                    JOIN customers c   ON s.customer_id  = c.id
                    JOIN sales_items si ON si.sale_id     = s.id
                    JOIN medicines m   ON si.medicine_id  = m.id
                    ORDER BY s.bill_date DESC, s.bill_no, m.name
                """)
                sections.append(('Sales', ['Bill No','Date','Customer','Phone','Doctor',
                    'Medicine','Type','Qty','Rate','Amount','Batch No','Expiry Date',
                    'Bill Total','Discount','Cash Paid','Online Paid',
                    'Amount Paid','Due Amount','Total Due'], cur.fetchall()))
                put('Loading purchases…')
                cur.execute("""
                    SELECT p.purchase_no, COALESCE(p.bill_number,''), p.purchase_date,
                           s.name, COALESCE(s.phone,''), m.name, COALESCE(pi.type,''),
                           COALESCE(pi.qty,0), COALESCE(pi.free_qty,0),
                           COALESCE(pi.rate,0), COALESCE(pi.mrp,0),
                           COALESCE(pi.batch_no,''), COALESCE(pi.expiry_date,''),
                           p.total_amount, COALESCE(p.amount_paid,0),
                           COALESCE(p.due_amount,0), COALESCE(p.total_due,0)
                    FROM purchases p
                    JOIN suppliers s       ON p.supplier_id   = s.id
                    JOIN purchase_items pi ON pi.purchase_id  = p.id
                    JOIN medicines m       ON pi.medicine_id  = m.id
                    ORDER BY p.purchase_date DESC, p.purchase_no, m.name
                """)
                sections.append(('Purchases', ['Purchase No','Bill Number','Date','Supplier','Phone',
                    'Medicine','Type','Qty','Free Qty','Rate','MRP','Batch No','Expiry Date',
                    'Total Amount','Amount Paid','Due Amount','Total Due'], cur.fetchall()))
                put('Loading inventory…')
                cur.execute("""
                    SELECT m.name, m.type, COALESCE(m.batch_no,''), COALESCE(m.expiry_date,''),
                           COALESCE(m.stock_qty,0), COALESCE(m.unit,''),
                           COALESCE(m.mrp,0), COALESCE(m.rate,0), COALESCE(m.gst_percent,0),
                           COALESCE(m.hsn_code,''), COALESCE(m.manufacturer,''),
                           COALESCE(m.schedule,''), COALESCE(m.content_drug,''),
                           COALESCE(m.location,''), m.created_at
                    FROM medicines m ORDER BY m.name, m.batch_no
                """)
                sections.append(('Inventory', ['Name','Type','Batch No','Expiry Date','Stock Qty','Unit',
                    'MRP','Rate','GST%','HSN Code','Manufacturer',
                    'Schedule','Content/Drug','Location','Created At'], cur.fetchall()))
                return sections
            finally:
                if own:
                    conn.close()

        def _ready(sections):
            if not sections or not any(rows for _, _, rows in sections):
                showinfo('Nothing to Export', 'No data found.', parent=self._parent)
                return
            from core.export_manager import export_all_combined
            export_all_combined(self._parent, sections)

        self._run_export_job('Export All', _worker, _ready)

    def delete_all_tables(self):
        dlg = open_dialog(self._parent, "Enter Password", width=380, height=170, resizable=False)
        body = dlg.content
        ttk.Label(body, text="Password:").pack(pady=(18, 4))
        pwd_var = tk.StringVar()
        pwd_e = ttk.Entry(body, textvariable=pwd_var, show='*', width=30)
        pwd_e.pack(pady=4)
        pwd_e.focus()

        def _confirm():
            from core.license_manager import _MASTER_PASSWORD
            if pwd_var.get() != _MASTER_PASSWORD:
                showerror("Wrong Password", "Incorrect password.", parent=dlg)
                pwd_e.delete(0, tk.END)
                pwd_e.focus()
                return
            dlg.destroy()
            if not askyesno("Confirm Delete",
                            "This will permanently delete ALL local data for this store. "
                            "Server cloud data is not wiped here (use admin dashboard). Continue?",
                            parent=self._parent):
                return
            from core.background_workers import run_with_progress

            def _worker(put):
                cloud_msg = ''
                try:
                    from core.sync_prefs import is_online_mode
                    if is_online_mode():
                        cloud_msg = (
                            'Local wipe only. Clear this store on the server '
                            'admin dashboard if needed.\n'
                        )
                except Exception as exc:
                    cloud_msg = f'Server note failed: {exc}\n'
                put('Deleting local tables…')
                import sqlite3
                from core.background_workers import db_path_from_conn
                path = db_path_from_conn(self.conn)
                conn = sqlite3.connect(path, check_same_thread=False) if path else self.conn
                own = conn is not self.conn
                try:
                    cur = conn.cursor()
                    tables = ['sales_items','sales','purchase_items','purchases',
                              'medicine_shelf','medicines','customers','suppliers',
                              'doctors','shelves','pharmacy_profile','settings',
                              'racks','sections','boxes','shelf_settings']
                    for t in tables:
                        try:
                            cur.execute(f"DROP TABLE IF EXISTS {t}")
                        except Exception:
                            pass
                    for obj_type, name in [
                        ('TRIGGER','trg_purchases_after_insert'),('TRIGGER','trg_purchases_after_update'),
                        ('TRIGGER','trg_sales_after_insert'),('TRIGGER','trg_sales_after_update'),
                        ('VIEW','bills_cleared'),('VIEW','accounts_cleared'),('VIEW','supplier_due_status'),
                    ]:
                        try:
                            cur.execute(f"DROP {obj_type} IF EXISTS {name}")
                        except Exception:
                            pass
                    conn.commit()
                finally:
                    if own:
                        conn.close()
                return cloud_msg

            def _done(cloud_msg):
                root = self._parent.winfo_toplevel()
                main_app = getattr(root, '_main_app', None)
                if main_app and hasattr(main_app, 'create_tables'):
                    main_app.create_tables()
                    try:
                        from core.customer_service import migrate_schema
                        migrate_schema(self.conn)
                    except Exception:
                        pass
                showinfo(
                    "Success",
                    f"{cloud_msg}All local data deleted. The application will now restart.",
                    parent=self._parent,
                )
                _restart_app(root)

            run_with_progress(
                self._parent,
                'Delete All Data',
                _worker,
                on_complete=_done,
                on_error=lambda exc: showerror('Delete Error', str(exc), parent=self._parent),
            )

        pwd_e.bind('<Return>', lambda e: _confirm())
        ttk.Button(dlg.footer, text="OK", command=_confirm).pack(side=tk.LEFT, padx=6)
        ttk.Button(dlg.footer, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT, padx=6)
