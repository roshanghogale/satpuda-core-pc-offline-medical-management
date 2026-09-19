"""Return and write-off service for expired / near-expiry stock."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Any, Dict, List, Optional


def lookup_batch_purchase_online(
    medicine_id: int, medicine_name: str = "", batch_no: str = ""
) -> Optional[Dict[str, Any]]:
    """The same lookup, against the server.

    Without this the disposal screen could never link a return to the bill it
    came in on: lookup_batch_purchase reads the local purchases table, which in
    Online mode is an empty :memory: shell. So "Return to supplier" quietly
    became a write-off with no supplier credit -- the shop lost the money it was
    owed and nothing said so.

    Two calls, not one per bill: the store server filters purchases by medicine
    and batch (the same parameters Purchase History uses), and only the newest
    matching bill is opened for its lines.
    """
    from core import store_query_client as sq

    name = (medicine_name or "").strip()
    batch = (batch_no or "").strip()
    if not name:
        return None
    try:
        rows = (sq.list_purchases(medicine=name, batch=batch, limit=5) or {}).get("rows") or []
    except Exception:
        return None
    if not rows:
        return None
    # Newest bill first — the same "most recent purchase of this batch" rule the
    # SQL below uses with its ORDER BY.
    rows = sorted(
        [r for r in rows if isinstance(r, dict)],
        key=lambda r: str(r.get("purchase_date") or ""),
        reverse=True,
    )
    for row in rows:
        pid = int(row.get("id") or row.get("local_id") or 0)
        if pid <= 0:
            continue
        try:
            doc = sq.get_purchase(pid) or {}
        except Exception:
            continue
        for it in doc.get("items") or []:
            if not isinstance(it, dict):
                continue
            if int(it.get("medicine_id") or 0) != int(medicine_id):
                continue
            if batch and str(it.get("batch_no") or "").strip().lower() != batch.lower():
                continue
            return {
                "purchase_id": pid,
                "bill_number": str(doc.get("bill_number") or row.get("bill_number") or ""),
                "purchase_date": str(doc.get("purchase_date") or row.get("purchase_date") or ""),
                "supplier_id": int(doc.get("supplier_id") or row.get("supplier_id") or 0) or None,
                "supplier_name": str(doc.get("supplier_name") or row.get("supplier_name") or ""),
                "qty": float(it.get("qty") or 0),
                "rate": float(it.get("rate") or 0),
            }
    return None


def lookup_batch_purchase(conn, medicine_id: int, batch_no: str = "") -> Optional[Dict[str, Any]]:
    """Find purchase record for a medicine batch."""
    cur = conn.cursor()
    batch = (batch_no or "").strip()
    if medicine_id and batch:
        cur.execute(
            """
            SELECT p.id, p.bill_number, p.purchase_date, s.id, s.name,
                   pi.qty, pi.rate
            FROM purchase_items pi
            JOIN purchases p ON pi.purchase_id = p.id
            LEFT JOIN suppliers s ON p.supplier_id = s.id
            WHERE pi.medicine_id = ? AND COALESCE(pi.batch_no, '') = ?
            ORDER BY p.purchase_date DESC, p.id DESC
            LIMIT 1
            """,
            (int(medicine_id), batch),
        )
        row = cur.fetchone()
        if row:
            return {
                "purchase_id": int(row[0]),
                "bill_number": row[1] or "",
                "purchase_date": str(row[2] or ""),
                "supplier_id": int(row[3]) if row[3] else None,
                "supplier_name": row[4] or "",
                "original_qty": float(row[5] or 0),
                "rate": float(row[6] or 0),
            }

    if medicine_id:
        cur.execute(
            """
            SELECT p.id, p.bill_number, p.purchase_date, s.id, s.name,
                   pi.qty, pi.rate
            FROM purchase_items pi
            JOIN purchases p ON pi.purchase_id = p.id
            LEFT JOIN suppliers s ON p.supplier_id = s.id
            WHERE pi.medicine_id = ?
            ORDER BY p.purchase_date DESC, p.id DESC
            LIMIT 1
            """,
            (int(medicine_id),),
        )
        row = cur.fetchone()
        if row:
            return {
                "purchase_id": int(row[0]),
                "bill_number": row[1] or "",
                "purchase_date": str(row[2] or ""),
                "supplier_id": int(row[3]) if row[3] else None,
                "supplier_name": row[4] or "",
                "original_qty": float(row[5] or 0),
                "rate": float(row[6] or 0),
            }
    return None


def fetch_medicine_batch(conn, medicine_id: int) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, name, COALESCE(batch_no,''), COALESCE(expiry_date,''),
               COALESCE(stock_qty,0), COALESCE(unit,''), COALESCE(type,''),
               COALESCE(rate,0)
        FROM medicines WHERE id=?
        """,
        (int(medicine_id),),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "medicine_id": int(row[0]),
        "name": row[1] or "",
        "batch_no": row[2] or "",
        "expiry_date": str(row[3] or ""),
        "stock_qty": float(row[4] or 0),
        "unit": row[5] or "",
        "type": row[6] or "",
        "rate": float(row[7] or 0),
    }


def lookup_medicine_by_name_batch(
    conn, medicine_name: str, batch_no: str = "",
) -> Optional[Dict[str, Any]]:
    from core.reorder_service import lookup_medicine_id
    mid = lookup_medicine_id(conn, medicine_name, batch_no=batch_no)
    if not mid:
        return None
    return fetch_medicine_batch(conn, mid)


def _next_disposal_no(cur) -> str:
    cur.execute("SELECT COALESCE(MAX(id),0)+1 FROM stock_disposals")
    n = int(cur.fetchone()[0] or 1)
    return f"SD{datetime.now().strftime('%Y%m%d')}{n:04d}"


def _deduct_stock(cur, medicine_id: int, qty: float) -> None:
    cur.execute(
        "SELECT COALESCE(stock_qty,0) FROM medicines WHERE id=?",
        (int(medicine_id),),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError("Medicine not found.")
    current = float(row[0] or 0)
    if qty > current + 0.001:
        raise ValueError(f"Cannot dispose {qty} — only {current} in stock.")
    new_qty = max(0.0, current - qty)
    cur.execute(
        "UPDATE medicines SET stock_qty=? WHERE id=?",
        (new_qty, int(medicine_id)),
    )


def submit_return(
    conn,
    medicine_id: int,
    quantity: float,
    reason: str,
    *,
    batch_no: str = "",
    supplier_id: Optional[int] = None,
    purchase_id: Optional[int] = None,
    bill_number: str = "",
    original_purchase_qty: float = 0,
    expected_credit_note: bool = False,
    notes: str = "",
    rate_hint: float = 0.0,
) -> str:
    from core.online_guard import ensure_can_mutate
    ensure_can_mutate()
    qty = float(quantity or 0)
    if qty <= 0:
        raise ValueError("Return quantity must be greater than zero.")

    try:
        from core.sync_prefs import is_online_mode

        online = is_online_mode()
    except Exception:
        online = False
    if online:
        # Same reason as submit_writeoff below: conn is an empty :memory: shell
        # in Online mode, so _deduct_stock could only ever answer "Medicine not
        # found." The rate has to come from the caller or the server, because
        # lookup_batch_purchase reads the local purchases table too.
        rate_online = float(rate_hint or 0)
        if purchase_id and rate_online <= 0:
            try:
                from core import store_query_client as sq

                doc = sq.get_purchase(int(purchase_id)) or {}
                for it in (doc.get("items") or []):
                    if int(it.get("medicine_id") or 0) == int(medicine_id):
                        rate_online = float(it.get("rate") or 0)
                        break
            except Exception:
                rate_online = 0.0
        return _submit_return_online(
            int(medicine_id),
            qty,
            reason,
            batch_no=batch_no,
            supplier_id=supplier_id,
            purchase_id=purchase_id,
            bill_number=bill_number,
            original_purchase_qty=original_purchase_qty,
            expected_credit_note=expected_credit_note,
            notes=notes,
            rate=rate_online,
        )

    cur = conn.cursor()
    _deduct_stock(cur, medicine_id, qty)
    disposal_no = _next_disposal_no(cur)
    refund = 0.0
    if purchase_id:
        pinfo = lookup_batch_purchase(conn, medicine_id, batch_no)
        rate = float((pinfo or {}).get("rate") or 0)
        refund = round(qty * rate, 2)

    cur.execute(
        """
        INSERT INTO stock_disposals (
            disposal_no, medicine_id, batch_no, supplier_id, purchase_id,
            bill_number, quantity, original_purchase_qty, reason,
            disposal_type, expected_credit_note, notes, disposal_date
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            disposal_no,
            int(medicine_id),
            (batch_no or "").strip(),
            supplier_id,
            purchase_id,
            (bill_number or "").strip(),
            qty,
            float(original_purchase_qty or 0),
            (reason or "").strip(),
            "return",
            1 if expected_credit_note else 0,
            (notes or "").strip(),
            str(date.today()),
        ),
    )
    disposal_id = int(cur.lastrowid)

    if purchase_id and supplier_id and refund > 0:
        return_no = f"PR{disposal_id}"
        cur.execute(
            """
            INSERT INTO purchase_returns (
                return_no, purchase_id, supplier_id, return_date,
                refund_amount, reason
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                return_no,
                int(purchase_id),
                int(supplier_id),
                str(date.today()),
                refund,
                (reason or "").strip(),
            ),
        )
        ret_id = int(cur.lastrowid)
        cur.execute(
            """
            INSERT INTO purchase_return_items
                (return_id, medicine_id, qty, rate, amount)
            VALUES (?,?,?,?,?)
            """,
            (ret_id, int(medicine_id), qty, refund / qty if qty else 0, refund),
        )
        from core.purchase_service import recalculate_supplier_due
        conn.commit()
        recalculate_supplier_due(conn, int(supplier_id))
    else:
        conn.commit()

    from core.medicine_visibility import hide_medicines_after_return
    hide_medicines_after_return(conn, [int(medicine_id)], reason=reason)
    from core.sync_coordinator import after_medicines_hidden, after_stock_disposal_saved
    after_stock_disposal_saved(conn, disposal_id)
    after_medicines_hidden(conn, [int(medicine_id)])
    if purchase_id and supplier_id and refund > 0:
        try:
            from core.sync_coordinator import after_purchase_return_saved
            after_purchase_return_saved(conn, ret_id, int(supplier_id))
        except Exception:
            pass

    return disposal_no


def _submit_writeoff_online(
    medicine_id: int,
    qty: float,
    reason: str,
    *,
    batch_no: str = "",
    notes: str = "",
) -> str:
    """Write off stock in Online mode: server stock down, disposal doc queued."""
    from core.online_catalog import invalidate
    from core.online_mutation_queue import enqueue
    from core.server_crud import (
        allocate_id,
        bump_meta,
        get_doc,
        upsert_medicine_online,
    )

    existing = get_doc("medicines", int(medicine_id)) or {}
    if not existing:
        raise ValueError("Medicine not found.")
    current = float(existing.get("stock_qty") or 0)
    if qty > current + 0.001:
        raise ValueError(f"Cannot dispose {qty} — only {current:g} in stock.")

    med = bump_meta(dict(existing))
    med["id"] = int(medicine_id)
    med["local_id"] = int(medicine_id)
    med["stock_qty"] = max(0.0, current - qty)
    upsert_medicine_online(med)

    try:
        disposal_id = int(allocate_id("stock_disposals"))
    except Exception:
        disposal_id = 0
    disposal_no = f"SD{datetime.now().strftime('%Y%m%d')}{max(1, disposal_id):04d}"
    payload = {
        "disposal_no": disposal_no,
        "medicine_id": int(medicine_id),
        "batch_no": (batch_no or existing.get("batch_no") or "").strip(),
        "quantity": qty,
        "qty": qty,
        "reason": (reason or "").strip(),
        "disposal_type": "writeoff",
        "notes": (notes or "").strip(),
        "disposal_date": str(date.today()),
    }
    if disposal_id > 0:
        payload["id"] = disposal_id
        payload["local_id"] = disposal_id
    enqueue(
        collection="stock_disposals",
        op="upsert",
        payload=payload,
        local_id=disposal_id if disposal_id > 0 else None,
    )
    invalidate("medicines")
    return disposal_no


def _submit_return_online(
    medicine_id: int,
    qty: float,
    reason: str,
    *,
    batch_no: str = "",
    supplier_id: Optional[int] = None,
    purchase_id: Optional[int] = None,
    bill_number: str = "",
    original_purchase_qty: float = 0,
    expected_credit_note: bool = False,
    notes: str = "",
    rate: float = 0.0,
) -> str:
    """Return stock to the supplier in Online mode.

    The offline path writes three things: the stock comes off the medicine, a
    stock_disposal records what left the shelf and why, and -- when the return
    is against a real purchase bill -- a purchase_return with one line carries
    the credit the supplier owes. Online there is no local database to write any
    of it to (conn is an empty :memory: shell), so this does the same three
    things through the server: read the medicine, write its stock back, and file
    the two documents in the mutation queue.

    Modelled on _submit_writeoff_online above, which has worked this way for a
    while; the only addition is the purchase_return half.
    """
    from core.online_catalog import invalidate
    from core.online_mutation_queue import enqueue
    from core.server_crud import (
        allocate_id,
        bump_meta,
        get_doc,
        upsert_medicine_online,
    )

    existing = get_doc("medicines", int(medicine_id)) or {}
    if not existing:
        raise ValueError("Medicine not found.")
    current = float(existing.get("stock_qty") or 0)
    if qty > current + 0.001:
        raise ValueError(f"Cannot return {qty} — only {current:g} in stock.")

    med = bump_meta(dict(existing))
    med["id"] = int(medicine_id)
    med["local_id"] = int(medicine_id)
    med["stock_qty"] = max(0.0, current - qty)
    upsert_medicine_online(med)

    try:
        disposal_id = int(allocate_id("stock_disposals"))
    except Exception:
        disposal_id = 0
    disposal_no = f"SD{datetime.now().strftime('%Y%m%d')}{max(1, disposal_id):04d}"
    line_rate = float(rate or 0)
    refund = round(qty * line_rate, 2) if purchase_id else 0.0

    payload = {
        "disposal_no": disposal_no,
        "medicine_id": int(medicine_id),
        "batch_no": (batch_no or existing.get("batch_no") or "").strip(),
        "supplier_id": int(supplier_id) if supplier_id else None,
        "purchase_id": int(purchase_id) if purchase_id else None,
        "bill_number": (bill_number or "").strip(),
        "quantity": qty,
        "qty": qty,
        "original_purchase_qty": float(original_purchase_qty or 0),
        "reason": (reason or "").strip(),
        "disposal_type": "return",
        "expected_credit_note": 1 if expected_credit_note else 0,
        "notes": (notes or "").strip(),
        "disposal_date": str(date.today()),
    }
    if disposal_id > 0:
        payload["id"] = disposal_id
        payload["local_id"] = disposal_id
    enqueue(
        collection="stock_disposals",
        op="upsert",
        payload=payload,
        local_id=disposal_id if disposal_id > 0 else None,
    )

    if purchase_id and supplier_id and refund > 0:
        try:
            ret_id = int(allocate_id("purchase_returns"))
        except Exception:
            ret_id = 0
        return_no = f"PR{max(1, ret_id or disposal_id)}"
        ret_payload = {
            "return_no": return_no,
            "purchase_id": int(purchase_id),
            "supplier_id": int(supplier_id),
            "return_date": str(date.today()),
            "refund_amount": refund,
            "reason": (reason or "").strip(),
            "items": [
                {
                    "medicine_id": int(medicine_id),
                    "qty": qty,
                    "rate": line_rate,
                    "amount": refund,
                }
            ],
        }
        if ret_id > 0:
            ret_payload["id"] = ret_id
            ret_payload["local_id"] = ret_id
        enqueue(
            collection="purchase_returns",
            op="upsert",
            payload=ret_payload,
            local_id=ret_id if ret_id > 0 else None,
        )

    invalidate("medicines")
    return disposal_no


def submit_writeoff(
    conn,
    medicine_id: int,
    quantity: float,
    reason: str,
    *,
    batch_no: str = "",
    notes: str = "",
) -> str:
    from core.online_guard import ensure_can_mutate
    ensure_can_mutate()
    qty = float(quantity or 0)
    if qty <= 0:
        raise ValueError("Write-off quantity must be greater than zero.")

    try:
        from core.sync_prefs import is_online_mode

        online = is_online_mode()
    except Exception:
        online = False
    if online:
        # Online mode has no local medicines table -- conn is an empty :memory:
        # shell -- so every write-off used to die on "Medicine not found." from
        # _deduct_stock below. Read stock from the server, write it back, and file
        # the disposal document through the mutation queue.
        return _submit_writeoff_online(
            int(medicine_id), qty, reason, batch_no=batch_no, notes=notes
        )

    cur = conn.cursor()
    _deduct_stock(cur, medicine_id, qty)
    disposal_no = _next_disposal_no(cur)
    cur.execute(
        """
        INSERT INTO stock_disposals (
            disposal_no, medicine_id, batch_no, quantity, reason,
            disposal_type, notes, disposal_date
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            disposal_no,
            int(medicine_id),
            (batch_no or "").strip(),
            qty,
            (reason or "").strip(),
            "writeoff",
            (notes or "").strip(),
            str(date.today()),
        ),
    )
    disposal_id = int(cur.lastrowid)
    conn.commit()

    from core.medicine_visibility import hide_medicines_after_return
    hide_medicines_after_return(conn, [int(medicine_id)], reason=reason)
    from core.sync_coordinator import after_medicines_hidden, after_stock_disposal_saved
    after_stock_disposal_saved(conn, disposal_id)
    after_medicines_hidden(conn, [int(medicine_id)])

    return disposal_no


def collect_return_candidates(
    conn,
    *,
    include_expired: bool = True,
    include_near_expiry: bool = True,
) -> List[Dict[str, Any]]:
    """Near-expiry and/or expired medicines with stock."""
    from core.alert_monitoring_service import (
        fetch_expired_medicines,
        fetch_near_expiry_medicines,
    )
    from core.reorder_service import lookup_medicine_id

    seen: set = set()
    items: List[Dict[str, Any]] = []

    def _add(
        name: str,
        batch: str,
        qty: Any,
        supplier_name: str,
        bill_number: str,
        reason_tag: str,
    ) -> None:
        mid = lookup_medicine_id(conn, name, batch_no=batch)
        if not mid:
            return
        key = (int(mid), (batch or "").strip())
        if key in seen:
            return
        seen.add(key)
        qty_f = float(qty or 0)
        if qty_f <= 0:
            return
        items.append({
            "medicine_id": int(mid),
            "medicine_name": (name or "").strip(),
            "batch_no": (batch or "").strip(),
            "quantity": qty_f,
            "supplier_name": (supplier_name or "").strip(),
            "bill_number": (bill_number or "").strip(),
            "reason_tag": reason_tag,
        })

    for name, batch, _expiry, qty, supplier, bill in (
        fetch_expired_medicines(conn) if include_expired else []
    ):
        _add(name, batch, qty, supplier, bill, "Expired stock")

    for name, batch, _expiry, _days, qty, supplier, bill in (
        fetch_near_expiry_medicines(conn) if include_near_expiry else []
    ):
        _add(name, batch, qty, supplier, bill, "Near expiry")

    return items


def collect_return_candidates_online(
    conn,
    *,
    include_expired: bool = True,
    include_near_expiry: bool = True,
) -> List[Dict[str, Any]]:
    """collect_return_candidates, from the store's shelf (Online).

    Online the engine's connection is an empty :memory: shell: the SQL above finds
    no medicines, so the alert popup's Return found nothing to return on a shop
    with a shelf full of expiring stock. The rows here are the alert sections' own
    (online_stock_sections), so Return offers exactly the batches the popup
    listed. Same dict shape as the offline list, plus name / expiry_date / type /
    unit for the online bulk builder. Raises when the store cannot be read -- an
    unreadable shelf is not an empty one.
    """
    from core.alert_monitoring_service import (
        online_stock_sections,
        online_visible_medicines,
    )

    meds = online_visible_medicines()
    sections = online_stock_sections(conn, meds)
    newest: Dict[tuple, tuple] = {}
    for m in meds:
        try:
            mid = int(m.get("id") or m.get("local_id") or 0)
        except (TypeError, ValueError):
            continue
        if mid <= 0:
            continue
        key = ((m.get("name") or "").strip(), str(m.get("batch_no") or "").strip())
        # lookup_medicine_id's rule: the newest id carrying this name and batch.
        if key not in newest or mid > newest[key][0]:
            newest[key] = (mid, m)

    seen: set = set()
    items: List[Dict[str, Any]] = []

    def _add(name: str, batch: str, qty: Any, reason_tag: str) -> None:
        key = ((name or "").strip(), (batch or "").strip())
        hit = newest.get(key)
        if not hit:
            return
        mid, m = hit
        if (mid, key[1]) in seen:
            return
        seen.add((mid, key[1]))
        qty_f = float(qty or 0)
        if qty_f <= 0:
            return
        items.append({
            "medicine_id": mid,
            "medicine_name": key[0],
            "name": key[0],
            "batch_no": key[1],
            "quantity": qty_f,
            "supplier_name": "",
            "bill_number": "",
            "reason_tag": reason_tag,
            "expiry_date": str(m.get("expiry_date") or ""),
            "type": str(m.get("type") or ""),
            "unit": str(m.get("unit") or ""),
        })

    if include_expired:
        for name, batch, _disp, qty, _supplier, _bill in sections["expired"]:
            _add(name, batch, qty, "Expired stock")
    if include_near_expiry:
        for name, batch, _disp, _days, qty, _supplier, _bill in sections["near_expiry"]:
            _add(name, batch, qty, "Near expiry")
    return items


def create_supplier_grouped_returns(
    conn,
    reason: str = "Bulk return — grouped by supplier",
) -> Dict[str, Any]:
    """
    Process returns (or write-offs when no purchase record) for all near-expiry
    and expired stock, grouped by supplier. Mirrors reorder bulk grouping.
    """
    items = collect_return_candidates(conn)
    if not items:
        return {
            "total": 0,
            "groups": {},
            "disposal_nos": [],
            "writeoffs": 0,
            "skipped": [],
        }

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        supplier = (item.get("supplier_name") or "").strip() or "(No Supplier)"
        groups.setdefault(supplier, []).append(item)

    disposal_nos: List[str] = []
    writeoffs = 0
    skipped: List[Dict[str, str]] = []
    group_counts: Dict[str, int] = {}

    for supplier_label, group_items in sorted(groups.items(), key=lambda x: x[0].lower()):
        count = 0
        for item in group_items:
            med_id = int(item["medicine_id"])
            batch = item.get("batch_no", "")
            qty = float(item["quantity"])
            tag = item.get("reason_tag", "")
            item_reason = f"{reason} — {tag}" if tag else reason
            pinfo = lookup_batch_purchase(conn, med_id, batch)
            try:
                if pinfo and pinfo.get("purchase_id") and pinfo.get("supplier_id"):
                    no = submit_return(
                        conn,
                        med_id,
                        qty,
                        item_reason,
                        batch_no=batch,
                        supplier_id=int(pinfo["supplier_id"]),
                        purchase_id=int(pinfo["purchase_id"]),
                        bill_number=pinfo.get("bill_number") or item.get("bill_number", ""),
                        original_purchase_qty=float(pinfo.get("original_qty") or 0),
                        expected_credit_note=True,
                        notes=f"Supplier group: {supplier_label}",
                    )
                else:
                    no = submit_writeoff(
                        conn,
                        med_id,
                        qty,
                        item_reason,
                        batch_no=batch,
                        notes=(
                            f"No purchase record — supplier group: {supplier_label}"
                            if supplier_label != "(No Supplier)"
                            else "No purchase record"
                        ),
                    )
                    writeoffs += 1
                disposal_nos.append(no)
                count += 1
            except Exception as exc:
                skipped.append({
                    "medicine": item.get("medicine_name", ""),
                    "batch": batch,
                    "error": str(exc),
                })
        group_counts[supplier_label] = count

    return {
        "total": len(disposal_nos),
        "groups": group_counts,
        "disposal_nos": disposal_nos,
        "writeoffs": writeoffs,
        "skipped": skipped,
    }


def fetch_return_lines_for_supplier(conn, supplier_id: int) -> List[Dict[str, Any]]:
    """Return candidates (near expiry / expired) for one supplier."""
    from core.reorder_service import _resolve_supplier_id

    cur = conn.cursor()
    cur.execute("SELECT name FROM suppliers WHERE id=?", (int(supplier_id),))
    row = cur.fetchone()
    supplier_name = (row[0] or "").strip() if row else ""
    lines = []
    for item in collect_return_candidates(conn):
        item_supplier = (item.get("supplier_name") or "").strip()
        if supplier_name and item_supplier.lower() == supplier_name.lower():
            pinfo = lookup_batch_purchase(
                conn, int(item["medicine_id"]), item.get("batch_no", ""))
            lines.append({**item, "purchase_info": pinfo})
    return lines


def build_bulk_return_prefill(conn) -> Dict[str, Any]:
    """Group return candidates by supplier for the grouped return UI."""
    from core.reorder_service import _resolve_supplier_id

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in collect_return_candidates(conn):
        supplier = (item.get("supplier_name") or "").strip() or "(No Supplier)"
        pinfo = lookup_batch_purchase(
            conn, int(item["medicine_id"]), item.get("batch_no", ""))
        groups.setdefault(supplier, []).append({**item, "purchase_info": pinfo})

    supplier_groups = []
    for label in sorted(groups.keys(), key=lambda x: x.lower()):
        sid = None if label == "(No Supplier)" else _resolve_supplier_id(conn, label)
        supplier_groups.append({
            "supplier_name": label,
            "supplier_id": sid,
            "lines": groups[label],
        })
    return {"bulk": True, "supplier_groups": supplier_groups}


def build_bulk_return_by_purchase(
    conn,
    items: Optional[List[Dict[str, Any]]] = None,
    *,
    include_expired: bool = True,
    include_near_expiry: bool = True,
) -> Dict[str, Any]:
    """Group return candidates by purchase; leftovers have no purchase."""
    if items is None:
        items = collect_return_candidates(
            conn, include_expired=include_expired, include_near_expiry=include_near_expiry)
    by_purchase: Dict[int, Dict[str, Any]] = {}
    writeoff_lines: List[Dict[str, Any]] = []

    for item in items:
        med_id = int(item["medicine_id"])
        batch = item.get("batch_no", "")
        pinfo = lookup_batch_purchase(conn, med_id, batch)
        line = {
            "medicine_id": med_id,
            "medicine_name": item.get("medicine_name", ""),
            "batch_no": batch,
            "quantity": float(item.get("quantity") or 0),
            "reason_tag": item.get("reason_tag", ""),
        }
        if pinfo and pinfo.get("purchase_id"):
            pid = int(pinfo["purchase_id"])
            grp = by_purchase.setdefault(pid, {
                "purchase_id": pid,
                "bill_number": pinfo.get("bill_number", ""),
                "supplier_name": pinfo.get("supplier_name", ""),
                "reason": line["reason_tag"] or "Near expiry / Expired stock",
                "lines": [],
            })
            grp["lines"].append(line)
        else:
            writeoff_lines.append(line)

    purchase_groups = sorted(
        by_purchase.values(),
        key=lambda g: (g.get("bill_number") or "", g["purchase_id"]),
    )
    return {
        "purchase_groups": purchase_groups,
        "writeoff_lines": writeoff_lines,
    }


def start_bulk_return_sequence(
    app,
    *,
    include_expired: bool = True,
    include_near_expiry: bool = True,
) -> bool:
    """Open tabbed purchase-return UI (one tab per purchase bill)."""
    data = build_bulk_return_by_purchase(
        app.conn, include_expired=include_expired, include_near_expiry=include_near_expiry)
    if not data.get("purchase_groups") and not data.get("writeoff_lines"):
        return False
    app.open_returns(kind="purchase", prefill=data)
    return True


def run_bulk_return_by_supplier(conn, parent=None) -> bool:
    """Start purchase-grouped return sequence (purchase returns, then write-off)."""
    try:
        root = parent.winfo_toplevel() if parent else None
        app = getattr(root, "_main_app", None) if root else None
        if app and hasattr(app, "open_stock_disposal"):
            app.open_stock_disposal(bulk=True)
            return True
    except Exception:
        pass
    from core.themed_messagebox import showwarning
    showwarning(
        "Return by Purchase",
        "Open Returns from the main menu.",
        parent=parent,
    )
    return False
