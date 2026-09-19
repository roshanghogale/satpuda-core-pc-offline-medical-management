"""Reorder / pending purchase order service."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from core.alert_thresholds import load_thresholds
from core.purchase_service import get_or_create_supplier


def _today() -> date:
    return date.today()


def _as_str(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def lookup_medicine_id(
    conn,
    medicine_name: str,
    pack_size: str = "",
    batch_no: str = "",
) -> Optional[int]:
    cur = conn.cursor()
    name = _as_str(medicine_name)
    if not name:
        return None
    pack = _as_str(pack_size)
    batch = _as_str(batch_no)
    if batch:
        cur.execute(
            "SELECT id FROM medicines WHERE name=? AND COALESCE(batch_no,'')=? "
            "ORDER BY id DESC LIMIT 1",
            (name, batch),
        )
        row = cur.fetchone()
        if row:
            return int(row[0])
    if pack:
        cur.execute(
            "SELECT id FROM medicines WHERE name=? AND COALESCE(unit,'')=? "
            "ORDER BY id DESC LIMIT 1",
            (name, pack),
        )
        row = cur.fetchone()
        if row:
            return int(row[0])
    cur.execute(
        "SELECT id FROM medicines WHERE name=? ORDER BY id DESC LIMIT 1",
        (name,),
    )
    row = cur.fetchone()
    return int(row[0]) if row else None


def current_stock_for_medicine(conn, medicine_name: str, pack_size: str = "") -> float:
    try:
        from core.sync_prefs import is_online_mode
        if is_online_mode():
            from core.online_catalog import medicines_for_name
            name = _as_str(medicine_name)
            pack = _as_str(pack_size)
            total = 0.0
            for m in medicines_for_name(name):
                if pack and _as_str(m.get("unit")) != pack:
                    continue
                if m.get("is_hidden"):
                    continue
                total += float(m.get("stock_qty") or 0)
            return total
    except Exception:
        pass

    cur = conn.cursor()
    name = _as_str(medicine_name)
    pack = _as_str(pack_size)
    if pack:
        cur.execute(
            "SELECT COALESCE(SUM(stock_qty),0) FROM medicines "
            "WHERE name=? AND COALESCE(unit,'')=?",
            (name, pack),
        )
    else:
        cur.execute(
            "SELECT COALESCE(SUM(stock_qty),0) FROM medicines WHERE name=?",
            (name,),
        )
    row = cur.fetchone()
    return float(row[0] or 0) if row else 0.0


def min_stock_level(conn, med_type: str) -> float:
    # The map is keyed the way is_low_stock_qty writes and reads it: normalised
    # and lowercased. Looking it up under the raw "Tablet" missed every time, so
    # a shop that set 50 got the built-in 10 on every reorder line it ever made.
    from core.layout_config import normalize_med_type_name

    low_thr, _ = load_thresholds(conn)
    key = normalize_med_type_name(med_type or "").lower()
    return float(low_thr.get(key, low_thr.get("others", 10)) or 10)


def reorder_default_qty(conn) -> float:
    default = 10.0
    try:
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE name='reorder_default_qty'")
        row = cur.fetchone()
        if row and row[0]:
            default = float(row[0])
    except Exception:
        pass
    return default


def suggest_order_quantity(
    conn,
    medicine_name: str,
    med_type: str,
    current_stock: float,
    pack_size: str = "",
) -> float:
    """Suggest how many to order — always uses default qty setting when reordering."""
    return reorder_default_qty(conn)


def sync_medicine_suppliers_from_purchases(conn, medicine_name: str) -> None:
    """Build medicine_suppliers links from purchase history."""
    name = (medicine_name or "").strip()
    if not name:
        return
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.id, pi.rate, p.purchase_date
        FROM purchase_items pi
        JOIN medicines m ON pi.medicine_id = m.id
        JOIN purchases p ON pi.purchase_id = p.id
        JOIN suppliers s ON p.supplier_id = s.id
        WHERE m.name = ?
        ORDER BY p.purchase_date DESC, p.id DESC
        """,
        (name,),
    )
    seen = set()
    for supplier_id, rate, pdate in cur.fetchall():
        sid = int(supplier_id)
        if sid in seen:
            continue
        seen.add(sid)
        cur.execute(
            """
            INSERT INTO medicine_suppliers
                (medicine_name, supplier_id, last_rate, last_purchase_date)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(medicine_name, supplier_id) DO UPDATE SET
                last_rate=excluded.last_rate,
                last_purchase_date=excluded.last_purchase_date
            """,
            (name, sid, float(rate or 0), pdate),
        )
    conn.commit()


def fetch_medicine_suppliers(conn, medicine_name: str) -> List[Dict[str, Any]]:
    name = (medicine_name or "").strip()
    if not name:
        return []
    sync_medicine_suppliers_from_purchases(conn, name)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.id, s.name, COALESCE(s.phone,''), COALESCE(ms.last_rate,0),
               ms.last_purchase_date
        FROM medicine_suppliers ms
        JOIN suppliers s ON s.id = ms.supplier_id
        WHERE ms.medicine_name = ?
        ORDER BY ms.last_purchase_date DESC, s.name COLLATE NOCASE
        """,
        (name,),
    )
    return [
        {
            "supplier_id": int(r[0]),
            "name": r[1] or "",
            "phone": r[2] or "",
            "last_rate": float(r[3] or 0),
            "last_purchase_date": str(r[4] or ""),
        }
        for r in cur.fetchall()
    ]


def _next_order_no(cur) -> str:
    cur.execute("SELECT COALESCE(MAX(id),0)+1 FROM pending_orders")
    n = int(cur.fetchone()[0] or 1)
    return f"RO{datetime.now().strftime('%Y%m%d')}{n:04d}"


def _next_group_id(cur) -> str:
    cur.execute("SELECT COALESCE(MAX(id),0)+1 FROM pending_orders")
    n = int(cur.fetchone()[0] or 1)
    return f"ROG{datetime.now().strftime('%Y%m%d')}-{n:04d}"


def _group_key(order_group_id, order_id: int) -> str:
    gid = _as_str(order_group_id)
    if gid:
        return gid
    return f"single:{int(order_id)}"


def save_pending_order(conn, data: dict, status: str = "draft") -> int:
    cur = conn.cursor()
    order_id = data.get("id")
    status = (status or "draft").strip().lower()
    group_id = _as_str(data.get("order_group_id")) or None
    fields = (
        data.get("medicine_id"),
        _as_str(data.get("medicine_name")),
        _as_str(data.get("pack_size")),
        data.get("supplier_id"),
        _as_str(data.get("supplier_name_manual")),
        _as_str(data.get("supplier_phone")),
        _as_str(data.get("supplier_email")),
        1 if data.get("order_offline") else 0,
        _as_str(data.get("offline_note")),
        float(data.get("quantity") or 0),
        float(data.get("unit_price") or 0),
        float(data.get("current_stock") or 0),
        float(data.get("min_stock") or 0),
        data.get("order_date") or str(_today()),
        _as_str(data.get("expected_delivery_date")),
        status,
        _as_str(data.get("notes")),
        group_id,
    )
    if order_id:
        cur.execute(
            """
            UPDATE pending_orders SET
                medicine_id=?, medicine_name=?, pack_size=?,
                supplier_id=?, supplier_name_manual=?, supplier_phone=?,
                supplier_email=?, order_offline=?, offline_note=?,
                quantity=?, unit_price=?, current_stock=?, min_stock=?,
                order_date=?, expected_delivery_date=?, status=?, notes=?,
                order_group_id=?
            WHERE id=?
            """,
            fields + (int(order_id),),
        )
        conn.commit()
        return int(order_id)

    order_no = _next_order_no(cur)
    cur.execute(
        """
        INSERT INTO pending_orders (
            order_no, medicine_id, medicine_name, pack_size,
            supplier_id, supplier_name_manual, supplier_phone, supplier_email,
            order_offline, offline_note, quantity, unit_price,
            current_stock, min_stock, order_date, expected_delivery_date,
            status, notes, order_group_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (order_no,) + fields,
    )
    conn.commit()
    new_id = int(cur.lastrowid)

    sid = data.get("supplier_id")
    mname = _as_str(data.get("medicine_name"))
    if sid and mname:
        cur.execute(
            """
            INSERT INTO medicine_suppliers
                (medicine_name, supplier_id, last_rate, last_purchase_date)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(medicine_name, supplier_id) DO UPDATE SET
                last_rate=excluded.last_rate
            """,
            (mname, int(sid), float(data.get("unit_price") or 0), str(_today())),
        )
        conn.commit()
    return new_id


def save_supplier_pending_orders(
    conn,
    header: dict,
    lines: List[dict],
    status: str = "draft",
    group_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Save all medicine lines for one supplier tab as a single order group."""
    cur = conn.cursor()
    status = (status or "draft").strip().lower()
    valid_lines = [ln for ln in lines if float(ln.get("quantity") or 0) > 0]
    if not valid_lines:
        raise ValueError("No medicines with quantity to save.")

    if group_id and str(group_id).startswith("single:"):
        group_id = None

    if not group_id:
        group_id = _next_group_id(cur)
    else:
        keep_ids = {int(ln["id"]) for ln in valid_lines if ln.get("id")}
        cur.execute(
            "SELECT id FROM pending_orders WHERE order_group_id=?",
            (group_id,),
        )
        for (old_id,) in cur.fetchall():
            if int(old_id) not in keep_ids:
                cur.execute("DELETE FROM pending_orders WHERE id=?", (int(old_id),))

    saved_ids: List[int] = []
    for line in valid_lines:
        med_name = _as_str(line.get("medicine_name"))
        pack = _as_str(line.get("pack_size"))
        oid = save_pending_order(conn, {
            "id": line.get("id"),
            "order_group_id": group_id,
            "medicine_id": line.get("medicine_id") or lookup_medicine_id(
                conn, med_name, pack),
            "medicine_name": med_name,
            "pack_size": pack,
            "supplier_id": header.get("supplier_id"),
            "supplier_name_manual": _as_str(header.get("supplier_name_manual")),
            "supplier_phone": _as_str(header.get("supplier_phone")),
            "supplier_email": _as_str(header.get("supplier_email")),
            "order_offline": header.get("order_offline"),
            "offline_note": _as_str(header.get("offline_note")),
            "quantity": float(line.get("quantity") or 0),
            "unit_price": float(line.get("unit_price") or 0),
            "current_stock": float(line.get("current_stock") or 0),
            "min_stock": float(line.get("min_stock") or 0),
            "order_date": header.get("order_date") or str(_today()),
            "expected_delivery_date": _as_str(header.get("expected_delivery_date")),
            "notes": _as_str(header.get("notes")),
        }, status=status)
        saved_ids.append(int(oid))

    return {"group_id": group_id, "order_ids": saved_ids, "count": len(saved_ids)}


def fetch_pending_orders(conn, status: Optional[str] = None) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    if status:
        cur.execute(
            """
            SELECT po.id, po.order_no, po.medicine_name, po.pack_size,
                   COALESCE(s.name, po.supplier_name_manual, ''),
                   po.quantity, po.unit_price, po.status,
                   po.order_date, po.expected_delivery_date, po.notes,
                   po.medicine_id, po.supplier_id, po.order_group_id
            FROM pending_orders po
            LEFT JOIN suppliers s ON s.id = po.supplier_id
            WHERE po.status = ?
            ORDER BY po.order_date DESC, po.id DESC
            """,
            (status.strip().lower(),),
        )
    else:
        cur.execute(
            """
            SELECT po.id, po.order_no, po.medicine_name, po.pack_size,
                   COALESCE(s.name, po.supplier_name_manual, ''),
                   po.quantity, po.unit_price, po.status,
                   po.order_date, po.expected_delivery_date, po.notes,
                   po.medicine_id, po.supplier_id, po.order_group_id
            FROM pending_orders po
            LEFT JOIN suppliers s ON s.id = po.supplier_id
            ORDER BY
                CASE po.status
                    WHEN 'ordered' THEN 0
                    WHEN 'draft' THEN 1
                    WHEN 'received' THEN 2
                    ELSE 3
                END,
                po.order_date DESC, po.id DESC
            """
        )
    cols = (
        "id", "order_no", "medicine_name", "pack_size", "supplier_name",
        "quantity", "unit_price", "status", "order_date",
        "expected_delivery_date", "notes", "medicine_id", "supplier_id",
        "order_group_id",
    )
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_pending_order_groups(conn, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Pending orders grouped by supplier batch (one row per saved supplier order)."""
    rows = fetch_pending_orders(conn, status)
    groups: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        st = (row.get("status") or "").lower()
        if st == "cancelled":
            continue
        gk = _group_key(row.get("order_group_id"), int(row["id"]))
        if gk not in groups:
            groups[gk] = {
                "group_id": gk,
                "order_no": row.get("order_no") or "",
                "supplier_name": row.get("supplier_name") or "",
                "status": row.get("status") or "",
                "order_date": row.get("order_date") or "",
                "lines": [],
                "first_id": int(row["id"]),
            }
        groups[gk]["lines"].append(row)
    result: List[Dict[str, Any]] = []
    for g in groups.values():
        meds = [_as_str(ln.get("medicine_name")) for ln in g["lines"] if ln.get("medicine_name")]
        preview = ", ".join(meds[:3])
        if len(meds) > 3:
            preview = f"{preview} (+{len(meds) - 3} more)"
        result.append({
            "group_id": g["group_id"],
            "order_no": g["order_no"],
            "supplier_name": g["supplier_name"],
            "medicine_count": len(g["lines"]),
            "medicines_preview": preview or "—",
            "total_qty": sum(float(ln.get("quantity") or 0) for ln in g["lines"]),
            "status": g["status"],
            "order_date": g["order_date"],
            "first_id": g["first_id"],
        })
    result.sort(key=lambda x: (x.get("order_date") or "", x.get("first_id") or 0), reverse=True)
    return result


def fetch_pending_orders_by_group(conn, group_id: str) -> List[Dict[str, Any]]:
    gid = _as_str(group_id)
    if gid.startswith("single:"):
        oid = int(gid.split(":", 1)[1])
        order = fetch_pending_order(conn, oid)
        return [order] if order else []
    cur = conn.cursor()
    cur.execute(
        """
        SELECT po.id, po.order_no, po.medicine_name, po.pack_size,
               COALESCE(s.name, po.supplier_name_manual, ''),
               po.quantity, po.unit_price, po.status,
               po.order_date, po.expected_delivery_date, po.notes,
               po.medicine_id, po.supplier_id, po.order_group_id,
               po.supplier_name_manual, po.supplier_phone, po.order_offline,
               po.offline_note, po.current_stock, po.min_stock
        FROM pending_orders po
        LEFT JOIN suppliers s ON s.id = po.supplier_id
        WHERE po.order_group_id=?
        ORDER BY po.id
        """,
        (gid,),
    )
    cols = (
        "id", "order_no", "medicine_name", "pack_size", "supplier_name",
        "quantity", "unit_price", "status", "order_date",
        "expected_delivery_date", "notes", "medicine_id", "supplier_id",
        "order_group_id", "supplier_name_manual", "supplier_phone",
        "order_offline", "offline_note", "current_stock", "min_stock",
    )
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_pending_order(conn, order_id: int) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM pending_orders WHERE id=?", (int(order_id),))
    row = cur.fetchone()
    if not row:
        return None
    cur.execute("PRAGMA table_info(pending_orders)")
    names = [c[1] for c in cur.fetchall()]
    return dict(zip(names, row))


def mark_order_received(conn, order_id: int) -> None:
    """Mark pending order received. Stock must already be added via Purchase (new batch)."""
    order = fetch_pending_order(conn, order_id)
    if not order:
        raise ValueError("Order not found.")
    if (order.get("status") or "").lower() == "received":
        raise ValueError("Order already received.")

    cur = conn.cursor()
    cur.execute(
        "UPDATE pending_orders SET status='received' WHERE id=?",
        (int(order_id),),
    )
    mname = (order.get("medicine_name") or "").strip()
    pack = (order.get("pack_size") or "").strip()
    if mname:
        if pack:
            cur.execute(
                "UPDATE medicines SET is_hidden=0 "
                "WHERE name=? AND COALESCE(unit,'')=? AND COALESCE(stock_qty, 0) > 0",
                (mname, pack),
            )
        else:
            cur.execute(
                "UPDATE medicines SET is_hidden=0 "
                "WHERE name=? AND COALESCE(stock_qty, 0) > 0",
                (mname,),
            )
    conn.commit()


def complete_pending_order_from_purchase(conn, order_id: int) -> None:
    """Mark pending order received after Purchase saved stock."""
    try:
        mark_order_received(conn, int(order_id))
    except ValueError:
        pass


def build_purchase_prefill(conn, order_id: int) -> dict:
    """Build prefill dict for opening the Purchase page from a pending order."""
    order = fetch_pending_order(conn, int(order_id))
    if not order:
        raise ValueError("Order not found.")
    supplier_name = (order.get("supplier_name_manual") or "").strip()
    if order.get("supplier_id"):
        cur = conn.cursor()
        cur.execute("SELECT name FROM suppliers WHERE id=?", (int(order["supplier_id"]),))
        row = cur.fetchone()
        if row and row[0]:
            supplier_name = row[0]
    return {
        "supplier_name": supplier_name,
        "medicine_name": (order.get("medicine_name") or "").strip(),
        "pack_size": (order.get("pack_size") or "").strip(),
        "quantity": float(order.get("quantity") or 0),
        "rate": float(order.get("unit_price") or 0),
        "order_id": int(order_id),
        "order_no": order.get("order_no") or "",
    }


def collect_reorder_candidates(
    conn,
    *,
    include_low_stock: bool = True,
    include_out_of_stock: bool = True,
) -> List[Dict[str, Any]]:
    """Low-stock and out-of-stock medicines eligible for supplier-grouped reorder."""
    from core.alert_monitoring_service import (
        fetch_low_stock_alerts,
        fetch_out_of_stock_medicines,
    )

    seen: set = set()
    items: List[Dict[str, Any]] = []

    def _add(name, pack, med_type, stock, rate, supplier_name):
        key = (name.lower(), _as_str(pack))
        if key in seen:
            return
        seen.add(key)
        stock_f = float(stock or 0)
        pack_s = _as_str(pack)
        med_t = _as_str(med_type) or "Others"
        supplier_s = _as_str(supplier_name)
        items.append({
            "medicine_name": _as_str(name),
            "pack_size": pack_s,
            "med_type": med_t,
            "current_stock": stock_f,
            "unit_price": float(rate or 0),
            "supplier_name": supplier_s,
            "suggested_qty": suggest_order_quantity(conn, name, med_t, stock_f, pack_s),
        })

    try:
        from core.sync_prefs import is_online_mode

        online = is_online_mode()
    except Exception:
        online = False
    if online:
        # Online the engine's connection is an empty :memory: shell, so the SQL
        # below found nothing: "Reorder by Supplier" from the alert popup answered
        # "No low or out-of-stock medicines" on a shop the popup had just listed
        # them for. Read the same sections the popup reads. No fallback: a store
        # that cannot be read raises instead of passing for a full shelf.
        from core.alert_monitoring_service import (
            online_stock_sections,
            online_visible_medicines,
        )

        meds = online_visible_medicines()
        sections = online_stock_sections(conn, meds)
        rate_by: Dict[str, float] = {}
        type_by: Dict[str, str] = {}

        def _mid(m: Dict[str, Any]) -> int:
            try:
                return int(m.get("id") or m.get("local_id") or 0)
            except (TypeError, ValueError):
                return 0

        for m in sorted(meds, key=_mid):
            nm = _as_str(m.get("name"))
            # The SQL's picks: type off the first row, rate off the newest.
            type_by.setdefault(nm, _as_str(m.get("type")))
            try:
                rate_by[nm] = float(m.get("rate") or 0)
            except (TypeError, ValueError):
                rate_by[nm] = 0.0
        # The supplier, picked the way the offline SQL picks it
        # (_latest_supplier_by_medicine_id): the newest purchase that carries
        # the medicine row and names a supplier. Low stock asks for the row with
        # the most stock (the later row on a tie), out of stock for the pack's
        # newest row. The shelf list names no supplier, so every item used to
        # land under "(No Supplier)".
        from core.alert_monitoring_service import (
            _pack_key,
            online_latest_supplier_by_medicine_id,
        )

        try:
            supplier_by_mid = online_latest_supplier_by_medicine_id()
        except Exception as exc:
            print(f"[reorder] online supplier lookup failed: {exc}")
            supplier_by_mid = {}
        low_sample: Dict[str, int] = {}
        low_best: Dict[str, float] = {}
        oos_sample: Dict[Any, int] = {}
        for m in sorted(meds, key=_mid):
            nm = _as_str(m.get("name"))
            try:
                qty_m = float(m.get("stock_qty") or 0)
            except (TypeError, ValueError):
                qty_m = 0.0
            if qty_m > 0 and qty_m >= low_best.get(nm, 0.0):
                low_best[nm] = qty_m
                low_sample[nm] = _mid(m)
            oos_sample[_pack_key(nm, str(m.get("unit") or ""))] = _mid(m)

        if include_low_stock:
            for name, qty, unit, supplier in sections["low_stock"]:
                supplier = supplier or supplier_by_mid.get(low_sample.get(_as_str(name), 0), "")
                _add(name, unit, type_by.get(_as_str(name)) or "Others", qty,
                     rate_by.get(_as_str(name), 0.0), supplier)
        if include_out_of_stock:
            for name, unit, mrp, rate, med_type, supplier in sections["out_of_stock"]:
                supplier = supplier or supplier_by_mid.get(
                    oos_sample.get(_pack_key(_as_str(name), unit), 0), ""
                )
                _add(name, unit, med_type, 0, rate or mrp, supplier)
        return items

    if include_low_stock:
        for name, qty, unit, supplier in fetch_low_stock_alerts(conn):
            cur = conn.cursor()
            cur.execute(
                "SELECT COALESCE(rate, 0) FROM medicines WHERE name=? "
                "AND COALESCE(is_hidden,0)=0 ORDER BY id DESC LIMIT 1",
                (name,),
            )
            row = cur.fetchone()
            rate = float(row[0] or 0) if row else 0.0
            cur.execute(
                "SELECT COALESCE(type,'') FROM medicines WHERE name=? "
                "AND COALESCE(is_hidden,0)=0 LIMIT 1",
                (name,),
            )
            trow = cur.fetchone()
            med_type = trow[0] if trow else "Others"
            _add(name, unit, med_type, qty, rate, supplier)

    if include_out_of_stock:
        for name, unit, mrp, rate, med_type, supplier in fetch_out_of_stock_medicines(conn):
            _add(name, unit, med_type, 0, rate or mrp, supplier)

    return items


def _resolve_supplier_id(conn, supplier_name: str) -> Optional[int]:
    name = _as_str(supplier_name)
    if not name:
        return None
    try:
        from core.sync_prefs import is_online_mode
        if is_online_mode():
            from core.online_catalog import find_supplier_by_name
            s = find_supplier_by_name(name)
            if s:
                return int(s.get("id") or s.get("local_id") or 0) or None
            return None
    except Exception:
        pass
    cur = conn.cursor()
    cur.execute("SELECT id FROM suppliers WHERE name=? COLLATE NOCASE LIMIT 1", (name,))
    row = cur.fetchone()
    return int(row[0]) if row else None


def fetch_supplier_choices(conn) -> List[Dict[str, Any]]:
    """All suppliers for dropdowns: id, name, phone."""
    try:
        from core.sync_prefs import is_online_mode
        if is_online_mode():
            from core.online_catalog import suppliers as oc_suppliers
            rows = []
            for s in oc_suppliers():
                sid = int(s.get("id") or s.get("local_id") or 0)
                name = (s.get("name") or "").strip()
                if not sid or not name:
                    continue
                rows.append({
                    "supplier_id": sid,
                    "name": name,
                    "phone": (s.get("phone") or "").strip(),
                })
            rows.sort(key=lambda r: r["name"].lower())
            return rows
    except Exception:
        pass
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, COALESCE(name,''), COALESCE(phone,'')
        FROM suppliers
        ORDER BY name COLLATE NOCASE
        """
    )
    return [
        {"supplier_id": int(r[0]), "name": r[1] or "", "phone": r[2] or ""}
        for r in cur.fetchall()
    ]


def fetch_medicine_names_for_supplier(conn, supplier_id: int) -> List[str]:
    """Medicine names linked to a supplier via purchases or medicine_suppliers."""
    try:
        from core.sync_prefs import is_online_mode
        if is_online_mode():
            # Online: local medicine_suppliers/purchases are empty — offer store inventory names.
            from core.online_catalog import search_medicine_names
            return [r["name"] for r in search_medicine_names("", limit=20000) if r.get("name")]
    except Exception:
        pass
    sid = int(supplier_id)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT medicine_name FROM medicine_suppliers WHERE supplier_id=?
        UNION
        SELECT DISTINCT m.name
        FROM purchase_items pi
        JOIN purchases p ON pi.purchase_id = p.id
        JOIN medicines m ON pi.medicine_id = m.id
        WHERE p.supplier_id = ?
        ORDER BY 1 COLLATE NOCASE
        """,
        (sid, sid),
    )
    return [r[0] for r in cur.fetchall() if r[0]]


def create_supplier_grouped_draft_orders(conn) -> Dict[str, Any]:
    """
    Create draft pending orders for all low/out-of-stock medicines,
    grouped by supplier (one draft per medicine line, same supplier).
    Returns summary dict with counts per supplier group.
    """
    items = collect_reorder_candidates(conn)
    if not items:
        return {"total": 0, "groups": {}, "order_ids": []}

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        supplier = _as_str(item.get("supplier_name")) or "(No Supplier)"
        groups.setdefault(supplier, []).append(item)

    order_ids: List[int] = []
    group_counts: Dict[str, int] = {}
    cur = conn.cursor()

    for supplier_label, group_items in sorted(groups.items(), key=lambda x: x[0].lower()):
        supplier_id = None
        manual_name = ""
        if supplier_label != "(No Supplier)":
            supplier_id = _resolve_supplier_id(conn, supplier_label)
            if not supplier_id:
                manual_name = supplier_label
        else:
            manual_name = ""

        group_lines: List[Dict[str, Any]] = []
        for item in group_items:
            data = {
                "medicine_id": lookup_medicine_id(
                    conn, item["medicine_name"], item.get("pack_size", "")),
                "medicine_name": item["medicine_name"],
                "pack_size": item.get("pack_size", ""),
                "supplier_id": supplier_id,
                "supplier_name_manual": manual_name if not supplier_id else "",
                "supplier_phone": "",
                "supplier_email": "",
                "order_offline": supplier_label == "(No Supplier)",
                "offline_note": "No supplier on record" if supplier_label == "(No Supplier)" else "",
                "quantity": float(item.get("suggested_qty") or 0),
                "unit_price": float(item.get("unit_price") or 0),
                "current_stock": float(item.get("current_stock") or 0),
                "min_stock": min_stock_level(conn, item.get("med_type", "Others")),
                "order_date": str(_today()),
                "expected_delivery_date": "",
                "notes": f"Auto-generated for supplier group: {supplier_label}",
            }
            if data["quantity"] <= 0:
                continue
            group_lines.append(data)

        if not group_lines:
            continue
        group_id = _next_group_id(cur)
        for data in group_lines:
            data["order_group_id"] = group_id
            oid = save_pending_order(conn, data, status="draft")
            order_ids.append(oid)
        group_counts[supplier_label] = len(group_lines)

    return {
        "total": len(order_ids),
        "groups": group_counts,
        "order_ids": order_ids,
    }


def cancel_pending_order(conn, order_id: int) -> None:
    cur = conn.cursor()
    cur.execute(
        "UPDATE pending_orders SET status='cancelled' WHERE id=?",
        (int(order_id),),
    )
    conn.commit()


def cancel_pending_order_group(conn, group_id: str) -> None:
    for order in fetch_pending_orders_by_group(conn, group_id):
        if (order.get("status") or "").lower() != "cancelled":
            cancel_pending_order(conn, int(order["id"]))


def mark_order_group_received(conn, group_id: str) -> None:
    for order in fetch_pending_orders_by_group(conn, group_id):
        if (order.get("status") or "").lower() != "received":
            mark_order_received(conn, int(order["id"]))
