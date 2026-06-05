"""
Central registry of table columns per page, with optional DB field names.
Used by Settings → Appearance → Column Visibility and by each page's Treeview.
Export column prefs are per report (export menu button), not only per page.
"""

import json
import os

from core.layout_config import load_layout

# page_key -> list of (display_name, db_table.db_column or '')
TABLE_COLUMNS = {
    'billing': [
        ('Medicine', 'medicines.name'),
        ('Batch', 'medicines.batch_no'),
        ('Expiry', 'medicines.expiry_date'),
        ('Qty', 'sales_items.qty'),
        ('Type', 'medicines.type'),
        ('MRP', 'medicines.mrp'),
        ('Disc ₹', 'sales_items.item_discount'),
        ('Amount', 'sales_items.amount'),
        ('Schedule', 'medicines.schedule'),
        ('Location', 'medicines.location'),
    ],
    'inventory': [
        ('Name', 'medicines.name'),
        ('Type', 'medicines.type'),
        ('Batch', 'medicines.batch_no'),
        ('Expiry', 'medicines.expiry_date'),
        ('Days Left', ''),
        ('Stock', 'medicines.stock_qty'),
        ('Unit', 'medicines.unit'),
        ('MRP', 'medicines.mrp'),
        ('Rate', 'medicines.rate'),
        ('Manufacturer', 'medicines.manufacturer'),
        ('Schedule', 'medicines.schedule'),
        ('Location', 'medicines.location'),
    ],
    'sales_history': [
        ('Bill No', 'sales.bill_no'),
        ('Date', 'sales.bill_date'),
        ('Customer', 'customers.name'),
        ('Phone', 'customers.phone'),
        ('Doctor', 'sales.doctor_name'),
        ('Schedule', 'medicines.schedule'),
        ('Total Amount', 'sales.total_amount'),
        ('Discount', 'sales.discount'),
        ('Amount Paid', 'sales.amount_paid'),
        ('Cash Paid', 'sales.cash_paid'),
        ('Online Paid', 'sales.online_paid'),
        ('Previous Due', 'sales.previous_due'),
        ('Due Amount', 'sales.due_amount'),
        ('Credit Amount', 'sales.credit_amount'),
        ('Total Due', 'sales.total_due'),
    ],
    'purchase_history': [
        ('Bill No', 'purchases.bill_number'),
        ('Date', 'purchases.purchase_date'),
        ('Supplier', 'suppliers.name'),
        ('Phone', 'suppliers.phone'),
        ('Final Amount', 'purchases.final_amount'),
        ('Paid at Entry', 'purchases.amount_paid_at_entry'),
        ('Paid via Payment', 'supplier_payments.amount'),
        ('Returns', 'purchase_returns.refund_amount'),
        ('Entry Due', ''),
        ('Status', ''),
        ('Items', 'purchase_items'),
    ],
    'purchase': [
        ('Medicine', 'medicines.name'),
        ('Type', 'medicines.type'),
        ('Batch', 'medicines.batch_no'),
        ('Expiry', 'medicines.expiry_date'),
        ('Qty', 'purchase_items.qty'),
        ('Pack', ''),
        ('HSN', 'medicines.hsn_code'),
        ('Free', 'purchase_items.free_qty'),
        ('Rate', 'purchase_items.rate'),
        ('Disc%', 'purchase_items.discount_percent'),
        ('GST%', 'purchase_items.gst_percent'),
        ('Taxable', ''),
        ('GST Amt', ''),
        ('Amount', 'purchase_items.amount'),
    ],
    'customers': [
        ('Name', 'customers.name'),
        ('Phone', 'customers.phone'),
        ('Address', 'customers.address'),
        ('Total Due', 'customers.total_due'),
        ('Credit', 'customers.total_credit'),
    ],
    'doctors': [
        ('Name', 'doctors.name'),
        ('Registration No', 'doctors.registration_number'),
        ('Phone', 'doctors.phone'),
        ('Created Date', 'doctors.created_at'),
    ],
    'suppliers': [
        ('Name', 'suppliers.name'),
        ('Phone', 'suppliers.phone'),
        ('GSTIN', 'suppliers.gstin'),
        ('Address', 'suppliers.address'),
        ('Total Due', 'suppliers.total_due'),
        ('Credit', 'suppliers.total_credit'),
        ('Status', ''),
    ],
}

# page_key -> report_key -> (menu label, column names for that export)
EXPORT_REPORTS = {
    'sales_history': {
        'sales_register': (
            'Sales Register (all bills)',
            ['Bill No', 'Date', 'Customer', 'Phone', 'Total Amount', 'Discount',
             'Cash Paid', 'Online Paid', 'Amount Paid', 'Previous Due', 'Due Amount',
             'Total Due', 'Doctor'],
        ),
        'monthly_summary': (
            'Monthly Summary',
            ['Month', 'Bills', 'Total Sales', 'Discount', 'Cash Paid', 'Online Paid',
             'Amount Paid', 'Due Amount'],
        ),
        'daily_summary': (
            'Daily Sales Summary',
            ['Date', 'Bills', 'Total Amount', 'Cash Paid', 'Online Paid', 'Amount Paid', 'Due Amount'],
        ),
        'customer_due': (
            'Customer Due Report',
            ['Customer', 'Phone', 'Date', 'Bill No', 'Total Amount', 'Due Amount'],
        ),
        'doctor_wise': (
            'Doctor-wise Sales',
            ['Doctor', 'Date', 'Customer', 'Bill No', 'Medicine', 'Schedule', 'Qty', 'Rate', 'Amount'],
        ),
        'payment_mode': (
            'Payment Mode Report',
            ['Date', 'Bill No', 'Customer', 'Total Amount', 'Cash Paid', 'Online Paid',
             'Amount Paid', 'Due Amount'],
        ),
        'schedule_report': (
            'Schedule Report',
            ['Date', 'Customer', 'Doctor', 'Medicine', 'Content/Drug', 'Schedule',
             'Expiry', 'Qty', 'Rate', 'Amount'],
        ),
    },
    'inventory': {
        'stock_statement': (
            'Stock Statement (all)',
            ['Name', 'Type', 'Batch', 'Expiry', 'Stock', 'Unit', 'MRP', 'Rate', 'Manufacturer', 'Schedule'],
        ),
        'near_expiry': (
            'Near Expiry Report',
            ['Name', 'Type', 'Batch', 'Expiry', 'Stock', 'Manufacturer'],
        ),
        'expired_stock': (
            'Expired Stock Report',
            ['Name', 'Type', 'Batch', 'Expiry', 'Stock', 'Manufacturer'],
        ),
        'schedule_wise_stock': (
            'Schedule-wise Stock',
            ['Schedule', 'Name', 'Batch', 'Expiry', 'Stock', 'MRP'],
        ),
    },
    'purchase_history': {
        'purchase_register': (
            'Purchase Register',
            ['Bill No', 'Date', 'Supplier', 'Phone', 'Final Amount', 'Paid at Entry', 'Returns'],
        ),
        'monthly_summary': (
            'Monthly Purchase Summary',
            ['Month', 'Purchases', 'Final Amount', 'Paid at Entry'],
        ),
        'gst_purchase': (
            'GST Purchase Report',
            ['Bill No', 'Date', 'Supplier', 'Medicine', 'HSN', 'GST%', 'Qty', 'Rate', 'Amount'],
        ),
    },
    'suppliers': {
        'supplier_due': (
            'Supplier Due Report',
            ['Name', 'Phone', 'Total Due', 'Credit'],
        ),
    },
    'customers': {
        'customer_list': (
            'Customer List (all)',
            ['Name', 'Phone', 'Address', 'Total Due', 'Credit'],
        ),
        'customer_due_list': (
            'Customer Due List',
            ['Name', 'Phone', 'Total Due', 'Credit'],
        ),
    },
}

PAGE_LABELS = {
    'billing': 'Billing — Selected Medicines',
    'inventory': 'Inventory — Medicine List',
    'sales_history': 'Sales History — Bills List',
    'purchase_history': 'Purchase History — Purchases List',
    'purchase': 'Purchase — Items List',
    'customers': 'Contacts — Customers List',
    'doctors': 'Settings — Doctors List',
    'suppliers': 'Settings — Suppliers List',
}

EXPORT_PAGE_LABELS = {
    pk: PAGE_LABELS.get(pk, pk)
    for pk in EXPORT_REPORTS
}

QUICK_ACCESS_BUTTONS = [
    ('new_bill', '➕ New Bill'),
    ('new_purchase', '📦 New Purchase'),
    ('search_medicine', '🔍 Search Medicine'),
    ('contacts', '👤 Contacts'),
    ('ledger', '📊 Ledger'),
    ('export_sales', '📊 Export Sales'),
    ('export_purchases', '📦 Export Purchases'),
    ('export_inventory', '🗃 Export Inventory'),
    ('export_all', '📁 Export All'),
    ('bills_due', '🧾 Bills & Due'),
    ('stock_expiry', '📦 Stock & Expiry'),
    ('general_products', '🏷 General Products'),
]

_DEFAULT_QUICK_ACCESS = {
    key: (key != 'general_products')
    for key, _ in QUICK_ACCESS_BUTTONS
}


def all_column_names(page_key):
    return [col for col, _ in TABLE_COLUMNS.get(page_key, [])]


def default_column_visibility():
    out = {}
    for page_key, cols in TABLE_COLUMNS.items():
        out[page_key] = {col: True for col, _ in cols}
    return out


def default_export_column_visibility():
    """All export reports: every column enabled by default."""
    out = {}
    for page_key, reports in EXPORT_REPORTS.items():
        out[page_key] = {}
        for report_key, (_label, columns) in reports.items():
            out[page_key][report_key] = {col: True for col in columns}
    return out


def _normalize_page_export_saved(page_key, raw_page):
    """Support legacy flat {col: bool} and nested {report_key: {col: bool}}."""
    if not raw_page:
        return {}
    if any(isinstance(v, dict) for v in raw_page.values()):
        return {k: dict(v) for k, v in raw_page.items() if isinstance(v, dict)}
    if all(isinstance(v, bool) for v in raw_page.values()):
        legacy = dict(raw_page)
        nested = {}
        for report_key in EXPORT_REPORTS.get(page_key, {}):
            nested[report_key] = dict(legacy)
        return nested
    return {}


def get_column_visibility(page_key=None):
    cfg = load_layout()
    saved = cfg.get('column_visibility') or {}
    defaults = default_column_visibility()
    if page_key:
        page = dict(defaults.get(page_key, {}))
        page.update(saved.get(page_key, {}))
        return page
    merged = {}
    for pk, cols in defaults.items():
        merged[pk] = dict(cols)
        merged[pk].update(saved.get(pk, {}))
    return merged


def get_visible_columns(page_key, all_columns=None):
    """Return ordered list of visible column names (at least one column always shown)."""
    if all_columns is None:
        all_columns = all_column_names(page_key)
    vis = get_column_visibility(page_key)
    visible = [c for c in all_columns if vis.get(c, True)]
    return visible or list(all_columns)


def apply_column_visibility(tree, page_key, all_columns=None):
    """Set Treeview displaycolumns from saved preferences."""
    if all_columns is None:
        all_columns = list(tree['columns'])
    visible = get_visible_columns(page_key, all_columns)
    tree.configure(displaycolumns=visible)


def get_export_column_visibility(page_key, report_key):
    """Per-report export column prefs for a page export menu option."""
    cfg = load_layout()
    saved = cfg.get('export_column_visibility') or {}
    defaults = default_export_column_visibility()
    report_cols = list(EXPORT_REPORTS.get(page_key, {}).get(report_key, (None, []))[1])
    page_default = {col: True for col in report_cols}
    page_saved = _normalize_page_export_saved(page_key, saved.get(page_key, {}))
    out = dict(page_default)
    if report_key in page_saved:
        out.update(page_saved[report_key])
    elif page_saved:
        first = next(iter(page_saved.values()), {})
        if isinstance(first, dict):
            out.update(first)
    screen = get_column_visibility(page_key)
    for col in report_cols:
        if col not in out and col in screen:
            out[col] = screen[col]
    return out


def get_export_visible_columns(page_key, headers, report_key):
    headers = list(headers)
    vis = get_export_column_visibility(page_key, report_key)
    visible = [c for c in headers if vis.get(c, True)]
    return visible or headers


def _tree_display_columns(tree, all_cols):
    disp = tree.cget('displaycolumns')
    if not disp or disp == ('#all',) or (isinstance(disp, (tuple, list)) and '#all' in disp):
        return list(all_cols)
    visible = [c for c in disp if c in all_cols]
    return visible or list(all_cols)


def export_tree_current_view(tree):
    """
    Export exactly what the user sees: on-screen columns and rows currently in the tree
    (after filters). Does not use export-column report settings.
    """
    all_cols = list(tree['columns'])
    visible = _tree_display_columns(tree, all_cols)
    indices = [all_cols.index(c) for c in visible]
    rows = []
    for iid in tree.get_children(''):
        vals = list(tree.item(iid)['values'])
        rows.append([vals[i] if i < len(vals) else '' for i in indices])
    return visible, rows


def export_tree_data(tree, page_key, all_columns=None):
    """Deprecated alias — current view uses on-screen columns only."""
    return export_tree_current_view(tree)


def export_table(parent, title, headers, rows, filename, page_key, report_key):
    """Export with a column picker, then format chooser (CSV / Excel / PDF)."""
    from core.export_manager import export_data
    from core.themed_messagebox import showinfo
    col_vis = prompt_export_columns(parent, page_key, report_key, list(headers))
    if col_vis is None:
        return
    h, r = filter_export_table(list(headers), rows, page_key, report_key, column_vis=col_vis)
    if not r:
        showinfo(
            "Export",
            "No export columns selected. Enable columns under Settings → Appearance → "
            "Export Report Columns.",
        )
        return
    export_data(parent, title, h, r, filename)


def filter_export_table(headers, rows, page_key, report_key, column_vis=None):
    """Subset headers/rows using per-report export column prefs."""
    headers = list(headers)
    if column_vis is not None:
        visible = [c for c in headers if column_vis.get(c, True)]
        visible = visible or headers
    else:
        visible = get_export_visible_columns(page_key, headers, report_key)
    if visible == headers:
        return headers, rows
    idx = [headers.index(c) for c in visible]
    out_rows = []
    for row in rows:
        r = list(row)
        out_rows.append([r[i] if i < len(r) else '' for i in idx])
    return visible, out_rows


def update_export_column_visibility(page_key, report_key, col_vis):
    """Persist export column choices without a full Appearance save."""
    from core.layout_config import _get_config_path, save_layout
    path = _get_config_path()
    data = {}
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            data = {}
    export_vis = data.get('export_column_visibility') or {}
    page = _normalize_page_export_saved(page_key, export_vis.get(page_key, {}))
    page[report_key] = dict(col_vis)
    export_vis[page_key] = page
    data['export_column_visibility'] = export_vis
    save_layout(data)


def prompt_export_columns(parent, page_key, report_key, headers):
    """
    Let the user choose export columns before running a report export.
    Returns {column: bool} or None if cancelled. At least one column required.
    """
    import tkinter as tk
    from core.scroll_manager import open_dialog
    from core.themed_messagebox import showwarning

    try:
        import ttkbootstrap as ttk
    except ImportError:
        from tkinter import ttk

    headers = list(headers)
    if not headers:
        return {}

    report_label = EXPORT_REPORTS.get(page_key, {}).get(report_key, (report_key, headers))[0]
    page_label = EXPORT_PAGE_LABELS.get(page_key, page_key)
    saved = get_export_column_visibility(page_key, report_key)

    dlg = open_dialog(
        parent,
        f"Export columns — {report_label}",
        width=420,
        height=min(520, 140 + len(headers) * 28),
        resizable=True,
    )
    body = dlg.content
    ttk.Label(
        body,
        text=f"{page_label}\nSelect columns to include in this export:",
        wraplength=380,
    ).pack(padx=12, pady=(10, 6), anchor='w')

    vars_map = {}
    chk_frame = ttk.Frame(body)
    chk_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)
    for col in headers:
        var = tk.BooleanVar(value=saved.get(col, True))
        vars_map[col] = var
        ttk.Checkbutton(chk_frame, text=col, variable=var).pack(anchor=tk.W, pady=2)

    remember = tk.BooleanVar(value=True)
    ttk.Checkbutton(
        body,
        text="Remember these columns for this report",
        variable=remember,
    ).pack(anchor=tk.W, padx=12, pady=(4, 8))

    result = {'cancelled': True}

    def select_all():
        for var in vars_map.values():
            var.set(True)

    def clear_all():
        for var in vars_map.values():
            var.set(False)

    btn_row = ttk.Frame(body)
    btn_row.pack(fill=tk.X, padx=12, pady=(0, 4))
    ttk.Button(btn_row, text="Select all", command=select_all).pack(side=tk.LEFT, padx=(0, 6))
    ttk.Button(btn_row, text="Clear all", command=clear_all).pack(side=tk.LEFT)

    def on_ok():
        picked = {col: var.get() for col, var in vars_map.items()}
        if not any(picked.values()):
            showwarning(
                "Export Columns",
                "Select at least one column to export.",
                parent=dlg,
            )
            return
        if remember.get():
            update_export_column_visibility(page_key, report_key, picked)
        result.update({'cancelled': False, 'visibility': picked})
        dlg.destroy()

    def on_cancel():
        dlg.destroy()

    try:
        ttk.Button(dlg.footer, text="Continue", command=on_ok, bootstyle='primary').pack(
            side=tk.LEFT, padx=4)
        ttk.Button(dlg.footer, text="Cancel", command=on_cancel, bootstyle='secondary').pack(
            side=tk.RIGHT, padx=4)
    except Exception:
        ttk.Button(dlg.footer, text="Continue", command=on_ok).pack(side=tk.LEFT, padx=4)
        ttk.Button(dlg.footer, text="Cancel", command=on_cancel).pack(side=tk.RIGHT, padx=4)

    dlg.wait_window()
    if result.get('cancelled'):
        return None
    return result.get('visibility')


def get_quick_access_settings():
    cfg = load_layout()
    saved = cfg.get('quick_access') or {}
    out = dict(_DEFAULT_QUICK_ACCESS)
    out.update(saved)
    return out


def is_quick_access_visible(button_key):
    return get_quick_access_settings().get(button_key, True)
