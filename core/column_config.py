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
        ('MRP/Tab', ''),
        ('Rate', 'medicines.rate'),
        ('Rate/Tab', ''),
        ('Manufacturer', 'medicines.manufacturer'),
        ('Supplier Name', ''),
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
        ('Purchase No', 'purchases.purchase_no'),
        ('Bill No', 'purchases.bill_number'),
        ('Date', 'purchases.purchase_date'),
        ('Supplier', 'suppliers.name'),
        ('Phone', 'suppliers.phone'),
        ('Final Amount', 'purchases.final_amount'),
        ('Paid at Entry', 'purchases.amount_paid_at_entry'),
        ('Cash Paid', 'purchases.cash_paid_at_entry'),
        ('Online Paid', 'purchases.online_paid_at_entry'),
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
        ('Schedule', 'medicines.schedule'),
        ('Free', 'purchase_items.free_qty'),
        ('MRP', 'purchase_items.mrp'),
        ('MRP/Tab', ''),
        ('Rate', 'purchase_items.rate'),
        ('Rate/Tab', ''),
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
            ['Date', 'Bill No', 'Customer', 'Doctor', 'Medicine', 'Batch', 'Content/Drug', 'Schedule',
             'Expiry', 'Qty', 'Rate', 'Amount'],
        ),
    },
    'inventory': {
        'stock_statement': (
            'Stock Statement (all)',
            ['Name', 'Type', 'Batch', 'Expiry', 'Stock', 'Unit',
             'MRP', 'MRP/Tab', 'Rate', 'Rate/Tab', 'Manufacturer', 'Schedule'],
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
            ['Bill No', 'Date', 'Supplier', 'Phone', 'Final Amount', 'Paid at Entry', 'Cash Paid', 'Online Paid', 'Returns'],
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

# Columns the DESKTOP screens cannot draw, whatever the tick says. The
# registry above is shared with the Tk trees, which do render these; on the
# React pages they are not in the column list at all, so a checkbox for them
# is a control with nothing on the other end. Ticking "Returns" on Purchase
# History did nothing and never could.
DESKTOP_MISSING_COLUMNS = {
    'sales_history': {'Schedule'},
    'purchase_history': {'Paid via Payment', 'Returns', 'Items'},
    # The Purchase items table draws fifteen of the eighteen registry columns.
    'purchase': {'Pack', 'MRP/Tab', 'Rate/Tab'},
}


def desktop_table_columns():
    """TABLE_COLUMNS minus the columns the desktop screens never render."""
    out = {}
    for page, cols in TABLE_COLUMNS.items():
        missing = DESKTOP_MISSING_COLUMNS.get(page, set())
        out[page] = [(name, db) for name, db in cols if name not in missing]
    return out


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
    ('alerts', '🔔 Alerts'),
    ('general_products', '🏷 General Products'),
]

_DEFAULT_QUICK_ACCESS = {
    key: (key != 'general_products')
    for key, _ in QUICK_ACCESS_BUTTONS
}

DASHBOARD_SECTIONS = [
    ('home_dashboard', 'Home — Dashboard stats'),
    ('inventory_summary', 'Inventory — Summary bar'),
    ('sales_summary', 'Sales History — Summary bar'),
    ('purchase_summary', 'Purchase History — Summary bar'),
]

_DEFAULT_DASHBOARD_SECTIONS = {
    key: True
    for key, _ in DASHBOARD_SECTIONS
}


def all_column_names(page_key):
    return [col for col, _ in TABLE_COLUMNS.get(page_key, [])]


def default_column_visibility():
    """Defaults for Settings → Layout & Lists → Column Visibility.

    A few ledger columns stay hidden until the user turns them on
    (Location, MRP/Tab, Rate/Tab, sales due/credit fields, purchase Paid at Entry).
    """
    hidden = {
        'inventory': {'Location', 'MRP/Tab', 'Rate/Tab'},
        'sales_history': {
            'Discount',
            'Cash Paid',
            'Online Paid',
            'Previous Due',
            'Due Amount',
            'Credit Amount',
        },
        'purchase_history': {'Bill No', 'Paid at Entry', 'Returns'},
    }
    out = {}
    for page_key, cols in TABLE_COLUMNS.items():
        hide = hidden.get(page_key) or set()
        out[page_key] = {col: (col not in hide) for col, _ in cols}
    return out


def default_export_column_visibility():
    """Export reports: Discount off by default; other columns on."""
    out = {}
    for page_key, reports in EXPORT_REPORTS.items():
        out[page_key] = {}
        for report_key, (_label, columns) in reports.items():
            out[page_key][report_key] = {
                col: (col != 'Discount') for col in columns
            }
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


def _migrate_purchase_history_column_prefs(page: dict) -> dict:
    """Sr column renamed to Purchase No (same setting)."""
    if 'Sr' in page and 'Purchase No' not in page:
        page = dict(page)
        page['Purchase No'] = page.pop('Sr')
    return page


def _migrate_sales_history_column_prefs(page: dict, cfg: dict) -> dict:
    """Hide Discount / show Total Due by default (one-time layout flip)."""
    page = dict(page or {})
    if cfg.get('sales_history_cols_v2'):
        return page
    page['Discount'] = False
    page['Total Due'] = True
    return page


def _ensure_sales_history_cols_v2(cfg: dict, page: dict) -> None:
    """Persist one-time column default flip so user toggles stick afterward."""
    if cfg.get('sales_history_cols_v2'):
        return
    try:
        from core.layout_config import save_layout
        vis = dict(cfg.get('column_visibility') or {})
        vis['sales_history'] = dict(page)
        cfg = dict(cfg)
        cfg['column_visibility'] = vis
        cfg['sales_history_cols_v2'] = True
        save_layout(cfg)
    except Exception:
        pass


def get_column_visibility(page_key=None):
    cfg = load_layout()
    saved = cfg.get('column_visibility') or {}
    defaults = default_column_visibility()
    if page_key:
        page = dict(defaults.get(page_key, {}))
        raw = saved.get(page_key, {})
        if page_key == 'purchase_history':
            raw = _migrate_purchase_history_column_prefs(raw)
        if page_key == 'sales_history':
            raw = _migrate_sales_history_column_prefs(raw, cfg)
        page.update(raw)
        if page_key == 'sales_history':
            # Defaults already flipped; ensure saved prefs get the one-shot update.
            if not cfg.get('sales_history_cols_v2'):
                page['Discount'] = False
                page['Total Due'] = True
                _ensure_sales_history_cols_v2(cfg, page)
        return page
    merged = {}
    for pk, cols in defaults.items():
        merged[pk] = dict(cols)
        raw = saved.get(pk, {})
        if pk == 'purchase_history':
            raw = _migrate_purchase_history_column_prefs(raw)
        if pk == 'sales_history':
            raw = _migrate_sales_history_column_prefs(raw, cfg)
        merged[pk].update(raw)
        if pk == 'sales_history' and not cfg.get('sales_history_cols_v2'):
            merged[pk]['Discount'] = False
            merged[pk]['Total Due'] = True
            _ensure_sales_history_cols_v2(cfg, merged[pk])
    return merged


def get_visible_columns(page_key, all_columns=None):
    """Return ordered list of visible column names (at least one column always shown)."""
    if all_columns is None:
        all_columns = all_column_names(page_key)
    vis = get_column_visibility(page_key)
    try:
        from core.record_indicators import INDICATOR_COL, STATUS_COL
        always_on = {INDICATOR_COL, STATUS_COL}
    except Exception:
        always_on = set()
    visible = [c for c in all_columns if vis.get(c, True) or c in always_on]
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
    """Export using column prefs from Settings → Appearance → Export report columns."""
    from core.export_manager import export_data
    from core.themed_messagebox import showinfo

    h, r = filter_export_table(list(headers), rows, page_key, report_key)
    if not r:
        showinfo(
            "Export",
            "No export columns selected. Enable columns under Settings → Appearance → "
            "Export report columns.",
        )
        return
    export_data(parent, title, h, r, filename)


def export_table_voice(parent, title, headers, rows, filename, page_key, report_key, fmt="csv"):
    """Voice export — saved column prefs, no dialogs, file goes to Downloads."""
    from core.export_manager import export_data_direct
    headers = list(headers)
    visible = get_export_visible_columns(page_key, headers, report_key)
    col_vis = {h: (h in visible) for h in headers}
    h, r = filter_export_table(headers, rows, page_key, report_key, column_vis=col_vis)
    if not r:
        return None, "No export columns enabled. Check Settings → Appearance → Export Report Columns."
    return export_data_direct(parent, title, h, r, filename, fmt)


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
    visible = [c for c in visible if c in headers]
    if not visible:
        return headers, rows
    idx = [headers.index(c) for c in visible]
    out_rows = []
    for row in rows:
        r = list(row)
        out_rows.append([r[i] if i < len(r) else '' for i in idx])
    return visible, out_rows


def get_quick_access_settings():
    cfg = load_layout()
    saved = cfg.get('quick_access') or {}
    out = dict(_DEFAULT_QUICK_ACCESS)
    out.update(saved)
    return out


def is_quick_access_visible(button_key):
    return get_quick_access_settings().get(button_key, True)


def get_dashboard_section_settings():
    cfg = load_layout()
    saved = cfg.get('dashboard_sections') or {}
    out = dict(_DEFAULT_DASHBOARD_SECTIONS)
    out.update(saved)
    return out


def is_dashboard_section_visible(section_key):
    return get_dashboard_section_settings().get(section_key, True)
