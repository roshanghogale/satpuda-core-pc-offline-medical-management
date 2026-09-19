"""
ui/purchase/purchase_history.py
────────────────────────────────
Purchase History page — aligned with centralized accounting model.

Data sources (Phase 1 compliance):
  - purchases.total_amount          ← bill value
  - purchases.amount_paid_at_entry  ← payment made at purchase time (write-once)
  - purchase_returns.refund_amount  ← per-bill returns (summed)
  - suppliers.total_due             ← authoritative supplier balance
  - suppliers.total_credit          ← authoritative supplier credit

REMOVED:
  - purchases.total_due             (stale snapshot — not used)
  - purchases.credit_amount         (stale snapshot — not used)
  - mutated purchases.amount_paid   (not used for display)
  - "Paid via Payment" distribution column (misleading — removed)
"""
import tkinter as tk
from tkinter import ttk, messagebox
from core.themed_messagebox import showinfo, showwarning, showerror, askyesno
from datetime import date, datetime
import sqlite3
import logging

from core.alert_colors import get_alert_color
from core.font_config import *
from core.layout_config import PURCHASE_HISTORY_ROWS, SCHEDULES
from core.column_config import apply_column_visibility, all_column_names, is_dashboard_section_visible
from core.record_indicators import (
    extend_columns,
    indicator_column_widths,
    column_heading,
    prepare_tree_row,
    purchase_history_status,
    register_tree_tags,
)
from core.scroll_manager import open_dialog
from core.export_manager import export_data
from core.column_config import export_table
from core.list_sort import SORT_OPTIONS, sort_purchase_history_rows
from widgets.searchable_combo import SearchableCombo
from ui.purchase.purchase_history_edit import open_edit_window, delete_purchase


class PurchaseHistoryPage:

    def __init__(self, parent, conn):
        self.conn   = conn
        self.cursor = conn.cursor()
        self.parent = parent
        self.purchase_data = []
        self._purchase_loading = False
        self._filter_loading = False
        self._tree_populating = False
        self._pending_sync_refresh = False
        self._tree_populate_seq = 0

        self._build_ui()
        self._load_supplier_filter()
        top = self.parent.winfo_toplevel()
        if not getattr(top, '_startup_prewarm', False):
            self._apply_default_history_scope()
            self.parent.after(0, self._schedule_load_purchase_history)
        if not getattr(top, '_startup_prewarm', False):
            self.parent.after(100, self._setup_arrow_nav)
        if not getattr(top, '_startup_prewarm', False):
            self.parent.after(200, lambda: self._focus_date(self.from_date))
        self._register_keyboard()
        self._bus_token = None
        try:
            from core.sync_prefs import is_online_mode
            from core.sync_v3.data_change_bus import subscribe

            if not is_online_mode():
                self._bus_token = subscribe(
                    {"purchases", "supplier_payments", "purchase_returns"},
                    lambda _col: self.ensure_data_loaded(),
                )
                top = self.parent.winfo_toplevel()
                top.bind("<Destroy>", self._on_destroy_bus, add="+")
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

            self._live_token = live_sub(
                {"purchases", "supplier_payments", "purchase_returns"},
                _on_live,
            )
        except Exception:
            pass

    def _on_destroy_bus(self, event=None):
        try:
            if event and event.widget is not self.parent.winfo_toplevel():
                return
        except Exception:
            pass
        if self._bus_token:
            try:
                from core.sync_v3.data_change_bus import unsubscribe

                unsubscribe(self._bus_token)
            except Exception:
                pass
            self._bus_token = None

    def _make_filter_date(self, parent):
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
            page_id='purchase_history',
            first_focus=lambda: self._focus_date(self.from_date),
            on_ctrl_f=lambda: self.supplier_filter.entry.focus_set(),
            on_ctrl_enter=self.apply_filter,
            on_ctrl_shift_c=self._clear_filter_shortcut,
            on_ctrl_e=self._export_menu,
            f2_target=self.purchase_tree,
        )
        self._inner_frame._keyboard_bindings = bindings
        KeyboardRegistry.register_page(self._inner_frame, bindings)

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Fixed layout (no outer canvas): only the Treeview scrolls.
        main_frame = ttk.Frame(self.parent)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self._inner_frame = main_frame

        # Filter bar
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

        ttk.Label(ff, text="Supplier:").grid(row=1, column=0, padx=5, pady=5)
        self.supplier_filter = SearchableCombo(ff, width=20)
        self.supplier_filter.grid(row=1, column=1, padx=5, pady=5)
        self.supplier_filter.entry.bind('<FocusIn>', lambda e: self._load_supplier_filter(), add='+')

        ttk.Label(ff, text="Status:").grid(row=1, column=2, padx=5, pady=5)
        self.due_filter = SearchableCombo(
            ff, values=['Due Only', 'Credit Only', 'Paid / Cleared'], width=15)
        self.due_filter.grid(row=1, column=3, padx=5, pady=5)

        ttk.Label(ff, text="Schedule:").grid(row=1, column=4, padx=5, pady=5)
        self.schedule_filter = SearchableCombo(
            ff, values=[s for s in SCHEDULES if s] + ['Non-Scheduled'], width=15)
        self.schedule_filter.grid(row=1, column=5, padx=5, pady=5)

        self.clear_btn = ttk.Button(ff, text="Clear Filter", command=self.clear_filter)
        self.clear_btn.grid(row=1, column=6, padx=5, pady=5)

        ttk.Label(ff, text="Serial No:").grid(row=2, column=0, padx=5, pady=5)
        self.serial_filter = ttk.Entry(ff, width=18)
        self.serial_filter.grid(row=2, column=1, padx=5, pady=5)

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
        self.supplier_filter.bind_apply_on_select(self.apply_filter)
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
            self.fy_filter, self.supplier_filter, self.due_filter,
            self.schedule_filter,
            on_last_return=lambda: self.serial_filter.focus_set(),
        )
        wire_entry_filter_chain(
            self.serial_filter, self.medicine_filter, self.batch_filter,
            last_action=lambda: self.sort_filter.focus(),
        )
        wire_combo_filter_chain(
            self.sort_filter,
            on_last_return=self.apply_filter,
        )
        for entry in (self.serial_filter, self.medicine_filter, self.batch_filter):
            entry.bind('<Return>', lambda e: self.apply_filter(), add='+')

        # Tree — Phase 3: removed "Paid via Payment" column
        tf = ttk.Frame(main_frame)
        tf.pack(fill=tk.BOTH, expand=True, pady=5)

        self._all_columns = tuple(all_column_names('purchase_history'))
        self._tree_columns = extend_columns(self._all_columns)
        widths = {
            'Purchase No': 48, 'Bill No': 110, 'Date': 95, 'Supplier': 160, 'Phone': 105,
            'Final Amount': 110, 'Paid at Entry': 105, 'Cash Paid': 90, 'Online Paid': 95,
            'Paid via Payment': 115,
            'Returns': 80, 'Entry Due': 90, 'Status': 90, 'Items': 55,
        }
        widths.update(indicator_column_widths())
        self.purchase_tree = ttk.Treeview(
            tf, columns=self._tree_columns, show='headings',
            height=PURCHASE_HISTORY_ROWS, style='Large.Treeview')
        for col in self._tree_columns:
            self.purchase_tree.heading(col, text=column_heading(col))
            self.purchase_tree.column(col, width=widths.get(col, 90))
        apply_column_visibility(self.purchase_tree, 'purchase_history', self._tree_columns)

        vsb = ttk.Scrollbar(tf, orient=tk.VERTICAL,   command=self.purchase_tree.yview)
        hsb = ttk.Scrollbar(tf, orient=tk.HORIZONTAL, command=self.purchase_tree.xview)
        self.purchase_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.purchase_tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        tf.grid_rowconfigure(0, weight=1)
        tf.grid_columnconfigure(0, weight=1)

        register_tree_tags(self.purchase_tree)

        from core.tree_action_menu import setup_tree_actions
        self._action_menu = setup_tree_actions(
            self.parent,
            self.purchase_tree,
            [
                ("Edit Purchase", self.edit_purchase),
                ("Delete Purchase", self.delete_purchase),
            ],
            on_double=self.edit_purchase,
            on_delete=lambda e: self.delete_purchase(),
            escape_to=self.from_date,
        )
        self._ctx = self._action_menu.ctx_menu

        # Summary (below the list, same as before)
        self._show_summary = is_dashboard_section_visible('purchase_summary')
        self.total_purchases_var = tk.StringVar()
        self.total_amount_var    = tk.StringVar()
        self.total_entry_paid_var= tk.StringVar()
        self.total_returns_var   = tk.StringVar()
        self.total_due_var       = tk.StringVar()
        self.total_credit_var    = tk.StringVar()
        self.total_items_var     = tk.StringVar()

        if self._show_summary:
            sf = ttk.LabelFrame(main_frame, text="Purchase Summary")
            sf.pack(fill=tk.X, pady=5)
            for col, (lbl, var, color) in enumerate([
                ("Total Purchases:", self.total_purchases_var, None),
                ("Final Amount:", self.total_amount_var, 'success'),
                ("Paid at Entry:", self.total_entry_paid_var, None),
                ("Total Returns:", self.total_returns_var, 'info'),
                ("Total Due (All Time):", self.total_due_var, 'danger'),
                ("Total Credit (All Time):", self.total_credit_var, 'success'),
                ("Total Items:", self.total_items_var, None),
            ]):
                ttk.Label(sf, text=lbl).grid(row=0, column=col * 2, padx=8, pady=5)
                kw = {'font': (FONT_FAMILY, FONT_SIZE_LABELS, 'bold')}
                if color:
                    kw['foreground'] = get_alert_color(color)
                ttk.Label(sf, textvariable=var, **kw).grid(
                    row=0, column=col * 2 + 1, padx=8, pady=5)

            # Keep summary on-screen: pin to bottom; tree takes remaining space.
            ff.pack_forget()
            tf.pack_forget()
            sf.pack_forget()
            sf.pack(side=tk.BOTTOM, fill=tk.X, pady=5)
            ff.pack(side=tk.TOP, fill=tk.X, pady=5)
            tf.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=5)

    # ── Data loading ──────────────────────────────────────────────────────

    def _load_supplier_filter(self):
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.online_catalog import supplier_names
                self.supplier_filter.configure(values=supplier_names())
                return
        except Exception:
            pass
        try:
            self.cursor.execute(
                "SELECT DISTINCT name FROM suppliers ORDER BY name COLLATE NOCASE")
            self.supplier_filter.configure(
                values=[r[0] for r in self.cursor.fetchall()])
        except Exception:
            pass

    def _sorted_purchase_data(self, rows=None):
        return sort_purchase_history_rows(
            rows if rows is not None else self.purchase_data,
            self.sort_filter.get(),
        )

    def _resort_visible(self):
        if self._purchase_loading or self._filter_loading or not self.purchase_data:
            return
        paid_map = self._compute_paid_via_payment_map(self.cursor, self.purchase_data)
        self.purchase_data = self._sorted_purchase_data()
        self._redisplay_purchases(self.purchase_data, paid_map)

    def _base_query(self, use_schedule_join=False):
        """
        Fetches final_amount (after discount/rounding), paid_at_entry,
        paid_via_payment (from supplier_payments), and returns per bill.
        """
        pay_sub = ("(SELECT COALESCE(SUM(sp.amount),0) FROM supplier_payments sp "
                   " WHERE sp.supplier_id=p.supplier_id "
                   " AND sp.id <= (SELECT COALESCE(MAX(sp2.id),0) FROM supplier_payments sp2 "
                   "               WHERE sp2.supplier_id=p.supplier_id))")
        # Simpler: just show total payments for the supplier (not per-bill distributed)
        pay_sub = ("COALESCE((SELECT SUM(sp.amount) FROM supplier_payments sp "
                   "          WHERE sp.supplier_id=p.supplier_id), 0)")
        if use_schedule_join:
            return (
                "SELECT DISTINCT COALESCE(p.bill_number,p.purchase_no), p.purchase_date,"
                " COALESCE(s.name, '(unknown)'), COALESCE(s.phone, ''),"
                " COALESCE(p.final_amount, p.total_amount),"
                " COALESCE("
                "NULLIF(COALESCE(p.cash_paid_at_entry,0)+COALESCE(p.online_paid_at_entry,0),0),"
                "NULLIF(p.amount_paid_at_entry,0),p.amount_paid,0),"
                " COALESCE(p.cash_paid_at_entry, 0),"
                " COALESCE(p.online_paid_at_entry, 0),"
                " COALESCE((SELECT SUM(pr.refund_amount) FROM purchase_returns pr"
                "           WHERE pr.purchase_id=p.id), 0),"
                " (SELECT COUNT(*) FROM purchase_items WHERE purchase_id=p.id),"
                " p.id, p.account_cleared,"
                " p.supplier_id, p.purchase_no"
                " FROM purchases p"
                " LEFT JOIN suppliers s ON p.supplier_id=s.id"
                " LEFT JOIN purchase_items pi ON p.id=pi.purchase_id"
                " WHERE COALESCE(p.deleted, 0) = 0"
            )
        return (
            "SELECT COALESCE(p.bill_number,p.purchase_no), p.purchase_date,"
            " COALESCE(s.name, '(unknown)'), COALESCE(s.phone, ''),"
            " COALESCE(p.final_amount, p.total_amount),"
            " COALESCE("
            "NULLIF(COALESCE(p.cash_paid_at_entry,0)+COALESCE(p.online_paid_at_entry,0),0),"
            "NULLIF(p.amount_paid_at_entry,0),p.amount_paid,0),"
            " COALESCE(p.cash_paid_at_entry, 0),"
            " COALESCE(p.online_paid_at_entry, 0),"
            " COALESCE((SELECT SUM(pr.refund_amount) FROM purchase_returns pr"
            "           WHERE pr.purchase_id=p.id AND COALESCE(pr.deleted,0)=0), 0),"
            " COUNT(pi.id),"
            " p.id, p.account_cleared,"
            " p.supplier_id, p.purchase_no"
            " FROM purchases p"
            " LEFT JOIN suppliers s ON p.supplier_id=s.id"
            " LEFT JOIN purchase_items pi ON p.id=pi.purchase_id"
            " WHERE COALESCE(p.deleted, 0) = 0"
        )

    def _compute_paid_via_payment_map(self, cursor, data):
        from core.purchase_service import compute_paid_via_payments_by_bill
        supplier_ids = list({p[12] for p in data if len(p) > 12 and p[12]})
        # Prefer the same connection the fetch used (cursor.connection).
        conn = getattr(cursor, 'connection', None) or self.conn
        return compute_paid_via_payments_by_bill(conn, supplier_ids)

    def _resolve_active_date_filters(self):
        fd = self._get_date_filter(self.from_date)
        td = self._get_date_filter(self.to_date)
        from core.history_prefs import resolve_history_dates
        return resolve_history_dates(fd, td)

    def _fetch_purchase_rows(self):
        fd, td, _applied = self._resolve_active_date_filters()
        return self._fetch_filtered_purchase_rows((fd, td, '', '', '', '', '', ''))

    def _fetch_filtered_purchase_rows(self, params):
        import sqlite3
        from core.background_workers import db_path_from_conn
        fd, td, sup, due, sch, serial, med_name, batch_no = params

        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core import store_query_client as sq
                from core.fy_serial import display_purchase_no

                q = (sup or serial or "").strip()
                if not (fd or td):
                    from core.history_prefs import current_fy_bounds
                    fd, td = current_fy_bounds()
                sch_q = "" if not sch or sch == "All" else sch
                data = sq.list_purchases(
                    from_date=fd or "",
                    to_date=td or "",
                    q=q,
                    schedule=sch_q,
                    medicine=(med_name or "").strip(),
                    batch=(batch_no or "").strip(),
                    limit=5000,
                )
                from core.online_mutation_queue import (
                    overlay_purchase_dicts,
                    merge_server_rows,
                )

                merged = merge_server_rows(
                    list(data.get("rows") or []),
                    overlay_purchase_dicts(),
                    collection="purchases",
                )
                from core.due_fifo import (
                    fifo_paid_via_by_bill,
                    purchase_entry_paid,
                    purchase_remaining_and_via,
                )
                from core.online_mutation_queue import overlay_supplier_payment_dicts

                pays = []
                try:
                    pays = list(
                        (sq.list_supplier_payments(limit=5000) or {}).get("rows") or []
                    )
                except Exception:
                    pays = []
                pays = merge_server_rows(
                    pays,
                    overlay_supplier_payment_dicts(),
                    collection="supplier_payments",
                )
                fifo_via = fifo_paid_via_by_bill(merged, pays)

                rows_out = []
                paid_map = {}
                for r in merged:
                    name = r.get("supplier_name") or "(unknown)"
                    if sup and sup.lower() not in str(name).lower():
                        continue
                    pno = str(r.get("purchase_no") or "")
                    bno = str(r.get("bill_number") or "") or display_purchase_no(pno)
                    if serial and serial.lower() not in pno.lower() and serial.lower() not in bno.lower():
                        continue
                    final_amt = float(r.get("final_amount") or r.get("total_amount") or 0)
                    entry_paid = purchase_entry_paid(r)
                    returns = float(
                        r.get("returns_amount")
                        or r.get("refund_amount")
                        or r.get("returns")
                        or 0
                    )
                    pid = int(r.get("id") or r.get("local_id") or 0)
                    remaining, via_payment = purchase_remaining_and_via(
                        r, fifo_via.get(pid)
                    )
                    if due == 'Due Only' and remaining <= 0:
                        continue
                    if due == 'Credit Only' and entry_paid + returns + via_payment <= final_amt:
                        continue
                    if due == 'Paid / Cleared' and remaining > 0.01:
                        continue
                    paid_map[pid] = via_payment
                    rows_out.append((
                        bno,
                        r.get("purchase_date"),
                        name,
                        "",
                        final_amt,
                        entry_paid,
                        float(r.get("cash_paid_at_entry") or 0),
                        float(r.get("online_paid_at_entry") or 0),
                        returns,
                        int(r.get("item_count") or 0),
                        pid,
                        1 if remaining <= 0.01 else 0,
                        int(r.get("supplier_id") or 0),
                        pno,
                    ))
                return rows_out, paid_map
        except Exception as exc:
            logging.warning("purchase history server fetch: %s", exc)
            try:
                from core.sync_prefs import is_online_mode

                if is_online_mode():
                    # Never paint empty local SQLite in Online — peers save to server only.
                    return [], {}
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
            q = self._base_query(use_schedule_join=use_item_join)
            qparams = []
            if fd:
                q += " AND p.purchase_date>=?"
                qparams.append(fd)
            if td:
                q += " AND p.purchase_date<=?"
                qparams.append(td)
            if sup:
                q += " AND s.name LIKE ?"
                qparams.append(f'%{sup}%')
            if serial:
                q += (
                    " AND ("
                    " CAST(COALESCE(p.fy_serial, p.id) AS TEXT) LIKE ?"
                    " OR COALESCE(p.purchase_no,'') LIKE ?"
                    " OR COALESCE(p.bill_number,'') LIKE ?"
                    ")"
                )
                like = f'%{serial}%'
                qparams.extend([like, like, like])
            if use_item_join:
                if sch and sch != 'All':
                    if sch == 'Non-Scheduled':
                        q += " AND (pi.schedule IS NULL OR pi.schedule='')"
                    else:
                        q += " AND pi.schedule=?"
                        qparams.append(sch)
                if med_name:
                    q += (
                        " AND EXISTS ("
                        " SELECT 1 FROM purchase_items pi2"
                        " JOIN medicines m2 ON m2.id=pi2.medicine_id"
                        " WHERE pi2.purchase_id=p.id AND m2.name LIKE ?"
                        ")"
                    )
                    qparams.append(f'%{med_name}%')
                if batch_no:
                    q += (
                        " AND EXISTS ("
                        " SELECT 1 FROM purchase_items pi3"
                        " WHERE pi3.purchase_id=p.id"
                        " AND COALESCE(pi3.batch_no,'') LIKE ?"
                        ")"
                    )
                    qparams.append(f'%{batch_no}%')
            if not use_item_join:
                q += " GROUP BY p.id"
            q += " ORDER BY p.purchase_date DESC, p.id DESC"
            cur.execute(q, qparams)
            data = cur.fetchall()
            if due:
                filtered = []
                for p in data:
                    final_amt = float(p[4] or 0)
                    entry_paid = float(p[5] or 0)
                    returns = float(p[8] or 0)
                    entry_due = round(max(0.0, final_amt - entry_paid - returns), 2)
                    if due == 'Due Only' and entry_due > 0:
                        filtered.append(p)
                    elif due == 'Credit Only' and entry_paid + returns > final_amt:
                        filtered.append(p)
                    elif due == 'Paid / Cleared' and entry_due == 0:
                        filtered.append(p)
                data = filtered
            return data, self._compute_paid_via_payment_map(cur, data)
        finally:
            if own_conn:
                conn.close()

    def _schedule_load_purchase_history(self):
        if self._purchase_loading:
            return
        from core.background_workers import run_in_thread
        self._purchase_loading = True
        run_in_thread(
            self._fetch_purchase_rows,
            name='PurchaseHistoryLoad',
            root=self.parent,
            on_success=self._apply_purchase_rows,
            on_error=self._on_purchase_load_error,
        )

    def _on_purchase_load_error(self, exc):
        logging.error(f"Error loading purchase history: {exc}")
        self.purchase_data = []
        self._purchase_loading = False
        showerror("Database Error", "Failed to load purchase history.")

    def _apply_purchase_rows(self, payload):
        data, paid_map = payload
        self.purchase_data = self._sorted_purchase_data(data or [])
        self._redisplay_purchases(self.purchase_data, paid_map)
        self.update_summary()
        self._purchase_loading = False

    def _redisplay_purchases(self, data, paid_map):
        from core.ui_tree_loader import (
            populate_tree_batched,
            restore_tree_yview,
            save_tree_yview,
            sync_tree_by_iid,
        )

        self._tree_populate_seq = getattr(self, '_tree_populate_seq', 0) + 1
        seq = self._tree_populate_seq
        self._tree_populating = True
        yview = save_tree_yview(self.purchase_tree)
        paid_via_payment_map = paid_map or {}

        painted = []
        for p in data:
            payload = self._purchase_row_payload(p, paid_via_payment_map)
            if payload:
                painted.append(payload)

        mode = sync_tree_by_iid(self.purchase_tree, painted)

        def _done():
            self._tree_populating = False
            restore_tree_yview(self.purchase_tree, yview)
            if self._pending_sync_refresh:
                self._pending_sync_refresh = False
                try:
                    self.parent.after(200, self.queue_sync_refresh)
                except Exception:
                    pass

        if mode in ("noop", "diff"):
            _done()
            return

        kids = self.purchase_tree.get_children()
        if kids:
            self.purchase_tree.delete(*kids)

        def _insert_one(p):
            self._insert_purchase_row(p, paid_via_payment_map)

        populate_tree_batched(
            self.purchase_tree,
            data,
            _insert_one,
            self.parent,
            batch_size=80,
            on_done=_done,
            should_stop=lambda: seq != self._tree_populate_seq,
        )

    def load_purchase_history(self):
        self._pending_sync_refresh = False
        self._schedule_load_purchase_history()

    def queue_sync_refresh(self):
        if self._purchase_loading or self._filter_loading or self._tree_populating:
            self._pending_sync_refresh = True
            return
        self._schedule_load_purchase_history()

    def ensure_data_loaded(self):
        # Reload only when empty or marked dirty by sync / pending refresh.
        if self._purchase_loading:
            self._pending_sync_refresh = True
            return
        if self.purchase_data and not self._pending_sync_refresh:
            return
        self._pending_sync_refresh = False
        self._schedule_load_purchase_history()

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

    def apply_filter(self):
        if self._filter_loading:
            return
        params = (
            self._get_date_filter(self.from_date),
            self._get_date_filter(self.to_date),
            self.supplier_filter.get().strip(),
            self.due_filter.get(),
            self.schedule_filter.get(),
            (self.serial_filter.get() or '').strip(),
            (self.medicine_filter.get() or '').strip(),
            (self.batch_filter.get() or '').strip(),
        )
        self._filter_loading = True
        from core.background_workers import run_in_thread
        run_in_thread(
            lambda: self._fetch_filtered_purchase_rows(params),
            name='PurchaseHistoryFilter',
            root=self.parent,
            on_success=self._apply_filter_rows,
            on_error=self._on_filter_load_error,
        )

    def _on_filter_load_error(self, exc):
        self._filter_loading = False
        logging.error(f"Error filtering purchase history: {exc}")
        showerror("Database Error", "Failed to filter purchase history.")

    def _apply_filter_rows(self, payload):
        data, paid_map = payload
        self.purchase_data = self._sorted_purchase_data(data or [])
        self._redisplay_purchases(self.purchase_data, paid_map)
        self.update_summary(data)
        self._filter_loading = False

    def _cell(self, item_id, col_name: str):
        try:
            idx = self._tree_columns.index(col_name)
            vals = self.purchase_tree.item(item_id)['values']
            return vals[idx] if idx < len(vals) else ''
        except (ValueError, tk.TclError, IndexError):
            return ''

    def _purchase_row_payload(self, p, paid_via_payment_map):
        from core.purchase_service import parse_purchase_entry_serial

        final_amt  = float(p[4] or 0)
        entry_paid = float(p[5] or 0)
        cash_paid  = float(p[6] or 0)
        online_paid= float(p[7] or 0)
        split_paid = round(cash_paid + online_paid, 2)
        if entry_paid <= 0.01 and split_paid > 0.01:
            entry_paid = split_paid
        if cash_paid <= 0 and online_paid <= 0 and entry_paid > 0:
            cash_paid = entry_paid
        returns    = float(p[8] or 0)
        item_count = p[9]
        purchase_id= p[10]
        purchase_no = p[13] if len(p) > 13 else None
        via_payment= paid_via_payment_map.get(purchase_id, 0.0)
        sr = parse_purchase_entry_serial(purchase_no, purchase_id=purchase_id)

        entry_due = round(max(0.0, final_amt - entry_paid - via_payment - returns), 2)

        if entry_due <= 0.01:
            status = "Cleared"
        elif entry_paid + via_payment > 0:
            status = "Partial"
        else:
            status = "Due"

        vals = (
            sr,
            p[0], p[1], p[2], p[3],
            f"₹{final_amt:.2f}",
            f"₹{entry_paid:.2f}" if entry_paid else "-",
            f"₹{cash_paid:.2f}" if cash_paid else "-",
            f"₹{online_paid:.2f}" if online_paid else "-",
            f"₹{via_payment:.2f}" if via_payment else "-",
            f"₹{returns:.2f}"    if returns    else "-",
            f"₹{entry_due:.2f}"  if entry_due  else "-",
            status,
            item_count,
        )
        status_key = purchase_history_status(entry_due, entry_paid, via_payment)
        values, tags = prepare_tree_row(vals, status_key, badge_text=status.upper())
        return str(purchase_id), values, tags

    def _insert_purchase_row(self, p, paid_via_payment_map):
        payload = self._purchase_row_payload(p, paid_via_payment_map)
        if not payload:
            return
        iid, values, tags = payload
        self.purchase_tree.insert(
            '', tk.END, iid=iid, values=values, tags=tags)

    def _populate_tree(self, data, paid_via_payment_map=None):
        """
        Shows final_amount (after discount/rounding), paid_at_entry,
        paid_via_payment (supplier payments distributed oldest-first),
        and entry_due = final_amount - entry_paid - paid_via_payment - returns.
        """
        if paid_via_payment_map is None:
            paid_via_payment_map = self._compute_paid_via_payment_map(self.cursor, data)

        for p in data:
            self._insert_purchase_row(p, paid_via_payment_map)

    def clear_filter(self):
        self._clear_date_filter(self.from_date)
        self._clear_date_filter(self.to_date)
        for combo in (self.supplier_filter, self.due_filter,
                      self.schedule_filter, self.fy_filter):
            try:
                combo.hide_list()
                combo.set('')
            except Exception:
                pass
        for entry in (self.serial_filter, self.medicine_filter, self.batch_filter):
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
            self.load_purchase_history()

    def _clear_filter_shortcut(self, event=None):
        self.clear_filter()
        return 'break'

    def update_summary(self, data=None):
        """Phase 7: summary uses filtered purchases and suppliers table balances."""
        if not getattr(self, '_show_summary', True):
            return
        if data is None:
            data = self.purchase_data

        n            = len(data)
        total        = sum(float(p[4] or 0) for p in data)   # final_amount
        entry_paid   = sum(float(p[5] or 0) for p in data)
        # Tuple: [6]=cash, [7]=online, [8]=returns, [9]=item_count, [10]=id
        total_returns= sum(float(p[8] or 0) for p in data)
        items        = sum(int(p[9] or 0) for p in data)
        all_ids = []
        for p in data:
            try:
                all_ids.append(int(p[10]))
            except (TypeError, ValueError, IndexError):
                pass

        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core import store_query_client as sq
                rem = sq.purchases_summary(
                    from_date=self._get_date_filter(self.from_date) or "",
                    to_date=self._get_date_filter(self.to_date) or "",
                    ids=all_ids,
                ) or {}
                # Row totals match Offline; due/credit from suppliers (global).
                self.total_purchases_var.set(str(n))
                self.total_amount_var.set(f"₹{total:.2f}")
                self.total_entry_paid_var.set(f"₹{entry_paid:.2f}")
                ret_val = rem.get("total_returns")
                if ret_val is None:
                    ret_val = total_returns
                self.total_returns_var.set(f"₹{float(ret_val):.2f}")
                self.total_due_var.set(f"₹{float(rem.get('supplier_due') or 0):.2f}")
                self.total_credit_var.set(f"₹{float(rem.get('supplier_credit') or 0):.2f}")
                self.total_items_var.set(str(items))
                return
        except Exception as exc:
            print(f"[purchase history] online summary: {exc}")

        # Phase 7: due/credit from suppliers table — single source of truth
        self.cursor.execute(
            "SELECT COALESCE(SUM(total_due),0), COALESCE(SUM(total_credit),0) "
            "FROM suppliers")
        row = self.cursor.fetchone()
        global_due    = float(row[0] or 0)
        global_credit = float(row[1] or 0)

        self.total_purchases_var.set(str(n))
        self.total_amount_var.set(f"₹{total:.2f}")
        self.total_entry_paid_var.set(f"₹{entry_paid:.2f}")
        self.total_returns_var.set(f"₹{total_returns:.2f}")
        self.total_due_var.set(f"₹{global_due:.2f}")
        self.total_credit_var.set(f"₹{global_credit:.2f}")
        self.total_items_var.set(str(items))

    # ── Context menu / actions ────────────────────────────────────────────

    def _show_ctx(self, event):
        if self.purchase_tree.selection():
            self._ctx.post(event.x_root, event.y_root)

    def _selected_id(self):
        sel = self.purchase_tree.selection()
        if not sel:
            return None
        try:
            return int(sel[0])
        except (ValueError, IndexError):
            return None

    def edit_purchase(self, event=None):
        sel = self.purchase_tree.selection()
        if not sel:
            return
        purchase_id = self._selected_id()
        if not purchase_id:
            showerror("Error", "Purchase not found.")
            return
        bill_label = self._cell(sel[0], 'Bill No')
        open_edit_window(self.parent, self.conn, purchase_id,
                         bill_label, self.load_purchase_history)

    def delete_purchase(self):
        sel = self.purchase_tree.selection()
        if not sel:
            return
        purchase_id = self._selected_id()
        if not purchase_id:
            showerror("Error", "Purchase not found.")
            return
        bill_label = self._cell(sel[0], 'Bill No')
        delete_purchase(self.conn, purchase_id, bill_label, self.load_purchase_history)

    # ── Exports ───────────────────────────────────────────────────────────

    def _export_menu(self):
        from core.export_manager import show_export_option_dialog
        show_export_option_dialog(self.parent, "Export Purchase Reports", [
            ("Current View",        self._export_current_view),
            ("Purchase Register",   self._export_purchase_register),
            ("Monthly Summary",     self._export_monthly_summary),
            ("Supplier Due Report", self._export_supplier_due),
            ("GST Purchase Report", self._export_gst_purchase),
        ], width=320)

    def _get_date_range(self):
        return self._get_date_filter(self.from_date), self._get_date_filter(self.to_date)

    def _export_current_view(self):
        from core.column_config import export_tree_current_view
        cols, rows = export_tree_current_view(self.purchase_tree)
        if not rows:
            showinfo("No Records", "No data visible.")
            return
        export_data(self.parent, 'Purchases - Current View',
                    cols, rows, 'purchases_current_view')

    def _export_purchase_register(self):
        """Phase 1: uses amount_paid_at_entry, not stale snapshot columns."""
        fd, td = self._get_date_range()
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core import store_query_client as sq
                from core.fy_serial import display_purchase_no
                data = sq.list_purchases(from_date=fd or "", to_date=td or "", limit=5000)
                rows = []
                for r in data.get("rows") or []:
                    pno = str(r.get("purchase_no") or "")
                    bno = str(r.get("bill_number") or "") or display_purchase_no(pno)
                    rows.append((
                        bno,
                        r.get("purchase_date") or "",
                        r.get("supplier_name") or "",
                        r.get("supplier_phone") or r.get("phone") or "",
                        float(r.get("final_amount") or r.get("total_amount") or 0),
                        float(r.get("amount_paid_at_entry") or r.get("amount_paid") or 0),
                        float(r.get("cash_paid_at_entry") or 0),
                        float(r.get("online_paid_at_entry") or 0),
                        0.0,
                    ))
                if not rows:
                    showinfo("No Records", "No purchases found.")
                    return
                export_table(self.parent, 'Purchase Register',
                             ['Bill No', 'Date', 'Supplier', 'Phone',
                              'Final Amount', 'Paid at Entry', 'Cash Paid', 'Online Paid', 'Returns'],
                             rows, 'purchase_register', 'purchase_history', 'purchase_register')
                return
        except Exception as exc:
            logging.warning("purchase register online export: %s", exc)
        q = """SELECT COALESCE(p.bill_number,p.purchase_no), p.purchase_date,
                      s.name, s.phone, p.total_amount,
                      COALESCE(p.amount_paid_at_entry, p.amount_paid, 0),
                      COALESCE(p.cash_paid_at_entry, 0),
                      COALESCE(p.online_paid_at_entry, 0),
                      COALESCE((SELECT SUM(pr.refund_amount) FROM purchase_returns pr
                                WHERE pr.purchase_id=p.id), 0)
               FROM purchases p JOIN suppliers s ON p.supplier_id=s.id WHERE 1=1"""
        params = []
        if fd: q += ' AND p.purchase_date>=?'; params.append(fd)
        if td: q += ' AND p.purchase_date<=?'; params.append(td)
        q += ' ORDER BY p.purchase_date DESC'
        self.cursor.execute(q, params)
        rows = self.cursor.fetchall()
        if not rows:
            showinfo("No Records", "No purchases found.")
            return
        export_table(self.parent, 'Purchase Register',
                     ['Bill No', 'Date', 'Supplier', 'Phone',
                      'Final Amount', 'Paid at Entry', 'Cash Paid', 'Online Paid', 'Returns'],
                     rows, 'purchase_register', 'purchase_history', 'purchase_register')

    def _export_monthly_summary(self):
        fd, td = self._get_date_range()
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core import store_query_client as sq
                data = sq.list_purchases(from_date=fd or "", to_date=td or "", limit=5000)
                buckets = {}
                for r in data.get("rows") or []:
                    ym = str(r.get("purchase_date") or "")[:7]
                    if not ym:
                        continue
                    b = buckets.setdefault(ym, [0, 0.0, 0.0])
                    b[0] += 1
                    b[1] += float(r.get("final_amount") or r.get("total_amount") or 0)
                    b[2] += float(r.get("amount_paid_at_entry") or r.get("amount_paid") or 0)
                if not buckets:
                    showinfo("No Records", "No purchases found.")
                    return

                def fmt(ym):
                    try:
                        return datetime.strptime(ym, '%Y-%m').strftime('%b-%Y')
                    except Exception:
                        return ym

                rows = [[fmt(k), v[0], f'{v[1]:.2f}', f'{v[2]:.2f}']
                        for k, v in sorted(buckets.items(), reverse=True)]
                rows.append(['TOTAL',
                             sum(v[0] for v in buckets.values()),
                             f'{sum(v[1] for v in buckets.values()):.2f}',
                             f'{sum(v[2] for v in buckets.values()):.2f}'])
                export_table(self.parent, 'Monthly Purchase Summary',
                              ['Month', 'Purchases', 'Final Amount', 'Paid at Entry'],
                              rows, 'monthly_purchase_summary', 'purchase_history', 'monthly_summary')
                return
        except Exception as exc:
            logging.warning("purchase monthly online export: %s", exc)
        q = """SELECT strftime('%Y-%m',p.purchase_date), COUNT(*),
                      SUM(p.total_amount),
                      SUM(COALESCE(p.amount_paid_at_entry, p.amount_paid, 0))
               FROM purchases p WHERE 1=1"""
        params = []
        if fd: q += ' AND p.purchase_date>=?'; params.append(fd)
        if td: q += ' AND p.purchase_date<=?'; params.append(td)
        q += " GROUP BY strftime('%Y-%m',p.purchase_date) ORDER BY 1 DESC"
        self.cursor.execute(q, params)
        raw = self.cursor.fetchall()
        if not raw:
            showinfo("No Records", "No purchases found.")
            return

        def fmt(ym):
            try:
                return datetime.strptime(ym, '%Y-%m').strftime('%b-%Y')
            except Exception:
                return ym

        rows = [[fmt(r[0]), r[1], f'{r[2]:.2f}', f'{r[3]:.2f}'] for r in raw]
        rows.append(['TOTAL',
                     sum(r[1] for r in raw),
                     f'{sum(r[2] for r in raw):.2f}',
                     f'{sum(r[3] for r in raw):.2f}'])
        export_table(self.parent, 'Monthly Purchase Summary',
                      ['Month', 'Purchases', 'Final Amount', 'Paid at Entry'],
                      rows, 'monthly_purchase_summary', 'purchase_history', 'monthly_summary')

    def _export_supplier_due(self):
        """Phase 2: reads from suppliers table — single source of truth."""
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.online_catalog import suppliers as oc_suppliers
                rows = []
                for s in oc_suppliers():
                    due = float(s.get("total_due") or 0)
                    if due <= 0:
                        continue
                    rows.append((
                        s.get("name") or "",
                        s.get("phone") or "",
                        due,
                        float(s.get("total_credit") or s.get("credit") or 0),
                    ))
                rows.sort(key=lambda r: float(r[2]), reverse=True)
                if not rows:
                    showinfo("No Records", "No outstanding dues.")
                    return
                export_table(self.parent, 'Supplier Due Report',
                              ['Name', 'Phone', 'Total Due', 'Credit'],
                              rows, 'supplier_due_report', 'suppliers', 'supplier_due')
                return
        except Exception as exc:
            logging.warning("supplier due online export: %s", exc)
        self.cursor.execute("""
            SELECT s.name, s.phone,
                   COALESCE(s.total_due, 0), COALESCE(s.total_credit, 0)
            FROM suppliers s
            WHERE s.total_due > 0
            ORDER BY s.total_due DESC
        """)
        rows = self.cursor.fetchall()
        if not rows:
            showinfo("No Records", "No outstanding dues.")
            return
        export_table(self.parent, 'Supplier Due Report',
                      ['Name', 'Phone', 'Total Due', 'Credit'],
                      rows, 'supplier_due_report', 'suppliers', 'supplier_due')

    def _export_gst_purchase(self):
        fd, td = self._get_date_range()
        try:
            from core.sync_prefs import is_online_mode
            if is_online_mode():
                from core.desktop_export_service import _purchase_export
                data = _purchase_export(self.conn, "gst_purchase", fd or "", td or "")
                if data.get("error"):
                    showinfo("GST Purchase Report", data["error"])
                    return
                rows = data.get("rows") or []
                if not rows:
                    showinfo("No Records", "No items found.")
                    return
                export_table(
                    self.parent,
                    "GST Purchase Report",
                    data.get("columns")
                    or [
                        "Bill No",
                        "Date",
                        "Supplier",
                        "Medicine",
                        "HSN",
                        "GST%",
                        "Qty",
                        "Rate",
                        "Amount",
                    ],
                    rows,
                    "gst_purchase_report",
                    "purchase_history",
                    "gst_purchase",
                )
                return
        except Exception as exc:
            showerror("GST Purchase Report", str(exc))
            return
        q = """SELECT COALESCE(p.bill_number,p.purchase_no), p.purchase_date,
                      s.name, m.name, pi.hsn_code,
                      COALESCE(pi.gst_pct,pi.gst_value,0), pi.qty, pi.rate,
                      COALESCE(pi.item_amount,pi.amount,0)
               FROM purchase_items pi
               JOIN purchases p ON pi.purchase_id=p.id
               JOIN suppliers s ON p.supplier_id=s.id
               JOIN medicines m ON pi.medicine_id=m.id WHERE 1=1"""
        params = []
        if fd: q += ' AND p.purchase_date>=?'; params.append(fd)
        if td: q += ' AND p.purchase_date<=?'; params.append(td)
        q += ' ORDER BY p.purchase_date DESC'
        self.cursor.execute(q, params)
        rows = self.cursor.fetchall()
        if not rows:
            showinfo("No Records", "No items found.")
            return
        export_table(self.parent, 'GST Purchase Report',
                      ['Bill No', 'Date', 'Supplier', 'Medicine',
                       'HSN', 'GST%', 'Qty', 'Rate', 'Amount'],
                      rows, 'gst_purchase_report', 'purchase_history', 'gst_purchase')

    # ── Keyboard nav ──────────────────────────────────────────────────────

    def _setup_arrow_nav(self):
        if getattr(self, '_arrow_nav_ready', False):
            return
        from core.focus_chain import wire_focus_ring
        wire_focus_ring([
            self.from_date, self.to_date, self.fy_filter,
            self.supplier_filter, self.due_filter, self.schedule_filter,
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
        if not val or '-' not in val or val not in self._fy_years:
            return
        try:
            y = int(val.split('-')[0])
        except ValueError:
            return
        self._set_date_filter(self.from_date, f"{y}-04-01")
        self._set_date_filter(self.to_date, f"{y + 1}-03-31")
        self.apply_filter()

    def _apply_default_history_scope(self) -> bool:
        from core.history_prefs import current_fy_bounds, current_fy_label, load_history_scope
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
