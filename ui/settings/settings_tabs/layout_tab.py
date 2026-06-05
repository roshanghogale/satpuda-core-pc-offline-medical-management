import tkinter as tk
import os
from tkinter import filedialog
try:
    import ttkbootstrap as ttk
    TTKBOOTSTRAP_AVAILABLE = True
except ImportError:
    from tkinter import ttk
    TTKBOOTSTRAP_AVAILABLE = False
from core.themed_messagebox import showinfo, showwarning, showerror
from core.font_config import *
from core.layout_config import (
    _DEFAULTS, _BANNER_DEFAULTS, _SCHEDULE_UNIT_DEFAULTS, _TYPE_QTY_DEFAULTS,
    _DEFAULT_SCHEDULES, _DEFAULT_MED_TYPES, save_layout, load_layout,
    copy_custom_home_banner,
)
from core.column_config import (
    TABLE_COLUMNS, PAGE_LABELS, QUICK_ACCESS_BUTTONS, default_column_visibility,
    EXPORT_REPORTS, EXPORT_PAGE_LABELS, default_export_column_visibility,
    _normalize_page_export_saved,
)
from ui.settings.settings_tabs.appearance_scroll import AppearanceScrollPane
from core.app_setup import AVAILABLE_THEMES, load_theme, save_theme, restart_app as _restart_app


# Sidebar section id → button label
_NAV_SECTIONS = [
    ('theme',        '\U0001f3a8  Theme'),
    ('font',         'Font Size'),
    ('banner',       'Home Banner'),
    ('quick_access', 'Quick Access'),
    ('columns',      'Column Visibility'),
    ('rows',         'Table Row Counts'),
    ('units',        'Medicine Units'),
    ('schedules',    'Schedules'),
    ('med_types',    'Medicine Types'),
]


class LayoutTab:
    def __init__(self, notebook, parent_root):
        self._root = parent_root
        self._panels = {}
        self._nav_buttons = {}
        self._active_section = None

        outer = ttk.Frame(notebook)
        notebook.add(outer, text="Appearance")

        shell = ttk.Frame(outer)
        shell.pack(fill=tk.BOTH, expand=True)

        # ── Left navigation ───────────────────────────────────────────────
        nav_outer = ttk.LabelFrame(shell, text="Sections")
        nav_outer.pack(side=tk.LEFT, fill=tk.Y, padx=(8, 4), pady=8)

        nav_scroll = ttk.Frame(nav_outer)
        nav_scroll.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        for section_id, label in _NAV_SECTIONS:
            if section_id == 'theme' and not TTKBOOTSTRAP_AVAILABLE:
                continue
            btn = ttk.Button(
                nav_scroll, text=label, width=22,
                command=lambda k=section_id: self._show_section(k),
            )
            btn.pack(fill=tk.X, pady=2)
            self._nav_buttons[section_id] = btn

        ttk.Separator(nav_outer, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=6, pady=8)
        try:
            ttk.Button(
                nav_outer, text="Save Appearance\n& Restart",
                command=self._save, bootstyle='success', width=22,
            ).pack(padx=6, pady=(0, 8))
        except Exception:
            ttk.Button(
                nav_outer, text="Save Appearance & Restart",
                command=self._save, width=22,
            ).pack(padx=6, pady=(0, 8))

        # ── Right content: dedicated scroll pane (canvas + tk.Frame) ──────
        right_col = ttk.Frame(shell)
        right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 8), pady=8)
        self._scroller = AppearanceScrollPane(right_col)
        self._content_host = self._scroller.frame

        saved_font = 10
        try:
            from core.font_config import _get_font_size_path
            path = _get_font_size_path()
            if os.path.exists(path):
                saved_font = int(open(path).read().strip())
        except Exception:
            pass
        self._saved = load_layout()
        self._saved_font = saved_font

        self._build_all_panels()

        first = 'font' if not TTKBOOTSTRAP_AVAILABLE else 'theme'
        self._show_section(first)

    def _panel(self, section_id):
        """Section root frame inside the scrollable area."""
        wrapper = ttk.Frame(self._content_host)
        self._panels[section_id] = wrapper
        return wrapper

    def sync_input_canvas(self):
        """Point GlobalInputController at the Appearance scroll canvas."""
        app = getattr(self._root, '_main_app', None)
        if not app or not hasattr(app, 'input_ctrl'):
            return
        app.input_ctrl.set_active_canvas(self._scroller.canvas)

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
        for key, btn in self._nav_buttons.items():
            try:
                btn.configure(bootstyle='primary' if key == section_id else 'secondary')
            except Exception:
                pass

    def _build_all_panels(self):
        saved = self._saved
        if TTKBOOTSTRAP_AVAILABLE:
            self._build_theme_panel()
        self._build_font_panel()
        self._build_banner_panel()
        self._build_quick_access_panel()
        self._build_columns_panel()
        self._build_rows_panel()
        self._build_units_panel()
        self._build_schedules_panel()
        self._build_med_types_panel()

    # ── Theme ─────────────────────────────────────────────────────────────

    def _build_theme_panel(self):
        frame = self._panel('theme')
        current = load_theme()
        theme_display = [f"{label}  ({key})" for key, label in AVAILABLE_THEMES.items()]
        current_display = f"{AVAILABLE_THEMES.get(current, current)}  ({current})"

        tf = ttk.LabelFrame(frame, text="Application Theme")
        tf.pack(fill=tk.X, padx=12, pady=12)

        ttk.Label(
            tf,
            text="Choose a color theme for the app. Applying a theme restarts immediately.",
            justify=tk.LEFT, wraplength=520,
        ).pack(padx=12, pady=(10, 8), anchor='w')

        tr = ttk.Frame(tf)
        tr.pack(fill=tk.X, padx=12, pady=8)
        ttk.Label(tr, text="Theme:",
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).pack(side=tk.LEFT, padx=(0, 8))
        self._theme_var = tk.StringVar(value=current_display)
        ttk.Combobox(tr, textvariable=self._theme_var,
                     values=theme_display, state='readonly', width=32).pack(side=tk.LEFT, padx=(0, 10))
        try:
            ttk.Button(tr, text="Apply Theme",
                       command=self._apply_theme_from_combo, bootstyle='primary').pack(side=tk.LEFT)
        except Exception:
            ttk.Button(tr, text="Apply Theme", command=self._apply_theme_from_combo).pack(side=tk.LEFT)

        ttk.Label(
            tf,
            text="Active: " + AVAILABLE_THEMES.get(current, current) + f"  ({current})",
            font=(FONT_FAMILY, FONT_SIZE_LABELS),
        ).pack(padx=12, pady=(0, 12), anchor='w')

    # ── Font ──────────────────────────────────────────────────────────────

    def _build_font_panel(self):
        frame = self._panel('font')
        saved_font = self._saved_font

        ff = ttk.LabelFrame(frame, text="Font Size")
        ff.pack(fill=tk.X, padx=12, pady=12)
        ttk.Label(
            ff,
            text="Adjust the base font size used across the app. Takes effect after restart.",
            justify=tk.LEFT, wraplength=520,
        ).pack(padx=12, pady=(10, 8), anchor='w')

        sr = ttk.Frame(ff)
        sr.pack(padx=12, pady=8)
        self.font_size_var = tk.IntVar(value=saved_font)
        ttk.Label(sr, text="Base Font Size:").pack(side=tk.LEFT, padx=5)
        ttk.Button(sr, text="\u2212", width=3,
                   command=lambda: self._adjust_font(-1)).pack(side=tk.LEFT, padx=2)
        self.font_spin = ttk.Spinbox(sr, from_=7, to=20,
                                     textvariable=self.font_size_var, width=5, state='readonly')
        self.font_spin.pack(side=tk.LEFT, padx=4)
        ttk.Button(sr, text="+", width=3,
                   command=lambda: self._adjust_font(1)).pack(side=tk.LEFT, padx=2)
        ttk.Label(sr, text="(7 = smallest, 20 = largest)").pack(side=tk.LEFT, padx=10)

        self.font_preview_var = tk.StringVar(value=f"Preview: Aa Bb Cc 123 — size {saved_font}")
        self.font_preview_lbl = ttk.Label(ff, textvariable=self.font_preview_var,
                                          font=('Segoe UI', saved_font))
        self.font_preview_lbl.pack(padx=12, pady=(4, 12), anchor='w')

    # ── Banner ────────────────────────────────────────────────────────────

    def _build_banner_panel(self):
        frame = self._panel('banner')
        saved = self._saved

        bf = ttk.LabelFrame(frame, text="Home Page Banner")
        bf.pack(fill=tk.X, padx=12, pady=12)
        ttk.Label(
            bf,
            text="Set banner width and choose a custom shop image for the home screen.",
            justify=tk.LEFT, wraplength=520,
        ).pack(padx=12, pady=(10, 8), anchor='w')

        size_row = ttk.Frame(bf)
        size_row.pack(fill=tk.X, padx=12, pady=6)
        self._banner_size_var = tk.IntVar(
            value=int(saved.get('home_banner_size', _BANNER_DEFAULTS['home_banner_size']))
        )
        ttk.Label(size_row, text="Banner size (px):").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Spinbox(
            size_row, from_=300, to=4000, textvariable=self._banner_size_var,
            width=7, state='readonly',
        ).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(size_row, text="Width only; height keeps image aspect ratio",
                  foreground='gray').pack(side=tk.LEFT)

        default_row = ttk.Frame(bf)
        default_row.pack(fill=tk.X, padx=12, pady=6)
        self._banner_default_var = tk.BooleanVar(
            value=bool(saved.get('home_banner_use_default', False))
        )
        self._banner_default_cb = ttk.Checkbutton(
            default_row,
            text="Use default banner (disables custom banner selection)",
            variable=self._banner_default_var,
            command=self._toggle_banner_controls,
        )
        self._banner_default_cb.pack(side=tk.LEFT)

        path_row = ttk.Frame(bf)
        path_row.pack(fill=tk.X, padx=12, pady=6)
        ttk.Label(path_row, text="Custom banner:").pack(side=tk.LEFT, padx=(0, 6))
        self._banner_path_var = tk.StringVar(value=saved.get('home_banner_path', '') or '')
        self._banner_path_entry = ttk.Entry(path_row, textvariable=self._banner_path_var, state='readonly')
        self._banner_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self._banner_browse_btn = ttk.Button(path_row, text="Browse...", command=self._browse_home_banner)
        self._banner_browse_btn.pack(side=tk.LEFT, padx=2)
        self._banner_reset_btn = ttk.Button(path_row, text="Clear Custom", command=self._reset_home_banner)
        self._banner_reset_btn.pack(side=tk.LEFT, padx=2)
        self._toggle_banner_controls()

        ttk.Label(
            bf,
            text="Default image: assets/home_banner.png",
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
            foreground='gray',
        ).pack(padx=12, pady=(0, 12), anchor='w')

    # ── Quick access ──────────────────────────────────────────────────────

    def _build_quick_access_panel(self):
        frame = self._panel('quick_access')
        saved = self._saved

        qf = ttk.LabelFrame(frame, text="Home Screen — Quick Actions")
        qf.pack(fill=tk.X, padx=12, pady=12)
        ttk.Label(
            qf,
            text="Choose which buttons appear in the Quick Actions panel on the home page.",
            justify=tk.LEFT, wraplength=520,
        ).pack(padx=12, pady=(10, 8), anchor='w')

        qa_saved = saved.get('quick_access') or {}
        self._qa_vars = {}
        qg = ttk.Frame(qf)
        qg.pack(fill=tk.X, padx=12, pady=(0, 12))
        for i, (key, label) in enumerate(QUICK_ACCESS_BUTTONS):
            var = tk.BooleanVar(value=qa_saved.get(key, True))
            self._qa_vars[key] = var
            ttk.Checkbutton(qg, text=label, variable=var).grid(
                row=i // 2, column=i % 2, sticky=tk.W, padx=12, pady=4)

    # ── Column visibility ─────────────────────────────────────────────────

    def _build_columns_panel(self):
        frame = self._panel('columns')
        saved = self._saved

        hdr = ttk.Label(
            frame,
            text="On-screen columns control tables in the app. Export columns apply to each export "
                 "menu option (Sales Register, Near Expiry, Due reports, etc.). "
                 "Current View export always uses visible on-screen columns and filtered rows only.",
            justify=tk.LEFT, wraplength=620,
        )
        hdr.pack(padx=12, pady=(8, 4), anchor='w')

        col_saved = saved.get('column_visibility') or {}
        export_saved_raw = saved.get('export_column_visibility') or {}
        export_defaults = default_export_column_visibility()
        defaults = default_column_visibility()
        self._col_vars = {}
        self._export_report_vars = {}

        screen_lf = ttk.LabelFrame(frame, text="On-screen table columns")
        screen_lf.pack(fill=tk.X, padx=8, pady=6)
        grid = ttk.Frame(screen_lf)
        grid.pack(fill=tk.X, padx=4, pady=4)
        for pi, (page_key, cols) in enumerate(TABLE_COLUMNS.items()):
            pf = ttk.LabelFrame(grid, text=PAGE_LABELS.get(page_key, page_key))
            pf.grid(row=pi // 2, column=pi % 2, sticky=tk.NW, padx=6, pady=6)
            self._col_vars[page_key] = {}
            page_saved = col_saved.get(page_key, {})
            for ci, (col_name, db_field) in enumerate(cols):
                label = col_name if not db_field else f"{col_name}  ({db_field})"
                def_on = defaults[page_key].get(col_name, True)
                var = tk.BooleanVar(value=page_saved.get(col_name, def_on))
                self._col_vars[page_key][col_name] = var
                ttk.Checkbutton(pf, text=label, variable=var).grid(
                    row=ci, column=0, sticky=tk.W, padx=6, pady=2)

        export_lf = ttk.LabelFrame(frame, text="Export report columns (per export button)")
        export_lf.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        pick = ttk.Frame(export_lf)
        pick.pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(pick, text="Page:").pack(side=tk.LEFT, padx=(0, 4))
        self._export_page_var = tk.StringVar()
        page_keys = list(EXPORT_REPORTS.keys())
        page_labels = [EXPORT_PAGE_LABELS.get(k, k) for k in page_keys]
        self._export_page_keys = page_keys
        self._export_page_combo = ttk.Combobox(
            pick, width=36, state='readonly', textvariable=self._export_page_var,
            values=page_labels)
        self._export_page_combo.pack(side=tk.LEFT, padx=4)
        if page_labels:
            self._export_page_var.set(page_labels[0])

        ttk.Label(pick, text="Report:").pack(side=tk.LEFT, padx=(12, 4))
        self._export_report_var = tk.StringVar()
        self._export_report_combo = ttk.Combobox(
            pick, width=32, state='readonly', textvariable=self._export_report_var)
        self._export_report_combo.pack(side=tk.LEFT, padx=4)

        self._export_report_holder = ttk.Frame(export_lf)
        self._export_report_holder.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        for page_key in EXPORT_REPORTS:
            normalized = _normalize_page_export_saved(
                page_key, export_saved_raw.get(page_key, {}))
            self._export_report_vars[page_key] = {}
            for report_key, (_rlabel, columns) in EXPORT_REPORTS[page_key].items():
                rep_saved = normalized.get(report_key, {})
                self._export_report_vars[page_key][report_key] = {
                    col: tk.BooleanVar(
                        value=rep_saved.get(col, export_defaults[page_key][report_key].get(col, True))
                    )
                    for col in columns
                }

        def _on_export_page_change(_event=None):
            label = self._export_page_var.get()
            try:
                idx = page_labels.index(label)
            except ValueError:
                idx = 0
            page_key = page_keys[idx]
            reports = EXPORT_REPORTS[page_key]
            rlabels = [reports[rk][0] for rk in reports]
            self._export_report_combo.configure(values=rlabels)
            if rlabels:
                self._export_report_var.set(rlabels[0])
            self._export_report_keys = list(reports.keys())
            self._export_active_page = page_key
            _refresh_export_report_checks()

        def _on_export_report_change(_event=None):
            _refresh_export_report_checks()

        def _refresh_export_report_checks():
            for w in self._export_report_holder.winfo_children():
                w.destroy()
            page_key = getattr(self, '_export_active_page', page_keys[0] if page_keys else '')
            rlabel = self._export_report_var.get()
            report_key = None
            for rk, (lab, _cols) in EXPORT_REPORTS.get(page_key, {}).items():
                if lab == rlabel:
                    report_key = rk
                    break
            if not report_key:
                return
            vars_map = self._export_report_vars[page_key][report_key]
            canvas = tk.Canvas(self._export_report_holder, highlightthickness=0, height=220)
            scroll = ttk.Scrollbar(self._export_report_holder, orient=tk.VERTICAL, command=canvas.yview)
            inner = ttk.Frame(canvas)
            inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.create_window((0, 0), window=inner, anchor=tk.NW)
            canvas.configure(yscrollcommand=scroll.set)
            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scroll.pack(side=tk.RIGHT, fill=tk.Y)
            for col, var in vars_map.items():
                ttk.Checkbutton(inner, text=col, variable=var).pack(anchor=tk.W, padx=8, pady=2)

        self._export_page_combo.bind('<<ComboboxSelected>>', _on_export_page_change)
        self._export_report_combo.bind('<<ComboboxSelected>>', _on_export_report_change)
        _on_export_page_change()

    # ── Row counts ────────────────────────────────────────────────────────

    def _build_rows_panel(self):
        frame = self._panel('rows')
        saved = self._saved

        rf = ttk.LabelFrame(frame, text="Visible Row Count per Page")
        rf.pack(fill=tk.X, padx=12, pady=12)
        ttk.Label(
            rf,
            text="How many rows are visible in each page list (table height).",
            justify=tk.LEFT, wraplength=520,
        ).pack(padx=12, pady=(10, 8), anchor='w')

        self._row_vars = {}
        pages = [
            ('billing_rows',          'Billing — Selected Medicines',     4, 30),
            ('inventory_rows',        'Inventory — Medicine List',         5, 50),
            ('sales_history_rows',    'Sales History — Bills List',        5, 50),
            ('purchase_history_rows', 'Purchase History — Purchases List', 5, 50),
            ('purchase_rows',         'Purchase — Items List',             2, 20),
            ('customers_rows',        'Contacts — Customers List',         5, 50),
            ('doctors_rows',          'Settings — Doctors List',           2, 20),
            ('suppliers_rows',        'Settings — Suppliers List',         2, 20),
        ]
        rg = ttk.Frame(rf)
        rg.pack(padx=12, pady=(0, 12))
        for i, (key, label, mn, mx) in enumerate(pages):
            ttk.Label(rg, text=label + ":").grid(row=i, column=0, sticky=tk.W, padx=5, pady=4)
            var = tk.IntVar(value=saved.get(key, _DEFAULTS.get(key, 8)))
            self._row_vars[key] = var
            ttk.Spinbox(rg, from_=mn, to=mx, textvariable=var, width=5, state='readonly').grid(
                row=i, column=1, padx=8, pady=4)
            ttk.Label(rg, text=f"rows  (default: {_DEFAULTS.get(key, '?')})").grid(
                row=i, column=2, sticky=tk.W, padx=4)

    # ── Units ─────────────────────────────────────────────────────────────

    def _build_units_panel(self):
        frame = self._panel('units')
        saved = self._saved

        uf = ttk.LabelFrame(frame, text="Unit / Measure per Medicine Type")
        uf.pack(fill=tk.X, padx=12, pady=12)
        ttk.Label(
            uf,
            text=(
                "Unit codes:  d = strips × tablets/strip  |  g = grams  |  ml = millilitres.  "
                "Default Qty pre-fills Purchase entry."
            ),
            justify=tk.LEFT, wraplength=600,
        ).pack(padx=12, pady=(10, 8), anchor='w')

        self._unit_vars = {}
        self._typeqty_vars = {}
        ug = ttk.Frame(uf)
        ug.pack(fill=tk.X, padx=12, pady=(0, 12))
        ttk.Label(ug, text="Type", width=18, anchor='w',
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(row=0, column=0, padx=5, pady=2)
        ttk.Label(ug, text="Unit / Measure", width=14, anchor='w',
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(row=0, column=1, padx=8, pady=2)
        ttk.Label(ug, text="Default Qty (0=empty)", width=20, anchor='w',
                  font=(FONT_FAMILY, FONT_SIZE_LABELS, 'bold')).grid(row=0, column=2, padx=8, pady=2)

        unit_med_types = saved.get('med_types', list(_SCHEDULE_UNIT_DEFAULTS.keys()))
        for i, mt in enumerate(unit_med_types):
            r = i + 1
            ttk.Label(ug, text=f"{mt}:", width=18, anchor='w').grid(
                row=r, column=0, sticky=tk.W, padx=5, pady=3)
            uvar = tk.StringVar(value=saved.get(f'unit_{mt}', _SCHEDULE_UNIT_DEFAULTS.get(mt, '')))
            self._unit_vars[mt] = uvar
            ttk.Entry(ug, textvariable=uvar, width=14).grid(
                row=r, column=1, padx=8, pady=3, sticky=tk.W)
            raw = saved.get(f'typeqty_{mt}', _TYPE_QTY_DEFAULTS.get(mt, 0))
            qvar = tk.StringVar(value=str(raw) if raw != 0 else '0')
            self._typeqty_vars[mt] = qvar
            ttk.Entry(ug, textvariable=qvar, width=10).grid(
                row=r, column=2, padx=8, pady=3, sticky=tk.W)

    # ── Schedules ─────────────────────────────────────────────────────────

    def _build_schedules_panel(self):
        frame = self._panel('schedules')
        saved = self._saved

        sf = ttk.LabelFrame(frame, text="Schedule List")
        sf.pack(fill=tk.X, padx=12, pady=12)
        ttk.Label(
            sf,
            text="Schedules used on medicines and in sales filters (H, H1, X, …).",
            justify=tk.LEFT, wraplength=520,
        ).pack(padx=12, pady=(10, 8), anchor='w')

        sb_body = ttk.Frame(sf)
        sb_body.pack(fill=tk.X, padx=12, pady=4)
        self._sch_listbox = tk.Listbox(sb_body, height=12, selectmode=tk.SINGLE, exportselection=False)
        sch_sb = ttk.Scrollbar(sb_body, orient=tk.VERTICAL, command=self._sch_listbox.yview)
        self._sch_listbox.configure(yscrollcommand=sch_sb.set)
        self._sch_listbox.pack(side=tk.LEFT, fill=tk.Y)
        sch_sb.pack(side=tk.RIGHT, fill=tk.Y)
        for s in saved.get('schedules', list(_DEFAULT_SCHEDULES)):
            self._sch_listbox.insert(tk.END, s if s else '(blank)')

        sc = ttk.Frame(sf)
        sc.pack(fill=tk.X, padx=12, pady=(8, 12))
        self._sch_new = ttk.Entry(sc, width=12)
        self._sch_new.pack(side=tk.LEFT, padx=(0, 4))
        self._sch_new.bind('<Return>', lambda e: self._sch_add())
        ttk.Button(sc, text="Add",
                   command=lambda: [self._sch_add(), self._save(silent=True)]).pack(side=tk.LEFT, padx=2)
        ttk.Button(sc, text="Remove Selected",
                   command=lambda: [
                       self._sch_listbox.delete(self._sch_listbox.curselection()[0])
                       if self._sch_listbox.curselection() else None,
                       self._save(silent=True),
                   ]).pack(side=tk.LEFT, padx=2)

    # ── Medicine types ────────────────────────────────────────────────────

    def _build_med_types_panel(self):
        frame = self._panel('med_types')
        saved = self._saved

        tf2 = ttk.LabelFrame(frame, text="Medicine Type List")
        tf2.pack(fill=tk.X, padx=12, pady=12)
        ttk.Label(
            tf2,
            text="Types shown in billing, purchase, and inventory dropdowns.",
            justify=tk.LEFT, wraplength=520,
        ).pack(padx=12, pady=(10, 8), anchor='w')

        tb_body = ttk.Frame(tf2)
        tb_body.pack(fill=tk.X, padx=12, pady=4)
        self._typ_listbox = tk.Listbox(tb_body, height=14, selectmode=tk.SINGLE, exportselection=False)
        typ_sb = ttk.Scrollbar(tb_body, orient=tk.VERTICAL, command=self._typ_listbox.yview)
        self._typ_listbox.configure(yscrollcommand=typ_sb.set)
        self._typ_listbox.pack(side=tk.LEFT, fill=tk.Y)
        typ_sb.pack(side=tk.RIGHT, fill=tk.Y)
        for t in saved.get('med_types', list(_DEFAULT_MED_TYPES)):
            self._typ_listbox.insert(tk.END, t)

        tc = ttk.Frame(tf2)
        tc.pack(fill=tk.X, padx=12, pady=(8, 12))
        self._typ_new = ttk.Entry(tc, width=22)
        self._typ_new.pack(side=tk.LEFT, padx=(0, 4))
        self._typ_new.bind('<Return>', lambda e: self._typ_add())
        ttk.Button(tc, text="Add",
                   command=lambda: [self._typ_add(), self._save(silent=True)]).pack(side=tk.LEFT, padx=2)
        ttk.Button(tc, text="Remove Selected",
                   command=lambda: [
                       self._typ_listbox.delete(self._typ_listbox.curselection()[0])
                       if self._typ_listbox.curselection() else None,
                       self._save(silent=True),
                   ]).pack(side=tk.LEFT, padx=2)

    # ── Actions ───────────────────────────────────────────────────────────

    def _adjust_font(self, delta):
        new_val = max(7, min(20, self.font_size_var.get() + delta))
        self.font_size_var.set(new_val)
        self.font_preview_var.set(f"Preview: Aa Bb Cc 123 — size {new_val}")
        try:
            self.font_preview_lbl.configure(font=('Segoe UI', new_val))
        except Exception:
            pass

    def _apply_theme_from_combo(self):
        val = self._theme_var.get()
        try:
            key = val.split('(')[-1].rstrip(')')
            self._change_theme(key)
        except Exception as e:
            print(f"Theme apply error: {e}")

    def _change_theme(self, theme_name):
        if TTKBOOTSTRAP_AVAILABLE:
            try:
                save_theme(theme_name)
                showinfo("Theme Changed", "Application will restart with the new theme.")
                _restart_app(self._root)
            except Exception as e:
                print(f"Error changing theme: {e}")

    def _sch_add(self):
        val = self._sch_new.get().strip()
        if val and val not in self._sch_listbox.get(0, tk.END):
            self._sch_listbox.insert(tk.END, val)
            self._sch_new.delete(0, tk.END)

    def _typ_add(self):
        val = self._typ_new.get().strip()
        if val and val not in self._typ_listbox.get(0, tk.END):
            self._typ_listbox.insert(tk.END, val)
            self._typ_new.delete(0, tk.END)

    def _browse_home_banner(self):
        path = filedialog.askopenfilename(
            title="Select Home Page Banner",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.gif *.webp *.bmp"),
                ("PNG files", "*.png"),
                ("JPEG files", "*.jpg *.jpeg"),
                ("All files", "*.*"),
            ],
            parent=self._root,
        )
        if not path:
            return
        try:
            saved_path = copy_custom_home_banner(path)
            self._banner_path_var.set(saved_path)
            showinfo(
                "Banner Selected",
                "Custom banner saved.\nUse Save Appearance & Restart to apply on the home page.",
                parent=self._root,
            )
        except Exception as exc:
            showerror("Banner Error", f"Could not save banner image:\n{exc}", parent=self._root)

    def _toggle_banner_controls(self):
        use_default = self._banner_default_var.get()
        state = 'disabled' if use_default else 'normal'
        try:
            self._banner_browse_btn.configure(state=state)
            self._banner_reset_btn.configure(state=state)
        except Exception:
            pass

    def _reset_home_banner(self):
        self._banner_path_var.set('')
        showinfo(
            "Default Banner",
            "Default banner will be used after Save Appearance & Restart.",
            parent=self._root,
        )

    def _save(self, silent=False):
        size = self.font_size_var.get()
        try:
            from core.font_config import _get_font_size_path
            with open(_get_font_size_path(), 'w') as f:
                f.write(str(size))
        except Exception as e:
            showerror("Error", f"Could not save font size: {e}")
            return
        try:
            data = {k: v.get() for k, v in self._row_vars.items()}
            for mt, var in self._unit_vars.items():
                data[f'unit_{mt}'] = var.get()
            for mt, var in self._typeqty_vars.items():
                raw = var.get().strip()
                try:
                    data[f'typeqty_{mt}'] = int(raw)
                except ValueError:
                    data[f'typeqty_{mt}'] = raw if raw else 0
            raw_sch = list(self._sch_listbox.get(0, tk.END))
            data['schedules'] = ['' if s == '(blank)' else s for s in raw_sch]
            new_types = list(self._typ_listbox.get(0, tk.END))
            data['med_types'] = new_types
            data['home_banner_size'] = int(self._banner_size_var.get())
            data['home_banner_use_default'] = bool(self._banner_default_var.get())
            data['home_banner_path'] = '' if self._banner_default_var.get() else self._banner_path_var.get().strip()
            data['quick_access'] = {k: v.get() for k, v in self._qa_vars.items()}
            col_vis = {}
            for page_key, cols in self._col_vars.items():
                page_vis = {col: var.get() for col, var in cols.items()}
                if not any(page_vis.values()):
                    showwarning(
                        "Column Visibility",
                        f"At least one on-screen column must stay visible for "
                        f"{PAGE_LABELS.get(page_key, page_key)}.",
                        parent=self._root,
                    )
                    return
                col_vis[page_key] = page_vis
            data['column_visibility'] = col_vis
            export_vis = {}
            for page_key, reports in self._export_report_vars.items():
                export_vis[page_key] = {}
                for report_key, cols in reports.items():
                    page_ex = {col: var.get() for col, var in cols.items()}
                    if page_ex and not any(page_ex.values()):
                        rlabel = EXPORT_REPORTS[page_key][report_key][0]
                        showwarning(
                            "Export Columns",
                            f"At least one export column must stay enabled for "
                            f"{rlabel} ({EXPORT_PAGE_LABELS.get(page_key, page_key)}).",
                            parent=self._root,
                        )
                        return
                    export_vis[page_key][report_key] = page_ex
            data['export_column_visibility'] = export_vis
            for t in new_types:
                if f'unit_{t}' not in data:
                    data[f'unit_{t}'] = _SCHEDULE_UNIT_DEFAULTS.get(t, '')
                if f'typeqty_{t}' not in data:
                    data[f'typeqty_{t}'] = _TYPE_QTY_DEFAULTS.get(t, 0)
            save_layout(data)
        except Exception as e:
            showerror("Error", f"Could not save layout: {e}")
            return
        if not silent:
            showinfo("Appearance Saved", "Appearance settings saved. The application will now restart.")
            _restart_app(self._root)
