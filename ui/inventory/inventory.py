import tkinter as tk
from tkinter import messagebox
from datetime import datetime, timedelta
import sqlite3

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.alert_colors import get_alert_color
from core.font_config import *
from core.layout_config import (
    INVENTORY_ROWS, load_layout, _DEFAULT_SCHEDULES, get_med_types,
    is_strip_count_type, parse_tablets_per_stripe,
)
from core.column_config import apply_column_visibility, is_dashboard_section_visible
from core.stock_utils import stock_value_at_mrp, sum_inventory_mrp_value
from core.export_manager import export_data
from core.column_config import export_table
from widgets.searchable_combo import SearchableCombo
from ui.inventory.inventory_dialogs import (
    open_edit_dialog, open_view_dialog, delete_medicine, delete_zero_stock_medicines,
    delete_expired_medicines,
)
from core.record_indicators import (
    extend_columns,
    indicator_column_widths,
    column_heading,
    inventory_status,
    prepare_tree_row,
    register_tree_tags,
)
from core.batch_visibility import (
    compute_name_stock_totals,
    compute_oos_anchor_ids,
    should_hide_depleted_batch,
)


class InventoryPage:

    def __init__(self, parent, conn):
        self.conn   = conn
        self.cursor = conn.cursor()
        self.parent = parent
        self.medicines_data = []
        self._name_stock_totals = {}
        self._pack_stock_totals = {}
        self._oos_pack_count = 0
        self.show_location  = False
        self._inventory_loading = False
        self._filter_pending = None
        self._filter_gen = 0
        self._tree_populating = False
        self._pending_sync_refresh = False
        self._tree_populate_seq = 0

        self._build_ui()
        top = self.parent.winfo_toplevel()
        if not getattr(top, '_startup_prewarm', False):
            self.parent.after(0, self._schedule_load_inventory)
        if not getattr(top, '_startup_prewarm', False):
            self.parent.after(100, self._setup_arrow_nav)
        if not getattr(top, '_startup_prewarm', False):
            self.parent.after(200, self.search_entry.focus)
        self._register_keyboard()
        self._live_token = None
        try:
            from core.store_live_refresh import subscribe as live_sub

            def _on_live(_evt):
                try:
                    self.parent.after(300, self.queue_sync_refresh)
                except Exception:
                    pass

            self._live_token = live_sub(
                {"medicines", "stock_operations"},
                _on_live,
            )
        except Exception:
            pass

    def _clear_filters(self):
        for combo in (self.search_entry, self.type_filter, self.stock_filter,
                      self.expiry_filter, self.schedule_filter):
            try:
                combo.hide_list()
                combo.set('')
            except Exception:
                pass
        self.load_inventory()

    def _clear_filters_shortcut(self, event=None):
        self._clear_filters()
        return 'break'

    def _register_keyboard(self):
        from core.keyboard_registry import KeyboardRegistry, PageBindings
        bindings = PageBindings(
            page_id='inventory',
            first_focus=lambda: self.search_entry.entry.focus_set(),
            on_ctrl_f=lambda: self.search_entry.entry.focus_set(),
            on_ctrl_enter=self.filter_inventory,
            on_ctrl_shift_c=self._clear_filters_shortcut,
            on_ctrl_e=self._export_current_view,
            f2_target=self.inventory_tree,
        )
        self._inner_frame._keyboard_bindings = bindings
        KeyboardRegistry.register_page(self._inner_frame, bindings)

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Fixed layout: filters + pinned summary stay visible; only the tree scrolls.
        main_frame = ttk.Frame(self.parent)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self._inner_frame = main_frame
        try:
            main_frame.configure(padding=(10, 10))
        except Exception:
            pass

        layout = load_layout()
        med_types = get_med_types()
        schedules = [s for s in layout.get('schedules', list(_DEFAULT_SCHEDULES)) if s]

        # Filter
        ff = ttk.LabelFrame(main_frame, text="Search & Filter")
        ff.pack(fill=tk.X, pady=5)

        ttk.Label(ff, text="Search:").grid(row=0, column=0, padx=5, pady=5)
        self.search_entry = SearchableCombo(ff, width=30)
        self.search_entry.grid(row=0, column=1, padx=5, pady=5)
        self.search_entry.bind('<<ComboboxSelected>>', self.filter_inventory)
        self.search_entry.entry.bind('<KeyRelease>', self.filter_inventory)
        self.search_entry.bind_apply_on_select(self.filter_inventory)
        self.search_entry.entry.bind('<FocusIn>', lambda e: self._load_names(), add='+')
        self.parent.after(0, self._load_names)

        ttk.Label(ff, text="Type:").grid(row=0, column=2, padx=5, pady=5)
        self.type_filter = SearchableCombo(ff, values=med_types, width=18)
        self.type_filter.grid(row=0, column=3, padx=5, pady=5)
        self.type_filter.bind_apply_on_select(self.filter_inventory)

        ttk.Label(ff, text="Stock Status:").grid(row=0, column=4, padx=5, pady=5)
        self.stock_filter = SearchableCombo(ff, values=['In Stock','Low Stock','Out of Stock'], width=14)
        self.stock_filter.grid(row=0, column=5, padx=5, pady=5)
        self.stock_filter.bind_apply_on_select(self.filter_inventory)

        ttk.Label(ff, text="Expiry Status:").grid(row=1, column=0, padx=5, pady=5)
        self.expiry_filter = SearchableCombo(ff, values=['Near Expiry','Expired'], width=14)
        self.expiry_filter.grid(row=1, column=1, padx=5, pady=5)
        self.expiry_filter.bind_apply_on_select(self.filter_inventory)

        ttk.Label(ff, text="Schedule:").grid(row=1, column=2, padx=5, pady=5)
        self.schedule_filter = SearchableCombo(
            ff, values=schedules + ['Non-Scheduled'], width=14)
        self.schedule_filter.grid(row=1, column=3, padx=5, pady=5)
        self.schedule_filter.bind_apply_on_select(self.filter_inventory)

        self.refresh_btn = ttk.Button(ff, text="Refresh", command=self.load_inventory)
        self.refresh_btn.grid(row=1, column=4, padx=10, pady=5)
        try:
            self.export_btn = ttk.Button(ff, text="📤 Export", command=self._export_menu,
                                         bootstyle="info")
        except Exception:
            self.export_btn = ttk.Button(ff, text="📤 Export", command=self._export_menu)
        self.export_btn.grid(row=1, column=5, padx=10, pady=5)
        try:
            self.remove_zero_btn = ttk.Button(
                ff, text="Remove Out of Stock",
                command=self._remove_zero_stock,
                bootstyle="danger-outline",
            )
        except Exception:
            self.remove_zero_btn = ttk.Button(
                ff, text="Remove Out of Stock", command=self._remove_zero_stock,
            )
        self.remove_zero_btn.grid(row=1, column=6, padx=10, pady=5)
        try:
            self.remove_expired_btn = ttk.Button(
                ff, text="Remove Expired",
                command=self._remove_expired,
                bootstyle="warning-outline",
            )
        except Exception:
            self.remove_expired_btn = ttk.Button(
                ff, text="Remove Expired", command=self._remove_expired,
            )
        self.remove_expired_btn.grid(row=1, column=7, padx=10, pady=5)

        self._load_status_var = tk.StringVar(value="")
        ttk.Label(
            ff, textvariable=self._load_status_var,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).grid(row=2, column=0, columnspan=8, sticky=tk.W, padx=5, pady=(0, 4))

        # Tree
        tf = ttk.Frame(main_frame)
        tf.pack(fill=tk.BOTH, expand=True, pady=5)
        self._tree_frame = tf

        try:
            self.cursor.execute("SELECT show_location FROM shelf_settings LIMIT 1")
            r = self.cursor.fetchone()
            self.show_location = bool(r[0]) if r else False
        except Exception:
            self.show_location = False

        self._build_tree(tf)
        self._wire_tree_keyboard()

        from core.focus_chain import wire_combo_filter_chain
        wire_combo_filter_chain(
            self.search_entry, self.type_filter, self.stock_filter,
            self.expiry_filter, self.schedule_filter,
            on_last_return=self.filter_inventory,
        )

        # Summary
        self._show_summary = is_dashboard_section_visible('inventory_summary')
        self.total_medicines_var = tk.StringVar()
        self.low_stock_var       = tk.StringVar()
        self.out_of_stock_var    = tk.StringVar()
        self.near_expiry_var     = tk.StringVar()
        self.expired_var         = tk.StringVar()
        self.total_value_var     = tk.StringVar()
        if self._show_summary:
            sf = ttk.LabelFrame(main_frame, text="Inventory Summary")
            sf.pack(fill=tk.X, pady=5)
            for col, (lbl, var, color) in enumerate([
                ('Total Medicines:', self.total_medicines_var, None),
                ('Low Stock:',       self.low_stock_var,       'warning'),
                ('Out of Stock:',    self.out_of_stock_var,    'danger'),
                ('Near Expiry:',     self.near_expiry_var,     'warning'),
                ('Expired:',         self.expired_var,         'danger'),
                ('Total Value:',     self.total_value_var,     'success'),
            ]):
                ttk.Label(sf, text=lbl).grid(row=0, column=col*2, padx=8, pady=5)
                kw = {'font': (FONT_FAMILY, FONT_SIZE_LABELS, 'bold')}
                if color:
                    kw['foreground'] = get_alert_color(color)
                ttk.Label(sf, textvariable=var, **kw).grid(row=0, column=col*2+1, padx=8, pady=5)

            # Keep summary on-screen: pin to bottom; tree takes remaining space.
            ff.pack_forget()
            tf.pack_forget()
            sf.pack_forget()
            sf.pack(side=tk.BOTTOM, fill=tk.X, pady=5)
            ff.pack(side=tk.TOP, fill=tk.X, pady=5)
            tf.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=5)

    def _build_tree(self, tf):
        all_cols = ('Name','Type','Batch','Expiry','Days Left','Stock','Unit','MRP','MRP/Tab','Rate','Rate/Tab','Manufacturer','Supplier Name','Schedule','Location')
        if self.show_location:
            base_cols = all_cols
        else:
            base_cols = tuple(c for c in all_cols if c != 'Location')
        cols = extend_columns(base_cols)
        widths = {'Name':260,'Type':75,'Batch':90,'Expiry':75,'Days Left':70,
                  'Stock':65,'Unit':55,'MRP':60,'MRP/Tab':60,'Rate':60,'Rate/Tab':60,'Manufacturer':110,'Supplier Name':150,'Schedule':75,'Location':90}
        widths.update(indicator_column_widths())

        self.inventory_tree = ttk.Treeview(tf, columns=cols, show='headings',
                                           height=INVENTORY_ROWS, style='Large.Treeview')
        for col in cols:
            self.inventory_tree.heading(col, text=column_heading(col))
            # Same as Sales/Purchase History: do not pin stretch on one column.
            # Windows ttk.Treeview blocks header drag-reorder when only Name stretches.
            self.inventory_tree.column(col, width=widths.get(col, 100), minwidth=40)
        apply_column_visibility(self.inventory_tree, 'inventory', cols)

        vsb = ttk.Scrollbar(tf, orient=tk.VERTICAL,   command=self.inventory_tree.yview)
        hsb = ttk.Scrollbar(tf, orient=tk.HORIZONTAL, command=self.inventory_tree.xview)
        self.inventory_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.inventory_tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        tf.grid_rowconfigure(0, weight=1)
        tf.grid_columnconfigure(0, weight=1)
        self._vsb = vsb; self._hsb = hsb

        register_tree_tags(self.inventory_tree)

    def _wire_tree_keyboard(self):
        from core.tree_action_menu import setup_tree_actions
        self._action_menu = setup_tree_actions(
            self.parent,
            self.inventory_tree,
            [
                ("Edit Medicine", self.edit_medicine),
                ("View Details", self.view_details),
                ("Reorder", self.reorder_selected),
                "---",
                ("Delete Medicine", self.delete_medicine),
            ],
            on_double=self.view_details,
            on_delete=lambda e: self.delete_medicine(),
            escape_to=self.search_entry.entry,
        )
        self._ctx = self._action_menu.ctx_menu

    # ── Data loading ──────────────────────────────────────────────────────

    def _load_names(self):
        self.cursor.execute("SELECT DISTINCT name FROM medicines ORDER BY name COLLATE NOCASE")
        self.search_entry.configure(values=[r[0] for r in self.cursor.fetchall()])

    def _set_inventory_loading(self, loading: bool, message: str = ''):
        self._inventory_loading = loading
        try:
            self._load_status_var.set(message)
            state = 'disabled' if loading else 'normal'
            self.refresh_btn.configure(state=state)
            self.export_btn.configure(state=state)
            self.remove_zero_btn.configure(state=state)
            if hasattr(self, 'remove_expired_btn'):
                self.remove_expired_btn.configure(state=state)
        except tk.TclError:
            pass

    def _schedule_load_inventory(self):
        if self._inventory_loading:
            return
        from core.background_workers import run_in_thread
        self._set_inventory_loading(True, 'Loading inventory…')
        run_in_thread(
            self._fetch_inventory_rows,
            name='InventoryLoad',
            root=self.parent,
            on_success=self._apply_inventory_rows,
            on_error=self._on_inventory_load_error,
        )

    def _fetch_inventory_rows(self):
        import sqlite3
        from core.background_workers import db_path_from_conn

        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.online_catalog import medicines as oc_medicines

                catalog = oc_medicines() or []
                rows = []
                for r in catalog:
                    if not isinstance(r, dict):
                        continue
                    if r.get("is_hidden"):
                        continue
                    rows.append((
                        r.get("name") or "",
                        r.get("type") or "",
                        r.get("batch_no") or "",
                        r.get("expiry_date") or "",
                        int(float(r.get("stock_qty") or 0)),
                        r.get("unit") or "",
                        float(r.get("mrp") or 0),
                        float(r.get("rate") or 0),
                        r.get("manufacturer") or "",
                        r.get("schedule") or "",
                        r.get("location") or "",
                        int(r.get("id") or r.get("local_id") or 0),
                    ))
                names = sorted({r[0] for r in rows if r[0]}, key=lambda x: x.lower())
                return True, rows, names
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("inventory server fetch: %s", exc)

        path = db_path_from_conn(self.conn)
        conn = sqlite3.connect(path, check_same_thread=False, timeout=30) if path else self.conn
        own = conn is not self.conn
        try:
            if own:
                conn.execute('PRAGMA busy_timeout=30000')
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT show_location FROM shelf_settings LIMIT 1")
                r = cursor.fetchone()
                show_location = bool(r[0]) if r else False
            except Exception:
                show_location = False

            if show_location:
                try:
                    cursor.execute("""
                        SELECT name,type,batch_no,expiry_date,stock_qty,unit,mrp,rate,
                               manufacturer,schedule,location,id FROM medicines
                        WHERE COALESCE(is_hidden,0)=0
                        ORDER BY name,batch_no
                    """)
                except Exception:
                    cursor.execute("""
                        SELECT name,type,batch_no,expiry_date,stock_qty,unit,mrp,rate,
                               manufacturer,schedule,'',id FROM medicines
                        WHERE COALESCE(is_hidden,0)=0
                        ORDER BY name,batch_no
                    """)
            else:
                cursor.execute("""
                    SELECT name,type,batch_no,expiry_date,stock_qty,unit,mrp,rate,
                           manufacturer,schedule,id FROM medicines
                    WHERE COALESCE(is_hidden,0)=0
                    ORDER BY name,batch_no
                """)
            rows = cursor.fetchall()
            cursor.execute(
                "SELECT DISTINCT name FROM medicines WHERE COALESCE(is_hidden,0)=0 ORDER BY name"
            )
            names = [r[0] for r in cursor.fetchall()]
            return show_location, rows, names
        finally:
            if own:
                try:
                    conn.close()
                except Exception:
                    pass

    def _on_inventory_load_error(self, exc):
        self._set_inventory_loading(False, f'Load failed: {exc}')

    def _pack_key(self, med) -> tuple:
        return (med[0], (med[5] or '').strip())

    def _compute_name_stock_totals(self, rows):
        totals = {}
        for med in rows:
            totals[med[0]] = totals.get(med[0], 0.0) + float(med[4] or 0)
        return totals

    def _compute_pack_stock_totals(self, rows):
        totals = {}
        for med in rows:
            key = self._pack_key(med)
            totals[key] = totals.get(key, 0.0) + float(med[4] or 0)
        return totals

    def _is_depleted_batch_row(self, med) -> bool:
        """Hide zero-stock rows when other batches still have stock."""
        return should_hide_depleted_batch(
            med,
            getattr(self, '_name_stock_totals', {}),
            getattr(self, '_oos_anchor_ids', {}),
        )

    def _apply_inventory_rows(self, payload):
        show_location, rows, names = payload
        from core.alert_monitoring_service import medicine_pack_keys_fully_out_of_stock
        self._name_stock_totals = compute_name_stock_totals(rows)
        self._pack_stock_totals = self._compute_pack_stock_totals(rows)
        self._oos_anchor_ids = compute_oos_anchor_ids(rows)
        self._oos_medicine_names = {
            name for name, total in self._name_stock_totals.items() if float(total or 0) <= 0
        }
        self._oos_pack_count = len(medicine_pack_keys_fully_out_of_stock(self.conn))
        visible_rows = [r for r in rows if not should_hide_depleted_batch(
            r, self._name_stock_totals, self._oos_anchor_ids)]
        names = sorted({r[0] for r in visible_rows}, key=lambda x: x.lower())
        if self.show_location != show_location:
            self.show_location = show_location
            self.inventory_tree.destroy()
            self._build_tree(self._tree_frame)
            self._wire_tree_keyboard()

        from core.ui_tree_loader import populate_tree_batched, restore_tree_yview, save_tree_yview

        self._tree_populate_seq = getattr(self, '_tree_populate_seq', 0) + 1
        seq = self._tree_populate_seq
        self._tree_populating = True
        yview = save_tree_yview(self.inventory_tree)

        kids = self.inventory_tree.get_children()
        if kids:
            self.inventory_tree.delete(*kids)

        self.medicines_data = rows
        try:
            from core.desktop_pages_service import latest_supplier_by_medicine_id

            self._supplier_by_med = latest_supplier_by_medicine_id(
                self.conn, [r[-1] for r in rows if r]
            )
        except Exception:
            self._supplier_by_med = {}

        def _insert_one(med):
            if self._is_depleted_batch_row(med):
                return
            stock = med[4]
            status = inventory_status(
                stock, med[1], med[3],
                is_low_stock=lambda qty, t: self._is_medicine_low_stock(
                    med[0], t, med[5] if len(med) > 5 else None,
                ),
                is_expired=self._is_expired,
                is_near_expiry=self._is_near_expiry,
            )
            raw = list(med[:-1])
            days = self._days_left(raw[3])
            days_str = f"{days}d" if days is not None else ''
            raw[3] = self._fmt_expiry(raw[3])
            unit_raw = raw[5]
            med_type = raw[1]
            mrp_s, mrp_t = self._split_price_cols(raw[6], med_type, unit_raw)
            rate_s, rate_t = self._split_price_cols(raw[7], med_type, unit_raw)
            raw[5] = self._fmt_unit(unit_raw, med_type)
            rest = list(raw[8:])
            try:
                mid = int(med[-1])
            except Exception:
                mid = 0
            supplier = str((getattr(self, '_supplier_by_med', {}) or {}).get(mid) or '')
            values = raw[:4] + [days_str] + raw[4:6] + [mrp_s, mrp_t, rate_s, rate_t] + (
                [rest[0], supplier] + rest[1:] if rest else [supplier]
            )
            if self.show_location and values:
                values[-1] = self._fmt_location(values[-1])
            row_vals, tags = prepare_tree_row(tuple(values), status)
            self.inventory_tree.insert('', tk.END, iid=str(med[-1]), values=row_vals, tags=tags)

        def _done():
            self._tree_populating = False
            restore_tree_yview(self.inventory_tree, yview)
            self.update_summary()
            try:
                self.search_entry.configure(values=names)
            except Exception:
                pass
            self._set_inventory_loading(False, f'{len(visible_rows):,} medicines loaded')
            if self._pending_sync_refresh:
                self._pending_sync_refresh = False
                try:
                    self.parent.after(700, self.queue_sync_refresh)
                except Exception:
                    pass

        populate_tree_batched(
            self.inventory_tree,
            visible_rows,
            _insert_one,
            self.parent,
            batch_size=100,
            on_done=_done,
            should_stop=lambda: seq != self._tree_populate_seq,
        )

    def load_inventory(self):
        self._pending_sync_refresh = False
        self._schedule_load_inventory()

    def queue_sync_refresh(self):
        if self._inventory_loading or self._tree_populating:
            self._pending_sync_refresh = True
            return
        self._schedule_load_inventory()

    def ensure_data_loaded(self):
        """Reload list if empty (e.g. after page was built while hidden at startup)."""
        try:
            empty = len(self.inventory_tree.get_children()) == 0
        except Exception:
            empty = True
        if not empty:
            return
        if self._inventory_loading:
            self._inventory_loading = False
        self._schedule_load_inventory()

    def _insert_row_from_med(self, med):
        tags = []
        stock = med[4]
        if stock == 0:
            tags = ['out_of_stock']
        elif self._is_medicine_low_stock(med[0], med[1], med[5] if len(med) > 5 else None):
            tags = ['low_stock']
        elif self._is_expired(med[3]):
            tags = ['expired']
        elif self._is_near_expiry(med[3], med[1]):
            tags = ['near_expiry']
        raw = list(med[:-1])
        days = self._days_left(raw[3])
        days_str = f"{days}d" if days is not None else ''
        raw[3] = self._fmt_expiry(raw[3])
        unit_raw = raw[5]
        med_type = raw[1]
        mrp_s, mrp_t = self._split_price_cols(raw[6], med_type, unit_raw)
        rate_s, rate_t = self._split_price_cols(raw[7], med_type, unit_raw)
        raw[5] = self._fmt_unit(unit_raw, med_type)
        rest = list(raw[8:])
        try:
            mid = int(med[-1])
        except Exception:
            mid = 0
        supplier = str((getattr(self, '_supplier_by_med', {}) or {}).get(mid) or '')
        values = raw[:4] + [days_str] + raw[4:6] + [mrp_s, mrp_t, rate_s, rate_t] + (
            [rest[0], supplier] + rest[1:] if rest else [supplier]
        )
        if self.show_location and values:
            values[-1] = self._fmt_location(values[-1])
        self.inventory_tree.insert('', tk.END, iid=str(med[-1]), values=values, tags=tags)

    def _insert_rows(self, data):
        for med in data:
            self._insert_row_from_med(med)

    def filter_inventory(self, event=None):
        if event and getattr(event, 'keysym', None) in (
                'Up', 'Down', 'Left', 'Right', 'Return', 'Escape', 'Tab'):
            return
        if self._filter_pending:
            try:
                self.parent.after_cancel(self._filter_pending)
            except Exception:
                pass
        self._filter_pending = self.parent.after(350, self._run_filter_inventory)

    def _filter_medicines_data(self, search, typ, stock_f, exp_f, sch_f):
        oos_names = getattr(self, '_oos_medicine_names', None)
        starts, contains = [], []
        for med in self.medicines_data:
            if self._is_depleted_batch_row(med):
                continue
            if stock_f != 'Out of Stock' and self._is_depleted_batch_row(med):
                continue
            if typ and med[1] != typ:
                continue
            if stock_f == 'In Stock' and med[4] <= 0:
                continue
            if stock_f == 'Low Stock' and not self._is_medicine_low_stock(
                    med[0], med[1], med[5] if len(med) > 5 else None):
                continue
            if stock_f == 'Out of Stock':
                if float(self._name_stock_totals.get(med[0], 0) or 0) > 0:
                    continue
                if self._oos_anchor_ids.get(med[0]) != med[-1]:
                    continue
            if exp_f == 'Near Expiry' and not self._is_near_expiry(med[3], med[1]):
                continue
            if exp_f == 'Expired' and not self._is_expired(med[3]):
                continue
            if sch_f:
                if sch_f == 'Non-Scheduled' and med[9] and med[9].strip():
                    continue
                elif sch_f != 'Non-Scheduled' and med[9] != sch_f:
                    continue
            if search:
                n = med[0].lower()
                b = (med[2] or '').lower()
                if n.startswith(search) or b.startswith(search):
                    starts.append(med)
                elif search in n or search in b:
                    contains.append(med)
            else:
                starts.append(med)
        return starts + contains

    def _run_filter_inventory(self):
        self._filter_pending = None
        search = self.search_entry.get().lower()
        typ = self.type_filter.get()
        stock_f = self.stock_filter.get()
        exp_f = self.expiry_filter.get()
        sch_f = self.schedule_filter.get()
        rows = self._filter_medicines_data(search, typ, stock_f, exp_f, sch_f)
        kids = self.inventory_tree.get_children()
        if kids:
            self.inventory_tree.delete(*kids)
        from core.ui_tree_loader import populate_tree_batched
        populate_tree_batched(
            self.inventory_tree,
            rows,
            self._insert_row_from_med,
            self.parent,
            batch_size=120,
        )

    def update_summary(self):
        if not getattr(self, '_show_summary', True):
            return
        visible = [m for m in self.medicines_data if not self._is_depleted_batch_row(m)]
        n     = len(visible)
        low_names = set()
        for m in visible:
            name = m[0]
            if name in low_names:
                continue
            if self._is_medicine_low_stock(name, m[1] or '', m[5] if len(m) > 5 else None):
                low_names.add(name)
        low = len(low_names)
        out   = len(getattr(self, '_oos_medicine_names', set()))
        near  = sum(1 for m in visible if self._is_near_expiry(m[3], m[1] or ''))
        exp   = sum(1 for m in visible if self._is_expired(m[3]))
        val   = sum(
            stock_value_at_mrp(m[4], m[6], m[1] or '', m[5])
            for m in visible if m[6]
        )
        self.total_medicines_var.set(str(n))
        self.low_stock_var.set(str(low))
        self.out_of_stock_var.set(str(out))
        self.near_expiry_var.set(str(near))
        self.expired_var.set(str(exp))
        self.total_value_var.set(f"₹{val:.2f}")

    # ── Helpers ───────────────────────────────────────────────────────────

    def _get_setting(self, name, default):
        try:
            self.cursor.execute("SELECT value FROM settings WHERE name=?", (name,))
            r = self.cursor.fetchone()
            return int(r[0]) if r else default
        except Exception:
            return default

    def _is_medicine_low_stock(self, name, med_type, unit=None):
        """True when combined stock across all batches is below threshold."""
        qty = float(self._name_stock_totals.get(name, 0) or 0)
        if qty <= 0:
            return False
        from core.alert_thresholds import is_low_stock_qty, load_thresholds
        low_thr, _ = load_thresholds(self.conn)
        return is_low_stock_qty(qty, med_type, low_thr, self.cursor, unit=unit)

    def _is_low_stock(self, qty, med_type, unit=None):
        from core.alert_thresholds import is_low_stock_qty, load_thresholds
        low_thr, _ = load_thresholds(self.conn)
        return is_low_stock_qty(qty, med_type, low_thr, self.cursor, unit=unit)

    def _is_near_expiry(self, expiry_date, med_type):
        try:
            exp = datetime.strptime(expiry_date, '%Y-%m-%d')
            months = self._get_setting(f'near_expiry_{med_type.lower()}', 3)
            return datetime.now() < exp <= datetime.now() + timedelta(days=months * 30)
        except Exception:
            return False

    def _is_expired(self, expiry_date):
        try:
            return datetime.strptime(expiry_date, '%Y-%m-%d') <= datetime.now()
        except Exception:
            return False

    def _days_left(self, expiry_date):
        try:
            return (datetime.strptime(str(expiry_date), '%Y-%m-%d') - datetime.now()).days
        except Exception:
            return None

    def _fmt_expiry(self, expiry_date):
        if not expiry_date: return ''
        try:
            parts = str(expiry_date).split('-')
            return f"{parts[1]}/{parts[0][2:]}" if len(parts) >= 2 else str(expiry_date)
        except Exception:
            return str(expiry_date)

    def _split_price_cols(self, strip_price, med_type, unit):
        """Return (strip_display, per_tablet_display) for tablet/bolus/capsule."""
        try:
            strip_val = float(strip_price or 0)
        except (TypeError, ValueError):
            strip_val = 0.0
        strip_txt = f"{strip_val:.2f}"
        try:
            from core.layout_config import is_strip_count_type, parse_tablets_per_stripe
            if not is_strip_count_type(med_type or "", unit):
                return strip_txt, ""
            tps = max(1, int(parse_tablets_per_stripe(unit)))
            tab = strip_val / tps
            if abs(tab - round(tab, 2)) < 1e-9:
                tab_txt = f"{tab:.2f}"
            else:
                tab_txt = f"{tab:.4f}".rstrip("0").rstrip(".")
            return strip_txt, tab_txt
        except Exception:
            return strip_txt, ""

    def _fmt_unit(self, unit, med_type):
        if not unit: return ''
        text = str(unit).strip()
        if any(sep in text for sep in ('*', 'x', 'X', '×')):
            return text
        if is_strip_count_type(str(med_type)):
            try: return f"{parse_tablets_per_stripe(text)}'S"
            except Exception: return text
        return text

    def _fmt_location(self, location):
        import re
        if not location: return ''
        nums = re.findall(r'\d+', location)
        if 'box' in location and len(nums) >= 3:
            return f"r{nums[0]}s{nums[1]}b{nums[2]}"
        elif len(nums) >= 2:
            return f"r{nums[0]}s{nums[1]}"
        return location

    # ── Context menu / actions ────────────────────────────────────────────

    def _show_ctx(self, event):
        row = self.inventory_tree.identify_row(event.y)
        if row:
            self.inventory_tree.selection_set(row)
            self.inventory_tree.focus(row)
        if self.inventory_tree.selection():
            self._ctx.post(event.x_root, event.y_root)

    def _selected_id(self):
        sel = self.inventory_tree.selection()
        if not sel: return None
        try: return int(sel[0])
        except (ValueError, IndexError): return None

    def _close_action_menus(self):
        try:
            menu = getattr(self, '_action_menu', None)
            if menu is not None:
                menu._close()
        except Exception:
            pass
        try:
            ctx = getattr(self, '_ctx', None)
            if ctx is not None:
                ctx.unpost()
        except Exception:
            pass

    def edit_medicine(self):
        self._close_action_menus()
        med_id = self._selected_id()
        if med_id:
            open_edit_dialog(self.parent, self.conn, med_id, self.load_inventory)

    def view_details(self, event=None):
        self._close_action_menus()
        med_id = self._selected_id()
        if med_id:
            open_view_dialog(self.parent, self.conn, med_id)

    def delete_medicine(self):
        self._close_action_menus()
        sel = self.inventory_tree.selection()
        if not sel: return
        med_id = self._selected_id()
        if not med_id: return
        values = self.inventory_tree.item(sel[0])['values']
        delete_medicine(self.conn, med_id, values[0], values[2], self.load_inventory)

    def reorder_selected(self):
        self._close_action_menus()
        sel = self.inventory_tree.selection()
        if not sel:
            return
        med_id = self._selected_id()
        if not med_id:
            return
        values = list(self.inventory_tree.item(sel[0])['values'])
        cols = list(self.inventory_tree['columns'])

        def _col(name: str, default=""):
            try:
                idx = cols.index(name)
                return values[idx] if idx < len(values) else default
            except ValueError:
                return default

        def _safe_float(value, default=0.0):
            try:
                return float(value or 0)
            except (TypeError, ValueError):
                return default

        name = _col('Name')
        pack = _col('Unit')
        med_type = _col('Type') or "Others"
        stock = _safe_float(_col('Stock', 0))
        rate = _safe_float(_col('Rate', 0))
        from core.reorder_service import min_stock_level, suggest_order_quantity
        prefill = {
            "medicine_name": name,
            "pack_size": pack,
            "med_type": med_type,
            "current_stock": stock,
            "unit_price": rate,
            "min_stock": min_stock_level(self.conn, med_type),
            "suggested_qty": suggest_order_quantity(
                self.conn, name, med_type, stock, pack),
        }
        try:
            root = self.parent.winfo_toplevel()
            app = getattr(root, "_main_app", None)
            if app and hasattr(app, "open_reorder"):
                app.open_reorder(prefill)
                app._style_nav_button("Settings")
        except Exception:
            pass

    def _remove_zero_stock(self):
        delete_zero_stock_medicines(self.conn, self.load_inventory, parent=self.parent)

    def _remove_expired(self):
        delete_expired_medicines(self.conn, self.load_inventory, parent=self.parent)

    # ── Exports ───────────────────────────────────────────────────────────

    def _export_menu(self):
        from core.export_manager import show_export_option_dialog
        show_export_option_dialog(self.parent, "Export Inventory Reports", [
            ("Current View (filtered)", self._export_current_view),
            ("Stock Statement (all)",  self._export_stock_statement),
            ("Near Expiry Report",     self._export_near_expiry),
            ("Expired Stock Report",   self._export_expired),
            ("Schedule-wise Stock",    self._export_schedule_stock),
        ], width=320)

    def _export_current_view(self):
        from core.column_config import export_tree_current_view
        cols, rows = export_tree_current_view(self.inventory_tree)
        if not rows:
            messagebox.showinfo("No Records", "No medicines visible in the current list.")
            return
        export_data(self.parent, 'Inventory - Current View', cols, rows, 'inventory_current_view')

    def _export_stock_statement(self):
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.online_catalog import medicines as oc_medicines
                rows = []
                for m in oc_medicines():
                    if m.get("is_hidden"):
                        continue
                    name = m.get("name") or ""
                    typ = m.get("type") or ""
                    unit = m.get("unit") or ""
                    mrp_s, mrp_t = self._split_price_cols(m.get("mrp"), typ, unit)
                    rate_s, rate_t = self._split_price_cols(m.get("rate"), typ, unit)
                    rows.append((
                        name, typ, m.get("batch_no") or "", m.get("expiry_date") or "",
                        m.get("stock_qty") or 0, unit, mrp_s, mrp_t, rate_s, rate_t,
                        m.get("manufacturer") or "", m.get("schedule") or "",
                    ))
                export_table(self.parent, 'Stock Statement',
                             ['Name', 'Type', 'Batch', 'Expiry', 'Stock', 'Unit',
                              'MRP', 'MRP/Tab', 'Rate', 'Rate/Tab', 'Manufacturer', 'Schedule'],
                             rows, 'stock_statement', 'inventory', 'stock_statement')
                return
        except Exception:
            pass
        self.cursor.execute("""
            SELECT name,type,batch_no,expiry_date,stock_qty,unit,mrp,rate,manufacturer,schedule
            FROM medicines ORDER BY name
        """)
        rows = []
        for r in self.cursor.fetchall():
            name, typ, batch, exp, stock, unit, mrp, rate, mfr, sch = r
            mrp_s, mrp_t = self._split_price_cols(mrp, typ, unit)
            rate_s, rate_t = self._split_price_cols(rate, typ, unit)
            rows.append((name, typ, batch, exp, stock, unit, mrp_s, mrp_t, rate_s, rate_t, mfr, sch))
        export_table(self.parent, 'Stock Statement',
                     ['Name', 'Type', 'Batch', 'Expiry', 'Stock', 'Unit',
                      'MRP', 'MRP/Tab', 'Rate', 'Rate/Tab', 'Manufacturer', 'Schedule'],
                     rows, 'stock_statement', 'inventory', 'stock_statement')

    def _export_near_expiry(self):
        threshold = (datetime.now() + timedelta(days=90)).strftime('%Y-%m-%d')
        today = datetime.now().strftime('%Y-%m-%d')
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.online_catalog import medicines as oc_medicines
                rows = []
                for m in oc_medicines():
                    if m.get("is_hidden"):
                        continue
                    exp = str(m.get("expiry_date") or "")
                    if exp and today < exp <= threshold:
                        rows.append((
                            m.get("name") or "", m.get("type") or "", m.get("batch_no") or "",
                            exp, m.get("stock_qty") or 0, m.get("manufacturer") or "",
                        ))
                if not rows:
                    messagebox.showinfo("No Records", "No medicines expiring within 90 days."); return
                export_table(self.parent, 'Near Expiry Report',
                             ['Name', 'Type', 'Batch', 'Expiry', 'Stock', 'Manufacturer'],
                             rows, 'near_expiry_report', 'inventory', 'near_expiry')
                return
        except Exception:
            pass
        self.cursor.execute("""
            SELECT name,type,batch_no,expiry_date,stock_qty,manufacturer
            FROM medicines WHERE expiry_date>? AND expiry_date<=? ORDER BY expiry_date ASC
        """, (today, threshold))
        rows = self.cursor.fetchall()
        if not rows:
            messagebox.showinfo("No Records", "No medicines expiring within 90 days."); return
        export_table(self.parent, 'Near Expiry Report',
                     ['Name', 'Type', 'Batch', 'Expiry', 'Stock', 'Manufacturer'],
                     rows, 'near_expiry_report', 'inventory', 'near_expiry')

    def _export_expired(self):
        today = datetime.now().strftime('%Y-%m-%d')
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.online_catalog import medicines as oc_medicines
                rows = []
                for m in oc_medicines():
                    if m.get("is_hidden"):
                        continue
                    exp = str(m.get("expiry_date") or "")
                    if exp and exp <= today:
                        rows.append((
                            m.get("name") or "", m.get("type") or "", m.get("batch_no") or "",
                            exp, m.get("stock_qty") or 0, m.get("manufacturer") or "",
                        ))
                if not rows:
                    messagebox.showinfo("No Records", "No expired medicines found."); return
                export_table(self.parent, 'Expired Stock Report',
                             ['Name', 'Type', 'Batch', 'Expiry', 'Stock', 'Manufacturer'],
                             rows, 'expired_stock_report', 'inventory', 'expired_stock')
                return
        except Exception:
            pass
        self.cursor.execute("""
            SELECT name,type,batch_no,expiry_date,stock_qty,manufacturer
            FROM medicines WHERE expiry_date<=? ORDER BY expiry_date DESC
        """, (today,))
        rows = self.cursor.fetchall()
        if not rows:
            messagebox.showinfo("No Records", "No expired medicines found."); return
        export_table(self.parent, 'Expired Stock Report',
                     ['Name', 'Type', 'Batch', 'Expiry', 'Stock', 'Manufacturer'],
                     rows, 'expired_stock_report', 'inventory', 'expired_stock')

    def _export_schedule_stock(self):
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.online_catalog import medicines as oc_medicines
                rows = [
                    (
                        m.get("schedule") or "Non-Scheduled",
                        m.get("name") or "",
                        m.get("batch_no") or "",
                        m.get("expiry_date") or "",
                        m.get("stock_qty") or 0,
                        m.get("mrp") or 0,
                    )
                    for m in oc_medicines() if not m.get("is_hidden")
                ]
                rows.sort(key=lambda r: (str(r[0]).lower(), str(r[1]).lower()))
                export_table(self.parent, 'Schedule-wise Stock',
                             ['Schedule', 'Name', 'Batch', 'Expiry', 'Stock', 'MRP'],
                             rows, 'schedule_wise_stock', 'inventory', 'schedule_wise_stock')
                return
        except Exception:
            pass
        self.cursor.execute("""
            SELECT COALESCE(schedule,'Non-Scheduled'),name,batch_no,expiry_date,stock_qty,mrp
            FROM medicines ORDER BY 1,name
        """)
        export_table(self.parent, 'Schedule-wise Stock',
                     ['Schedule', 'Name', 'Batch', 'Expiry', 'Stock', 'MRP'],
                     self.cursor.fetchall(), 'schedule_wise_stock', 'inventory', 'schedule_wise_stock')

    # ── Keyboard nav ──────────────────────────────────────────────────────

    def _setup_arrow_nav(self):
        if getattr(self, '_arrow_nav_ready', False):
            return
        from core.focus_chain import wire_focus_ring
        wire_focus_ring([
            self.search_entry, self.type_filter, self.stock_filter,
            self.expiry_filter, self.schedule_filter,
            self.refresh_btn, self.export_btn, self.remove_zero_btn,
            getattr(self, 'remove_expired_btn', None),
        ])
        self._arrow_nav_ready = True

    def _apply_location_column_visibility(self):
        """Called by main.py when returning to inventory page."""
        pass  # handled by load_inventory recreating tree if needed
