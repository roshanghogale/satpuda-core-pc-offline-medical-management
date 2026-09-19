import tkinter as tk
from tkinter import messagebox
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno
from datetime import datetime, date
import sqlite3

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.alert_colors import get_alert_color
from core.font_config import *
from core.layout_config import SALES_HISTORY_ROWS, get_configured_schedules
from core.column_config import apply_column_visibility, all_column_names, is_dashboard_section_visible
from widgets.searchable_combo import SearchableCombo

from ui.sales.sales_history_exports import export_menu
from ui.sales.sales_history_actions import (
    view_bill_details, edit_bill, print_bill, print_bill_slot_silent, save_bill_pdf_a6,
    print_all_bills_sequential, delete_bill
)
from core.bill_config import load_bill_print_settings, get_print_slot_settings
from ui.sales.sales_history_actions import PRINT_ALL_SLOT
from core.list_sort import SORT_OPTIONS, sort_sales_history_rows
from core.record_indicators import (
    extend_columns,
    indicator_column_widths,
    column_heading,
    load_record_indicator_prefs,
    prepare_tree_row,
    register_tree_tags,
    sales_history_display_due,
    sales_history_status,
)


class SalesHistoryPage:

    def __init__(self, parent, conn):
        self.conn   = conn
        self.cursor = conn.cursor()
        self.parent = parent
        self.sales_data = []
        self._sales_loading = False
        self._filter_loading = False
        self._tree_populating = False
        self._pending_sync_refresh = False
        self._tree_populate_seq = 0

        self._build_ui()
        self._load_customer_names()
        top = self.parent.winfo_toplevel()
        if not getattr(top, '_startup_prewarm', False):
            self._apply_default_history_scope()
            self.parent.after(0, self._schedule_load_sales_history)
        if not getattr(top, '_startup_prewarm', False):
            self.parent.after(100, self._setup_arrow_nav)
        if not getattr(top, '_startup_prewarm', False):
            self.parent.after(200, lambda: self._focus_date(self.from_date))
        self._register_keyboard()
        self._bus_token = None
        try:
            from core.sync_prefs import is_online_mode
            from core.sync_v3.data_change_bus import subscribe

            # Online: store_live_refresh is the single remote refresh path.
            # Offline Sync V3: DataChangeBus after local/remote apply.
            if not is_online_mode():
                self._bus_token = subscribe(
                    {"sales", "customer_payments", "sales_returns"},
                    lambda _col: self.ensure_data_loaded() if hasattr(self, "ensure_data_loaded") else self.load_sales_history(),
                )
        except Exception:
            pass
        self._live_token = None
        try:
            from core.store_live_refresh import subscribe as live_sub

            def _on_live(_evt):
                try:
                    self.parent.after(300, self.queue_sync_refresh)
                except Exception:
                    pass

            self._live_token = live_sub({"sales", "customer_payments", "sales_returns"}, _on_live)
        except Exception:
            pass

    def _make_filter_date(self, parent):
        """Date filter with calendar button (same as Sales bill date). Starts empty."""
        try:
            from ttkbootstrap.widgets import DateEntry
            w = DateEntry(parent, dateformat='%Y-%m-%d', width=12, bootstyle='primary')
            try:
                w.entry.delete(0, tk.END)
            except Exception:
                pass
            return w
        except Exception:
            return ttk.Entry(parent, width=12)

    @staticmethod
    def _date_entry(w):
        return getattr(w, 'entry', w)

    def _focus_date(self, w):
        try:
            self._date_entry(w).focus_set()
        except Exception:
            pass

    def _get_date_filter(self, w):
        try:
            return (self._date_entry(w).get() or '').strip()
        except Exception:
            return ''

    def _set_date_filter(self, w, value):
        raw = (value or '').strip()
        e = self._date_entry(w)
        try:
            e.delete(0, tk.END)
            if raw:
                e.insert(0, raw)
        except Exception:
            pass
        if raw and hasattr(w, 'set_date'):
            try:
                w.set_date(datetime.strptime(raw[:10], '%Y-%m-%d').date())
            except Exception:
                pass

    def _clear_date_filter(self, w):
        self._set_date_filter(w, '')

    def _register_keyboard(self):
        from core.keyboard_registry import KeyboardRegistry, PageBindings
        bindings = PageBindings(
            page_id='sales_history',
            first_focus=lambda: self._focus_date(self.from_date),
            on_ctrl_f=lambda: self.customer_filter.entry.focus_set(),
            on_ctrl_enter=self.apply_filter,
            on_ctrl_shift_c=self._clear_filter_shortcut,
            on_ctrl_e=self._export_menu,
            on_f7=self._print_sales_1_shortcut,
            on_f8=self._print_sales_2_shortcut,
            on_f9=self._print_sales_1_shortcut,
            f2_target=self.sales_tree,
        )
        self._inner_frame._keyboard_bindings = bindings
        KeyboardRegistry.register_page(self._inner_frame, bindings)

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Fixed layout (no outer canvas): filters + summary stay put; only the
        # Treeview scrolls. Outer make_scrollable caused page flicker because
        # tall filters/summary overflowed and fought the tree scrollbar.
        main_frame = ttk.Frame(self.parent)
        try:
            main_frame.configure(padding=(10, 10))
        except Exception:
            pass
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self._inner_frame = main_frame

        # Filter
        ff = ttk.LabelFrame(main_frame, text="Filter Options")
        ff.pack(fill=tk.X, pady=5)

        ttk.Label(ff, text="From Date:").grid(row=0, column=0, padx=5, pady=5)
        self.from_date = self._make_filter_date(ff)
        self.from_date.grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(ff, text="To Date:").grid(row=0, column=2, padx=5, pady=5)
        self.to_date = self._make_filter_date(ff)
        self.to_date.grid(row=0, column=3, padx=5, pady=5)

        ttk.Label(ff, text="Fin Year:").grid(row=0, column=4, padx=5, pady=5)
        self._fy_years = self._build_fy_list()
        self.fy_filter = SearchableCombo(ff, values=self._fy_years, width=10)
        self.fy_filter.grid(row=0, column=5, padx=5, pady=5)
        self.fy_filter.entry.bind('<<ComboboxSelected>>', lambda e: self._apply_fy_filter(), add='+')
        self.fy_filter.entry.bind('<Return>', lambda e: self._apply_fy_filter(), add='+')

        ttk.Label(ff, text="Customer:").grid(row=1, column=0, padx=5, pady=5)
        self.customer_filter = SearchableCombo(ff, width=20)
        self.customer_filter.grid(row=1, column=1, padx=5, pady=5)
        self.customer_filter.entry.bind('<FocusIn>', lambda e: self._load_customer_names(), add='+')

        ttk.Label(ff, text="Due:").grid(row=1, column=2, padx=5, pady=5)
        self.due_filter = SearchableCombo(ff, values=['Due Only','Credit Only','Paid / Cleared'], width=15)
        self.due_filter.grid(row=1, column=3, padx=5, pady=5)

        ttk.Label(ff, text="Schedule:").grid(row=1, column=4, padx=5, pady=5)
        self.schedule_filter = SearchableCombo(
            ff, values=get_configured_schedules() + ['Non-Scheduled'], width=15)
        self.schedule_filter.grid(row=1, column=5, padx=5, pady=5)

        self.clear_btn = ttk.Button(ff, text="Clear Filter", command=self.clear_filter)
        self.clear_btn.grid(row=1, column=6, padx=5, pady=5)

        ttk.Label(ff, text="Bill No:").grid(row=2, column=0, padx=5, pady=5)
        self.bill_no_filter = ttk.Entry(ff, width=18)
        self.bill_no_filter.grid(row=2, column=1, padx=5, pady=5)

        ttk.Label(ff, text="Medicine:").grid(row=2, column=2, padx=5, pady=5)
        self.medicine_filter = ttk.Entry(ff, width=18)
        self.medicine_filter.grid(row=2, column=3, padx=5, pady=5)

        ttk.Label(ff, text="Batch No:").grid(row=2, column=4, padx=5, pady=5)
        self.batch_filter = ttk.Entry(ff, width=15)
        self.batch_filter.grid(row=2, column=5, padx=5, pady=5)

        ttk.Label(ff, text="Sort By:").grid(row=3, column=0, padx=5, pady=5)
        self.sort_filter = SearchableCombo(ff, values=list(SORT_OPTIONS), width=22)
        self.sort_filter.set(SORT_OPTIONS[0])
        self.sort_filter.grid(row=3, column=1, columnspan=3, padx=5, pady=5, sticky=tk.W)

        self.apply_btn = ttk.Button(ff, text="Apply Filter", command=self.apply_filter)
        self.apply_btn.grid(row=0, column=6, padx=10, pady=5)
        try:
            self.export_btn = ttk.Button(ff, text="📤 Export", command=self._export_menu,
                                         bootstyle="info")
        except Exception:
            self.export_btn = ttk.Button(ff, text="📤 Export", command=self._export_menu)
        self.export_btn.grid(row=0, column=7, padx=10, pady=5, sticky=tk.N)
        _print_all_label = (
            get_print_slot_settings(load_bill_print_settings(), PRINT_ALL_SLOT).get('label')
            or 'A6'
        )
        try:
            self.print_all_btn = ttk.Button(
                ff, text=f"Print All ({_print_all_label})",
                command=self._print_all_bills, bootstyle="primary",
            )
        except Exception:
            self.print_all_btn = ttk.Button(
                ff, text="Print All Bills", command=self._print_all_bills,
            )
        self.print_all_btn.grid(row=0, column=8, padx=10, pady=5, sticky=tk.N)
        self.customer_filter.bind_apply_on_select(self.apply_filter)
        self.due_filter.bind_apply_on_select(self.apply_filter)
        self.schedule_filter.bind_apply_on_select(self.apply_filter)
        self.sort_filter.bind_apply_on_select(self._resort_visible)
        self.fy_filter.bind_apply_on_select(self._apply_fy_filter)

        from core.focus_chain import wire_combo_filter_chain, wire_entry_filter_chain
        wire_entry_filter_chain(
            self._date_entry(self.from_date), self._date_entry(self.to_date),
            last_action=lambda: self.fy_filter.focus(),
        )
        wire_combo_filter_chain(
            self.fy_filter, self.customer_filter, self.due_filter,
            self.schedule_filter,
            on_last_return=lambda: self.bill_no_filter.focus_set(),
        )
        wire_entry_filter_chain(
            self.bill_no_filter, self.medicine_filter, self.batch_filter,
            last_action=lambda: self.sort_filter.focus(),
        )
        wire_combo_filter_chain(
            self.sort_filter,
            on_last_return=self.apply_filter,
        )
        for entry in (self.bill_no_filter, self.medicine_filter, self.batch_filter):
            entry.bind('<Return>', lambda e: self.apply_filter(), add='+')

        self._load_status_var = tk.StringVar(value="")
        ttk.Label(
            ff, textvariable=self._load_status_var,
            font=(FONT_FAMILY, FONT_SIZE_SUPPORTING_TEXT),
        ).grid(row=4, column=0, columnspan=9, sticky=tk.W, padx=5, pady=(0, 4))

        # Tree
        tf = ttk.Frame(main_frame)
        tf.pack(fill=tk.BOTH, expand=True, pady=5)

        self._all_columns = tuple(all_column_names('sales_history'))
        self._tree_columns = extend_columns(self._all_columns)
        widths = {
            'Bill No': 100, 'Date': 100, 'Customer': 150, 'Phone': 120,
            'Doctor': 120, 'Schedule': 90,
            'Total Amount': 100, 'Discount': 90, 'Amount Paid': 100, 'Cash Paid': 90, 'Online Paid': 90,
            'Previous Due': 100, 'Due Amount': 100, 'Credit Amount': 100, 'Total Due': 100,
        }
        widths.update(indicator_column_widths())
        self.sales_tree = ttk.Treeview(tf, columns=self._tree_columns, show='headings',
                                       height=SALES_HISTORY_ROWS, style='Large.Treeview')
        for col in self._tree_columns:
            self.sales_tree.heading(col, text=column_heading(col))
            self.sales_tree.column(col, width=widths.get(col, 100))
        apply_column_visibility(self.sales_tree, 'sales_history', self._tree_columns)

        vsb = ttk.Scrollbar(tf, orient=tk.VERTICAL,   command=self.sales_tree.yview)
        hsb = ttk.Scrollbar(tf, orient=tk.HORIZONTAL, command=self.sales_tree.xview)
        self.sales_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.sales_tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        tf.grid_rowconfigure(0, weight=1)
        tf.grid_columnconfigure(0, weight=1)

        register_tree_tags(self.sales_tree)

        from core.tree_action_menu import setup_tree_actions
        _slot1 = get_print_slot_settings(load_bill_print_settings(), 1).get('label') or 'Print Sales 1'
        _slot2 = get_print_slot_settings(load_bill_print_settings(), 2).get('label') or 'Print Sales 2'
        self._action_menu = setup_tree_actions(
            self.parent,
            self.sales_tree,
            [
                ("View Bill Details", self._view_bill),
                ("Edit Bill", self._edit_bill),
                ("Print Bill (dialog)", self._print_bill),
                (_slot1, self._print_sales_1),
                (_slot2, self._print_sales_2),
                ("Save PDF (A6)", self._save_bill_pdf),
                "---",
                ("Delete Bill", self._delete_bill),
            ],
            on_double=self._view_bill,
            on_delete=lambda e: self._delete_bill(),
            escape_to=self.from_date,
        )
        self._ctx = self._action_menu.ctx_menu

        # Summary (below the list, same as before)
        self._show_summary = is_dashboard_section_visible('sales_summary')
        self.total_bills_var    = tk.StringVar()
        self.total_sales_var    = tk.StringVar()
        self.total_due_var      = tk.StringVar()
        self.total_profit_var   = tk.StringVar()
        self.today_revenue_var  = tk.StringVar()
        self.today_profit_var   = tk.StringVar()
        self.today_cash_var     = tk.StringVar()
        self.today_online_var   = tk.StringVar()
        self.month_revenue_var  = tk.StringVar()
        self.month_profit_var   = tk.StringVar()
        self.total_discount_var = tk.StringVar()
        self.total_paid_var     = tk.StringVar()
        self.total_returns_var  = tk.StringVar()

        if self._show_summary:
            sf = ttk.LabelFrame(main_frame, text="Sales Summary")
            sf.pack(fill=tk.X, pady=5)
            row0 = [
                ("Total Bills:", self.total_bills_var, None),
                ("Total Sales:", self.total_sales_var, 'success'),
                ("Total Due (All Time):", self.total_due_var, 'danger'),
                ("Total Profit:", self.total_profit_var, 'success'),
                ("Today Revenue:", self.today_revenue_var, 'info'),
                ("Today Profit:", self.today_profit_var, 'success'),
            ]
            row1 = [
                ("Today Cash:", self.today_cash_var, None),
                ("Today Online:", self.today_online_var, None),
                ("Month Revenue:", self.month_revenue_var, 'info'),
                ("Month Profit:", self.month_profit_var, 'success'),
                ("Total Discount:", self.total_discount_var, 'warning'),
                ("Total Paid:", self.total_paid_var, 'success'),
            ]
            row2 = [("Total Returns:", self.total_returns_var, 'info')]
            for col, (lbl, var, color) in enumerate(row0):
                ttk.Label(sf, text=lbl).grid(row=0, column=col * 2, padx=6, pady=5)
                kw = {'font': (FONT_FAMILY, FONT_SIZE_LABELS, 'bold')}
                if color:
                    kw['foreground'] = get_alert_color(color)
                ttk.Label(sf, textvariable=var, **kw).grid(row=0, column=col * 2 + 1, padx=6, pady=5)
            for col, (lbl, var, color) in enumerate(row1):
                ttk.Label(sf, text=lbl).grid(row=1, column=col * 2, padx=6, pady=5)
                kw = {'font': (FONT_FAMILY, FONT_SIZE_LABELS, 'bold')}
                if color:
                    kw['foreground'] = get_alert_color(color)
                ttk.Label(sf, textvariable=var, **kw).grid(row=1, column=col * 2 + 1, padx=6, pady=5)
            for col, (lbl, var, color) in enumerate(row2):
                ttk.Label(sf, text=lbl).grid(row=2, column=col * 2, padx=6, pady=5)
                kw = {'font': (FONT_FAMILY, FONT_SIZE_LABELS, 'bold')}
                if color:
                    kw['foreground'] = get_alert_color(color)
                ttk.Label(sf, textvariable=var, **kw).grid(row=2, column=col * 2 + 1, padx=6, pady=5)

            # Keep summary on-screen: pin to bottom; tree takes remaining space.
            ff.pack_forget()
            tf.pack_forget()
            sf.pack_forget()
            sf.pack(side=tk.BOTTOM, fill=tk.X, pady=5)
            ff.pack(side=tk.TOP, fill=tk.X, pady=5)
            tf.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=5)

    # ── Data loading ──────────────────────────────────────────────────────

    def _load_customer_names(self):
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.online_catalog import customer_names
                # Same store customer set as Sales (include COUNTER SALE for parity).
                self.customer_filter.configure(values=customer_names())
                return
        except Exception:
            pass
        self.cursor.execute("SELECT DISTINCT name FROM customers ORDER BY name COLLATE NOCASE")
        self.customer_filter.configure(values=[r[0] for r in self.cursor.fetchall()])

    def _sorted_sales_data(self, rows=None):
        return sort_sales_history_rows(rows if rows is not None else self.sales_data,
                                       self.sort_filter.get())

    def _resort_visible(self):
        if self._sales_loading or self._filter_loading or not self.sales_data:
            return
        self._redisplay_sales(self._sorted_sales_data())

    def _set_sales_loading(self, loading: bool, message: str = ''):
        self._sales_loading = loading
        try:
            self._load_status_var.set(message)
            state = 'disabled' if loading else 'normal'
            self.apply_btn.configure(state=state)
            self.clear_btn.configure(state=state)
            self.export_btn.configure(state=state)
            if hasattr(self, 'print_all_btn'):
                self.print_all_btn.configure(state=state)
        except tk.TclError:
            pass

    def _schedule_load_sales_history(self):
        if self._sales_loading:
            return
        from core.background_workers import run_in_thread
        self._set_sales_loading(True, 'Loading sales history…')
        run_in_thread(
            self._fetch_sales_rows,
            name='SalesHistoryLoad',
            root=self.parent,
            on_success=self._apply_sales_rows,
            on_error=self._on_sales_load_error,
        )

    def _resolve_active_date_filters(self):
        fd = self._get_date_filter(self.from_date)
        td = self._get_date_filter(self.to_date)
        from core.history_prefs import resolve_history_dates
        return resolve_history_dates(fd, td)

    def _fetch_sales_rows(self):
        fd, td, _applied = self._resolve_active_date_filters()
        return self._fetch_filtered_sales_rows((fd, td, '', '', '', '', '', ''))

    def _on_sales_load_error(self, exc):
        self._set_sales_loading(False, f'Load failed: {exc}')

    def _apply_sales_rows(self, rows):
        self.sales_data = self._sorted_sales_data(rows)
        self._redisplay_sales(self.sales_data)

    def _redisplay_sales(self, rows, *, on_done=None):
        from core.ui_tree_loader import (
            populate_tree_batched,
            restore_tree_yview,
            save_tree_yview,
            sync_tree_by_iid,
        )

        self._tree_populate_seq = getattr(self, '_tree_populate_seq', 0) + 1
        seq = self._tree_populate_seq
        self._tree_populating = True
        yview = save_tree_yview(self.sales_tree)

        def _row_payload(sale):
            display_due = sales_history_display_due(sale)
            bill_no, sale_id = sale[14], sale[15]
            from core.fy_serial import display_sales_bill_no
            bill_no = display_sales_bill_no(str(bill_no or ''))
            doctor = sale[16] or ''
            schedule = (sale[17] or '') if len(sale) > 17 else ''
            vals = (
                bill_no, sale[0], sale[1], sale[2], doctor, schedule,
                sale[3], sale[4] or 0, sale[5], sale[6], sale[7], sale[8], sale[9], sale[10],
                display_due,
            )
            values, tags = prepare_tree_row(vals, sales_history_status(sale))
            return str(sale_id), values, tags

        painted = [_row_payload(s) for s in rows]
        mode = sync_tree_by_iid(self.sales_tree, painted)

        def _finish():
            self._tree_populating = False
            restore_tree_yview(self.sales_tree, yview)
            if on_done:
                on_done(len(rows))
            else:
                fd, td, _ = self._resolve_active_date_filters()
                self.update_summary(fd=fd, td=td)
                self._set_sales_loading(False, f'{len(rows):,} bills loaded')
            if self._pending_sync_refresh:
                self._pending_sync_refresh = False
                try:
                    self.parent.after(200, self.queue_sync_refresh)
                except Exception:
                    pass

        if mode in ("noop", "diff"):
            _finish()
            return

        kids = self.sales_tree.get_children()
        if kids:
            self.sales_tree.delete(*kids)

        def _insert_one(sale):
            iid, values, tags = _row_payload(sale)
            self.sales_tree.insert('', tk.END, iid=iid, values=values, tags=tags)

        populate_tree_batched(
            self.sales_tree,
            rows,
            _insert_one,
            self.parent,
            batch_size=100,
            on_done=_finish,
            should_stop=lambda: seq != self._tree_populate_seq,
        )

    def load_sales_history(self):
        self._pending_sync_refresh = False
        self._schedule_load_sales_history()

    def queue_sync_refresh(self):
        """Online server sync — defer if the list is loading or being scrolled in."""
        if self._sales_loading or self._filter_loading or self._tree_populating:
            self._pending_sync_refresh = True
            return
        self._schedule_load_sales_history()

    def ensure_data_loaded(self):
        # Reload only when empty or marked dirty by sync / pending refresh.
        if self._sales_loading:
            self._pending_sync_refresh = True
            return
        if self.sales_data and not self._pending_sync_refresh:
            return
        self._pending_sync_refresh = False
        self._schedule_load_sales_history()

    def on_page_shown(self):
        """Re-apply FY scope; reload list only when dirty or empty."""
        try:
            from core.history_prefs import (
                current_fy_bounds,
                current_fy_label,
                load_history_scope,
            )

            if load_history_scope() == "current_fy":
                want = current_fy_label()
                have = ""
                try:
                    have = (self.fy_filter.get() or "").strip()
                except Exception:
                    pass
                fd = self._get_date_filter(self.from_date)
                td = self._get_date_filter(self.to_date)
                bfd, btd = current_fy_bounds()
                if have != want or fd != bfd or td != btd:
                    if self._apply_default_history_scope():
                        self.apply_filter()
                        return
        except Exception:
            pass
        self.ensure_data_loaded()

    def _fetch_filtered_sales_rows(self, params):
        import sqlite3
        from core.background_workers import db_path_from_conn
        fd, td, cus, due, sch, bill_no, med_name, batch_no = params

        # Online mode: paint from Satpuda Core Server (store-scoped), not local SQLite.
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core import store_query_client as sq
                from core.fy_serial import display_sales_bill_no

                q = (cus or bill_no or "").strip()
                # Same as Offline: empty dates → current FY (01 Apr – 31 Mar).
                if not (fd or td):
                    from core.history_prefs import current_fy_bounds
                    fd, td = current_fy_bounds()
                sch_q = "" if not sch or sch == "All" else sch
                data = sq.list_sales(
                    from_date=fd or "",
                    to_date=td or "",
                    q=q,
                    schedule=sch_q,
                    medicine=(med_name or "").strip(),
                    batch=(batch_no or "").strip(),
                    limit=5000,
                )
                from core.online_mutation_queue import (
                    overlay_sales_dicts,
                    merge_server_rows,
                )

                merged = merge_server_rows(
                    list(data.get("rows") or []),
                    overlay_sales_dicts(),
                    collection="sales",
                )
                # FIFO overlay (same as purchase history / pages): customer payments clear bill dues.
                from core.due_fifo import fifo_sales_remaining_by_bill, sale_entry_paid
                from core.online_mutation_queue import (
                    overlay_customer_payment_dicts,
                    overlay_sales_return_dicts,
                )
                try:
                    pays = list((sq.list_customer_payments(limit=5000) or {}).get("rows") or [])
                except Exception:
                    pays = []
                try:
                    rets = list((sq.list_sales_returns(limit=5000) or {}).get("rows") or [])
                except Exception:
                    rets = []
                pays = merge_server_rows(
                    pays, overlay_customer_payment_dicts(), collection="customer_payments",
                )
                rets = merge_server_rows(
                    rets, overlay_sales_return_dicts(), collection="sales_returns",
                )
                fifo_sales = fifo_sales_remaining_by_bill(merged, pays, rets)
                rows_out = []
                for r in merged:
                    name = (r.get("customer_name") or "Customer")
                    if cus and cus.lower() not in str(name).lower():
                        continue
                    bno = str(r.get("bill_no") or "")
                    if bill_no and bill_no.lower() not in bno.lower():
                        continue
                    try:
                        sid = int(r.get("id") or 0)
                    except (TypeError, ValueError):
                        sid = 0
                    entry_paid = sale_entry_paid(r)
                    total_amt = float(r.get("total_amount") or 0)
                    unpaid = max(0.0, total_amt - entry_paid)
                    fifo_hit = fifo_sales.get(sid)
                    if fifo_hit is not None:
                        due_amt, acct = fifo_hit
                        due_amt = float(due_amt or 0)
                        acct = 1 if acct else 0
                    else:
                        due_amt = float(r.get("due_amount") or 0)
                        acct = 1 if r.get("account_cleared") else 0
                    via_payment = max(0.0, unpaid - float(due_amt or 0)) if unpaid > 0.01 else 0.0
                    if acct or float(due_amt or 0) <= 0.01:
                        due_amt = 0.0
                        acct = 1
                    total_due = due_amt
                    bill_cleared = 1 if r.get("bill_cleared") else (1 if unpaid <= 0.01 else 0)
                    credit = float(r.get("credit_amount") or 0)
                    if due == 'Due Only' and not (total_due > 0 and not acct):
                        continue
                    if due == 'Credit Only' and credit <= 0:
                        continue
                    if due == 'Paid / Cleared' and not acct:
                        continue
                    # Amount Paid column: entry + paid via payment (so cleared bills don't show ₹0).
                    display_paid = entry_paid + via_payment
                    rows_out.append((
                        r.get("bill_date"),
                        name,
                        "",
                        total_amt,
                        float(r.get("discount") or 0),
                        display_paid,
                        float(r.get("cash_paid") or 0),
                        float(r.get("online_paid") or 0),
                        float(r.get("previous_due") or 0),
                        due_amt,
                        credit,
                        total_due,
                        bill_cleared,
                        acct,
                        bno,
                        sid,
                        r.get("doctor_name") or "",
                        r.get("schedules") or "",
                    ))
                return rows_out
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("sales history server fetch: %s", exc)
            try:
                from core.sync_prefs import is_online_mode

                if is_online_mode():
                    return []
            except Exception:
                pass

        path = db_path_from_conn(self.conn)
        conn = sqlite3.connect(path, check_same_thread=False) if path else self.conn
        own_conn = conn is not self.conn
        try:
            cur = conn.cursor()
            use_item_join = bool(
                (sch and sch != 'All') or med_name or batch_no
            )
            if use_item_join:
                q = """SELECT DISTINCT s.bill_date, c.name, c.phone,
                           s.total_amount, s.discount, s.amount_paid,
                           COALESCE(s.cash_paid,0), COALESCE(s.online_paid,0),
                           s.previous_due, COALESCE(s.due_amount,0), COALESCE(s.credit_amount,0),
                           COALESCE(s.total_due,0), s.bill_cleared, s.account_cleared,
                           s.bill_no, s.id, s.doctor_name,
                           (SELECT GROUP_CONCAT(DISTINCT NULLIF(m2.schedule,''))
                            FROM sales_items si2 JOIN medicines m2 ON si2.medicine_id=m2.id
                            WHERE si2.sale_id=s.id) AS bill_schedules
                       FROM sales s
                       JOIN customers c ON s.customer_id=c.id
                       JOIN sales_items si ON s.id=si.sale_id
                       JOIN medicines m ON si.medicine_id=m.id WHERE COALESCE(s.deleted, 0) = 0 AND COALESCE(s.is_autosave, 0) = 0"""
            else:
                q = """SELECT s.bill_date, c.name, c.phone,
                           s.total_amount, s.discount, s.amount_paid,
                           COALESCE(s.cash_paid,0), COALESCE(s.online_paid,0),
                           s.previous_due, COALESCE(s.due_amount,0), COALESCE(s.credit_amount,0),
                           COALESCE(s.total_due,0), s.bill_cleared, s.account_cleared,
                           s.bill_no, s.id, s.doctor_name,
                           (SELECT GROUP_CONCAT(DISTINCT NULLIF(m.schedule,''))
                            FROM sales_items si JOIN medicines m ON si.medicine_id=m.id
                            WHERE si.sale_id=s.id) AS bill_schedules
                       FROM sales s JOIN customers c ON s.customer_id=c.id WHERE COALESCE(s.deleted, 0) = 0 AND COALESCE(s.is_autosave, 0) = 0"""
            qparams = []
            if fd:
                q += ' AND s.bill_date>=?'
                qparams.append(fd)
            if td:
                q += ' AND s.bill_date<=?'
                qparams.append(td)
            if cus:
                q += ' AND c.name LIKE ?'
                qparams.append(f'%{cus}%')
            if bill_no:
                q += ' AND s.bill_no LIKE ?'
                qparams.append(f'%{bill_no}%')
            if due == 'Due Only':
                q += ' AND s.total_due>0 AND s.account_cleared=0'
            elif due == 'Credit Only':
                q += ' AND COALESCE(s.credit_amount,0)>0'
            elif due == 'Paid / Cleared':
                q += ' AND s.account_cleared=1'
            if use_item_join:
                if sch and sch != 'All':
                    if sch == 'Non-Scheduled':
                        q += " AND (m.schedule IS NULL OR m.schedule='')"
                    else:
                        q += ' AND m.schedule=?'
                        qparams.append(sch)
                if med_name:
                    q += ' AND m.name LIKE ?'
                    qparams.append(f'%{med_name}%')
                if batch_no:
                    q += ' AND COALESCE(m.batch_no,\'\') LIKE ?'
                    qparams.append(f'%{batch_no}%')
            q += ' ORDER BY s.bill_date DESC, s.created_at DESC'
            cur.execute(q, qparams)
            return cur.fetchall()
        finally:
            if own_conn:
                conn.close()

    def apply_filter(self):
        if self._filter_loading:
            return
        params = (
            self._get_date_filter(self.from_date),
            self._get_date_filter(self.to_date),
            self.customer_filter.get().strip(),
            self.due_filter.get(),
            self.schedule_filter.get(),
            (self.bill_no_filter.get() or '').strip(),
            (self.medicine_filter.get() or '').strip(),
            (self.batch_filter.get() or '').strip(),
        )
        self._filter_loading = True
        self._set_sales_loading(True, 'Filtering sales history…')
        from core.background_workers import run_in_thread
        run_in_thread(
            lambda: self._fetch_filtered_sales_rows(params),
            name='SalesHistoryFilter',
            root=self.parent,
            on_success=self._apply_filter_rows,
            on_error=self._on_filter_load_error,
        )

    def _on_filter_load_error(self, exc):
        self._filter_loading = False
        self._set_sales_loading(False, f'Filter failed: {exc}')

    def _apply_filter_rows(self, data):
        self.sales_data = self._sorted_sales_data(data or [])

        def _done(count):
            self.update_summary(
                self.sales_data,
                fd=self._get_date_filter(self.from_date),
                td=self._get_date_filter(self.to_date),
            )
            self._filter_loading = False
            self._set_sales_loading(False, f'{count:,} bills')

        self._redisplay_sales(self.sales_data, on_done=_done)

    def _insert_sale_row(self, sale):
        display_due = sales_history_display_due(sale)
        bill_no, sale_id = sale[14], sale[15]
        from core.fy_serial import display_sales_bill_no
        bill_no = display_sales_bill_no(str(bill_no or ''))
        doctor = sale[16] or ''
        schedule = (sale[17] or '') if len(sale) > 17 else ''
        vals = (
            bill_no, sale[0], sale[1], sale[2], doctor, schedule,
            sale[3], sale[4] or 0, sale[5], sale[6], sale[7], sale[8], sale[9], sale[10],
            display_due,
        )
        values, tags = prepare_tree_row(vals, sales_history_status(sale))
        self.sales_tree.insert('', tk.END, iid=str(sale_id), values=values, tags=tags)

    def _populate_tree(self, data):
        for sale in data:
            self._insert_sale_row(sale)

    def clear_filter(self):
        self._clear_date_filter(self.from_date)
        self._clear_date_filter(self.to_date)
        for combo in (self.customer_filter, self.due_filter,
                      self.schedule_filter, self.fy_filter):
            try:
                combo.hide_list()
                combo.set('')
            except Exception:
                pass
        for entry in (self.bill_no_filter, self.medicine_filter, self.batch_filter):
            try:
                entry.delete(0, tk.END)
            except Exception:
                pass
        try:
            self.sort_filter.set(SORT_OPTIONS[0])
        except Exception:
            pass
        from core.history_prefs import load_history_scope
        if load_history_scope() == 'current_fy' and self._apply_default_history_scope():
            self.apply_filter()
        else:
            self.load_sales_history()

    def _clear_filter_shortcut(self, event=None):
        self.clear_filter()
        return 'break'

    def update_summary(self, data=None, fd='', td=''):
        if not getattr(self, '_show_summary', True):
            return
        if data is None:
            data = self.sales_data
        snapshot = list(data)
        self._summary_gen = getattr(self, '_summary_gen', 0) + 1
        gen = self._summary_gen
        from core.background_workers import run_in_thread
        run_in_thread(
            lambda: self._compute_summary_stats(snapshot, fd, td),
            name='SalesHistorySummary',
            root=self.parent,
            on_success=lambda stats: self._apply_summary_stats(stats, gen),
        )

    def _compute_summary_stats(self, data, fd='', td=''):
        from datetime import date as _date
        today_d = _date.today()
        this_month = today_d.replace(day=1)
        n = len(data)
        total_sales = sum(float(s[3] or 0) for s in data)
        total_disc = sum(float(s[4] or 0) for s in data)
        today_data = [s for s in data if str(s[0]) == str(today_d)]
        month_data = [s for s in data if str(s[0])[:7] == str(this_month)[:7]]
        total_billing_paid = sum(float(s[5] or 0) for s in data) if data else 0.0
        all_ids = []
        for s in data:
            try:
                all_ids.append(int(s[15]))
            except (TypeError, ValueError, IndexError):
                pass

        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core import store_query_client as sq
                # Scope to the same filtered bills Offline uses (not the whole FY).
                rem = sq.sales_summary(
                    from_date=fd or "",
                    to_date=td or "",
                    ids=all_ids,
                ) or {}
                return {
                    'n': n,
                    'total_sales': total_sales,
                    'actual_due': float(rem.get('total_due_global') or 0),
                    'total_disc': float(rem.get('total_discount') or total_disc),
                    'total_profit': float(rem.get('total_profit') or 0),
                    'today_rev': sum(float(s[3] or 0) for s in today_data),
                    'today_profit': float(rem.get('today_profit') or 0),
                    'today_cash': sum(float(s[6] or 0) for s in today_data),
                    'today_online': sum(float(s[7] or 0) for s in today_data),
                    'month_rev': sum(float(s[3] or 0) for s in month_data),
                    'month_profit': float(rem.get('month_profit') or 0),
                    'total_paid_all': round(
                        total_billing_paid + float(rem.get('standalone_paid') or 0), 2
                    ),
                    'total_returns': float(rem.get('total_returns') or 0),
                }
        except Exception as exc:
            print(f"[sales history] online summary: {exc}")

        import sqlite3
        from core.background_workers import db_path_from_conn
        from core.stock_utils import sale_line_profit
        path = db_path_from_conn(self.conn)
        conn = sqlite3.connect(path, check_same_thread=False) if path else self.conn
        own = conn is not self.conn
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COALESCE(SUM(total_due),0) FROM customers WHERE total_due > 0")
            actual_due = cur.fetchone()[0] or 0
            total_standalone_paid = 0.0
            if data:
                if fd and td:
                    cur.execute(
                        "SELECT COALESCE(SUM(amount),0) FROM customer_payments "
                        "WHERE payment_date >= ? AND payment_date <= ?", (fd, td))
                elif fd:
                    cur.execute(
                        "SELECT COALESCE(SUM(amount),0) FROM customer_payments "
                        "WHERE payment_date >= ?", (fd,))
                elif td:
                    cur.execute(
                        "SELECT COALESCE(SUM(amount),0) FROM customer_payments "
                        "WHERE payment_date <= ?", (td,))
                else:
                    cur.execute("SELECT COALESCE(SUM(amount),0) FROM customer_payments")
                total_standalone_paid = cur.fetchone()[0] or 0
            id_strs = [str(i) for i in all_ids]
            if id_strs:
                cur.execute(
                    f"SELECT COALESCE(SUM(item_discount), 0) FROM sales_items "
                    f"WHERE sale_id IN ({','.join(['?'] * len(id_strs))})",
                    id_strs,
                )
                total_disc = round(total_disc + (cur.fetchone()[0] or 0), 2)

            def _profit(ids):
                if not ids:
                    return 0
                cur.execute(
                    f"SELECT si.amount, si.qty, si.cost_price, "
                    f"m.type, COALESCE(m.unit, '1'), COALESCE(m.rate, 0) "
                    f"FROM sales_items si JOIN medicines m ON si.medicine_id = m.id "
                    f"WHERE si.sale_id IN ({','.join(['?'] * len(ids))})",
                    ids,
                )
                return round(sum(
                    sale_line_profit(row[0], row[1], row[2], row[5], row[3], row[4])
                    for row in cur.fetchall()
                ), 2)

            if id_strs:
                cur.execute(
                    f"SELECT COALESCE(SUM(sr.refund_amount),0) FROM sales_returns sr "
                    f"WHERE sr.sale_id IN ({','.join(['?'] * len(id_strs))})",
                    id_strs,
                )
                total_returns = cur.fetchone()[0] or 0
            else:
                total_returns = 0
            return {
                'n': n,
                'total_sales': total_sales,
                'actual_due': actual_due,
                'total_disc': total_disc,
                'total_profit': _profit(id_strs),
                'today_rev': sum(float(s[3] or 0) for s in today_data),
                'today_profit': _profit([str(s[15]) for s in today_data]),
                'today_cash': sum(float(s[6] or 0) for s in today_data),
                'today_online': sum(float(s[7] or 0) for s in today_data),
                'month_rev': sum(float(s[3] or 0) for s in month_data),
                'month_profit': _profit([str(s[15]) for s in month_data]),
                'total_paid_all': round(total_billing_paid + total_standalone_paid, 2),
                'total_returns': total_returns,
            }
        finally:
            if own:
                conn.close()

    def _apply_summary_stats(self, stats, gen):
        if gen != getattr(self, '_summary_gen', 0):
            return
        self.total_bills_var.set(str(stats['n']))
        self.total_sales_var.set(f"\u20b9{stats['total_sales']:.2f}")
        self.total_due_var.set(f"\u20b9{stats['actual_due']:.2f}")
        self.total_discount_var.set(f"\u20b9{stats['total_disc']:.2f}")
        self.total_profit_var.set(f"\u20b9{stats['total_profit']:.2f}")
        self.today_revenue_var.set(f"\u20b9{stats['today_rev']:.2f}")
        self.today_profit_var.set(f"\u20b9{stats['today_profit']:.2f}")
        self.today_cash_var.set(f"\u20b9{stats['today_cash']:.2f}")
        self.today_online_var.set(f"\u20b9{stats['today_online']:.2f}")
        self.month_revenue_var.set(f"\u20b9{stats['month_rev']:.2f}")
        self.month_profit_var.set(f"\u20b9{stats['month_profit']:.2f}")
        self.total_paid_var.set(f"\u20b9{stats['total_paid_all']:.2f}")
        try:
            self.total_returns_var.set(f"\u20b9{stats['total_returns']:.2f}")
        except AttributeError:
            pass

    # ── Context menu / actions ────────────────────────────────────────────

    def _show_ctx(self, event):
        if self.sales_tree.selection():
            self._ctx.post(event.x_root, event.y_root)

    def _selected(self):
        sel = self.sales_tree.selection()
        if not sel: return None, None
        try:
            sale_id = int(sel[0])
        except (ValueError, IndexError):
            return None, None
        values = self.sales_tree.item(sel[0])['values']
        return sale_id, values

    def _view_bill(self, event=None):
        sale_id, values = self._selected()
        if sale_id:
            view_bill_details(self.parent, self.conn, sale_id, values, self.sales_data)

    def _edit_bill(self):
        sale_id, values = self._selected()
        if sale_id:
            edit_bill(self.parent, self.conn, sale_id, values, self.load_sales_history)

    def _print_bill(self):
        sale_id, values = self._selected()
        if sale_id:
            print_bill(self.parent, self.conn, sale_id, values)

    def _save_bill_pdf(self, event=None):
        sale_id, values = self._selected()
        if sale_id:
            save_bill_pdf_a6(self.parent, self.conn, sale_id, values)
        return 'break'

    def _print_sales_1(self, event=None):
        sale_id, values = self._selected()
        if sale_id:
            print_bill_slot_silent(self.parent, self.conn, sale_id, values, 1)
        return 'break'

    def _print_sales_2(self, event=None):
        sale_id, values = self._selected()
        if sale_id:
            print_bill_slot_silent(self.parent, self.conn, sale_id, values, 2)
        return 'break'

    def _print_sales_1_shortcut(self, event=None):
        return self._print_sales_1(event)

    def _print_sales_2_shortcut(self, event=None):
        return self._print_sales_2(event)

    def _print_all_bills(self):
        from ui.sales.print_all_bills_dialog import show_print_all_bills_dialog

        picked = show_print_all_bills_dialog(
            self.parent,
            self.conn,
            default_from=self._get_date_filter(self.from_date),
            default_to=self._get_date_filter(self.to_date),
        )
        if not picked:
            return
        if isinstance(picked, tuple):
            items, paper = picked
        else:
            items, paper = picked, "A6"
        if not items:
            return
        try:
            self.print_all_btn.configure(state='disabled')
        except tk.TclError:
            pass
        print_all_bills_sequential(self.parent, self.conn, items, paper=paper)
        self.parent.after(max(500, len(items) * 400), self._enable_print_all_btn)

    def _enable_print_all_btn(self):
        try:
            if not self._sales_loading:
                self.print_all_btn.configure(state='normal')
        except tk.TclError:
            pass

    def _delete_bill(self):
        sale_id, values = self._selected()
        if sale_id:
            delete_bill(self.conn, sale_id, values, self.load_sales_history)

    # ── Exports ───────────────────────────────────────────────────────────

    def _export_menu(self):
        export_menu(
            parent           = self.parent,
            cursor           = self.cursor,
            from_date_fn     = lambda: self._get_date_filter(self.from_date),
            to_date_fn       = lambda: self._get_date_filter(self.to_date),
            schedule_filter_fn = lambda: self.schedule_filter.get(),
            export_current_view_fn = self._export_current_view,
        )

    def _export_current_view(self):
        from core.export_manager import export_data
        from core.column_config import export_tree_current_view
        cols, rows = export_tree_current_view(self.sales_tree)
        if not rows:
            showinfo("No Records", "No data visible."); return
        export_data(self.parent, 'Sales - Current View', cols, rows, 'sales_current_view')

    # ── Keyboard nav ──────────────────────────────────────────────────────

    def _setup_arrow_nav(self):
        if getattr(self, '_arrow_nav_ready', False):
            return
        from core.focus_chain import wire_focus_ring
        wire_focus_ring([
            self.from_date, self.to_date, self.fy_filter,
            self.customer_filter, self.due_filter, self.schedule_filter,
            self.sort_filter, self.apply_btn, self.clear_btn,
            self.export_btn,
        ])
        fe, te = self._date_entry(self.from_date), self._date_entry(self.to_date)
        fe.bind('<Return>', lambda e: self._focus_date(self.to_date), add='+')
        te.bind('<Return>', lambda e: self.fy_filter.focus(), add='+')
        self._arrow_nav_ready = True

    # ── FY filter ─────────────────────────────────────────────────────────

    @staticmethod
    def _build_fy_list():
        from core.fy_serial import fy_label, fy_start_year_for_date

        cur = fy_start_year_for_date(date.today())
        return list(reversed([fy_label(y) for y in range(2020, cur + 1)]))

    def _apply_fy_filter(self):
        val = self.fy_filter.get().strip()
        if not val or '-' not in val or val not in self._fy_years: return
        try: y = int(val.split('-')[0])
        except ValueError: return
        self._set_date_filter(self.from_date, f"{y}-04-01")
        self._set_date_filter(self.to_date, f"{y+1}-03-31")
        self.apply_filter()

    def _apply_default_history_scope(self) -> bool:
        from core.history_prefs import (
            current_fy_bounds,
            current_fy_label,
            load_history_scope,
        )
        if load_history_scope() != 'current_fy':
            return False
        fd, td = current_fy_bounds()
        self._set_date_filter(self.from_date, fd)
        self._set_date_filter(self.to_date, td)
        try:
            self.fy_filter.set(current_fy_label())
        except Exception:
            pass
        return True
