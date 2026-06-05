"""
ui/home_page.py
───────────────
Dashboard / home page.
"""
import tkinter as tk
from datetime import datetime, timedelta
import os

try:
    import ttkbootstrap as ttk
except ImportError:
    from tkinter import ttk

from core.font_config import *
from core.scroll_manager import make_scrollable, open_dialog

from core.layout_config import get_home_banner_path, get_home_banner_size
from core.column_config import is_quick_access_visible


def build_home(main_frame, conn, nav_click_fn, open_billing_fn,
               open_purchase_fn, open_inventory_fn, open_contacts_fn,
               open_ledger_fn, open_general_products_fn, input_ctrl, register_canvas_fn):
    """Build and pack the home dashboard into main_frame."""
    inner = make_scrollable(main_frame)
    inner.configure(padding=(10, 10))

    cursor = conn.cursor()
    today     = datetime.now().date()
    today_str = str(today)
    near_days = 90

    # ── Stats bar (footer) ───────────────────────────────────────────────
    stats_frame = ttk.LabelFrame(inner, text='📊 Dashboard')

    month_start   = today.replace(day=1)
    fy_start_year = today.year if today.month >= 4 else today.year - 1
    fy_start = f"{fy_start_year}-04-01"
    fy_end   = f"{fy_start_year + 1}-03-31"
    fy_label = f"{fy_start_year}-{str(fy_start_year+1)[2:]}"

    cursor.execute("SELECT COALESCE(SUM(total_amount),0),COALESCE(SUM(amount_paid),0),COUNT(*) FROM sales WHERE bill_date=?", (today_str,))
    t_sales, t_collected, t_bills = cursor.fetchone()
    cursor.execute("SELECT COALESCE(SUM(total_amount),0),COALESCE(SUM(amount_paid),0),COUNT(*) FROM sales WHERE bill_date>=? AND bill_date<=?", (str(month_start), today_str))
    m_sales, m_collected, m_bills = cursor.fetchone()
    cursor.execute("SELECT COALESCE(SUM(total_amount),0),COALESCE(SUM(amount_paid),0),COUNT(*) FROM sales WHERE bill_date>=? AND bill_date<=?", (fy_start, fy_end))
    y_sales, y_collected, y_bills = cursor.fetchone()

    cursor.execute("SELECT COALESCE(SUM(total_due),0) FROM customers WHERE total_due>0")
    total_cust_due = cursor.fetchone()[0]
    cursor.execute("SELECT COALESCE(SUM(total_due),0) FROM suppliers WHERE total_due>0")
    total_sup_due = cursor.fetchone()[0]
    cursor.execute("SELECT COALESCE(SUM(CAST(stock_qty AS REAL)*CAST(mrp AS REAL)),0) FROM medicines WHERE stock_qty>0 AND mrp>0")
    stock_val = cursor.fetchone()[0]

    today_cols = [
        ('Today Sales',     f'\u20b9{t_sales:,.0f}'),
        ('Today Collected', f'\u20b9{t_collected:,.0f}'),
        ('Today Bills',     str(t_bills)),
        ('Customer Due',    f'\u20b9{total_cust_due:,.0f}'),
        ('Supplier Due',    f'\u20b9{total_sup_due:,.0f}'),
        ('Stock Value',     f'\u20b9{stock_val:,.0f}'),
    ]
    month_year_cols = [
        ('Month Sales',                 f'\u20b9{m_sales:,.0f}'),
        ('Month Collected',             f'\u20b9{m_collected:,.0f}'),
        ('Month Bills',                 str(m_bills)),
        (f'Year Sales ({fy_label})',    f'\u20b9{y_sales:,.0f}'),
        (f'Year Collected ({fy_label})',f'\u20b9{y_collected:,.0f}'),
        (f'Year Bills ({fy_label})',    str(y_bills)),
    ]

    for i, (label, value) in enumerate(today_cols):
        sf = ttk.Frame(stats_frame)
        sf.grid(row=0, column=i, padx=12, pady=(6, 2), sticky='ew')
        stats_frame.grid_columnconfigure(i, weight=1)
        ttk.Label(sf, text=value, font=(FONT_FAMILY, FONT_SIZE_SECTION_TITLE, 'bold')).pack()
        ttk.Label(sf, text=label, font=(FONT_FAMILY, FONT_SIZE_DEFAULT)).pack()

    ttk.Separator(stats_frame, orient='horizontal').grid(
        row=1, column=0, columnspan=6, sticky='ew', padx=8, pady=2)

    for i, (label, value) in enumerate(month_year_cols):
        sf = ttk.Frame(stats_frame)
        sf.grid(row=2, column=i, padx=12, pady=(2, 6), sticky='ew')
        ttk.Label(sf, text=value, font=(FONT_FAMILY, FONT_SIZE_BUTTONS, 'bold')).pack()
        ttk.Label(sf, text=label, font=(FONT_FAMILY, FONT_SIZE_DEFAULT - 1)).pack()

    # ── Main body: Quick Actions (left column) + Banner (right) ──────────
    body = ttk.Frame(inner)
    body.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

    qa_frame = ttk.LabelFrame(body, text='Quick Actions')
    qa_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
    btn_row = ttk.Frame(qa_frame)          # vertical column of buttons
    btn_row.pack(fill=tk.Y, padx=8, pady=6)

    def _export(kind):
        from ui.settings.settings_tabs.database_tab import DatabaseTab
        import tkinter as tk
        # Create a hidden notebook just to satisfy DatabaseTab's constructor
        _nb = ttk.Notebook(main_frame)
        db = DatabaseTab(_nb, conn, main_frame)
        getattr(db, f'export_{kind}')()
        _nb.destroy()

    # ── Popup list dialog helper ──────────────────────────────────────────
    def _show_list_dialog(title, cols, col_widths, rows):
        dlg = open_dialog(main_frame, title, width=820, height=480, resizable=True)
        frm = dlg.content
        frm.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        tree = ttk.Treeview(frm, columns=cols, show='headings',
                            height=18, style='Large.Treeview')
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=col_widths.get(c, 120))
        vsb = ttk.Scrollbar(frm, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        for r in rows:
            tree.insert('', tk.END, values=r)
        ttk.Label(dlg.footer, text=f"{len(rows)} record(s)",
                  font=(FONT_FAMILY, FONT_SIZE_DEFAULT)).pack(side=tk.LEFT, padx=8)
        ttk.Button(dlg.footer, text="Close", command=dlg.destroy).pack(side=tk.RIGHT, padx=8)

    # ── Bills & Outstanding dropdown ──────────────────────────────────────
    def _bills_menu(event=None):
        m = tk.Menu(btn_row, tearoff=0)

        def _today_bills():
            cursor.execute("""SELECT s.bill_no, c.name, s.total_amount,
                                     s.amount_paid, s.due_amount
                              FROM sales s JOIN customers c ON s.customer_id=c.id
                              WHERE s.bill_date=? ORDER BY s.id DESC""", (today_str,))
            rows = [(r[0], r[1], f'\u20b9{r[2]:.2f}',
                     f'\u20b9{r[3]:.2f}',
                     f'\u20b9{r[4]:.2f}' if r[4] else '\u20b90.00')
                    for r in cursor.fetchall()]
            _show_list_dialog(
                f"Today's Bills — {today_str}",
                ('Bill No', 'Customer', 'Amount', 'Paid', 'Due'),
                {'Bill No': 110, 'Customer': 200, 'Amount': 110,
                 'Paid': 110, 'Due': 110},
                rows)

        def _outstanding():
            cursor.execute("""SELECT c.name, c.phone, c.total_due
                              FROM customers c WHERE c.total_due>0
                              ORDER BY c.total_due DESC""")
            rows = [(r[0], r[1] or '', f'\u20b9{r[2]:.2f}')
                    for r in cursor.fetchall()]
            _show_list_dialog(
                'Outstanding Customer Due',
                ('Customer', 'Phone', 'Due Amount'),
                {'Customer': 260, 'Phone': 160, 'Due Amount': 140},
                rows)

        m.add_command(label="🧾 Today's Bills",          command=_today_bills)
        m.add_command(label="💰 Outstanding Customer Due", command=_outstanding)
        if not bills_btn:
            return
        m.post(bills_btn.winfo_rootx(),
               bills_btn.winfo_rooty() + bills_btn.winfo_height())

    # ── Stock & Expiry dropdown ───────────────────────────────────────────
    def _stock_menu(event=None):
        m = tk.Menu(btn_row, tearoff=0)

        def _low_stock():
            cursor.execute("""SELECT name, type, stock_qty FROM medicines
                              WHERE stock_qty<=10 ORDER BY stock_qty ASC, name""")
            rows = cursor.fetchall()
            _show_list_dialog(
                'Low Stock / Out of Stock',
                ('Medicine', 'Type', 'Stock'),
                {'Medicine': 300, 'Type': 120, 'Stock': 100},
                rows)

        def _near_expiry():
            threshold = str(today + timedelta(days=near_days))
            cursor.execute("""SELECT name, batch_no, expiry_date, stock_qty
                              FROM medicines
                              WHERE expiry_date>? AND expiry_date<=?
                              ORDER BY expiry_date ASC""", (today_str, threshold))
            rows = []
            for r in cursor.fetchall():
                try:
                    exp_dt  = datetime.strptime(r[2], '%Y-%m-%d').date()
                    days    = (exp_dt - today).days
                    parts   = r[2].split('-')
                    exp_fmt = f"{parts[1]}/{parts[0][2:]}" if len(parts)==3 else r[2]
                except Exception:
                    days, exp_fmt = '', r[2]
                rows.append((r[0], r[1] or '', exp_fmt, f'{days}d', r[3]))
            _show_list_dialog(
                f'Near Expiry (within {near_days} days)',
                ('Medicine', 'Batch', 'Expiry', 'Days Left', 'Stock'),
                {'Medicine': 260, 'Batch': 100, 'Expiry': 90,
                 'Days Left': 90, 'Stock': 80},
                rows)

        def _expired():
            cursor.execute("""SELECT name, batch_no, expiry_date, stock_qty
                              FROM medicines WHERE expiry_date<=?
                              ORDER BY expiry_date DESC""", (today_str,))
            rows = []
            for r in cursor.fetchall():
                try:
                    exp_dt   = datetime.strptime(r[2], '%Y-%m-%d').date()
                    days_ago = (today - exp_dt).days
                    parts    = r[2].split('-')
                    exp_fmt  = f"{parts[1]}/{parts[0][2:]}" if len(parts)==3 else r[2]
                except Exception:
                    days_ago, exp_fmt = '', r[2]
                rows.append((r[0], r[1] or '', exp_fmt, f'{days_ago}d ago', r[3]))
            _show_list_dialog(
                'Expired / Return',
                ('Medicine', 'Batch', 'Expiry', 'Days Ago', 'Stock'),
                {'Medicine': 260, 'Batch': 100, 'Expiry': 90,
                 'Days Ago': 90, 'Stock': 80},
                rows)

        m.add_command(label="⚠️ Low Stock / Out of Stock",       command=_low_stock)
        m.add_command(label=f"📅 Near Expiry (within {near_days}d)", command=_near_expiry)
        m.add_command(label="❌ Expired / Return",                command=_expired)
        if not stock_btn:
            return
        m.post(stock_btn.winfo_rootx(),
               stock_btn.winfo_rooty() + stock_btn.winfo_height())

    def _make_btn(parent, text, cmd, style, key):
        if not is_quick_access_visible(key):
            return None
        try:
            b = ttk.Button(parent, text=text, command=cmd,
                           bootstyle=style, width=22)
        except Exception:
            b = ttk.Button(parent, text=text, command=cmd, width=22)
        b.pack(fill=tk.X, pady=2)
        return b

    def _sep_if_needed():
        ttk.Separator(btn_row, orient='horizontal').pack(fill=tk.X, pady=4)

    primary_btns = [
        _make_btn(btn_row, '➕ New Bill', open_billing_fn, 'success', 'new_bill'),
        _make_btn(btn_row, '📦 New Purchase', open_purchase_fn, 'primary', 'new_purchase'),
        _make_btn(btn_row, '🔍 Search Medicine', open_inventory_fn, 'info', 'search_medicine'),
        _make_btn(btn_row, '👤 Contacts', open_contacts_fn, 'secondary', 'contacts'),
        _make_btn(btn_row, '📊 Ledger', open_ledger_fn, 'danger', 'ledger'),
    ]
    export_btns = [
        _make_btn(btn_row, '📊 Export Sales', lambda: _export('sales'), 'success', 'export_sales'),
        _make_btn(btn_row, '📦 Export Purchases', lambda: _export('purchases'), 'primary', 'export_purchases'),
        _make_btn(btn_row, '🗃 Export Inventory', lambda: _export('inventory'), 'info', 'export_inventory'),
        _make_btn(btn_row, '📁 Export All', lambda: _export('all'), 'warning', 'export_all'),
    ]
    if any(primary_btns) and any(export_btns):
        _sep_if_needed()
    bills_btn = _make_btn(btn_row, '🧾 Bills & Due ▾', _bills_menu, 'secondary', 'bills_due')
    stock_btn = _make_btn(btn_row, '📦 Stock & Expiry ▾', _stock_menu, 'warning', 'stock_expiry')
    dropdown_btns = [b for b in (bills_btn, stock_btn) if b]
    if (any(primary_btns) or any(export_btns)) and dropdown_btns:
        _sep_if_needed()
    general_btn = _make_btn(
        btn_row, '🏷 General Products', open_general_products_fn, 'info', 'general_products',
    )
    if not any(primary_btns + export_btns + dropdown_btns + [general_btn]):
        ttk.Label(
            btn_row,
            text='No quick actions enabled.\nEnable buttons in Settings → Appearance.',
            font=(FONT_FAMILY, FONT_SIZE_DEFAULT),
        ).pack(pady=8)

    # ── Banner (fills remaining space to the right) ───────────────────────
    banner_frame = ttk.LabelFrame(body, text='')
    banner_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    try:
        from PIL import Image, ImageTk
        banner_path = get_home_banner_path()
        target_w, target_h = get_home_banner_size()
        img = Image.open(banner_path)
        if target_h <= 0:
            ratio = target_w / max(img.width, 1)
            target_h = max(1, int(img.height * ratio))
        img = img.resize((target_w, target_h), Image.LANCZOS)
        _banner_photo = ImageTk.PhotoImage(img)
        banner_lbl = tk.Label(banner_frame, image=_banner_photo, bd=0)
        banner_lbl.image = _banner_photo
        banner_lbl.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
    except Exception as e:
        # Fallback: plain text card if PIL not available or image missing
        fb = ttk.Frame(banner_frame)
        fb.pack(fill=tk.X, padx=8, pady=8)
        cursor.execute("SELECT name, address, phone, gstin, dl_number FROM pharmacy_profile LIMIT 1")
        prof = cursor.fetchone()
        if prof:
            ttk.Label(fb, text=prof[0] or 'Satpuda Medical Store',
                      font=(FONT_FAMILY, FONT_SIZE_SECTION_TITLE, 'bold')).pack()
            for line in (prof[1], prof[2], prof[3], prof[4]):
                if line:
                    ttk.Label(fb, text=line,
                              font=(FONT_FAMILY, FONT_SIZE_DEFAULT)).pack()
        else:
            ttk.Label(fb, text=f'Banner not found: {get_home_banner_path()}',
                      font=(FONT_FAMILY, FONT_SIZE_DEFAULT),
                      foreground='red').pack()

    # ── Register canvas ───────────────────────────────────────────────────
    # Pack stats footer last so it appears at the bottom
    stats_frame.pack(fill=tk.X, pady=(8, 0))

    inner.update_idletasks()
    if hasattr(inner, '_canvas'):
        inner._canvas.configure(scrollregion=inner._canvas.bbox('all'))

    def _register():
        try:
            register_canvas_fn(inner)
        except Exception:
            pass
    main_frame.after(50, _register)
