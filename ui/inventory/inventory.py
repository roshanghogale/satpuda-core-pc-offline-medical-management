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
from core.layout_config import INVENTORY_ROWS, load_layout, _DEFAULT_MED_TYPES, _DEFAULT_SCHEDULES, is_strip_count_type, parse_tablets_per_stripe
from core.column_config import apply_column_visibility, is_dashboard_section_visible
from core.scroll_manager import make_scrollable
from core.export_manager import export_data
from core.column_config import export_table
from widgets.searchable_combo import SearchableCombo
from ui.inventory.inventory_dialogs import open_edit_dialog, open_view_dialog, delete_medicine


class InventoryPage:

    def __init__(self, parent, conn):
        self.conn   = conn
        self.cursor = conn.cursor()
        self.parent = parent
        self.medicines_data = []
        self.show_location  = False

        self._build_ui()
        self.load_inventory()
        self.parent.after(100, self._setup_arrow_nav)
        self.parent.after(200, self.search_entry.focus)
        self._register_keyboard()

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
        main_frame = make_scrollable(self.parent)
        self._inner_frame = main_frame
        main_frame.configure(padding=(10, 10))

        layout = load_layout()
        med_types = layout.get('med_types', list(_DEFAULT_MED_TYPES))
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
        )
        self.schedule_filter.next_focus_widget = lambda: self.refresh_btn.focus_set()

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

    def _build_tree(self, tf):
        all_cols = ('Name','Type','Batch','Expiry','Days Left','Stock','Unit','MRP','Rate','Manufacturer','Schedule','Location')
        if self.show_location:
            cols = all_cols
        else:
            cols = tuple(c for c in all_cols if c != 'Location')
        widths = {'Name':150,'Type':80,'Batch':100,'Expiry':80,'Days Left':75,
                  'Stock':70,'Unit':60,'MRP':70,'Rate':70,'Manufacturer':120,'Schedule':80,'Location':100}

        self.inventory_tree = ttk.Treeview(tf, columns=cols, show='headings',
                                           height=INVENTORY_ROWS, style='Large.Treeview')
        for col in cols:
            self.inventory_tree.heading(col, text=col)
            self.inventory_tree.column(col, width=widths.get(col, 100))
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

        for tag, bg in [('out_of_stock','#ffebee'),('low_stock','#fff3e0'),
                        ('expired','#ffebee'),('near_expiry','#fff3e0')]:
            from core.alert_colors import get_tree_tag_colors
            clr = get_tree_tag_colors()
            self.inventory_tree.tag_configure('out_of_stock', background=clr['due_bg'],     foreground=clr['due_fg'])
            self.inventory_tree.tag_configure('low_stock',    background=clr['partial_bg'], foreground=clr['partial_fg'])
            self.inventory_tree.tag_configure('expired',      background=clr['due_bg'],     foreground=clr['due_fg'])
            self.inventory_tree.tag_configure('near_expiry',  background=clr['partial_bg'], foreground=clr['partial_fg'])
            break

    def _wire_tree_keyboard(self):
        from core.tree_action_menu import setup_tree_actions
        self._action_menu = setup_tree_actions(
            self.parent,
            self.inventory_tree,
            [
                ("Edit Medicine", self.edit_medicine),
                ("View Details", self.view_details),
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
        self.cursor.execute("SELECT DISTINCT name FROM medicines ORDER BY name")
        self.search_entry.configure(values=[r[0] for r in self.cursor.fetchall()])

    def load_inventory(self):
        try:
            self.cursor.execute("SELECT show_location FROM shelf_settings LIMIT 1")
            r = self.cursor.fetchone()
            new_loc = bool(r[0]) if r else False
        except Exception:
            new_loc = False

        if self.show_location != new_loc:
            self.show_location = new_loc
            self.inventory_tree.destroy()
            self._build_tree(self._tree_frame)
            self._wire_tree_keyboard()

        for item in self.inventory_tree.get_children():
            self.inventory_tree.delete(item)

        if self.show_location:
            try:
                self.cursor.execute("""
                    SELECT name,type,batch_no,expiry_date,stock_qty,unit,mrp,rate,
                           manufacturer,schedule,location,id FROM medicines ORDER BY name,batch_no
                """)
            except Exception:
                self.cursor.execute("""
                    SELECT name,type,batch_no,expiry_date,stock_qty,unit,mrp,rate,
                           manufacturer,schedule,'',id FROM medicines ORDER BY name,batch_no
                """)
        else:
            self.cursor.execute("""
                SELECT name,type,batch_no,expiry_date,stock_qty,unit,mrp,rate,
                       manufacturer,schedule,id FROM medicines ORDER BY name,batch_no
            """)
        self.medicines_data = self.cursor.fetchall()
        self._insert_rows(self.medicines_data)
        self.update_summary()

    def _insert_rows(self, data):
        for med in data:
            tags = []
            stock = med[4]
            if stock == 0:                              tags = ['out_of_stock']
            elif self._is_low_stock(stock, med[1]):     tags = ['low_stock']
            elif self._is_expired(med[3]):              tags = ['expired']
            elif self._is_near_expiry(med[3], med[1]):  tags = ['near_expiry']

            raw = list(med[:-1])
            days = self._days_left(raw[3])
            days_str = f"{days}d" if days is not None else ''
            raw[3] = self._fmt_expiry(raw[3])
            raw[5] = self._fmt_unit(raw[5], raw[1])
            values = raw[:4] + [days_str] + raw[4:]
            if self.show_location and len(values) > 11:
                values[11] = self._fmt_location(values[11])
            self.inventory_tree.insert('', tk.END, iid=str(med[-1]), values=values, tags=tags)

    def filter_inventory(self, event=None):
        search  = self.search_entry.get().lower()
        typ     = self.type_filter.get()
        stock_f = self.stock_filter.get()
        exp_f   = self.expiry_filter.get()
        sch_f   = self.schedule_filter.get()

        for item in self.inventory_tree.get_children():
            self.inventory_tree.delete(item)

        starts, contains = [], []
        for med in self.medicines_data:
            if typ and med[1] != typ: continue
            if stock_f == 'In Stock'    and med[4] <= 0: continue
            if stock_f == 'Low Stock'   and not self._is_low_stock(med[4], med[1]): continue
            if stock_f == 'Out of Stock'and med[4] > 0: continue
            if exp_f == 'Near Expiry'   and not self._is_near_expiry(med[3], med[1]): continue
            if exp_f == 'Expired'       and not self._is_expired(med[3]): continue
            if sch_f:
                if sch_f == 'Non-Scheduled' and med[9] and med[9].strip(): continue
                elif sch_f != 'Non-Scheduled' and med[9] != sch_f: continue
            if search:
                n = med[0].lower(); b = (med[2] or '').lower()
                if n.startswith(search) or b.startswith(search): starts.append(med)
                elif search in n or search in b: contains.append(med)
            else:
                starts.append(med)

        self._insert_rows(starts + contains)

    def update_summary(self):
        if not getattr(self, '_show_summary', True):
            return
        n     = len(self.medicines_data)
        low   = sum(1 for m in self.medicines_data if self._is_low_stock(m[4], m[1] or ''))
        out   = sum(1 for m in self.medicines_data if m[4] == 0)
        near  = sum(1 for m in self.medicines_data if self._is_near_expiry(m[3], m[1] or ''))
        exp   = sum(1 for m in self.medicines_data if self._is_expired(m[3]))
        val   = sum(m[4] * m[6] for m in self.medicines_data if m[6])
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

    def _is_low_stock(self, qty, med_type):
        threshold = self._get_setting(f'low_stock_{med_type.lower()}', 10)
        if is_strip_count_type(med_type):
            try:
                self.cursor.execute(
                    "SELECT unit FROM medicines WHERE type=? AND unit IS NOT NULL LIMIT 1",
                    (med_type,))
                r = self.cursor.fetchone()
                if r:
                    ups = parse_tablets_per_stripe(r[0])
                    return 0 < qty / ups < threshold
            except Exception:
                pass
        return 0 < qty < threshold

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

    def edit_medicine(self):
        med_id = self._selected_id()
        if med_id:
            open_edit_dialog(self.parent, self.conn, med_id, self.load_inventory)

    def view_details(self, event=None):
        med_id = self._selected_id()
        if med_id:
            open_view_dialog(self.parent, self.conn, med_id)

    def delete_medicine(self):
        sel = self.inventory_tree.selection()
        if not sel: return
        med_id = self._selected_id()
        if not med_id: return
        values = self.inventory_tree.item(sel[0])['values']
        delete_medicine(self.conn, med_id, values[0], values[2], self.load_inventory)

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
        self.cursor.execute("""
            SELECT name,type,batch_no,expiry_date,stock_qty,unit,mrp,rate,manufacturer,schedule
            FROM medicines ORDER BY name
        """)
        export_table(self.parent, 'Stock Statement',
                     ['Name', 'Type', 'Batch', 'Expiry', 'Stock', 'Unit', 'MRP', 'Rate',
                      'Manufacturer', 'Schedule'],
                     self.cursor.fetchall(), 'stock_statement', 'inventory', 'stock_statement')

    def _export_near_expiry(self):
        threshold = (datetime.now() + timedelta(days=90)).strftime('%Y-%m-%d')
        today = datetime.now().strftime('%Y-%m-%d')
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
        self.cursor.execute("""
            SELECT COALESCE(schedule,'Non-Scheduled'),name,batch_no,expiry_date,stock_qty,mrp
            FROM medicines ORDER BY 1,name
        """)
        export_table(self.parent, 'Schedule-wise Stock',
                     ['Schedule', 'Name', 'Batch', 'Expiry', 'Stock', 'MRP'],
                     self.cursor.fetchall(), 'schedule_wise_stock', 'inventory', 'schedule_wise_stock')

    # ── Keyboard nav ──────────────────────────────────────────────────────

    def _setup_arrow_nav(self):
        from core.focus_chain import wire_focus_ring
        wire_focus_ring([
            self.search_entry.entry, self.type_filter.entry,
            self.stock_filter.entry, self.expiry_filter.entry,
            self.schedule_filter.entry, self.refresh_btn, self.export_btn,
        ])

    def _apply_location_column_visibility(self):
        """Called by main.py when returning to inventory page."""
        pass  # handled by load_inventory recreating tree if needed
