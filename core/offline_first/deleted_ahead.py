"""A sales bill another device deleted, about to go up again from this PC.

The server applies an upsert of a record it holds as deleted: the bill comes back (flagged
"concurrent_edit", but back on every device). Two everyday ways to get there in offline-first:

  * the day's COUNTER SALE bill: PC A adds walk-in lines to its own day bill while PC B (or the
    phone) deletes that bill -- A's next send brought the deleted bill back with all its old
    lines, whose stock B had already put back on the shelf;
  * an edit of a bill on A while B deletes it, before A has pulled the delete.

So before each push the worker looks at the server's changes it has not pulled yet
(``SyncCycle.settle_deleted_ahead``). For each bill this PC is about to send again that the
server now holds as deleted, the delete wins and this PC's own change since the server's last
copy (``of_doc_base``) is settled here:

  * a counter bill that only GREW (walk-ins added after the server's copy): those walk-in
    sales are real -- the cash was taken, the stock went out -- so the lines and money added
    become a new counter bill of their own (no stock moves: it already went out);
  * anything else: the edit's own stock movement is put back, so the shelf matches the bills.

The bill is then removed here without a delete event (the server has deleted it already).
A bill whose server copy this PC never noted (made elsewhere, edited here before this
version) is left as before.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Iterable, Optional

log = logging.getLogger(__name__)

COLLECTION = "sales"
_MONEY = ("total_amount", "discount", "rounding", "cash_paid", "online_paid", "amount_paid")
_EPS = 1e-6


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _r2(v) -> float:
    return round(_f(v) + 0.0, 2)


# ── the server's last copy, as this PC knows it ───────────────────────────────

def base_from_doc(doc: dict) -> dict:
    """What a settle needs of a sales document: its lines and its money."""
    items = []
    for it in (doc or {}).get("items") or []:
        if not isinstance(it, dict):
            continue
        try:
            mid = int(it.get("medicine_id") or it.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if mid:
            items.append({"medicine_id": mid, "qty": _f(it.get("qty")), "amount": _f(it.get("amount")),
                          "item_discount": _f(it.get("item_discount") or it.get("medicine_discount"))})
    return {"items": items, **{k: _f((doc or {}).get(k)) for k in _MONEY}}


def note_base(conn: sqlite3.Connection, collection: str, local_id, doc) -> None:
    """The server now holds ``doc`` for this sale (this PC's event was taken, or a pull wrote
    it). Anything else is ignored."""
    if collection != COLLECTION or not isinstance(doc, dict):
        return
    try:
        lid = int(local_id or doc.get("id") or 0)
    except (TypeError, ValueError):
        return
    if lid <= 0:
        return
    if doc.get("deleted") in (True, 1, "1", "true"):
        conn.execute("DELETE FROM of_doc_base WHERE collection=? AND local_id=?", (collection, lid))
        return
    conn.execute(
        "INSERT INTO of_doc_base (collection, local_id, body) VALUES (?,?,?) "
        "ON CONFLICT(collection, local_id) DO UPDATE SET body=excluded.body",
        (collection, lid, json.dumps(base_from_doc(doc))),
    )


def _base(conn: sqlite3.Connection, sale_id: int) -> Optional[dict]:
    row = conn.execute("SELECT body FROM of_doc_base WHERE collection=? AND local_id=?",
                       (COLLECTION, int(sale_id))).fetchone()
    if row:
        try:
            return json.loads(row[0])
        except (TypeError, ValueError):
            pass
    # A store file from before of_doc_base: the last copy this PC sent, when it sent one.
    row = conn.execute(
        "SELECT payload FROM of_events WHERE collection=? AND local_id=? AND op='upsert' "
        "AND status='sent' AND COALESCE(outcome,'') IN ('applied','flagged') ORDER BY seq DESC LIMIT 1",
        (COLLECTION, int(sale_id)),
    ).fetchone()
    if not row:
        return None
    try:
        return base_from_doc((json.loads(row[0]) or {}).get("doc") or {})
    except (TypeError, ValueError):
        return None


# ── which bills to look for ───────────────────────────────────────────────────

def watch_list(conn: sqlite3.Connection) -> set[int]:
    """Sales this PC is about to send again that the server already holds (only those can have
    been deleted there)."""
    rows = conn.execute(
        "SELECT local_id FROM of_dirty WHERE collection=? AND op='upsert' "
        "UNION SELECT local_id FROM of_events WHERE collection=? AND op='upsert' AND status='pending' "
        "AND local_id IS NOT NULL",
        (COLLECTION, COLLECTION),
    ).fetchall()
    out = set()
    for (lid,) in rows:
        lid = int(lid)
        known = conn.execute(
            "SELECT 1 FROM of_versions WHERE collection=? AND local_id=? "
            "UNION SELECT 1 FROM of_events WHERE collection=? AND local_id=? AND status='sent' LIMIT 1",
            (COLLECTION, lid, COLLECTION, lid),
        ).fetchone()
        if known:
            out.add(lid)
    return out


def deleted_in(changes: Iterable[dict], wanted: set[int]) -> set[int]:
    """Of ``wanted``, the sales whose latest entry in these changes is a delete."""
    last: dict[int, str] = {}
    for ch in changes or []:
        if str(ch.get("collection") or "") != COLLECTION:
            continue
        try:
            lid = int(ch.get("local_id") or 0)
        except (TypeError, ValueError):
            continue
        if lid in wanted:
            doc = ch.get("doc") if isinstance(ch.get("doc"), dict) else {}
            gone = ch.get("operation") == "delete" or doc.get("deleted") in (True, 1, "1", "true")
            last[lid] = "delete" if gone else "upsert"
    return {lid for lid, op in last.items() if op == "delete"}


# ── settling one bill ─────────────────────────────────────────────────────────

def _lines(conn: sqlite3.Connection, sale_id: int) -> list[dict]:
    cur = conn.execute("SELECT * FROM sales_items WHERE sale_id=? ORDER BY id", (int(sale_id),))
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _per_medicine(items: Iterable[dict]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for it in items:
        try:
            mid = int(it.get("medicine_id") or 0)
        except (TypeError, ValueError):
            continue
        if not mid:
            continue
        acc = out.setdefault(mid, {"qty": 0.0, "amount": 0.0, "item_discount": 0.0})
        acc["qty"] += _f(it.get("qty"))
        acc["amount"] += _f(it.get("amount"))
        acc["item_discount"] += _f(it.get("item_discount"))
    return out


def _is_counter(conn: sqlite3.Connection, sale: dict) -> bool:
    from core.customer_service import is_counter_sale_name

    if int(sale.get("is_autosave") or 0):
        return False
    row = conn.execute("SELECT name FROM customers WHERE id=?", (int(sale.get("customer_id") or 0),)).fetchone()
    return bool(row and is_counter_sale_name(str(row[0] or "")))


def _drop_unsent(conn: sqlite3.Connection, sale_id: int) -> None:
    """This PC's unsent change of the bill goes: noted, or already an event."""
    conn.execute("DELETE FROM of_dirty WHERE collection=? AND local_id=?", (COLLECTION, int(sale_id)))
    first = conn.execute("SELECT MIN(seq) FROM of_events WHERE status='pending'").fetchone()[0]
    gone = conn.execute(
        "DELETE FROM of_events WHERE status='pending' AND collection=? AND local_id=?",
        (COLLECTION, int(sale_id)),
    ).rowcount
    if gone and first is not None:
        from core.offline_first.outbox import renumber_pending

        renumber_pending(conn, int(first))      # the events after it close the gap


def _new_bill_no(conn: sqlite3.Connection, bill_date: str) -> str:
    from core.fy_serial import allocate_bill_number, encode_sales_bill_no, fy_start_year_for_date
    from core.offline_first.numbers import take_serial

    fy = fy_start_year_for_date(bill_date or None)
    serial = take_serial(conn, "sales", fy)
    if serial is not None:
        return encode_sales_bill_no(serial, fy)
    return allocate_bill_number(conn, "SCB", bill_date=bill_date or None)


def _carve_out(conn: sqlite3.Connection, sale: dict, now_lines: list[dict], base: dict,
               grew: dict[int, float]) -> int:
    """The walk-in lines and money added since the server's copy, as a new counter bill.
    Capture is on: it is noted and sent like any bill. No stock moves."""
    from core.fy_serial import patch_sale_fy_fields
    from core.offline_first.ids import next_id

    base_by = _per_medicine(base.get("items") or [])
    new_id = next_id(conn, "sales")
    day = str(sale.get("bill_date") or "")[:10] or datetime.now().strftime("%Y-%m-%d")
    bill_no = _new_bill_no(conn, day)
    money = {k: _r2(_f(sale.get(k)) - _f(base.get(k))) for k in _MONEY}
    total, paid = money["total_amount"], money["amount_paid"]
    due = _r2(max(0.0, total - paid))
    row = dict(sale)
    row.update(money)
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    row.update({
        "id": new_id, "bill_no": bill_no, "due_amount": due, "total_due": due, "credit_amount": 0.0,
        "paid_due": 0.0, "bill_cleared": 1 if due <= 0.01 else 0, "account_cleared": 1 if due <= 0.01 else 0,
        "created_at": now, "updated_at": now, "version": 1, "deleted": 0, "is_autosave": 0,
    })
    if "client_uuid" in row:
        row["client_uuid"] = str(uuid.uuid4())
    for k in ("sync_status", "sync_state"):
        row.pop(k, None)                          # the table's own defaults
    if "device_id" in row:
        row["device_id"] = None
    cols = [c for c in row if c in sale]          # only the columns this store file has
    conn.execute(f"INSERT INTO sales ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                 [row[c] for c in cols])
    patch_sale_fy_fields(conn.cursor(), new_id, bill_no, day)
    done: set[int] = set()
    for line in now_lines:
        mid = int(line.get("medicine_id") or 0)
        add = grew.get(mid, 0.0)
        if add <= _EPS or mid in done:
            continue
        done.add(mid)
        now_m = _per_medicine([l for l in now_lines if int(l.get("medicine_id") or 0) == mid])[mid]
        was = base_by.get(mid, {"qty": 0.0, "amount": 0.0, "item_discount": 0.0})
        out = {k: v for k, v in line.items() if k != "id"}
        share = add / now_m["qty"] if now_m["qty"] else 1.0
        out.update({
            "sale_id": new_id, "qty": round(add, 4),
            "amount": _r2(now_m["amount"] - was["amount"]),
            "item_discount": _r2(now_m["item_discount"] - was["item_discount"]),
            "created_at": now, "updated_at": now, "version": 1, "deleted": 0,
        })
        for k in ("taxable", "gst_amt"):
            if out.get(k) is not None:
                out[k] = _r2(_f(out[k]) * share)
        out.pop("sync_status", None)
        if "device_id" in out:
            out["device_id"] = None
        lc = [c for c in out if c in line]
        conn.execute(f"INSERT INTO sales_items ({', '.join(lc)}) VALUES ({', '.join('?' * len(lc))})",
                     [out[c] for c in lc])
    return new_id


def settle(conn: sqlite3.Connection, sale_id: int) -> dict:
    """The server holds this sale as deleted and this PC has an unsent change of it. Call inside
    the worker's write transaction, capture ON. Returns what was done."""
    sale_id = int(sale_id)
    cur = conn.execute("SELECT * FROM sales WHERE id=?", (sale_id,))
    names = [d[0] for d in cur.description]
    r = cur.fetchone()
    if r is None:
        _drop_unsent(conn, sale_id)
        return {"sale_id": sale_id, "action": "gone"}
    sale = dict(zip(names, r))
    base = _base(conn, sale_id)
    if base is None:
        log.warning("offline-first: sale %s was deleted on another device; its server copy is "
                    "not known here, so this PC's change is sent as it is", sale_id)
        return {"sale_id": sale_id, "action": "unknown"}
    now_lines = _lines(conn, sale_id)
    now_by = _per_medicine(now_lines)
    base_by = _per_medicine(base.get("items") or [])
    diff = {mid: now_by.get(mid, {}).get("qty", 0.0) - base_by.get(mid, {}).get("qty", 0.0)
            for mid in set(now_by) | set(base_by)}
    out = {"sale_id": sale_id}
    grew = {m: d for m, d in diff.items() if d > _EPS}
    if grew and all(d > -_EPS for d in diff.values()) and _is_counter(conn, sale):
        out["action"] = "new_counter_bill"
        out["new_sale_id"] = _carve_out(conn, sale, now_lines, base, grew)
    else:
        # The edit's own stock movement back (the deleting device put back the server's lines).
        for mid, d in diff.items():
            if abs(d) > _EPS:
                conn.execute("UPDATE medicines SET stock_qty = COALESCE(stock_qty,0) + ? WHERE id=?", (d, mid))
        out["action"] = "delete_kept"
    # The bill itself leaves this PC as it left the server: no delete event (the server has it).
    conn.execute("UPDATE of_flags SET v='0' WHERE k='capture'")
    try:
        conn.execute("DELETE FROM sales_items WHERE sale_id=?", (sale_id,))
        conn.execute("DELETE FROM sales WHERE id=?", (sale_id,))
    finally:
        conn.execute("UPDATE of_flags SET v='1' WHERE k='capture'")
    _drop_unsent(conn, sale_id)
    conn.execute("DELETE FROM of_doc_base WHERE collection=? AND local_id=?", (COLLECTION, sale_id))
    cid = int(sale.get("customer_id") or 0)
    if cid:
        try:
            from core.customer_service import recalculate_customer_due

            recalculate_customer_due(conn, cid, commit=False, sync=False)
        except Exception as exc:
            log.debug("customer due after a deleted sale: %s", exc)
    log.warning("offline-first: sale %s (%s) was deleted on another device: %s", sale_id,
                sale.get("bill_no"), out["action"])
    return out
