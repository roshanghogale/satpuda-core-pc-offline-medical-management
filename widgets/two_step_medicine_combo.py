import tkinter as tk
try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk
from core.font_config import *
from core.layout_config import is_strip_count_type
import os
import sqlite3
import threading

class TwoStepMedicineCombo(ttk.Frame):
    def __init__(self, master, conn, width=60, *args, **kwargs):
        super().__init__(master, *args, **kwargs)
        
        self.conn = conn
        self.cursor = conn.cursor()
        self.width = width
        self.selected_medicine = None
        self.next_focus_widget = None
        
        # Step 1: Medicine name selection
        self.step1_var = tk.StringVar()
        self.step1_entry = ttk.Entry(self, textvariable=self.step1_var, width=width)
        self.step1_entry.pack(fill=tk.X)
        
        # Step 1 treeview (medicine names) — defer toplevel lookup until after pack/grid
        step1_columns = ('name', 'pack_size', 'stock', 'mrp', 'schedule')
        self._step1_columns = step1_columns
        self.step1_tree = None  # created lazily in _init_trees()
        self._step2_columns = ('batch', 'pack', 'expiry', 'stock', 'rate', 'mrp', 'manufacturer', 'schedule')
        self.step2_tree = None  # created lazily in _init_trees()
        self.after(0, self._init_trees)
        self.step1_visible = False
        self.step2_visible = False
        self.medicine_names = []
        self.filtered_medicines = []
        self.variants = []
        self._reserved_by_id = {}      # medicine_id -> qty already in current bill
        self._filter_pending = False   # debounce flag
        self._search_gen = 0
        self.bill_date_getter = None   # callable returning date for expiry-as-of filter
        try:
            from core.background_workers import db_path_from_conn
            self._db_path = db_path_from_conn(conn)
        except Exception:
            self._db_path = ""
        # Bind events — do NOT trace step1_var; drive filtering from key events only
        self.step1_entry.bind("<KeyRelease>", self.on_step1_key)
        self.step1_entry.bind("<Down>", self.on_step1_down)
        self.step1_entry.bind("<Up>", self.on_step1_up)
        self.step1_entry.bind("<Return>", self.on_step1_return)
        self.step1_entry.bind("<Tab>", self.on_step1_tab)
        self.step1_entry.bind("<FocusIn>", self.on_step1_focus_in)
        self.step1_entry.bind("<FocusOut>", self.on_step1_focus_out)
        self.step1_entry.bind("<Escape>", self.on_step1_escape)
        # Close dropdowns when clicking outside
        # Bind click-outside on the toplevel so each instance checks its own widgets
        self.winfo_toplevel().bind("<Button-1>", self.on_click_outside, add="+")
        self.load_medicine_names()
        self.after(0, self._bind_global_return)
        # Hide dropdowns when this widget is hidden or destroyed
        self.bind("<Unmap>", lambda e: (self.hide_step1(), self.hide_step2()))
        self.bind("<Destroy>", lambda e: (self.hide_step1(), self.hide_step2()))
        self.after(0, self._bind_ancestor_unmap)

    def _bind_global_return(self):
        """Bind global Return to the correct toplevel after widget is placed"""
        try:
            self.winfo_toplevel().bind("<Return>", self.on_global_return, add="+")
        except tk.TclError:
            pass

    def _bind_ancestor_unmap(self):
        """Walk up the widget tree and bind <Unmap> on every ancestor frame."""
        try:
            w = self.master
            toplevel = self.winfo_toplevel()
            while w and w is not toplevel:
                w.bind("<Unmap>", lambda e: (self.hide_step1(), self.hide_step2()), add="+")
                w = w.master
        except Exception:
            pass

    def _widget_is_or_inside(self, widget, container):
        if container is None:
            return False
        w = widget
        while w is not None:
            try:
                if w is container:
                    return True
                w = w.master
            except tk.TclError:
                break
        return False

    def _is_viewable(self):
        try:
            if not self.winfo_exists() or not self.step1_entry.winfo_exists():
                return False
            return bool(self.step1_entry.winfo_ismapped())
        except tk.TclError:
            return False

    def _resolve_db_path(self) -> str:
        path = (self._db_path or "").strip()
        if path and os.path.isfile(path):
            return path
        try:
            from core.background_workers import db_path_from_conn
            path = db_path_from_conn(self.conn) or ""
            if path:
                self._db_path = path
            return path
        except Exception:
            return ""

    def _query_medicines_sync(self, search: str):
        """Run inventory search; Online uses server catalog (no local store DB)."""
        try:
            from core.sync_prefs import is_online_mode

            if is_online_mode():
                from core.online_catalog import search_medicine_names

                # Online: filter from cached inventory; hide OOS + expired.
                q = (search or "").strip()
                limit = 400 if q else 250
                bill_as_of = None
                try:
                    from core.batch_visibility import parse_bill_as_of

                    bill_as_of = parse_bill_as_of(
                        self.bill_date_getter() if callable(self.bill_date_getter) else None
                    )
                except Exception:
                    bill_as_of = None
                rows = search_medicine_names(
                    q,
                    limit=limit,
                    show_zero=False,
                    as_of=bill_as_of,
                    reserved=dict(getattr(self, "_reserved_by_id", {}) or {}),
                )
                out = []
                for r in rows:
                    name = r.get("name") or ""
                    stock = int(float(r.get("stock") or 0))
                    med_type = r.get("type") or ""
                    unit = r.get("unit") or "1"
                    out.append(
                        {
                            "name": name,
                            "pack_info": self._format_pack_size(med_type, unit),
                            "type": med_type,
                            "unit": unit,
                            "stock": stock,
                            "total_stock": stock,
                            "batch_count": int(r.get("batch_count") or 1),
                            "mrp": r.get("mrp") or 0,
                            "schedule": r.get("schedule") or "",
                            "source": "inventory",
                        }
                    )
                return out
        except Exception:
            pass
        import sqlite3
        import time
        db_path = self._resolve_db_path()
        last_exc = None
        for attempt in range(8):
            try:
                if db_path:
                    from core.db_utils import open_store_db
                    conn = open_store_db(db_path, readonly=True, timeout=30.0)
                    try:
                        return self._query_master(search, limit=50, cursor=conn.cursor())
                    finally:
                        conn.close()
                return self._query_master(search, limit=50)
            except sqlite3.OperationalError as exc:
                last_exc = exc
                msg = str(exc).lower()
                if 'locked' not in msg and 'busy' not in msg:
                    raise
                time.sleep(0.05 * (2 ** min(attempt, 5)))
            except Exception:
                return []
        if last_exc is not None:
            return []
        return []

    def _ensure_trees(self):
        if self.step1_tree is None:
            self._init_trees()

    def _make_float_popup(self, parent):
        popup = tk.Toplevel(parent)
        popup.withdraw()
        popup.overrideredirect(True)
        try:
            popup.transient(parent)
            popup.attributes('-topmost', True)
        except tk.TclError:
            pass
        return popup

    def _init_trees(self):
        """Create floating treeviews in borderless popups (visible above canvas pages)."""
        toplevel = self.winfo_toplevel()
        step1_columns = self._step1_columns
        
        # Configure style safely
        try:
            style = ttk.Style()
            style.configure("Treeview", borderwidth=0)
            style.configure("Treeview.Heading", borderwidth=0)
        except Exception:
            pass

        self._step1_popup = self._make_float_popup(toplevel)
        self.step1_tree = ttk.Treeview(self._step1_popup, columns=step1_columns, show='headings', height=14)
        for col in step1_columns:
            self.step1_tree.column(col, anchor='w')
        self.step1_tree.heading('name', text='Medicine Name')
        self.step1_tree.heading('pack_size', text='Pack Size')
        self.step1_tree.heading('stock', text='Stock')
        self.step1_tree.heading('mrp', text='MRP')
        self.step1_tree.heading('schedule', text='Schedule')
        self.step1_tree.column('name', width=200, minwidth=50)
        self.step1_tree.column('pack_size', width=80, minwidth=50)
        self.step1_tree.column('stock', width=60, minwidth=50)
        self.step1_tree.column('mrp', width=80, minwidth=50)
        self.step1_tree.column('schedule', width=60, minwidth=40)
        self.step1_tree.pack(fill=tk.BOTH, expand=True)

        step2_columns = self._step2_columns
        self._step2_popup = self._make_float_popup(toplevel)
        self.step2_tree = ttk.Treeview(self._step2_popup, columns=step2_columns, show='headings', height=8)
        self.step2_tree.heading('batch', text='Batch')
        self.step2_tree.heading('pack', text='Pack')
        self.step2_tree.heading('expiry', text='Expiry')
        self.step2_tree.heading('stock', text='Stock')
        self.step2_tree.heading('rate', text='Rate')
        self.step2_tree.heading('mrp', text='MRP')
        self.step2_tree.heading('manufacturer', text='Manufacturer')
        self.step2_tree.heading('schedule', text='Sch')
        self.step2_tree.column('batch', width=80, minwidth=50)
        self.step2_tree.column('pack', width=60, minwidth=50)
        self.step2_tree.column('expiry', width=70, minwidth=50)
        self.step2_tree.column('stock', width=60, minwidth=50)
        self.step2_tree.column('rate', width=70, minwidth=50)
        self.step2_tree.column('mrp', width=70, minwidth=50)
        self.step2_tree.column('manufacturer', width=100, minwidth=50)
        self.step2_tree.column('schedule', width=40, minwidth=30)
        self.step2_tree.pack(fill=tk.BOTH, expand=True)

        self.step1_tree.bind("<Return>", self.on_step1_select)
        self.step1_tree.bind("<Double-Button-1>", self.on_step1_select)
        self.step1_tree.bind("<ButtonRelease-1>", self.on_step1_click)
        self.step1_tree.bind("<Escape>", self._on_step1_tree_escape)
        self.step2_tree.bind("<Return>", self.on_step2_select)
        self.step2_tree.bind("<Key-Return>", self.on_step2_select)
        self.step2_tree.bind("<Double-Button-1>", self.on_step2_select)
        self.step2_tree.bind("<ButtonRelease-1>", self.on_step2_click)
        self.step2_tree.bind("<Escape>", self._on_step2_tree_escape)
        self.step2_tree.bind("<Up>", self.on_step2_up)
        self.step2_tree.bind("<Down>", self.on_step2_down)
        self.step2_tree.bind("<KeyPress>", self.on_step2_key)
        
    def load_medicine_names(self):
        """No-op — medicine names come entirely from medicines_master via live SQL."""
        self.medicine_names = []
        self._purchased_names_lower = set()

    def set_reserved_stock(self, reserved_by_id: dict):
        """Qty already reserved in the current bill, keyed by medicine batch id."""
        self._reserved_by_id = dict(reserved_by_id or {})

    def _available_stock(self, med_id, stock_qty):
        reserved = getattr(self, "_reserved_by_id", {}) or {}
        qty = 0
        try:
            qty = reserved.get(med_id, reserved.get(str(med_id), 0))
            if not qty:
                qty = reserved.get(int(med_id), 0)
        except (TypeError, ValueError):
            qty = reserved.get(med_id, 0)
        return max(0, int(stock_qty or 0) - int(qty or 0))

    def _adjust_stock_for_reserved(self, medicines, cursor=None):
        if not self._reserved_by_id or not medicines:
            return medicines
        cur = cursor or self.cursor
        names = list({m['name'] for m in medicines if m.get('name')})
        if not names:
            return medicines
        placeholders = ','.join('?' * len(names))
        cur.execute(
            f"SELECT name, id, COALESCE(stock_qty, 0) FROM medicines "
            f"WHERE name IN ({placeholders})",
            names,
        )
        totals = {}
        for name, med_id, stock_qty in cur.fetchall():
            totals[name] = totals.get(name, 0) + self._available_stock(med_id, stock_qty)
        for med in medicines:
            total_available = totals.get(med['name'], 0)
            med['total_stock'] = total_available
            med['stock'] = total_available
        return medicines

    def _format_pack_size(self, med_type, unit):
        unit = unit or '1'
        if med_type and is_strip_count_type(med_type):
            return f"1*{unit}"
        return unit

    def _row_to_med_dict(self, row):
        name, schedule, mrp, med_type, unit, total_stock, batch_count = row
        total_stock = int(total_stock or 0)
        batch_count = int(batch_count or 1)
        return {
            'name': name,
            'pack_info': self._format_pack_size(med_type, unit),
            'type': med_type or '',
            'unit': unit or '1',
            'stock': total_stock,
            'total_stock': total_stock,
            'batch_count': batch_count,
            'mrp': mrp or 0,
            'schedule': schedule or '',
            'source': 'inventory',
        }

    def _show_zero_stock(self) -> bool:
        try:
            from core.sales_medicine_prefs import load_show_zero_stock_in_sales
            return load_show_zero_stock_in_sales()
        except Exception:
            return False

    def _name_filter_sql(self, include_zero_stock: bool = False) -> str:
        clauses = ["COALESCE(is_hidden, 0) = 0"]
        if not include_zero_stock and not self._show_zero_stock():
            clauses.append("COALESCE(stock_qty, 0) > 0")
        return "WHERE " + " AND ".join(clauses)

    def _fetch_latest_batches(self, where_sql, params, limit, cursor=None):
        """One row per medicine name with total stock summed across all batches."""
        cur = cursor or self.cursor
        hidden = "COALESCE(is_hidden, 0) = 0"
        extra = where_sql.replace("WHERE", "AND", 1) if where_sql.strip().upper().startswith("WHERE") else ""
        if not self._show_zero_stock():
            stock_clause = "AND COALESCE(stock_qty, 0) > 0"
        else:
            stock_clause = ""
        existed_clause, existed_params = self._medicine_existed_filter()
        sql = f"""
            SELECT m.name, COALESCE(m.schedule, ''), m.mrp, m.type,
                   COALESCE(m.unit, '1'),
                   COALESCE(agg.total_stock, 0),
                   COALESCE(agg.batch_count, 1)
            FROM medicines m
            INNER JOIN (
                SELECT name, MAX(id) AS max_id
                FROM medicines
                WHERE {hidden} {stock_clause} {existed_clause} {extra}
                GROUP BY name
            ) latest ON m.id = latest.max_id
            INNER JOIN (
                SELECT name,
                       SUM(COALESCE(stock_qty, 0)) AS total_stock,
                       COUNT(*) AS batch_count
                FROM medicines
                WHERE {hidden} {stock_clause} {existed_clause} {extra}
                GROUP BY name
            ) agg ON agg.name = m.name
            ORDER BY m.name COLLATE NOCASE
            LIMIT ?
        """
        bind_params: list = []
        bind_params.extend(existed_params)
        bind_params.extend(params)
        bind_params.extend(existed_params)
        bind_params.extend(params)
        bind_params.append(limit)
        cur.execute(sql, tuple(bind_params))
        return [self._row_to_med_dict(r) for r in cur.fetchall()]

    def _filter_sales_medicine_names(self, medicines, cursor=None):
        """Drop names with no sellable batch for the current bill date."""
        if not medicines:
            return medicines
        cur = cursor or self.cursor
        from core.batch_visibility import (
            compute_name_stock_totals,
            compute_oos_anchor_ids,
            is_expired_as_of,
            should_hide_depleted_batch,
        )
        names = [m['name'] for m in medicines if m.get('name')]
        if not names:
            return medicines
        placeholders = ','.join('?' * len(names))
        cur.execute(
            f"""
            SELECT m.id, m.name, m.expiry_date, COALESCE(m.stock_qty, 0),
                   COALESCE(m.created_at, ''),
                   (SELECT MIN(p.purchase_date)
                    FROM purchase_items pi
                    JOIN purchases p ON p.id = pi.purchase_id
                    WHERE pi.medicine_id = m.id) AS first_purchase
            FROM medicines m
            WHERE m.name IN ({placeholders}) AND COALESCE(m.is_hidden, 0) = 0
            """,
            names,
        )
        rows = cur.fetchall()
        created_by_id = {r[0]: r[4] for r in rows}
        purchase_by_id = {r[0]: r[5] for r in rows}
        vis_rows = [
            (r[1], '', '', r[2], r[3], '', '', '', '', '', '', r[0])
            for r in rows
        ]
        name_totals = compute_name_stock_totals(vis_rows)
        anchor_ids = compute_oos_anchor_ids(vis_rows)
        from core.batch_visibility import parse_bill_as_of
        bill_as_of = parse_bill_as_of(self._sales_as_of_date())
        sellable_names = set()
        for row in vis_rows:
            name = row[0]
            if not self._batch_visible_for_sales(
                row[-1], row[4], row[3], name_totals, anchor_ids, name,
                created_at=created_by_id.get(row[-1]),
                first_purchase=purchase_by_id.get(row[-1]),
            ):
                continue
            if is_expired_as_of(row[3], bill_as_of):
                continue
            if float(row[4] or 0) <= 0 and not self._show_zero_stock():
                continue
            sellable_names.add(name)
        return [m for m in medicines if m.get('name') in sellable_names]

    def _query_master(self, search: str, limit: int = 50, cursor=None):
        """Query inventory — one row per name with stock from the latest batch."""
        cur = cursor or self.cursor
        try:
            if not search or not search.strip():
                meds = self._adjust_stock_for_reserved(
                    self._fetch_latest_batches('', (), limit, cursor=cur), cursor=cur)
                return self._filter_sales_medicine_names(meds, cursor=cur)

            s = search.strip()
            prefix = self._fetch_latest_batches(
                'WHERE LOWER(name) LIKE LOWER(?)',
                (f'{s}%',),
                limit,
                cursor=cur,
            )
            if len(prefix) >= limit:
                return self._filter_sales_medicine_names(
                    self._adjust_stock_for_reserved(prefix, cursor=cur), cursor=cur)

            prefix_names = {m['name'].lower() for m in prefix}
            extra = self._fetch_latest_batches(
                'WHERE LOWER(name) LIKE LOWER(?) AND LOWER(name) NOT LIKE LOWER(?)',
                (f'%{s}%', f'{s}%'),
                limit - len(prefix),
                cursor=cur,
            )
            contains = [m for m in extra if m['name'].lower() not in prefix_names]
            return self._filter_sales_medicine_names(
                self._adjust_stock_for_reserved(prefix + contains, cursor=cur), cursor=cur)
        except Exception:
            return []

    def _step1_tree_values(self, med):
        stock = int(med.get('total_stock', med.get('stock')) or 0)
        batch_count = int(med.get('batch_count') or 1)
        if batch_count > 1:
            stock_text = f" {stock} ({batch_count} batches)"
        else:
            stock_text = f" {stock}"
        return (
            f" {med['name']}",
            f" {med['pack_info']}",
            stock_text,
            f" ₹{med['mrp']:.1f}" if med['mrp'] else ' —',
            f" {med.get('schedule') or '—'}",
        )

    def on_step1_focus_in(self, event):
        """Show dropdown immediately on focus using current entry text."""
        self._ensure_trees()
        self.after(10, self._do_filter)

    def focus_step1(self):
        """Focus medicine name entry and show the name dropdown."""
        try:
            self.hide_step2()
            self._ensure_trees()
            self.step1_entry.focus_set()
            self.after(10, self._do_filter)
        except Exception:
            pass

    def on_step1_key(self, event):
        """Trigger filter on every key except navigation keys."""
        if event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return
        # Cancel any pending filter and schedule a fresh one
        if self._filter_pending:
            try:
                self.after_cancel(self._filter_pending)
            except Exception:
                pass
        self._filter_pending = self.after(80, self._do_filter)

    def _do_filter(self):
        """Read current text from entry and refresh the dropdown (query off UI thread)."""
        self._filter_pending = False
        self._ensure_trees()
        if self.step1_tree is None:
            return
        search = self.step1_entry.get().strip()
        self._search_gen = int(getattr(self, "_search_gen", 0) or 0) + 1
        gen = self._search_gen
        from core.background_workers import run_in_thread

        def _work():
            return self._query_medicines_sync(search)

        def _apply(medicines):
            if gen != getattr(self, "_search_gen", 0):
                return
            try:
                if not self.winfo_exists():
                    return
                if self.step1_entry.get().strip() != search:
                    return
            except tk.TclError:
                return
            self.on_step1_change(search, medicines=medicines or [])

        run_in_thread(
            _work,
            name="SalesMedicineFilter",
            root=self,
            on_success=_apply,
        )

    def on_step1_change(self, search='', medicines=None):
        """Populate step1 tree from live inventory filtered by `search`."""
        if self.step1_tree is None:
            return

        for item in self.step1_tree.get_children():
            self.step1_tree.delete(item)

        if medicines is None:
            medicines = self._query_medicines_sync(search.strip())

        self.filtered_medicines = medicines

        if not self.filtered_medicines:
            self.hide_step1()
            return

        for med in self.filtered_medicines:
            self.step1_tree.insert('', tk.END, values=self._step1_tree_values(med))

        self.show_step1()
        children = self.step1_tree.get_children()
        if children:
            self.step1_tree.selection_set(children[0])
            try:
                self.step1_entry.focus_set()
            except tk.TclError:
                pass
    
    def on_step1_escape(self, event):
        if self.step2_visible:
            self.hide_step2()
            return "break"
        if self.step1_visible:
            self.hide_step1()
            return "break"
        try:
            from core.keyboard_registry import KeyboardRegistry
            KeyboardRegistry.blur_to_nav()
        except Exception:
            try:
                self.winfo_toplevel().focus_set()
            except Exception:
                pass
        return "break"

    def _on_step1_tree_escape(self, event):
        self.hide_step1()
        return "break"

    def _on_step2_tree_escape(self, event):
        self.hide_step2()
        return "break"

    def on_step1_focus_out(self, event):
        self.after(100, self._check_focus_and_hide_step1)

    def on_step1_down(self, event):
        if self.step1_visible:
            children = self.step1_tree.get_children()
            if children:
                current = self.step1_tree.selection()
                if current:
                    current_idx = children.index(current[0])
                    next_idx = min(current_idx + 1, len(children) - 1)
                else:
                    next_idx = 0
                
                self.step1_tree.selection_set(children[next_idx])
                self.step1_tree.focus(children[next_idx])
                self.step1_tree.see(children[next_idx])
        return "break"
    
    def on_step1_return(self, event):
        """Handle Enter in step1 entry"""
        search = self.step1_entry.get().strip()
        if not search:
            cb = getattr(self, 'empty_enter_callback', None)
            if callable(cb):
                cb()
                return "break"
        if self.step1_visible:
            current = self.step1_tree.selection()
            if current:
                self.select_medicine_name_from_tree(current[0])
            else:
                children = self.step1_tree.get_children()
                if children:
                    self.select_medicine_name_from_tree(children[0])
        else:
            if search:
                self.on_step1_change()
        return "break"
    
    def on_step1_up(self, event):
        if self.step1_visible:
            children = self.step1_tree.get_children()
            if children:
                current = self.step1_tree.selection()
                if current:
                    current_idx = children.index(current[0])
                    prev_idx = max(current_idx - 1, 0)
                else:
                    prev_idx = 0
                
                self.step1_tree.selection_set(children[prev_idx])
                self.step1_tree.focus(children[prev_idx])
                self.step1_tree.see(children[prev_idx])
        return "break"
    

    
    def on_step1_select(self, event):
        """Handle selection from step1 tree"""
        current = self.step1_tree.selection()
        if current:
            self.select_medicine_name_from_tree(current[0])
        return "break"
    

    
    def on_step1_click(self, event):
        """Handle click on step1 tree"""
        current = self.step1_tree.selection()
        if current:
            self.select_medicine_name_from_tree(current[0])
    
    def select_medicine_name_from_tree(self, item_id):
        """Select medicine name from tree and show variants."""
        values = self.step1_tree.item(item_id)['values']
        if values:
            medicine_name = values[0].strip()
            self.step1_var.set(medicine_name)
            self.hide_step1()
            self.load_variants(medicine_name)
    
    def _clear_medicine_selection(self, *, focus: bool = True):
        """Reset medicine field after no-stock or cancel."""
        self.step1_var.set('')
        self.selected_medicine = None
        self.variants = []
        self.hide_step1()
        self.hide_step2()
        if not focus:
            return
        try:
            self.step1_entry.focus_set()
        except tk.TclError:
            pass

    def _sales_as_of_date(self):
        getter = getattr(self, 'bill_date_getter', None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                pass
        from datetime import date
        return date.today()

    def _medicine_existed_filter(self, alias: str = ""):
        """SQL clause + bind value: only batches added on/before bill date."""
        from core.batch_visibility import parse_bill_as_of, medicine_existed_sql
        as_of = parse_bill_as_of(self._sales_as_of_date())
        return f"AND {medicine_existed_sql(alias)}", (as_of.isoformat(),)

    def _batch_visible_for_sales(
        self, med_id, stock, expiry, name_totals, anchor_ids, name,
        created_at=None, first_purchase=None,
    ):
        from core.batch_visibility import should_hide_depleted_batch, is_expired_as_of, medicine_existed_as_of, parse_bill_as_of
        row = (name, '', '', expiry, stock, '', '', '', '', '', '', med_id)
        if should_hide_depleted_batch(row, name_totals, anchor_ids):
            return False
        bill_as_of = parse_bill_as_of(self._sales_as_of_date())
        if not medicine_existed_as_of(
            created_at, bill_as_of, first_purchase_raw=first_purchase,
        ):
            return False
        if is_expired_as_of(expiry, bill_as_of):
            return False
        if float(stock or 0) <= 0 and not self._show_zero_stock():
            return False
        return True

    def _offline_variants_sync(self, medicine_name, show_zero):
        """Fetch batches for one name without scanning all purchase_items."""
        from core.sales_medicine_prefs import (
            BATCH_NEWEST_FIRST,
            batch_order_sql_clause,
            load_batch_sort_order,
        )
        from core.batch_visibility import (
            compute_name_stock_totals,
            compute_oos_anchor_ids,
            parse_bill_as_of,
        )

        db_path = self._resolve_db_path()
        conn = None
        cur = None
        if db_path:
            try:
                from core.db_utils import open_store_db
                conn = open_store_db(db_path, readonly=True, timeout=30.0)
                cur = conn.cursor()
            except Exception:
                conn = None
                cur = None
        if cur is None:
            cur = self.cursor

        as_of = parse_bill_as_of(self._sales_as_of_date())
        from core.batch_visibility import first_purchase_date_sql, medicine_existed_sql

        # Same rule as the name list: the earlier of the first purchase and created_at.
        # created_at alone hid every batch a back-dated purchase brought in today.
        existed_sql = f"AND {medicine_existed_sql('m')}"
        existed_params = (as_of.isoformat(),)
        order_by = batch_order_sql_clause("m")
        join_sql = ""
        join_params = ()
        if load_batch_sort_order() == BATCH_NEWEST_FIRST:
            join_sql = """
                LEFT JOIN (
                    SELECT pi.medicine_id, MAX(p.purchase_date) AS last_purchase_date
                    FROM purchase_items pi
                    JOIN purchases p ON p.id = pi.purchase_id
                    WHERE pi.medicine_id IN (
                        SELECT id FROM medicines WHERE name = ?
                    )
                    GROUP BY pi.medicine_id
                ) lp ON lp.medicine_id = m.id
            """
            join_params = (medicine_name,)

        sql = f"""
            SELECT m.id, m.name, m.batch_no, m.expiry_date, m.stock_qty, m.mrp, m.rate,
                   m.manufacturer, m.schedule, m.type, COALESCE(m.unit,'1') as unit,
                   COALESCE(m.created_at, ''), {first_purchase_date_sql('m')}
            FROM medicines m
            {join_sql}
            WHERE m.name = ?
              AND COALESCE(m.is_hidden, 0) = 0
              {existed_sql}
            ORDER BY {order_by}
        """
        payload = {"variants": [], "warn": None, "batch_count": 0}
        try:
            try:
                cur.execute(sql, (*join_params, medicine_name, *existed_params))
                rows = cur.fetchall()
            except Exception:
                rows = []

            if not rows:
                try:
                    cur.execute(
                        """
                        SELECT COUNT(*)
                        FROM medicines
                        WHERE name=? AND COALESCE(is_hidden,0)=0
                        """,
                        (medicine_name,),
                    )
                    batch_count = int((cur.fetchone() or [0])[0] or 0)
                except Exception:
                    batch_count = 0
                if batch_count > 0 and not show_zero:
                    payload["warn"] = "no_stock"
                    payload["batch_count"] = batch_count
                return payload

            vis_rows = [
                (r[1], '', '', r[3], r[4], '', '', '', '', '', '', r[0]) for r in rows
            ]
            name_totals = compute_name_stock_totals(vis_rows)
            anchor_ids = compute_oos_anchor_ids(vis_rows)
            variants = []
            for row in rows:
                (
                    med_id, name, batch, expiry, stock, mrp, rate, manufacturer,
                    schedule, med_type, unit, created_at, first_purchase,
                ) = row
                if not self._batch_visible_for_sales(
                    med_id, stock, expiry, name_totals, anchor_ids, name,
                    created_at=created_at,
                    first_purchase=first_purchase,
                ):
                    continue
                available = self._available_stock(med_id, stock)
                if available <= 0 and not show_zero:
                    continue
                expiry_display = expiry[:7] if expiry else 'N/A'
                pack_size = (
                    f"1*{unit}" if med_type and is_strip_count_type(med_type) else unit
                )
                variants.append({
                    'id': med_id, 'name': name, 'batch': batch,
                    'pack_size': pack_size, 'expiry_display': expiry_display,
                    'expiry': expiry,
                    'stock': available if available > 0 else float(stock or 0),
                    'mrp': mrp or 0, 'rate': rate or 0,
                    'manufacturer': manufacturer or 'N/A',
                    'schedule': schedule or '',
                    'type': med_type or '',
                })
            payload["variants"] = variants
            if not variants and rows:
                payload["warn"] = "reserved"
            return payload
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def load_variants(self, medicine_name):
        """Load batches for a medicine; auto-picks when only one batch exists."""
        from core.sales_medicine_prefs import load_show_zero_stock_in_sales
        from core.background_workers import run_in_thread

        show_zero = load_show_zero_stock_in_sales()
        self._variant_gen = int(getattr(self, "_variant_gen", 0) or 0) + 1
        gen = self._variant_gen

        def _apply_variants(variants, warn=None, batch_count=0):
            if gen != getattr(self, "_variant_gen", 0):
                return
            try:
                if not self.winfo_exists():
                    return
            except tk.TclError:
                return
            self.variants = list(variants or [])
            if len(self.variants) == 1:
                self.select_variant_direct(self.variants[0])
                return
            if self.variants:
                self.show_step2()
                return
            try:
                from core.themed_messagebox import showwarning
                if warn == "reserved":
                    showwarning(
                        "No Stock Available",
                        f'All stock for "{medicine_name}" is already in this bill.',
                        parent=self.winfo_toplevel(),
                    )
                elif warn == "no_stock":
                    msg = (
                        f'"{medicine_name}" has no stock in any batch.\n'
                        "Please select another medicine."
                    )
                    if int(batch_count or 0) > 1:
                        msg += (
                            f"\n\n({int(batch_count)} batches in inventory — "
                            "enable zero-stock in Settings → Sales & Billing, or reorder stock.)"
                        )
                    showwarning("No Stock", msg, parent=self.winfo_toplevel())
                else:
                    showwarning(
                        "No Stock",
                        f'"{medicine_name}" has no stock on the server.',
                        parent=self.winfo_toplevel(),
                    )
            except Exception:
                pass
            self._clear_medicine_selection()

        try:
            from core.sync_prefs import is_online_mode
            online = bool(is_online_mode())
        except Exception:
            online = False

        if online:
            from core.batch_visibility import is_expired_as_of, parse_bill_as_of
            from core.online_catalog import batches_for_name

            bill_as_of = parse_bill_as_of(
                self.bill_date_getter() if callable(self.bill_date_getter) else None
            )
            reserved = dict(getattr(self, "_reserved_by_id", {}) or {})

            def _online_work():
                batches = batches_for_name(
                    medicine_name, include_zero=show_zero, as_of=bill_as_of
                )
                variants = []
                for b in batches:
                    stock = float(b.get("stock") or 0)
                    mid = int(b.get("id") or 0)
                    try:
                        reserved_qty = float(
                            reserved.get(mid, reserved.get(str(mid), 0)) or 0
                        )
                    except (TypeError, ValueError):
                        reserved_qty = 0.0
                    available = stock - reserved_qty
                    expiry = b.get("expiry") or ""
                    if is_expired_as_of(expiry, bill_as_of):
                        continue
                    if available <= 0 and not show_zero:
                        continue
                    med_type = b.get("type") or ""
                    unit = b.get("unit") or "1"
                    pack_size = (
                        f"1*{unit}"
                        if med_type and is_strip_count_type(med_type)
                        else unit
                    )
                    variants.append(
                        {
                            "id": mid,
                            "name": b.get("name") or medicine_name,
                            "batch": b.get("batch") or "",
                            "pack_size": pack_size,
                            "expiry_display": expiry[:7] if expiry else "N/A",
                            "expiry": expiry,
                            "stock": available if available > 0 else stock,
                            "mrp": b.get("mrp") or 0,
                            "rate": b.get("rate") or 0,
                            "manufacturer": "N/A",
                            "schedule": b.get("schedule") or "",
                            "type": med_type,
                            "unit": unit,
                        }
                    )
                return variants

            run_in_thread(
                _online_work,
                name="SalesMedicineBatches",
                root=self,
                on_success=lambda variants: _apply_variants(variants),
            )
            return

        def _offline_work():
            return self._offline_variants_sync(medicine_name, show_zero)

        def _offline_apply(payload):
            payload = payload or {}
            _apply_variants(
                payload.get("variants") or [],
                warn=payload.get("warn"),
                batch_count=payload.get("batch_count") or 0,
            )

        run_in_thread(
            _offline_work,
            name="SalesMedicineBatchesOffline",
            root=self,
            on_success=_offline_apply,
        )

    def select_variant_direct(self, variant):
        """Select a batch without showing the batch picker."""
        if not variant:
            return
        self.selected_medicine = variant
        display_text = (
            f"{variant['name']} | B:{variant['batch']} | "
            f"Exp:{(variant['expiry'] or '')[:7]} | Stock:{variant['stock']}"
        )
        self.step1_var.set(display_text)
        self.hide_step2()
        self.step1_entry.event_generate('<<ComboboxSelected>>')
        if callable(self.next_focus_widget):
            self.next_focus_widget()
        elif self.next_focus_widget:
            self.next_focus_widget.focus()
    
    def show_step2(self):
        """Show step2 tree with variants"""
        if not self.variants or self.step2_tree is None:
            return
            
        # Clear existing items
        for item in self.step2_tree.get_children():
            self.step2_tree.delete(item)
        
        # Add variants to tree with separators
        for variant in self.variants:
            self.step2_tree.insert('', tk.END, values=(
                f" {variant['batch']}", f" {variant['pack_size']}", f" {variant['expiry_display']}",
                f" {variant['stock']}", f" ₹{variant['rate']:.1f}", f" ₹{variant['mrp']:.1f}",
                f" {variant['manufacturer']}", f" {variant['schedule']}"
            ))
        
        # Position step2 popup below the entry (screen coordinates)
        try:
            x = self.step1_entry.winfo_rootx()
            y = self.step1_entry.winfo_rooty() + self.step1_entry.winfo_height()
            width = max(self.step1_entry.winfo_width() + 220, 620)
            
            self._step2_popup.geometry(f"{width}x200+{x}+{y}")
            self._step2_popup.deiconify()
            self._step2_popup.lift()
            try:
                self._step2_popup.attributes('-topmost', True)
            except tk.TclError:
                pass
            self.step2_tree.focus_force()
            
            # Ensure selection works
            self.step2_tree.after(10, lambda: self.step2_tree.focus_force())
            
            # Select first variant and set focus properly
            children = self.step2_tree.get_children()
            if children:
                self.step2_tree.selection_set(children[0])
                self.step2_tree.focus(children[0])
                self.step2_tree.see(children[0])
                
            self.step2_visible = True
        except tk.TclError:
            pass
    
    def on_step2_select(self, event):
        """Handle selection from step2 tree"""
        current = self.step2_tree.selection()
        if current:
            self.select_variant_from_tree(current[0])
        else:
            # If no selection, select first item
            children = self.step2_tree.get_children()
            if children:
                self.select_variant_from_tree(children[0])
        return "break"
    
    def on_step2_click(self, event):
        """Handle click on step2 tree"""
        current = self.step2_tree.selection()
        if current:
            self.select_variant_from_tree(current[0])
    
    def on_step1_tab(self, event):
        """Handle Tab to move to step2 if visible"""
        if self.step2_visible:
            self.step2_tree.focus_set()
            children = self.step2_tree.get_children()
            if children:
                self.step2_tree.selection_set(children[0])
                self.step2_tree.focus(children[0])
        return "break"
    
    def on_step2_key(self, event):
        """Handle key press in step2"""
        if event.keysym == "Return":
            current = self.step2_tree.selection()
            if current:
                self.select_variant_from_tree(current[0])
            return "break"
        return None
    
    def on_step2_down(self, event):
        """Handle down arrow in step2"""
        children = self.step2_tree.get_children()
        current = self.step2_tree.selection()
        if current and children:
            current_idx = children.index(current[0])
            next_idx = min(current_idx + 1, len(children) - 1)
            self.step2_tree.selection_set(children[next_idx])
            self.step2_tree.focus(children[next_idx])
            self.step2_tree.see(children[next_idx])
        return "break"
    
    def on_step2_up(self, event):
        """Handle up arrow in step2 - go back to step1 if at top"""
        children = self.step2_tree.get_children()
        current = self.step2_tree.selection()
        if current and children:
            current_idx = children.index(current[0])
            if current_idx == 0:  # At top
                self.hide_step2()
                self.step1_entry.focus_set()
                return "break"
            else:
                prev_idx = max(current_idx - 1, 0)
                self.step2_tree.selection_set(children[prev_idx])
                self.step2_tree.focus(children[prev_idx])
                self.step2_tree.see(children[prev_idx])
        return "break"
    
    def select_variant_from_tree(self, item_id):
        """Select specific variant from tree and complete selection"""
        values = self.step2_tree.item(item_id)['values']
        if not values:
            return
            
        # Find the variant by index position (avoids batch string mismatch)
        try:
            item_index = self.step2_tree.get_children().index(item_id)
            selected_variant = self.variants[item_index] if item_index < len(self.variants) else None
        except (ValueError, IndexError):
            selected_variant = None
                
        if not selected_variant:
            return
            
        self.selected_medicine = selected_variant
        
        # Update display to show selected variant
        display_text = f"{selected_variant['name']} | B:{selected_variant['batch']} | Exp:{selected_variant['expiry'][:7]} | Stock:{selected_variant['stock']}"
        self.step1_var.set(display_text)
        
        self.hide_step2()
        
        # Trigger selection event
        self.step1_entry.event_generate('<<ComboboxSelected>>')
        
        # Move to next field
        if callable(self.next_focus_widget):
            self.next_focus_widget()
        elif self.next_focus_widget:
            self.next_focus_widget.focus()
    
    def show_step1(self):
        """Show step1 tree — always reposition so it stays visible while typing."""
        if self.step1_tree is None:
            return
        if not self.step1_tree.get_children():
            self.hide_step1()
            return
        if not self._is_viewable():
            self.hide_step1()
            return
        try:
            self.update_idletasks()
            x = self.step1_entry.winfo_rootx()
            y = self.step1_entry.winfo_rooty() + self.step1_entry.winfo_height()
            width = max(self.step1_entry.winfo_width() + 200, 620)
            self._step1_popup.geometry(f"{width}x300+{x}+{y}")
            self._step1_popup.deiconify()
            self._step1_popup.lift()
            try:
                self._step1_popup.attributes('-topmost', True)
            except tk.TclError:
                pass
            self.step1_visible = True
        except tk.TclError:
            pass
    
    def hide_step1(self):
        """Hide step1 tree"""
        try:
            if getattr(self, '_step1_popup', None) and self._step1_popup.winfo_exists():
                self._step1_popup.withdraw()
        except tk.TclError:
            pass
        self.step1_visible = False
    
    def hide_step2(self):
        """Hide step2 tree"""
        try:
            if getattr(self, '_step2_popup', None) and self._step2_popup.winfo_exists():
                self._step2_popup.withdraw()
        except tk.TclError:
            pass
        self.step2_visible = False
    
    def on_global_return(self, event):
        """Handle global Return key for step2 tree"""
        if self.step2_visible and event.widget == self.step2_tree:
            current = self.step2_tree.selection()
            if current:
                self.select_variant_from_tree(current[0])
                return "break"
        return None
    
    def on_click_outside(self, event):
        """Hide trees when clicking outside this combo's widgets"""
        w = event.widget
        if w is self.step1_entry:
            return
        if self._widget_is_or_inside(w, self.step1_tree):
            return
        if self._widget_is_or_inside(w, self.step2_tree):
            return
        if getattr(self, '_step1_popup', None) and self._widget_is_or_inside(w, self._step1_popup):
            return
        if getattr(self, '_step2_popup', None) and self._widget_is_or_inside(w, self._step2_popup):
            return
        try:
            self.hide_step1()
            self.hide_step2()
        except tk.TclError:
            pass
    
    def _check_focus_and_hide_step1(self):
        """Check focus and hide step1 if needed"""
        try:
            focused = self.focus_get()
            if focused == self.step1_entry:
                return
            if self._widget_is_or_inside(focused, self.step1_tree):
                return
            if self._widget_is_or_inside(focused, self.step2_tree):
                return
            if getattr(self, '_step1_popup', None) and self._widget_is_or_inside(focused, self._step1_popup):
                return
            if getattr(self, '_step2_popup', None) and self._widget_is_or_inside(focused, self._step2_popup):
                return
            self.hide_step1()
            self.hide_step2()
        except Exception:
            self.hide_step1()
            self.hide_step2()
    
    def get(self):
        """Get current selection"""
        return self.step1_var.get()
    
    def set(self, value):
        """Set current value"""
        self.step1_var.set(value)
    
    def focus(self):
        """Set focus to entry"""
        try:
            if self.winfo_exists() and self.step1_entry.winfo_exists():
                self.step1_entry.focus_set()
        except tk.TclError:
            pass
    
    def bind(self, event, callback):
        """Bind event to entry"""
        self.step1_entry.bind(event, callback)
    
    def get_selected_medicine(self):
        """Get the selected medicine data"""
        return self.selected_medicine
    
    def destroy(self):
        """Clean up when widget is destroyed"""
        self.hide_step1()
        self.hide_step2()
        try:
            if getattr(self, '_step1_popup', None) and self._step1_popup.winfo_exists():
                self._step1_popup.destroy()
            if getattr(self, '_step2_popup', None) and self._step2_popup.winfo_exists():
                self._step2_popup.destroy()
        except tk.TclError:
            pass
        super().destroy()