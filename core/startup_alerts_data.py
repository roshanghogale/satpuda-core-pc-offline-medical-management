"""Startup alert rows, gathered without any UI.

This lived in core/startup_alerts.py, whose first line is `import tkinter`. The
packaged desktop engine has no tkinter, so /api/startup/alerts raised
ModuleNotFoundError on every launch and the shop never saw its expiry or
low-stock warnings. The data gathering never needed Tk; only the window did.
"""
from __future__ import annotations

import sqlite3
from datetime import date


# Which page each alert offers to open. Data, not UI -- the desktop shell and
# the classic window both act on it.
_TAB_ACTIONS = {
    "Low Stock Alerts": ("reorder", "Reorder"),
    "Out of Stock": ("reorder", "Reorder"),
    "Near Expiry Alerts": ("return", "Return"),
    "Expired Medicines": ("return", "Return"),
    "Customer Due Alerts": (None, None),
}


# Which popup preference switches each tab (startup_alerts_prefs categories).
CATEGORY_BY_TITLE = {
    "Low Stock Alerts": "low_stock",
    "Out of Stock": "out_of_stock",
    "Near Expiry Alerts": "near_expiry",
    "Expired Medicines": "expired",
    "Customer Due Alerts": "customer_due",
}


def _online() -> bool:
    try:
        from core import sync_prefs

        return bool(sync_prefs.is_online_mode())
    except Exception:
        return False


def collect_startup_alerts(conn, db_path=None, categories=None):
    """Load alert rows (safe in a background thread).

    Online reads the store. `conn` is then the empty :memory: shell, and the SQL
    path found nothing there: zero tabs, show=False, and the popup never opened
    on any Online shop. A failed store read RAISES -- it must not look like a
    clean shelf.
    """
    if _online():
        alerts = _collect_alert_data_online(conn)
    elif db_path:
        bg = sqlite3.connect(db_path, check_same_thread=False)
        try:
            alerts = _collect_alert_data(bg)
        finally:
            bg.close()
    else:
        alerts = _collect_alert_data(conn)
    if categories:
        alerts = [
            a for a in alerts
            if categories.get(CATEGORY_BY_TITLE.get(a["title"], ""), True)
        ]
    return [a for a in alerts if a["rows"]]


def _collect_alert_data(conn):
    from core.alert_monitoring_service import (
        fetch_customer_due_summary,
        fetch_expired_medicines,
        fetch_low_stock_alerts,
        fetch_near_expiry_medicines,
        fetch_out_of_stock_medicines,
    )
    from core.alert_thresholds import alert_expiry_date
    from core.medicine_visibility import HIDDEN_FILTER_SQL

    cur = conn.cursor()
    today = date.today()

    low_stock = []
    for name, qty, unit, _supplier in fetch_low_stock_alerts(conn):
        # The hidden filter fetch_low_stock_alerts applies: without it a batch
        # the shop removed from lists came back as a row here.
        cur.execute(
            f"""
            SELECT COALESCE(type,''), COALESCE(batch_no,''), COALESCE(stock_qty,0)
            FROM medicines
            WHERE name=? AND COALESCE(stock_qty,0) > 0 AND {HIDDEN_FILTER_SQL}
            ORDER BY batch_no
            """,
            (name,),
        )
        batches = cur.fetchall()
        if batches:
            for med_type, batch_no, stock_qty in batches:
                low_stock.append((
                    name,
                    med_type,
                    batch_no,
                    float(stock_qty or 0),
                    unit or "",
                ))
        else:
            cur.execute(
                "SELECT COALESCE(type,'') FROM medicines WHERE name=? LIMIT 1",
                (name,),
            )
            row = cur.fetchone()
            med_type = row[0] if row else ""
            low_stock.append((name, med_type, "", qty, unit or ""))

    near_expiry = []
    for name, batch, expiry_display, days_left, qty, _supplier, _bill in fetch_near_expiry_medicines(conn):
        cur.execute(
            "SELECT COALESCE(type,'') FROM medicines WHERE name=? AND COALESCE(batch_no,'')=? LIMIT 1",
            (name, batch or ""),
        )
        row = cur.fetchone()
        med_type = row[0] if row else ""
        near_expiry.append((name, med_type, batch, expiry_display, days_left))

    expired = []
    for name, batch, expiry_display, _qty, _supplier, _bill in fetch_expired_medicines(conn):
        cur.execute(
            f"""
            SELECT COALESCE(type,''), COALESCE(expiry_date,'')
            FROM medicines
            WHERE name=? AND COALESCE(batch_no,'')=? AND {HIDDEN_FILTER_SQL}
            LIMIT 1
            """,
            (name, batch or ""),
        )
        row = cur.fetchone()
        med_type = row[0] if row else ""
        expiry_raw = row[1] if row else expiry_display
        expiry_dt = alert_expiry_date(expiry_raw)
        days_expired = max(0, (today - expiry_dt).days) if expiry_dt else 0
        expired.append((name, med_type, batch, expiry_display, days_expired))

    out_of_stock = []
    for name, unit, mrp, rate, med_type, supplier in fetch_out_of_stock_medicines(conn):
        out_of_stock.append((
            name,
            unit or "",
            round(float(mrp or 0), 2),
            round(float(rate or 0), 2),
            med_type or "",
            supplier or "",
        ))

    customer_due = fetch_customer_due_summary(conn)
    return _tabs(low_stock, out_of_stock, near_expiry, expired, customer_due)


def _collect_alert_data_online(conn):
    """The same five tabs, built from the store's shelf and customer balances.

    Row shapes match _collect_alert_data exactly, so the popup reads the same in
    both modes (tests/test_startup_alerts_reach_the_shop.py holds them together).
    """
    from core.alert_monitoring_service import (
        fetch_customer_due_summary_online,
        online_stock_sections,
        online_visible_medicines,
    )
    from core.alert_thresholds import alert_expiry_date

    meds = online_visible_medicines()
    sections = online_stock_sections(conn, meds)
    today = date.today()
    by_name: dict = {}
    first: dict = {}
    for m in meds:
        name = str(m.get("name") or "").strip()
        batch = str(m.get("batch_no") or "")
        by_name.setdefault(name, []).append(m)
        first.setdefault((name, batch), m)

    def _type(name, batch):
        return str((first.get((name, batch or "")) or {}).get("type") or "")

    low_stock = []
    for name, qty, unit, _supplier in sections["low_stock"]:
        batches = sorted(
            (m for m in by_name.get(name, []) if float(m.get("stock_qty") or 0) > 0),
            key=lambda m: str(m.get("batch_no") or ""),
        )
        for m in batches:
            low_stock.append((
                name,
                str(m.get("type") or ""),
                str(m.get("batch_no") or ""),
                float(m.get("stock_qty") or 0),
                unit or "",
            ))

    near_expiry = [
        (name, _type(name, batch), batch, disp, days_left)
        for name, batch, disp, days_left, _q, _s, _b in sections["near_expiry"]
    ]
    expired = []
    for name, batch, disp, _q, _s, _b in sections["expired"]:
        cut = alert_expiry_date((first.get((name, batch or "")) or {}).get("expiry_date"))
        expired.append((
            name, _type(name, batch), batch, disp,
            max(0, (today - cut).days) if cut else 0,
        ))
    out_of_stock = [
        (name, unit or "", round(float(mrp or 0), 2), round(float(rate or 0), 2),
         med_type or "", supplier or "")
        for name, unit, mrp, rate, med_type, supplier in sections["out_of_stock"]
    ]
    customer_due = fetch_customer_due_summary_online(conn)
    return _tabs(low_stock, out_of_stock, near_expiry, expired, customer_due)


def _alert(title, columns, rows):
    action_kind, action_label = _TAB_ACTIONS.get(title, (None, None))
    return {
        "title": title,
        "columns": columns,
        "rows": rows,
        "action_kind": action_kind,
        "action_label": action_label,
    }


def _tabs(low_stock, out_of_stock, near_expiry, expired, customer_due):
    return [
        _alert("Low Stock Alerts", ["Medicine", "Type", "Batch", "Stock", "Unit"], low_stock),
        _alert(
            "Out of Stock",
            ["Medicine", "Pack", "MRP", "Rate", "Type", "Supplier"],
            out_of_stock,
        ),
        _alert(
            "Near Expiry Alerts",
            ["Medicine", "Type", "Batch", "Expiry", "Days Left"],
            near_expiry,
        ),
        _alert(
            "Expired Medicines",
            ["Medicine", "Type", "Batch", "Expiry", "Days Expired"],
            expired,
        ),
        _alert("Customer Due Alerts", ["Customer", "Phone", "Due Amount"], customer_due),
    ]
