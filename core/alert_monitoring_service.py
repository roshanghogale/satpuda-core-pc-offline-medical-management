"""
Alert & Monitoring dashboard queries (Settings -> Alert & Monitoring).

Uses threshold values from Settings -> Layout & Lists -> Thresholds.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Set, Tuple

from core.alert_thresholds import (
    alert_expiry_date,
    bill_due_days,
    is_low_stock_qty,
    is_near_expiry,
    load_due_alert_settings,
    load_thresholds,
    parse_expiry,
    passes_due_alert_filter,
)
from core.medicine_visibility import HIDDEN_FILTER_SQL

_VISIBLE_MED_FILTER = f"WHERE {HIDDEN_FILTER_SQL}"


def _stock_by_medicine_name(conn) -> Dict[str, float]:
    cur = conn.cursor()
    cur.execute(f"""
        SELECT name, SUM(COALESCE(stock_qty, 0))
        FROM medicines
        {_VISIBLE_MED_FILTER}
        GROUP BY name
    """)
    return {row[0]: float(row[1] or 0) for row in cur.fetchall()}


def _normalize_pack_size(unit: str) -> str:
    return (unit or "").strip()


def _pack_key(name: str, unit: str) -> Tuple[str, str]:
    return (name, _normalize_pack_size(unit))


def _stock_by_name_and_pack(conn) -> Dict[Tuple[str, str], float]:
    cur = conn.cursor()
    cur.execute(f"""
        SELECT name, COALESCE(unit, ''), SUM(COALESCE(stock_qty, 0))
        FROM medicines
        {_VISIBLE_MED_FILTER}
        GROUP BY name, COALESCE(unit, '')
    """)
    return {
        _pack_key(row[0], row[1]): float(row[2] or 0)
        for row in cur.fetchall()
    }


def names_likely_same_product(a: str, b: str) -> bool:
    """True when two medicine names probably refer to the same product (e.g. UNIZIP vs UNIZIF)."""
    left = (a or "").strip().upper()
    right = (b or "").strip().upper()
    if not left or not right:
        return False
    if left == right:
        return True
    if left in right or right in left:
        return True
    if len(left) == len(right):
        diffs = sum(1 for x, y in zip(left, right) if x != y)
        if diffs == 1:
            return True
    return False


def medicine_names_fully_out_of_stock(conn) -> Set[str]:
    """Medicine names whose visible total stock across all batches and pack sizes is zero."""
    by_name = _stock_by_medicine_name(conn)
    return {name for name, total in by_name.items() if total <= 0}


def medicine_pack_keys_fully_out_of_stock(conn) -> Set[Tuple[str, str]]:
    """(name, pack_size) pairs with zero visible stock — one alert per packaging size."""
    by_pack = _stock_by_name_and_pack(conn)
    return {key for key, total in by_pack.items() if total <= 0}


def _latest_supplier_by_medicine_id(conn) -> Dict[int, str]:
    cur = conn.cursor()
    cur.execute("""
        SELECT pi.medicine_id, s.name, p.purchase_date, p.id
        FROM purchase_items pi
        JOIN purchases p ON p.id = pi.purchase_id
        LEFT JOIN suppliers s ON s.id = p.supplier_id
        WHERE pi.medicine_id IS NOT NULL
        ORDER BY p.purchase_date DESC, p.id DESC
    """)
    out: Dict[int, str] = {}
    for med_id, supplier, _pd, _pid in cur.fetchall():
        if med_id not in out and supplier:
            out[int(med_id)] = supplier
    return out


def online_latest_supplier_by_medicine_id() -> Dict[int, str]:
    """_latest_supplier_by_medicine_id for Online, from what the server already
    serves: the store's purchases (each with its lines) off the sync pull.
    Same pick -- the newest purchase by date, then id, that carries the medicine
    row and names a supplier; the supplier's name from the suppliers list, the
    purchase's own supplier_name when that list has no row for it."""
    from core import online_catalog as oc

    def _int(v: Any) -> int:
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    docs = [d for d in oc._pull_sync_pages("purchases", limit=50000) if isinstance(d, dict)]
    docs.sort(
        key=lambda d: (str(d.get("purchase_date") or ""), _int(d.get("id") or d.get("local_id"))),
        reverse=True,
    )
    names: Dict[int, str] = {}
    out: Dict[int, str] = {}
    for d in docs:
        if d.get("deleted"):
            continue
        sid = _int(d.get("supplier_id"))
        supplier = ""
        if sid:
            if sid not in names:
                try:
                    found = oc.find_supplier_by_id(sid) or {}
                except Exception:
                    found = {}
                names[sid] = str(found.get("name") or "").strip()
            supplier = names[sid]
        supplier = supplier or str(d.get("supplier_name") or "").strip()
        if not supplier:
            continue
        for it in d.get("items") or []:
            mid = _int(it.get("medicine_id")) if isinstance(it, dict) else 0
            if mid and mid not in out:
                out[mid] = supplier
    return out


def _bill_by_medicine_batch(conn) -> Dict[Tuple[int, str], str]:
    cur = conn.cursor()
    cur.execute("""
        SELECT pi.medicine_id, COALESCE(pi.batch_no, ''), p.bill_number,
               p.purchase_date, p.id
        FROM purchase_items pi
        JOIN purchases p ON p.id = pi.purchase_id
        ORDER BY p.purchase_date DESC, p.id DESC
    """)
    out: Dict[Tuple[int, str], str] = {}
    for med_id, batch, bill, _pd, _pid in cur.fetchall():
        key = (int(med_id), batch or "")
        if key not in out and bill:
            out[key] = str(bill)
    return out


def _fmt_expiry_display(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    dt = parse_expiry(text)
    if not dt:
        return text
    return dt.strftime("%d-%m-%Y")


def fetch_low_stock_alerts(conn) -> List[Tuple[Any, ...]]:
    cur = conn.cursor()
    low_thr, _ = load_thresholds(conn)
    suppliers = _latest_supplier_by_medicine_id(conn)
    oos = medicine_names_fully_out_of_stock(conn)

    cur.execute(f"""
        SELECT name, COALESCE(type,''), COALESCE(unit,''),
               COALESCE(stock_qty,0), id
        FROM medicines
        WHERE {HIDDEN_FILTER_SQL} AND COALESCE(stock_qty,0) > 0
        ORDER BY name COLLATE NOCASE, id
    """)

    by_name: Dict[str, Dict[str, Any]] = {}
    for name, med_type, unit, stock_qty, med_id in cur.fetchall():
        if name in oos:
            continue
        qty = float(stock_qty or 0)
        if qty <= 0:
            continue
        entry = by_name.get(name)
        if entry is None:
            entry = {
                "total_qty": 0.0,
                "type": med_type,
                "unit": unit,
                "sample_id": med_id,
            }
            by_name[name] = entry
        entry["total_qty"] += qty
        if med_type and not entry["type"]:
            entry["type"] = med_type
        if qty >= entry.get("_best_qty", 0):
            entry["_best_qty"] = qty
            if unit:
                entry["unit"] = unit
            if med_type:
                entry["type"] = med_type
            entry["sample_id"] = med_id

    rows: List[Tuple[Any, ...]] = []
    for name in sorted(by_name, key=lambda n: n.lower()):
        info = by_name[name]
        info.pop("_best_qty", None)
        qty = info["total_qty"]
        med_type = info["type"]
        unit = info.get("unit") or ""
        if not is_low_stock_qty(qty, med_type, low_thr, cur, unit=unit or None):
            continue
        supplier = suppliers.get(int(info["sample_id"]), "") if info.get("sample_id") else ""
        rows.append((
            name,
            int(qty) if qty == int(qty) else round(qty, 2),
            unit or "",
            supplier,
        ))
    return rows


def fetch_out_of_stock_medicines(conn) -> List[Tuple[Any, ...]]:
    cur = conn.cursor()
    suppliers = _latest_supplier_by_medicine_id(conn)
    oos_packs = medicine_pack_keys_fully_out_of_stock(conn)
    if not oos_packs:
        return []

    cur.execute(f"""
        SELECT name, COALESCE(unit, ''), COALESCE(type, ''),
               COALESCE(mrp, 0), COALESCE(rate, 0), id
        FROM medicines
        WHERE {HIDDEN_FILTER_SQL}
        ORDER BY name COLLATE NOCASE, unit COLLATE NOCASE, id DESC
    """)
    rows: List[Tuple[Any, ...]] = []
    seen: Set[Tuple[str, str]] = set()
    for name, unit, med_type, mrp, rate, med_id in cur.fetchall():
        key = _pack_key(name, unit)
        if key not in oos_packs or key in seen:
            continue
        seen.add(key)
        supplier = suppliers.get(int(med_id), "") if med_id else ""
        rows.append((
            name,
            unit or "",
            round(float(mrp or 0), 2),
            round(float(rate or 0), 2),
            med_type,
            supplier,
        ))
    return rows


def fetch_expired_medicines(conn) -> List[Tuple[Any, ...]]:
    cur = conn.cursor()
    suppliers = _latest_supplier_by_medicine_id(conn)
    bills = _bill_by_medicine_batch(conn)
    today = date.today()

    cur.execute(f"""
        SELECT id, name, COALESCE(batch_no,''), COALESCE(expiry_date,''),
               COALESCE(stock_qty,0)
        FROM medicines
        WHERE {HIDDEN_FILTER_SQL} AND COALESCE(stock_qty,0) > 0
        ORDER BY name COLLATE NOCASE, batch_no
    """)
    rows: List[Tuple[Any, ...]] = []
    for med_id, name, batch, expiry_raw, qty in cur.fetchall():
        expiry_dt = alert_expiry_date(expiry_raw)
        if not expiry_dt or expiry_dt >= today:
            continue
        supplier = suppliers.get(int(med_id), "")
        bill = bills.get((int(med_id), batch or ""), "")
        rows.append((
            name,
            batch,
            _fmt_expiry_display(expiry_raw),
            int(qty) if float(qty) == int(qty) else round(float(qty), 2),
            supplier,
            bill,
        ))
    return rows


def fetch_near_expiry_medicines(conn) -> List[Tuple[Any, ...]]:
    cur = conn.cursor()
    _, near_thr = load_thresholds(conn)
    suppliers = _latest_supplier_by_medicine_id(conn)
    bills = _bill_by_medicine_batch(conn)
    today = date.today()

    cur.execute(f"""
        SELECT id, name, COALESCE(type,''), COALESCE(batch_no,''),
               COALESCE(expiry_date,''), COALESCE(stock_qty,0)
        FROM medicines
        WHERE {HIDDEN_FILTER_SQL} AND COALESCE(stock_qty,0) > 0
        ORDER BY name COLLATE NOCASE, batch_no
    """)
    rows: List[Tuple[Any, ...]] = []
    for med_id, name, med_type, batch, expiry_raw, qty in cur.fetchall():
        expiry_dt = alert_expiry_date(expiry_raw)
        if not expiry_dt:
            continue
        days_left = (expiry_dt - today).days
        if not is_near_expiry(days_left, med_type, near_thr):
            continue
        supplier = suppliers.get(int(med_id), "")
        bill = bills.get((int(med_id), batch or ""), "")
        rows.append((
            name,
            batch,
            _fmt_expiry_display(expiry_raw),
            days_left,
            int(qty) if float(qty) == int(qty) else round(float(qty), 2),
            supplier,
            bill,
        ))
    return rows


def remaining_bill_due(due_amount, total_due, account_cleared) -> float:
    """What a bill still owes AFTER the FIFO cascade — not what it owed on day one.

    `sales.due_amount` is the entry-time snapshot `max(0, total_amount - amount_paid)`
    that `calc_payment_result` writes and never revisits. What is actually still
    outstanding is `sales.total_due` / `account_cleared`, which `cascade_sales_fifo`
    rewrites every time a payment, a return, or an over-payment on a later bill lands
    (due_fifo.py:238-265) and which `customers.total_due` is derived from.

    This list chased the snapshot, so it demanded money the shop had already been paid.
    Live Roshan: 21 bills totalling Rs 8,859.56, six of them already carrying
    `account_cleared = 1` and `total_due = 0` on the server — SHYAM GAYKI's SCB81 alone
    is Rs 2,680 — while `SUM(customers.total_due)` on the same store is Rs 4,712.26 and
    that is what the Home tile, the customer payment screen and the startup popup all
    show. With the cascade this list is 13 bills summing exactly Rs 4,712.26.

    Same rule as Sales History (`record_indicators` / `salesHistoryDisplayDue`), which
    has shown the cascaded figure all along; this screen was the odd one out.

    The one place this list is deliberately MORE cautious than Sales History: a bill that
    is not marked cleared and carries no cascaded figure at all falls back to the
    entry-time snapshot. `sales.total_due` is `REAL DEFAULT 0` (db_setup.py:53), so a row
    written by an old client, or half-imported, is indistinguishable from a settled one --
    and this is the list the shop chases money with. Every bill the fix actually removes
    from live Roshan carries `account_cleared = 1` explicitly, so the fallback costs the
    fix nothing and cannot hide a real balance.
    """
    if account_cleared:
        return 0.0
    remaining = round(float(total_due or 0), 2)
    if remaining > 0.01:
        return remaining
    snapshot = round(float(due_amount or 0), 2)
    return snapshot if snapshot > 0.01 else 0.0


def fetch_customer_due_bills(conn) -> List[Tuple[Any, ...]]:
    cur = conn.cursor()
    today = date.today()
    due_settings = load_due_alert_settings(conn)
    cur.execute("""
        SELECT c.name, COALESCE(c.phone,''), s.bill_no, s.bill_date,
               COALESCE(s.total_amount,0), COALESCE(s.amount_paid,0),
               remaining_due.due
        FROM sales s
        JOIN customers c ON c.id = s.customer_id
        JOIN (
            SELECT id,
                   CASE WHEN COALESCE(account_cleared,0)=1 THEN 0
                        WHEN COALESCE(total_due,0) > 0.01 THEN COALESCE(total_due,0)
                        ELSE COALESCE(due_amount,0) END AS due
            FROM sales
        ) AS remaining_due ON remaining_due.id = s.id
        WHERE remaining_due.due > 0.01
          AND COALESCE(s.deleted,0)=0
          AND COALESCE(s.is_autosave,0)=0
        ORDER BY s.bill_date DESC, s.bill_no DESC
    """)
    rows: List[Tuple[Any, ...]] = []
    for name, phone, bill_no, bill_date, total, paid, due in cur.fetchall():
        due_days = bill_due_days(bill_date, today)
        if not passes_due_alert_filter(due, due_days, due_settings):
            continue
        rows.append((
            name,
            phone or "",
            bill_no or "",
            bill_date or "",
            round(float(total or 0), 2),
            round(float(paid or 0), 2),
            round(float(due or 0), 2),
            due_days,
        ))
    return rows


def fetch_customer_due_summary(conn) -> List[Tuple[Any, ...]]:
    """Customer-level due rows for the startup alert popup."""
    cur = conn.cursor()
    due_settings = load_due_alert_settings(conn)
    min_amount = due_settings["min_amount"]
    min_days = int(due_settings["min_days"])
    today = date.today()

    if min_days > 0:
        cur.execute("""
            SELECT DISTINCT c.name, COALESCE(c.phone,''), COALESCE(c.total_due,0)
            FROM customers c
            JOIN sales s ON s.customer_id = c.id
            WHERE COALESCE(c.total_due,0) >= ?
              AND COALESCE(c.total_due,0) > 0.01
              AND COALESCE(s.account_cleared,0)=0
              AND COALESCE(s.deleted,0)=0
              AND COALESCE(s.is_autosave,0)=0
              AND (CASE WHEN COALESCE(s.total_due,0) > 0.01
                        THEN COALESCE(s.total_due,0)
                        ELSE COALESCE(s.due_amount,0) END) > 0.01
              AND (CASE WHEN COALESCE(s.total_due,0) > 0.01
                        THEN COALESCE(s.total_due,0)
                        ELSE COALESCE(s.due_amount,0) END) >= ?
              AND julianday(?) - julianday(s.bill_date) >= ?
            ORDER BY c.total_due DESC, c.name COLLATE NOCASE
        """, (min_amount, min_amount, str(today), min_days))
    else:
        cur.execute("""
            SELECT name, COALESCE(phone,''), COALESCE(total_due,0)
            FROM customers
            WHERE COALESCE(total_due,0) >= ?
              AND COALESCE(total_due,0) > 0.01
            ORDER BY total_due DESC, name COLLATE NOCASE
        """, (min_amount,))
        # `>= 0` alone (the default minimum) listed every customer in the shop,
        # zero balances included. A due alert is for money still owed.

    return [
        (n, p, round(float(d or 0), 2))
        for n, p, d in cur.fetchall()
    ]


def fetch_customer_due_summary_online(conn) -> List[Tuple[Any, ...]]:
    """fetch_customer_due_summary for Online, where `customers` is the store's.

    Same rule as the Offline SQL: a balance above zero and at least the shop's
    minimum; with a minimum-days setting, only customers holding at least one
    unsettled bill that old (and that large) -- read from the store's sales.
    """
    from core import online_catalog as oc

    due_settings = load_due_alert_settings(conn)
    min_amount = due_settings["min_amount"]
    min_days = int(due_settings["min_days"])
    custs = oc.customers() or []
    if not custs and oc.last_error("customers"):
        raise RuntimeError(
            f"Could not read customer dues from the store: {oc.last_error('customers')}"
        )
    eligible_ids: Set[str] = set()
    eligible_names: Set[str] = set()
    if min_days > 0:
        from core import store_query_client as sq

        today = date.today()
        for r in (sq.list_sales(limit=5000) or {}).get("rows") or []:
            if r.get("deleted") or r.get("is_autosave") or r.get("account_cleared"):
                continue
            due = remaining_bill_due(r.get("due_amount"), r.get("total_due"), False)
            if due <= 0.01 or due < min_amount:
                continue
            if bill_due_days(r.get("bill_date"), today) < min_days:
                continue
            if r.get("customer_id") not in (None, ""):
                eligible_ids.add(str(r.get("customer_id")))
            eligible_names.add(str(r.get("customer_name") or "").strip().upper())
    rows: List[Tuple[Any, ...]] = []
    for c in custs:
        if not isinstance(c, dict):
            continue
        total = float(c.get("total_due") or 0)
        if total <= 0.01 or total < min_amount:
            continue
        if min_days > 0:
            cid = str(c.get("id") or c.get("local_id") or "")
            nm = str(c.get("name") or "").strip().upper()
            if cid not in eligible_ids and nm not in eligible_names:
                continue
        rows.append((c.get("name") or "", c.get("phone") or "", round(total, 2)))
    rows.sort(key=lambda r: (-r[2], str(r[0]).lower()))
    return rows


def fetch_all_monitoring_sections(conn) -> Dict[str, List[Tuple[Any, ...]]]:
    try:
        from core.sync_prefs import is_online_mode

        online = is_online_mode()
    except Exception:
        online = False
    if online:
        # No fallback to the SQL below: Online that is an empty :memory: shell, and
        # a failed store read would come back as a clean, empty alert list.
        return _fetch_all_monitoring_online(conn)
    return {
        "low_stock": fetch_low_stock_alerts(conn),
        "out_of_stock": fetch_out_of_stock_medicines(conn),
        "expired": fetch_expired_medicines(conn),
        "near_expiry": fetch_near_expiry_medicines(conn),
        "customer_due": fetch_customer_due_bills(conn),
    }


def online_visible_medicines() -> List[Dict[str, Any]]:
    """The store's shelf as the alerts see it: not hidden, not deleted.

    Raises when the store could not be read, so an unreachable server is never
    painted as a shelf with nothing low and nothing expiring.
    """
    from core import online_catalog as oc

    rows = oc.medicines() or []
    if not rows:
        err = oc.last_error("medicines_inventory")
        if err:
            raise RuntimeError(f"Could not read the store's stock: {err}")
    return [
        m for m in rows
        if isinstance(m, dict) and not m.get("is_hidden") and not m.get("deleted")
    ]


def online_stock_sections(conn, meds=None) -> Dict[str, List[Tuple[Any, ...]]]:
    """Low / out / expired / near-expiry rows from the store's shelf (Online).

    Shared by Alert & Monitoring and the startup popup so the two cannot drift.
    """
    low_thr, near_thr = load_thresholds(conn)
    today = date.today()
    if meds is None:
        meds = online_visible_medicines()

    by_name: Dict[str, Dict[str, Any]] = {}
    by_pack: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for m in meds:
        name = (m.get("name") or "").strip()
        if not name:
            continue
        unit = _normalize_pack_size(str(m.get("unit") or ""))
        qty = float(m.get("stock_qty") or 0)
        med_type = (m.get("type") or "") or ""
        entry = by_name.setdefault(name, {
            "total_qty": 0.0, "type": med_type, "unit": unit,
        })
        entry["total_qty"] += qty
        if med_type and not entry["type"]:
            entry["type"] = med_type
        if qty >= float(entry.get("_best_qty") or 0):
            entry["_best_qty"] = qty
            if unit:
                entry["unit"] = unit
            if med_type:
                entry["type"] = med_type
        pk = _pack_key(name, unit)
        pack = by_pack.setdefault(pk, {
            "total_qty": 0.0, "type": med_type,
            "mrp": float(m.get("mrp") or 0), "rate": float(m.get("rate") or 0),
        })
        pack["total_qty"] += qty
        if med_type and not pack["type"]:
            pack["type"] = med_type

    oos_names = {n for n, info in by_name.items() if float(info["total_qty"]) <= 0}
    low_rows: List[Tuple[Any, ...]] = []
    for name in sorted(by_name, key=lambda n: n.lower()):
        info = by_name[name]
        qty = float(info["total_qty"])
        if name in oos_names or qty <= 0:
            continue
        med_type = info.get("type") or ""
        unit = info.get("unit") or ""
        if not is_low_stock_qty(qty, med_type, low_thr, None, unit=unit or None):
            continue
        low_rows.append((
            name,
            int(qty) if qty == int(qty) else round(qty, 2),
            unit,
            "",
        ))

    oos_rows: List[Tuple[Any, ...]] = []
    for (name, unit), info in sorted(by_pack.items(), key=lambda x: (x[0][0].lower(), x[0][1])):
        if float(info["total_qty"]) > 0:
            continue
        oos_rows.append((
            name,
            unit or "",
            round(float(info.get("mrp") or 0), 2),
            round(float(info.get("rate") or 0), 2),
            info.get("type") or "",
            "",
        ))

    expired_rows: List[Tuple[Any, ...]] = []
    near_rows: List[Tuple[Any, ...]] = []
    for m in meds:
        qty = float(m.get("stock_qty") or 0)
        if qty <= 0:
            continue
        expiry_raw = m.get("expiry_date") or ""
        expiry_dt = alert_expiry_date(expiry_raw)
        if not expiry_dt:
            continue
        name = (m.get("name") or "").strip()
        batch = m.get("batch_no") or ""
        med_type = m.get("type") or ""
        qty_disp = int(qty) if qty == int(qty) else round(qty, 2)
        if expiry_dt < today:
            expired_rows.append((
                name, batch, _fmt_expiry_display(str(expiry_raw)), qty_disp, "", "",
            ))
            continue
        days_left = (expiry_dt - today).days
        if is_near_expiry(days_left, med_type, near_thr):
            near_rows.append((
                name, batch, _fmt_expiry_display(str(expiry_raw)),
                days_left, qty_disp, "", "",
            ))

    return {
        "low_stock": low_rows,
        "out_of_stock": oos_rows,
        "expired": expired_rows,
        "near_expiry": near_rows,
    }


def _fetch_all_monitoring_online(conn) -> Dict[str, List[Tuple[Any, ...]]]:
    """Online: stock/expiry alerts from store inventory; dues from list_sales."""
    from core import store_query_client as sq

    stock = online_stock_sections(conn)
    today = date.today()
    due_settings = load_due_alert_settings(conn)
    due_rows: List[Tuple[Any, ...]] = []
    try:
        sales = (sq.list_sales(limit=5000) or {}).get("rows") or []
    except Exception:
        sales = []
    for r in sales:
        # The cascaded remainder, not the entry-time snapshot — see remaining_bill_due.
        due = remaining_bill_due(
            r.get("due_amount"),
            r.get("total_due"),
            bool(r.get("account_cleared")),
        )
        if due <= 0:
            continue
        bill_date = r.get("bill_date") or ""
        due_days = bill_due_days(bill_date, today)
        if not passes_due_alert_filter(due, due_days, due_settings):
            continue
        due_rows.append((
            r.get("customer_name") or "",
            r.get("customer_phone") or r.get("phone") or "",
            r.get("bill_no") or "",
            bill_date,
            round(float(r.get("total_amount") or 0), 2),
            round(float(r.get("amount_paid") or 0), 2),
            round(due, 2),
            due_days,
        ))

    return {**stock, "customer_due": due_rows}


def section_counts(conn) -> Dict[str, int]:
    data = fetch_all_monitoring_sections(conn)
    return {k: len(v) for k, v in data.items()}
