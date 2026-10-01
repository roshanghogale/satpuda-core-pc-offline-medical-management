"""
Alert & Monitoring dashboard queries (Settings -> Alert & Monitoring).

Uses threshold values from Settings -> Layout & Lists -> Thresholds.
"""
from __future__ import annotations

import re
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


def _norm_batch(batch: Any) -> str:
    """A batch as purchase save matches it: no spaces, upper case ("ab 12" is AB12)."""
    return re.sub(r"\s+", "", str(batch or "")).upper()


def _iso_day(raw: Any) -> str:
    """'2026-09-14 10:22:01' / '2026-09-14' -> '2026-09-14'; anything else -> ''."""
    text = str(raw or "").strip()[:10]
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else ""


def _show_day(iso: str) -> str:
    return f"{iso[8:10]}-{iso[5:7]}-{iso[:4]}" if iso else ""


def _real_purchases_sql(conn) -> str:
    """Saved purchases only: a draft (autosave) or a deleted bill is not where a batch came from."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(purchases)").fetchall()}
    where = ["pi.medicine_id IS NOT NULL"]
    if "is_autosave" in cols:
        where.append("COALESCE(p.is_autosave,0)=0")
    if "deleted" in cols:
        where.append("COALESCE(p.deleted,0)=0")
    return " AND ".join(where)


def _purchase_trail(conn) -> Dict[str, Any]:
    """Where each batch on the shelf came from.

    by_batch: (medicine id, batch) -> {bill, date, supplier} of the newest saved bill that
    brought it; by_med: medicine id -> the same for its newest bill; by_name: medicine name ->
    the newest bill of any row of that name (with its batch and expiry); shelf: medicine id ->
    the supplier written on the row itself (purchase, opening stock or import write it)."""
    cur = conn.cursor()
    cur.execute(f"""
        SELECT pi.medicine_id, COALESCE(pi.batch_no,''), COALESCE(pi.expiry_date,''),
               COALESCE(p.bill_number,''), COALESCE(p.purchase_date,''), COALESCE(s.name,''),
               COALESCE(m.name,'')
        FROM purchase_items pi
        JOIN purchases p ON p.id = pi.purchase_id
        LEFT JOIN suppliers s ON s.id = p.supplier_id
        LEFT JOIN medicines m ON m.id = pi.medicine_id
        WHERE {_real_purchases_sql(conn)}
        ORDER BY p.purchase_date DESC, p.id DESC
    """)
    by_batch: Dict[Tuple[int, str], Dict[str, str]] = {}
    by_med: Dict[int, Dict[str, str]] = {}
    by_name: Dict[str, Dict[str, str]] = {}
    for med_id, batch, expiry, bill, pdate, supplier, name in cur.fetchall():
        info = {"bill": str(bill or ""), "date": _iso_day(pdate), "supplier": str(supplier or ""),
                "batch": str(batch or ""), "expiry": str(expiry or "")}
        mid = int(med_id)
        by_batch.setdefault((mid, _norm_batch(batch)), info)
        by_med.setdefault(mid, info)
        if name:
            by_name.setdefault(str(name), info)
    shelf: Dict[int, str] = {}
    try:
        for mid, sup in cur.execute(
                "SELECT id, COALESCE(supplier_name,'') FROM medicines").fetchall():
            if sup and str(sup).strip():
                shelf[int(mid)] = str(sup).strip()
    except Exception:
        pass
    return {"by_batch": by_batch, "by_med": by_med, "by_name": by_name, "shelf": shelf}


def _batch_source(trail: Dict[str, Any], med_id: Any, batch: Any) -> Dict[str, str]:
    """Supplier, bill number and purchase date of one shelf batch."""
    try:
        mid = int(med_id)
    except (TypeError, ValueError):
        return {"bill": "", "date": "", "supplier": ""}
    hit = trail["by_batch"].get((mid, _norm_batch(batch))) or {}
    latest = trail["by_med"].get(mid) or {}
    supplier = hit.get("supplier") or trail["shelf"].get(mid, "") or latest.get("supplier", "")
    return {"bill": hit.get("bill", ""), "date": hit.get("date", ""), "supplier": supplier}


def _latest_supplier_by_medicine_id(conn) -> Dict[int, str]:
    """The supplier of each medicine row: the one written on the row, else its newest
    saved bill's. Reading only the bills left every opening-stock or imported row blank."""
    trail = _purchase_trail(conn)
    out: Dict[int, str] = {mid: info["supplier"] for mid, info in trail["by_med"].items() if info["supplier"]}
    out.update(trail["shelf"])
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
    """(medicine id, batch as purchase save matches it) -> bill number of its newest saved bill."""
    trail = _purchase_trail(conn)
    return {k: v["bill"] for k, v in trail["by_batch"].items() if v["bill"]}


def _fmt_expiry_display(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    dt = parse_expiry(text)
    if not dt:
        return text
    return dt.strftime("%d-%m-%Y")


def _num(qty: Any) -> Any:
    q = float(qty or 0)
    return int(q) if q == int(q) else round(q, 2)


def fetch_low_stock_alerts(conn, detail: bool = False) -> List[Tuple[Any, ...]]:
    """Low stock, one row per medicine name (its batches summed). With `detail` each row
    also carries its newest bill: batch, expiry, bill number, purchase date, and (hidden,
    last) that date as YYYY-MM-DD for the month / year filter."""
    cur = conn.cursor()
    low_thr, _ = load_thresholds(conn)
    trail = _purchase_trail(conn)
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
        mid = int(info["sample_id"]) if info.get("sample_id") else 0
        last = trail["by_name"].get(name) or trail["by_med"].get(mid) or {}
        supplier = trail["shelf"].get(mid, "") or last.get("supplier", "")
        row: Tuple[Any, ...] = (name, _num(qty), unit or "", supplier)
        if detail:
            row += (last.get("batch", ""), _fmt_expiry_display(last.get("expiry", "")),
                    last.get("bill", ""), _show_day(last.get("date", "")), last.get("date", ""))
        rows.append(row)
    return rows


def fetch_out_of_stock_medicines(conn, detail: bool = False) -> List[Tuple[Any, ...]]:
    """Out of stock, one row per name + pack. With `detail`: the newest bill's batch, bill
    number and purchase date, and (hidden, last) that date for the month / year filter."""
    cur = conn.cursor()
    trail = _purchase_trail(conn)
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
        mid = int(med_id) if med_id else 0
        last = trail["by_med"].get(mid) or trail["by_name"].get(name) or {}
        supplier = trail["shelf"].get(mid, "") or last.get("supplier", "")
        row: Tuple[Any, ...] = (
            name,
            unit or "",
            round(float(mrp or 0), 2),
            round(float(rate or 0), 2),
            med_type,
            supplier,
        )
        if detail:
            row += (last.get("batch", ""), last.get("bill", ""), _show_day(last.get("date", "")),
                    last.get("date", ""))
        rows.append(row)
    return rows


def _stocked_batches(conn) -> List[Tuple[Any, ...]]:
    cur = conn.cursor()
    cur.execute(f"""
        SELECT id, name, COALESCE(type,''), COALESCE(batch_no,''),
               COALESCE(expiry_date,''), COALESCE(stock_qty,0)
        FROM medicines
        WHERE {HIDDEN_FILTER_SQL} AND COALESCE(stock_qty,0) > 0
        ORDER BY name COLLATE NOCASE, batch_no
    """)
    return cur.fetchall()


def fetch_expired_medicines(conn, detail: bool = False) -> List[Tuple[Any, ...]]:
    """Expired batches still on the shelf. With `detail`: purchase date, and (hidden, last)
    the expiry as YYYY-MM-DD for the month / year filter."""
    trail = _purchase_trail(conn)
    today = date.today()
    rows: List[Tuple[Any, ...]] = []
    for med_id, name, _type, batch, expiry_raw, qty in _stocked_batches(conn):
        expiry_dt = alert_expiry_date(expiry_raw)
        if not expiry_dt or expiry_dt >= today:
            continue
        src = _batch_source(trail, med_id, batch)
        row: Tuple[Any, ...] = (
            name, batch, _fmt_expiry_display(expiry_raw), _num(qty), src["supplier"], src["bill"],
        )
        if detail:
            row += (_show_day(src["date"]), expiry_dt.isoformat())
        rows.append(row)
    return rows


def fetch_near_expiry_medicines(conn, detail: bool = False) -> List[Tuple[Any, ...]]:
    """Batches whose expiry falls inside the near-expiry threshold. With `detail`: purchase
    date, and (hidden, last) the expiry as YYYY-MM-DD."""
    _, near_thr = load_thresholds(conn)
    trail = _purchase_trail(conn)
    today = date.today()
    rows: List[Tuple[Any, ...]] = []
    for med_id, name, med_type, batch, expiry_raw, qty in _stocked_batches(conn):
        expiry_dt = alert_expiry_date(expiry_raw)
        if not expiry_dt:
            continue
        days_left = (expiry_dt - today).days
        if not is_near_expiry(days_left, med_type, near_thr):
            continue
        src = _batch_source(trail, med_id, batch)
        row: Tuple[Any, ...] = (
            name, batch, _fmt_expiry_display(expiry_raw), days_left, _num(qty), src["supplier"], src["bill"],
        )
        if detail:
            row += (_show_day(src["date"]), expiry_dt.isoformat())
        rows.append(row)
    return rows


def fetch_expiry_by_batch(conn) -> List[Tuple[Any, ...]]:
    """Every batch on the shelf that has an expiry, past or future -- what the month / year
    filter of the Expired and Near Expiry tabs picks from ("everything expiring in March
    2027" is further away than the near-expiry threshold).
    Row: name, batch, expiry, days left, qty, supplier, bill, purchase date, expiry ISO."""
    trail = _purchase_trail(conn)
    today = date.today()
    rows: List[Tuple[Any, ...]] = []
    for med_id, name, _type, batch, expiry_raw, qty in _stocked_batches(conn):
        expiry_dt = alert_expiry_date(expiry_raw)
        if not expiry_dt:
            continue
        src = _batch_source(trail, med_id, batch)
        rows.append((name, batch, _fmt_expiry_display(expiry_raw), (expiry_dt - today).days, _num(qty),
                     src["supplier"], src["bill"], _show_day(src["date"]), expiry_dt.isoformat()))
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


def fetch_all_monitoring_sections(conn, detail: bool = False) -> Dict[str, List[Tuple[Any, ...]]]:
    """Every alert list. `detail` (the Alert & Monitoring screen) adds the batch / bill /
    purchase-date columns and the "expiry_by_batch" list its month / year filter uses."""
    try:
        from core.sync_prefs import is_online_mode

        online = is_online_mode()
    except Exception:
        online = False
    if online:
        # No fallback to the SQL below: Online that is an empty :memory: shell, and
        # a failed store read would come back as a clean, empty alert list.
        return _fetch_all_monitoring_online(conn, detail=detail)
    out = {
        "low_stock": fetch_low_stock_alerts(conn, detail=detail),
        "out_of_stock": fetch_out_of_stock_medicines(conn, detail=detail),
        "expired": fetch_expired_medicines(conn, detail=detail),
        "near_expiry": fetch_near_expiry_medicines(conn, detail=detail),
        "customer_due": fetch_customer_due_bills(conn),
    }
    if detail:
        out["expiry_by_batch"] = fetch_expiry_by_batch(conn)
    return out


def online_purchase_trail(meds: List[Dict[str, Any]]) -> Dict[str, Any]:
    """_purchase_trail for Online, from the store's purchases off the sync pull and the
    shelf rows' own supplier_name."""
    from core import online_catalog as oc

    def _int(v: Any) -> int:
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    names_by_id = {_int(m.get("id") or m.get("local_id")): str(m.get("name") or "") for m in meds}
    shelf = {_int(m.get("id") or m.get("local_id")): str(m.get("supplier_name") or "").strip()
             for m in meds if str(m.get("supplier_name") or "").strip()}
    docs = [d for d in oc._pull_sync_pages("purchases", limit=50000) if isinstance(d, dict)]
    docs.sort(key=lambda d: (str(d.get("purchase_date") or ""), _int(d.get("id") or d.get("local_id"))),
              reverse=True)
    sup_names: Dict[int, str] = {}
    by_batch: Dict[Tuple[int, str], Dict[str, str]] = {}
    by_med: Dict[int, Dict[str, str]] = {}
    by_name: Dict[str, Dict[str, str]] = {}
    for d in docs:
        if d.get("deleted") or d.get("is_autosave"):
            continue
        sid = _int(d.get("supplier_id"))
        supplier = ""
        if sid:
            if sid not in sup_names:
                try:
                    sup_names[sid] = str((oc.find_supplier_by_id(sid) or {}).get("name") or "").strip()
                except Exception:
                    sup_names[sid] = ""
            supplier = sup_names[sid]
        supplier = supplier or str(d.get("supplier_name") or "").strip()
        for it in d.get("items") or []:
            if not isinstance(it, dict):
                continue
            mid = _int(it.get("medicine_id"))
            if not mid:
                continue
            info = {"bill": str(d.get("bill_number") or ""), "date": _iso_day(d.get("purchase_date")),
                    "supplier": supplier, "batch": str(it.get("batch_no") or ""),
                    "expiry": str(it.get("expiry_date") or "")}
            by_batch.setdefault((mid, _norm_batch(it.get("batch_no"))), info)
            by_med.setdefault(mid, info)
            name = names_by_id.get(mid) or str(it.get("medicine_name") or it.get("name") or "")
            if name:
                by_name.setdefault(name, info)
    return {"by_batch": by_batch, "by_med": by_med, "by_name": by_name, "shelf": shelf}


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


def online_stock_sections(conn, meds=None, detail: bool = False) -> Dict[str, List[Tuple[Any, ...]]]:
    """Low / out / expired / near-expiry rows from the store's shelf (Online).

    Shared by Alert & Monitoring and the startup popup so the two cannot drift. `detail`
    (Alert & Monitoring) adds the columns of fetch_*(detail=True) and "expiry_by_batch".
    Supplier and Bill Number used to be written as "" here: always blank Online.
    """
    low_thr, near_thr = load_thresholds(conn)
    today = date.today()
    if meds is None:
        meds = online_visible_medicines()

    def _mid(m: Dict[str, Any]) -> int:
        try:
            return int(m.get("id") or m.get("local_id") or 0)
        except (TypeError, ValueError):
            return 0

    if detail:
        try:
            trail = online_purchase_trail(meds)
        except Exception:
            trail = {"by_batch": {}, "by_med": {}, "by_name": {}, "shelf": {}}
    else:
        trail = {"by_batch": {}, "by_med": {}, "by_name": {},
                 "shelf": {_mid(m): str(m.get("supplier_name") or "").strip() for m in meds
                           if str(m.get("supplier_name") or "").strip()}}

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
            "total_qty": 0.0, "type": med_type, "unit": unit, "sample_id": _mid(m),
        })
        entry["total_qty"] += qty
        if med_type and not entry["type"]:
            entry["type"] = med_type
        if qty >= float(entry.get("_best_qty") or 0):
            entry["_best_qty"] = qty
            entry["sample_id"] = _mid(m)
            if unit:
                entry["unit"] = unit
            if med_type:
                entry["type"] = med_type
        pk = _pack_key(name, unit)
        pack = by_pack.setdefault(pk, {
            "total_qty": 0.0, "type": med_type,
            "mrp": float(m.get("mrp") or 0), "rate": float(m.get("rate") or 0),
            "sample_id": _mid(m), "name": name,
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
        mid = int(info.get("sample_id") or 0)
        last = trail["by_name"].get(name) or trail["by_med"].get(mid) or {}
        row: Tuple[Any, ...] = (
            name,
            int(qty) if qty == int(qty) else round(qty, 2),
            unit,
            trail["shelf"].get(mid, "") or last.get("supplier", ""),
        )
        if detail:
            row += (last.get("batch", ""), _fmt_expiry_display(last.get("expiry", "")),
                    last.get("bill", ""), _show_day(last.get("date", "")), last.get("date", ""))
        low_rows.append(row)

    oos_rows: List[Tuple[Any, ...]] = []
    for (name, unit), info in sorted(by_pack.items(), key=lambda x: (x[0][0].lower(), x[0][1])):
        if float(info["total_qty"]) > 0:
            continue
        mid = int(info.get("sample_id") or 0)
        last = trail["by_med"].get(mid) or trail["by_name"].get(info.get("name") or name) or {}
        row = (
            name,
            unit or "",
            round(float(info.get("mrp") or 0), 2),
            round(float(info.get("rate") or 0), 2),
            info.get("type") or "",
            trail["shelf"].get(mid, "") or last.get("supplier", ""),
        )
        if detail:
            row += (last.get("batch", ""), last.get("bill", ""), _show_day(last.get("date", "")),
                    last.get("date", ""))
        oos_rows.append(row)

    expired_rows: List[Tuple[Any, ...]] = []
    near_rows: List[Tuple[Any, ...]] = []
    all_rows: List[Tuple[Any, ...]] = []
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
        src = _batch_source(trail, _mid(m), batch)
        days_left = (expiry_dt - today).days
        if detail:
            all_rows.append((name, batch, _fmt_expiry_display(str(expiry_raw)), days_left, qty_disp,
                             src["supplier"], src["bill"], _show_day(src["date"]), expiry_dt.isoformat()))
        extra: Tuple[Any, ...] = (_show_day(src["date"]), expiry_dt.isoformat()) if detail else ()
        if expiry_dt < today:
            expired_rows.append((
                name, batch, _fmt_expiry_display(str(expiry_raw)), qty_disp, src["supplier"], src["bill"],
            ) + extra)
            continue
        if is_near_expiry(days_left, med_type, near_thr):
            near_rows.append((
                name, batch, _fmt_expiry_display(str(expiry_raw)),
                days_left, qty_disp, src["supplier"], src["bill"],
            ) + extra)

    out = {
        "low_stock": low_rows,
        "out_of_stock": oos_rows,
        "expired": expired_rows,
        "near_expiry": near_rows,
    }
    if detail:
        out["expiry_by_batch"] = all_rows
    return out


def _fetch_all_monitoring_online(conn, detail: bool = False) -> Dict[str, List[Tuple[Any, ...]]]:
    """Online: stock/expiry alerts from store inventory; dues from list_sales."""
    from core import store_query_client as sq

    stock = online_stock_sections(conn, detail=detail)
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
