"""Hybrid A: tiny durable mutation queue (Online Mac2 only).

Stores only this device's pending save/edit/delete payloads — never a full
sales/purchases mirror. Flushes via existing server APIs (push_bundle / delete).
Peer hints cancel matching pending rows (peer wins).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import date
from typing import Any, Optional

log = logging.getLogger(__name__)

_lock = threading.Lock()
_flush_lock = threading.Lock()
_flush_thread: Optional[threading.Thread] = None
_STATUS_PENDING = "pending"
_STATUS_DONE = "done"
_STATUS_CANCELLED = "cancelled"

# Temp local ids are negative so they never collide with server local_id.
_TEMP_ID_BASE = -1_000_000_000


def _store_key() -> str:
    try:
        from core.store_manager import get_active_store_key

        return (get_active_store_key() or "default").strip() or "default"
    except Exception:
        return "default"


def _queue_path() -> str:
    from core.license_manager import _appdata_dir

    folder = os.path.join(_appdata_dir(), "online_mutation_queue")
    os.makedirs(folder, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in _store_key())
    return os.path.join(folder, f"{safe}.json")


def _load() -> list[dict[str, Any]]:
    path = _queue_path()
    if not os.path.isfile(path):
        return []
    try:
        # utf-8-sig, not utf-8: a byte-order mark at the front made json.loads
        # raise, _load() return [], and the very next _save() overwrite the file
        # with an empty list -- every queued bill and payment in it gone, with
        # only a warning line in the log. utf-8-sig reads both kinds of file.
        with open(path, encoding="utf-8-sig") as fh:
            raw = json.loads(fh.read() or "[]")
        if isinstance(raw, list):
            return [r for r in raw if isinstance(r, dict)]
        log.warning("mutation queue load: file is not a list, keeping a copy")
    except Exception as exc:
        log.warning("mutation queue load: %s", exc)
    _quarantine_queue_file(path)
    return []


def _quarantine_queue_file(path: str) -> None:
    """Move an unreadable queue aside instead of letting it be overwritten.

    Whatever is in it may be the only record of mutations that never reached the
    server, so it must survive for us to recover by hand.
    """
    try:
        for n in range(1, 100):
            dest = f"{path}.unreadable-{n}"
            if not os.path.exists(dest):
                os.replace(path, dest)
                log.warning("mutation queue kept aside at %s", dest)
                return
    except Exception as exc:
        log.warning("mutation queue quarantine failed: %s", exc)


def _save(rows: list[dict[str, Any]]) -> None:
    path = _queue_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, default=str)
    os.replace(tmp, path)


def pending_rows(*, collection: str | None = None) -> list[dict[str, Any]]:
    with _lock:
        rows = _load()
    out = [r for r in rows if str(r.get("status") or "") == _STATUS_PENDING]
    if collection:
        col = collection.strip().lower()
        out = [r for r in out if str(r.get("collection") or "").lower() == col]
    return out


def pending_count() -> int:
    return len(pending_rows())


def temp_id_from_uuid(client_uuid: str) -> int:
    import hashlib

    digest = hashlib.md5((client_uuid or "").encode("utf-8")).hexdigest()[:8]
    n = int(digest, 16) % 900_000_000
    return _TEMP_ID_BASE - n


def enqueue(
    *,
    collection: str,
    op: str,
    payload: dict[str, Any],
    client_uuid: str | None = None,
    local_id: int | None = None,
    base_version: int = 0,
) -> dict[str, Any]:
    """Persist one user mutation. Returns the queue row (including temp id)."""
    cu = (client_uuid or payload.get("client_uuid") or str(uuid.uuid4())).strip()
    col = str(collection).strip().lower()
    op_n = str(op).strip().lower() or "upsert"
    lid = int(local_id or payload.get("id") or payload.get("local_id") or 0)
    if lid <= 0:
        lid = temp_id_from_uuid(cu)
    row = {
        "id": str(uuid.uuid4()),
        "client_uuid": cu,
        "collection": col,
        "op": op_n,
        "local_id": lid,
        "base_version": int(base_version or payload.get("version") or 0),
        "payload": dict(payload),
        "created_at": time.time(),
        "attempts": 0,
        "status": _STATUS_PENDING,
    }
    row["payload"]["client_uuid"] = cu
    row["payload"].setdefault("id", lid)
    row["payload"].setdefault("local_id", lid)
    with _lock:
        rows = _load()
        replaced = False
        if op_n == "upsert" and cu:
            for r in rows:
                if (
                    str(r.get("status")) == _STATUS_PENDING
                    and str(r.get("client_uuid") or "") == cu
                    and str(r.get("collection") or "").lower() == col
                    and str(r.get("op") or "") == "upsert"
                ):
                    r["payload"] = dict(payload)
                    r["payload"]["client_uuid"] = cu
                    r["payload"]["id"] = int(r.get("local_id") or lid)
                    r["payload"]["local_id"] = int(r.get("local_id") or lid)
                    r["base_version"] = int(base_version or payload.get("version") or 0)
                    row = r
                    replaced = True
                    break
        if not replaced:
            rows.append(row)
        cutoff = time.time() - 172800
        rows = [
            r
            for r in rows
            if str(r.get("status")) == _STATUS_PENDING
            or float(r.get("created_at") or 0) > cutoff
            # A refused bill row stays until the shop retries or discards it: its
            # balance and stock are on the server, and dropping it lost the bill.
            or (str(r.get("status")) == _STATUS_BLOCKED and _is_bill_row(r))
        ]
        _save(rows)
    kick_flush()
    try:
        from core.online_guard import _emit_status

        _emit_status("Online · Saved — syncing…")
    except Exception:
        pass
    try:
        from core.store_live_refresh import emit as live_emit

        live_emit(
            {
                "changes": [
                    {
                        "collection": col,
                        "local_id": int(row.get("local_id") or 0),
                        "operation": op_n,
                    }
                ],
                "source": "mutation_queue",
            }
        )
    except Exception:
        pass
    return row


def cancel_matching(
    *,
    client_uuid: str | None = None,
    collection: str | None = None,
    local_id: int | None = None,
) -> int:
    """Peer wins: drop pending rows for the same entity. Returns count cancelled."""
    cu = (client_uuid or "").strip()
    col = (collection or "").strip().lower()
    lid = int(local_id or 0)
    n = 0
    with _lock:
        rows = _load()
        for r in rows:
            if str(r.get("status")) != _STATUS_PENDING:
                continue
            if cu and str(r.get("client_uuid") or "") == cu:
                r["status"] = _STATUS_CANCELLED
                n += 1
                continue
            if (
                col
                and lid > 0
                and str(r.get("collection") or "").lower() == col
                and int(r.get("local_id") or 0) == lid
            ):
                r["status"] = _STATUS_CANCELLED
                n += 1
        if n:
            _save(rows)
    if n:
        log.info("mutation queue cancelled %s row(s) peer-wins", n)
    return n


def pending_by_local_id(collection: str, local_id: int) -> dict[str, Any] | None:
    col = (collection or "").strip().lower()
    lid = int(local_id or 0)
    if not col or not lid:
        return None
    for r in pending_rows(collection=col):
        if int(r.get("local_id") or 0) == lid:
            return r
    return None


def _is_bill_row(row: dict[str, Any]) -> bool:
    """A queued sale whose balance and stock reached the server: only its bill row is left."""
    payload = row.get("payload")
    return (
        str(row.get("collection") or "").lower() == "sales"
        and isinstance(payload, dict)
        and isinstance(payload.get("_bill_row"), dict)
    )


def bill_row_payload(exc) -> dict[str, Any]:
    """Queue payload for server_crud.SaleRowNotSaved: the bill row, and what History shows."""
    sale = dict(exc.sale)
    payload: dict[str, Any] = {
        key: sale.get(key)
        for key in (
            "bill_date", "customer_id", "customer_name", "doctor_name", "total_amount",
            "discount", "amount_paid", "cash_paid", "online_paid", "previous_due",
            "due_amount", "credit_amount", "total_due", "bill_cleared", "account_cleared",
            "items", "client_uuid",
        )
    }
    payload.update(
        {
            # Not the number last tried: another device may hold it.
            "bill_no": "PENDING",
            "_bill_row": sale,
            "_renumber": bool(exc.renumber),
            "_tried_serial": int(exc.tried_serial or 0),
        }
    )
    return payload


def keep_bill_row(exc) -> dict[str, Any]:
    """Queue ONLY the bill row of a sale whose balance and stock reached the server.

    The direct save's answer to server_crud.SaleRowNotSaved. Queueing the whole sale, as
    for a server that was never reached, posted the balance and the stock a second time
    when it replayed. A row the server refused itself is parked at once, so Sales History
    shows the bill as refused and Settings → Sync can retry it.
    """
    payload = bill_row_payload(exc)
    cu = str(payload.get("client_uuid") or "").strip() or str(uuid.uuid4())
    refused = bool(getattr(exc, "refused", False))
    row: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "client_uuid": cu,
        "collection": "sales",
        "op": "upsert",
        "local_id": temp_id_from_uuid(cu),
        "base_version": 0,
        "payload": payload,
        "created_at": time.time(),
        "attempts": 0,
        "status": _STATUS_BLOCKED if refused else _STATUS_PENDING,
    }
    if refused:
        row["blocked"] = True
        row["last_error"] = str(exc)[:500]
    with _lock:
        rows = _load()
        rows.append(row)
        _save(rows)
    if not refused:
        kick_flush()
    try:
        from core.store_live_refresh import emit as live_emit

        live_emit(
            {
                "changes": [
                    {"collection": "sales", "local_id": row["local_id"], "operation": "upsert"}
                ],
                "source": "mutation_queue",
            }
        )
    except Exception:
        pass
    return row


def _keep_only_the_bill_row(row_id: str, exc: Exception) -> dict[str, Any] | None:
    """After a partial commit, leave only the sale's bill row in its queue entry.

    Everything else the entry carried -- the customer's balance, the stock -- is already on
    the server. Replaying it, by itself or when the shop pressed Retry, posted both again.
    Returns the rewritten entry, or None when ``exc`` is not that case.
    """
    try:
        from core.server_crud import SaleRowNotSaved
    except Exception:
        return None
    if not isinstance(exc, SaleRowNotSaved):
        return None
    payload = bill_row_payload(exc)
    with _lock:
        rows = _load()
        for r in rows:
            if str(r.get("id")) == str(row_id):
                r["payload"] = payload
                _save(rows)
                return dict(r)
    return None


def bill_row_sale_id(local_id: int) -> int:
    """The server id of the sale a queued bill row (waiting or refused) stands for, else 0."""
    try:
        lid = int(local_id or 0)
    except (TypeError, ValueError):
        return 0
    if not lid:
        return 0
    with _lock:
        rows = _load()
    for r in rows:
        if (
            int(r.get("local_id") or 0) == lid
            and str(r.get("status") or "") in (_STATUS_PENDING, _STATUS_BLOCKED)
            and _is_bill_row(r)
        ):
            try:
                return int(r["payload"]["_bill_row"].get("id") or 0)
            except (TypeError, ValueError):
                return 0
    return 0


def queued_bill_row(local_id: int) -> dict[str, Any] | None:
    """The bill row queued under ``local_id`` in this store until it lands, else None.

    Waiting to be sent or refused alike: either way the sale's balance and stock are on the
    server and nothing but this row may go for that bill.
    """
    try:
        lid = int(local_id or 0)
    except (TypeError, ValueError):
        return None
    if not lid:
        return None
    with _lock:
        rows = _load()
    for r in rows:
        if (
            int(r.get("local_id") or 0) == lid
            and str(r.get("status") or "") in (_STATUS_PENDING, _STATUS_BLOCKED)
            and _is_bill_row(r)
        ):
            return r
    return None


def refused_bill_row(local_id: int) -> dict[str, Any] | None:
    """The refused bill row queued under ``local_id`` in this store, else None."""
    try:
        lid = int(local_id or 0)
    except (TypeError, ValueError):
        return None
    if not lid:
        return None
    for r in blocked_rows(all_stores=False):
        if int(r.get("local_id") or 0) == lid and _is_bill_row(r):
            return r
    return None


def _sale_key(row: dict[str, Any]) -> tuple[str, int]:
    """Which bill a queued sales write belongs to.

    A bill row and the edits queued behind it stand for the same sale (its server id); a
    create that has not reached the server is its own bill (its temp id).
    """
    if _is_bill_row(row):
        try:
            sid = int(row["payload"]["_bill_row"].get("id") or 0)
        except (TypeError, ValueError):
            sid = 0
        if sid > 0:
            return ("sale", sid)
    try:
        lid = int(row.get("local_id") or 0)
    except (TypeError, ValueError):
        lid = 0
    return ("sale", lid) if lid > 0 else ("queued", lid)


def overlay_sales_dicts() -> list[dict[str, Any]]:
    """Pending sales upserts as history-shaped dicts (not yet on server).

    A bill row the server refused is listed too, marked refused. Its customer balance and
    stock are on the server; reading pending rows only made a bill that exists nowhere
    else vanish from Sales History.

    One bill is one row. The edits of a bill whose row still waits are queued against the
    sale's server id, and each used to be drawn as a bill of its own: one waiting bill and
    three autosave ticks showed as four bills with different totals. They now fold into
    the bill's own row, which shows the latest edit's figures, and a bill the shop has
    already deleted (its delete is queued) is not offered again.
    """
    refused_rows = [r for r in blocked_rows(all_stores=False) if _is_bill_row(r)]
    sales_rows = pending_rows(collection="sales")
    deleting: set[int] = set()
    for r in sales_rows:
        if str(r.get("op")) == "delete":
            try:
                deleting.add(int(r.get("local_id") or 0))
            except (TypeError, ValueError):
                pass
    order: list[tuple[str, int]] = []
    heads: dict[tuple[str, int], dict[str, Any]] = {}
    latest: dict[tuple[str, int], dict[str, Any]] = {}
    for r in sales_rows + refused_rows:
        if str(r.get("op")) == "delete":
            continue
        key = _sale_key(r)
        if key not in heads:
            order.append(key)
            heads[key] = r
        if _is_bill_row(r):
            # While the row waits, the bill is known by the row's own id and status.
            heads[key] = r
        else:
            latest[key] = r
    out: list[dict[str, Any]] = []
    for key in order:
        if key[0] == "sale" and key[1] in deleting:
            continue
        out.append(_overlay_sale_row(heads[key], latest.get(key) or heads[key]))
    return out


def _overlay_sale_row(head: dict[str, Any], newest: dict[str, Any]) -> dict[str, Any]:
    """The Sales History row for one queued bill: named by ``head``, figures from ``newest``."""
    p = newest.get("payload") or {}
    refused = str(head.get("status") or "") == _STATUS_BLOCKED
    shown = {
        "id": int(head.get("local_id") or 0),
        "local_id": int(head.get("local_id") or 0),
        "client_uuid": head.get("client_uuid"),
        "pending": True,
        "bill_no": "REFUSED" if refused else ((head.get("payload") or {}).get("bill_no") or "PENDING"),
        "bill_date": p.get("bill_date") or date.today().isoformat(),
        "customer_name": p.get("customer_name") or "",
        "customer_id": p.get("customer_id"),
        "doctor_name": p.get("doctor_name") or "",
        "total_amount": p.get("total_amount") or 0,
        "discount": p.get("discount") or 0,
        "amount_paid": p.get("amount_paid") or 0,
        "cash_paid": p.get("cash_paid") or 0,
        "online_paid": p.get("online_paid") or 0,
        "previous_due": p.get("previous_due") or 0,
        "due_amount": p.get("due_amount") or 0,
        "credit_amount": p.get("credit_amount") or 0,
        "total_due": p.get("total_due") or 0,
        "bill_cleared": p.get("bill_cleared") or 0,
        "account_cleared": p.get("account_cleared") or 0,
        "item_count": len(p.get("medicines") or p.get("items") or []),
    }
    if refused:
        shown["refused"] = True
        shown["refused_reason"] = str(head.get("last_error") or "")
    return shown


def overlay_purchase_dicts() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in pending_rows(collection="purchases"):
        if str(r.get("op")) == "delete":
            continue
        p = r.get("payload") or {}
        out.append(
            {
                "id": int(r.get("local_id") or 0),
                "local_id": int(r.get("local_id") or 0),
                "client_uuid": r.get("client_uuid"),
                "pending": True,
                "purchase_no": p.get("purchase_no") or "PENDING",
                "bill_number": p.get("bill_number") or "",
                "purchase_date": p.get("purchase_date") or date.today().isoformat(),
                "supplier_name": p.get("supplier_name") or "",
                "supplier_id": p.get("supplier_id"),
                "final_amount": p.get("final_amount") or p.get("total_amount") or 0,
                "total_amount": p.get("total_amount") or 0,
                "amount_paid": p.get("amount_paid") or 0,
                "amount_paid_at_entry": p.get("amount_paid_at_entry") or 0,
                "cash_paid_at_entry": p.get("cash_paid_at_entry") or 0,
                "online_paid_at_entry": p.get("online_paid_at_entry") or 0,
                "due": p.get("due") or p.get("due_amount") or 0,
                "due_amount": p.get("due_amount") or p.get("due") or 0,
                "bill_cleared": p.get("bill_cleared") or 0,
                "item_count": len(p.get("items") or []),
            }
        )
    return out


def overlay_sales_return_dicts() -> list[dict[str, Any]]:
    """Pending sales returns as history-shaped dicts (not yet on server)."""
    out: list[dict[str, Any]] = []
    for r in pending_rows(collection="sales_returns"):
        if str(r.get("op")) == "delete":
            continue
        p = r.get("payload") or {}
        lid = int(r.get("local_id") or 0)
        out.append(
            {
                "id": lid,
                "local_id": lid,
                "client_uuid": r.get("client_uuid"),
                "pending": True,
                "return_no": p.get("return_no") or (f"SR{abs(lid)}" if lid else "PENDING"),
                "return_date": p.get("return_date") or date.today().isoformat(),
                "sale_id": p.get("sale_id"),
                "bill_no": p.get("bill_no") or "",
                "customer_id": p.get("customer_id"),
                "customer_name": p.get("customer_name") or "",
                "refund_amount": p.get("refund_amount") or 0,
                "discount": p.get("discount") or 0,
                "reason": p.get("reason") or "",
                "settle_mode": p.get("settle_mode") or "ledger",
                "refund_payout": p.get("refund_payout") or 0,
                "items": p.get("items") or [],
            }
        )
    return out


def overlay_purchase_return_dicts() -> list[dict[str, Any]]:
    """Pending purchase returns as history-shaped dicts (not yet on server)."""
    out: list[dict[str, Any]] = []
    for r in pending_rows(collection="purchase_returns"):
        if str(r.get("op")) == "delete":
            continue
        p = r.get("payload") or {}
        lid = int(r.get("local_id") or 0)
        out.append(
            {
                "id": lid,
                "local_id": lid,
                "client_uuid": r.get("client_uuid"),
                "pending": True,
                "return_no": p.get("return_no") or (f"PR{abs(lid)}" if lid else "PENDING"),
                "return_date": p.get("return_date") or date.today().isoformat(),
                "purchase_id": p.get("purchase_id"),
                "purchase_no": p.get("purchase_no") or p.get("bill_label") or "",
                "supplier_id": p.get("supplier_id"),
                "supplier_name": p.get("supplier_name") or "",
                "refund_amount": p.get("refund_amount") or 0,
                "discount": p.get("discount") or 0,
                "reason": p.get("reason") or "",
                "items": p.get("items") or [],
            }
        )
    return out


def pending_return_line_qty(
    collection: str, parent_key: str, parent_id: int, medicine_id: int
) -> float:
    """Qty already queued for return on this bill line (not flushed yet)."""
    total = 0.0
    col = (collection or "").strip().lower()
    pid = int(parent_id or 0)
    mid = int(medicine_id or 0)
    if not col or pid <= 0 or mid <= 0:
        return 0.0
    for r in pending_rows(collection=col):
        if str(r.get("op")) == "delete":
            continue
        p = r.get("payload") or {}
        try:
            if int(p.get(parent_key) or 0) != pid:
                continue
        except (TypeError, ValueError):
            continue
        for it in p.get("items") or []:
            if not isinstance(it, dict):
                continue
            try:
                if int(it.get("medicine_id") or 0) != mid:
                    continue
                total += float(it.get("qty") or 0)
            except (TypeError, ValueError):
                continue
    return total


def overlay_supplier_payment_dicts() -> list[dict[str, Any]]:
    """Pending supplier payments as history-shaped dicts (not yet on server)."""
    out: list[dict[str, Any]] = []
    for r in pending_rows(collection="supplier_payments"):
        if str(r.get("op")) == "delete":
            continue
        p = r.get("payload") or {}
        out.append(
            {
                "id": int(r.get("local_id") or 0),
                "local_id": int(r.get("local_id") or 0),
                "client_uuid": r.get("client_uuid"),
                "pending": True,
                "payment_no": p.get("payment_no") or "PENDING",
                "supplier_id": p.get("supplier_id"),
                "supplier_name": p.get("supplier_name") or p.get("party") or "",
                "payment_date": p.get("payment_date") or date.today().isoformat(),
                "amount": p.get("amount") or 0,
                "mode": p.get("mode") or "",
                "reference": p.get("reference") or p.get("note") or "",
                "due_before": p.get("due_before"),
                "due_after": p.get("due_after"),
            }
        )
    return out


def overlay_customer_payment_dicts() -> list[dict[str, Any]]:
    """Pending customer payments as history-shaped dicts (not yet on server)."""
    out: list[dict[str, Any]] = []
    for r in pending_rows(collection="customer_payments"):
        if str(r.get("op")) == "delete":
            continue
        p = r.get("payload") or {}
        out.append(
            {
                "id": int(r.get("local_id") or 0),
                "local_id": int(r.get("local_id") or 0),
                "client_uuid": r.get("client_uuid"),
                "pending": True,
                "customer_id": p.get("customer_id"),
                "customer_name": p.get("customer_name") or p.get("party") or "",
                "payment_date": p.get("payment_date") or date.today().isoformat(),
                "amount": p.get("amount") or 0,
                "payment_mode": p.get("payment_mode") or p.get("mode") or "",
                "mode": p.get("payment_mode") or p.get("mode") or "",
                "cash_amount": p.get("cash_amount") or p.get("cash") or 0,
                "online_amount": p.get("online_amount") or p.get("online") or 0,
                "reference_no": p.get("reference_no") or p.get("reference") or "",
                "note": p.get("note") or "",
            }
        )
    return out


def merge_server_rows(
    server_rows: list[dict[str, Any]],
    overlays: list[dict[str, Any]],
    *,
    collection: str | None = None,
) -> list[dict[str, Any]]:
    """Server rows + pending overlays; pending edits replace same local_id."""
    del_ids: set[int] = set()
    if collection:
        for r in pending_rows(collection=collection):
            if str(r.get("op")) == "delete":
                try:
                    del_ids.add(int(r.get("local_id") or 0))
                except (TypeError, ValueError):
                    pass
    overlay_by_id: dict[int, dict[str, Any]] = {}
    overlay_by_uuid: dict[str, dict[str, Any]] = {}
    for o in overlays:
        try:
            lid = int(o.get("id") or o.get("local_id") or 0)
        except (TypeError, ValueError):
            lid = 0
        cu = str(o.get("client_uuid") or "").strip()
        if lid > 0:
            overlay_by_id[lid] = o
        if cu:
            overlay_by_uuid[cu] = o

    seen_uuid: set[str] = set()
    seen_id: set[int] = set()
    fingerprints: set[tuple] = set()
    kept: list[dict[str, Any]] = []
    for r in server_rows:
        try:
            rid = int(r.get("id") or r.get("local_id") or 0)
        except (TypeError, ValueError):
            rid = 0
        if rid and rid in del_ids:
            continue
        cu = str(r.get("client_uuid") or "").strip()
        # Pending edit for this bill wins over stale server row
        if rid and rid in overlay_by_id:
            o = overlay_by_id.pop(rid)
            merged = dict(r)
            merged.update(o)
            merged["id"] = rid
            merged["local_id"] = rid
            kept.append(merged)
            if cu:
                seen_uuid.add(cu)
            seen_id.add(rid)
            ocu = str(o.get("client_uuid") or "").strip()
            if ocu:
                seen_uuid.add(ocu)
                overlay_by_uuid.pop(ocu, None)
            continue
        if cu and cu in overlay_by_uuid:
            o = overlay_by_uuid.pop(cu)
            merged = dict(r)
            merged.update(o)
            if rid:
                merged["id"] = rid
                merged["local_id"] = rid
                seen_id.add(rid)
                overlay_by_id.pop(rid, None)
            kept.append(merged)
            seen_uuid.add(cu)
            continue
        if cu:
            seen_uuid.add(cu)
        if rid:
            seen_id.add(rid)
        date_s = str(
            r.get("bill_date") or r.get("purchase_date") or ""
        )[:10]
        party = str(
            r.get("customer_name") or r.get("supplier_name") or ""
        ).strip().upper()
        try:
            amt = round(float(r.get("total_amount") or r.get("final_amount") or 0), 2)
        except (TypeError, ValueError):
            amt = 0.0
        if date_s and party:
            fingerprints.add((date_s, party, amt))
        kept.append(r)
    extra = []
    for o in overlays:
        cu = str(o.get("client_uuid") or "").strip()
        lid = int(o.get("id") or 0)
        if cu and cu in seen_uuid:
            continue
        if lid > 0 and lid in seen_id:
            continue
        date_s = str(
            o.get("bill_date") or o.get("purchase_date") or ""
        )[:10]
        party = str(
            o.get("customer_name") or o.get("supplier_name") or ""
        ).strip().upper()
        try:
            amt = round(float(o.get("total_amount") or o.get("final_amount") or 0), 2)
        except (TypeError, ValueError):
            amt = 0.0
        if date_s and party and (date_s, party, amt) in fingerprints:
            continue
        extra.append(o)
    return extra + kept


def queue_health() -> dict[str, Any]:
    """Pending count + age for status / long-drop warnings."""
    rows = pending_rows()
    now = time.time()
    oldest = 0.0
    for r in rows:
        age = now - float(r.get("created_at") or now)
        if age > oldest:
            oldest = age
    blocked = blocked_rows()
    return {
        "pending": len(rows),
        "oldest_sec": oldest,
        "blocked": len(blocked),
        # What the shop needs to read: which record, and why the server said no.
        "blocked_detail": [
            {
                "id": str(r.get("id") or ""),
                "collection": str(r.get("collection") or ""),
                "op": str(r.get("op") or ""),
                "local_id": r.get("local_id"),
                "error": str(r.get("last_error") or ""),
            }
            for r in blocked[:20]
        ],
        "warn": len(rows) >= 20 or oldest >= 600 or bool(blocked),
    }


def kick_flush() -> None:
    global _flush_thread
    with _lock:
        if _flush_thread is not None and _flush_thread.is_alive():
            return
        _flush_thread = threading.Thread(
            target=_flush_loop, daemon=True, name="OnlineMutationFlush"
        )
        _flush_thread.start()


def flush_now(*, wait_sec: float = 45.0) -> bool:
    """
    Wait until pending Online mutations are flushed (or timeout).
    Call after supplier/customer payments so server due updates before UI refresh.
    """
    deadline = time.time() + max(1.0, float(wait_sec))
    kick_flush()
    while time.time() < deadline:
        if not pending_rows():
            return True
        kick_flush()
        time.sleep(0.12)
    return not pending_rows()


_STATUS_BLOCKED = "blocked"

# Statuses the server returns when it is REFUSING the write, not failing at it.
# Retrying these forever only hides the refusal from the shop.
# 403 is deliberately NOT here: the token expiring or a store's access being
# re-checked comes back as 403, and store_query_client re-pairs and retries.
# Parking those would strand ordinary writes behind an auth blip.
_PERMANENT_STATUSES = (400, 409, 422)


def _is_permanent_refusal(exc: Exception) -> bool:
    try:
        from core.server_crud import BundleDocumentRejected, SaleRowNotSaved

        if isinstance(exc, SaleRowNotSaved):
            # Only the bill row is left, and sending it alone cannot post the balance or
            # the stock again: a network error or a run of taken numbers is simply tried
            # again. A row the server itself refused is parked for the shop.
            return exc.refused
        if isinstance(exc, BundleDocumentRejected):
            # The rest of that bundle already committed under its own savepoint.
            # Replaying it re-sent the customer's balance and, for a new sale, took a
            # fresh bill number on every attempt -- forever, since this is a plain
            # RuntimeError. Park it where the shop can see and retry it.
            return True
    except Exception:
        pass
    try:
        from core.server_api import ServerHttpError

        if isinstance(exc, ServerHttpError):
            return int(getattr(exc, "status", 0) or 0) in _PERMANENT_STATUSES
    except Exception:
        pass
    text = str(exc)
    return any(f"HTTP {code}" in text for code in _PERMANENT_STATUSES)


def _mark_blocked(row_id: str, message: str) -> None:
    """Park a refused mutation so it stops retrying and can be shown."""
    with _lock:
        rows = _load()
        for r in rows:
            if str(r.get("id")) == str(row_id):
                r["status"] = _STATUS_BLOCKED
                r["last_error"] = (message or "")[:500]
                r["blocked"] = True
        _save(rows)


def _all_queue_files() -> list[str]:
    """Every store's queue file on this device, not just the active store's."""
    import glob

    try:
        folder = os.path.dirname(_queue_path())
        return sorted(glob.glob(os.path.join(folder, "*.json")))
    except Exception:
        return []


def _read_queue_file(path: str) -> list[dict[str, Any]]:
    try:
        with open(path, encoding="utf-8-sig") as fh:
            raw = json.loads(fh.read() or "[]")
        return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
    except Exception:
        return []


def _write_queue_file(path: str, rows: list[dict[str, Any]]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, default=str)
    os.replace(tmp, path)


def blocked_rows(*, all_stores: bool = True) -> list[dict[str, Any]]:
    """Mutations the server refused. These need the shop to decide.

    Across EVERY store on the device by default. A refusal sits in the queue of
    the store it belongs to, so looking only at the active store hid a stuck
    change until the shop happened to switch to that store -- which is exactly
    how a purchase delete sat refused for a day without anyone being able to see
    it, let alone retry it.
    """
    out: list[dict[str, Any]] = []
    if not all_stores:
        with _lock:
            rows = _load()
        return [r for r in rows if str(r.get("status") or "") == _STATUS_BLOCKED]
    with _lock:
        for path in _all_queue_files():
            store = os.path.splitext(os.path.basename(path))[0]
            for r in _read_queue_file(path):
                if str(r.get("status") or "") == _STATUS_BLOCKED:
                    row = dict(r)
                    row["store_key"] = store
                    out.append(row)
    return out


def retry_blocked(row_id: str = "") -> int:
    """Put refused mutations back in the queue (after the cause is fixed).

    Across every store, for the same reason blocked_rows spans them all: the
    change is stuck in its own store's queue whichever store happens to be open.
    """
    n = 0
    with _lock:
        for path in _all_queue_files():
            rows = _read_queue_file(path)
            touched = 0
            for r in rows:
                if str(r.get("status") or "") != _STATUS_BLOCKED:
                    continue
                if row_id and str(r.get("id")) != str(row_id):
                    continue
                r["status"] = _STATUS_PENDING
                r["attempts"] = 0
                r.pop("blocked", None)
                touched += 1
            if touched:
                _write_queue_file(path, rows)
                n += touched
    if n:
        kick_flush()
    return n


def discard_blocked(row_id: str = "") -> int:
    """Drop refused mutations the shop has decided to abandon, in any store.

    Discarding a refused bill row also cancels what was queued behind it: an edit or a delete
    of a sale the server never received. Left in the queue, that edit ran against nothing,
    took the stock off a second time and came back as another refused bill. The
    unfinished-sale record that pointed at the row is marked discarded: it is no longer
    offered on every start, and the form that may still be open on it writes nothing more.
    Forgotten outright, that form's next tick or F7 saved the sale as a new bill and posted
    the balance and the stock a second time.
    """
    n = 0
    forgotten: list[int] = []
    with _lock:
        for path in _all_queue_files():
            rows = _read_queue_file(path)
            dropped = [
                r for r in rows
                if str(r.get("status") or "") == _STATUS_BLOCKED
                and (not row_id or str(r.get("id")) == str(row_id))
            ]
            if not dropped:
                continue
            gone = {str(r.get("id")) for r in dropped}
            sales = {
                _sale_key(r) for r in dropped
                if _is_bill_row(r) and _sale_key(r)[0] == "sale"
            }
            keep: list[dict[str, Any]] = []
            for r in rows:
                if str(r.get("id")) in gone:
                    continue
                if (
                    sales
                    and str(r.get("collection") or "").lower() == "sales"
                    and not _is_bill_row(r)
                    and str(r.get("status") or "") in (_STATUS_PENDING, _STATUS_BLOCKED)
                    and _sale_key(r) in sales
                ):
                    r["status"] = _STATUS_CANCELLED
                    r["last_error"] = "Cancelled: the refused bill it waited for was discarded."
                keep.append(r)
            _write_queue_file(path, keep)
            n += len(dropped)
            forgotten.extend(int(r.get("local_id") or 0) for r in dropped if _is_bill_row(r))
    for lid in forgotten:
        try:
            from core.autosave_session import mark_sale_discarded

            mark_sale_discarded(lid)
        except Exception as exc:
            log.warning("unfinished-sale record of a discarded bill row: %s", exc)
    return n


def _mark(row_id: str, status: str) -> None:
    with _lock:
        rows = _load()
        for r in rows:
            if str(r.get("id")) == row_id:
                r["status"] = status
                r["attempts"] = int(r.get("attempts") or 0) + 1
        _save(rows)


def _record_error(row_id: str, message: str) -> None:
    """Store the last failure on the queued row so it can be diagnosed."""
    try:
        with _lock:
            rows = _load()
            for r in rows:
                if str(r.get("id")) == str(row_id):
                    r["last_error"] = (message or "")[:500]
                    break
            _save(rows)
    except Exception:
        pass


def _bump_attempt(row_id: str) -> None:
    with _lock:
        rows = _load()
        for r in rows:
            if str(r.get("id")) == row_id:
                r["attempts"] = int(r.get("attempts") or 0) + 1
        _save(rows)


_BUNDLE_UPSERTS = frozenset(
    {"customers", "suppliers", "doctors", "general_products"}
)

# Collections the single-row flusher may push as a plain document. Keep this to
# records that carry no stock or ledger consequences of their own -- anything that
# moves stock or money needs its own _flush_* helper.
_GENERIC_UPSERTS = frozenset({"stock_disposals", "pending_orders"})


def _is_simple_bundle_row(row: dict[str, Any]) -> bool:
    if str(row.get("op") or "") != "upsert":
        return False
    col = str(row.get("collection") or "")
    if col not in _BUNDLE_UPSERTS:
        return False
    payload = row.get("payload") or {}
    if not isinstance(payload, dict):
        return False
    if payload.get("stock_ops") or payload.get("_fifo"):
        return False
    return True


def _take_simple_group(pending: list[dict[str, Any]]) -> list[dict[str, Any]]:
    group: list[dict[str, Any]] = []
    for r in pending:
        if not _is_simple_bundle_row(r):
            break
        group.append(r)
        if len(group) >= 80:
            break
    return group if len(group) >= 2 else []


def _flush_simple_group(rows: list[dict[str, Any]]) -> None:
    from core.server_crud import _meta, push_bundle

    bundle: dict[str, list] = {}
    for r in rows:
        col = str(r.get("collection") or "")
        payload = {
            k: v
            for k, v in (r.get("payload") or {}).items()
            if not str(k).startswith("_")
        }
        bundle.setdefault(col, []).append(_meta(payload))
    if bundle:
        push_bundle(bundle)


# A row must never be reordered against another row touching the SAME document,
# so the flush loop defers by (collection, local_id) rather than by row.
_DEFER_AFTER_ATTEMPTS = 3


def _dep_key(row: dict[str, Any]) -> tuple[str, int]:
    if _is_bill_row(row):
        # Keyed by the sale it stands for, so an edit or a delete queued for that sale
        # (by its server id) waits behind the bill row instead of reaching the server first.
        try:
            return ("sales", int(row["payload"]["_bill_row"].get("id") or 0))
        except (TypeError, ValueError):
            pass
    try:
        lid = int(row.get("local_id") or 0)
    except (TypeError, ValueError):
        lid = 0
    if lid == 0:
        payload = row.get("payload") or {}
        try:
            lid = int(payload.get("local_id") or payload.get("id") or 0)
        except (TypeError, ValueError):
            lid = 0
    return (str(row.get("collection") or ""), lid)


def _refresh_after_version_conflict(message: str) -> None:
    """Drop cached copies when the server says this device is out of date.

    The conflict message is self-perpetuating otherwise: the push fails because
    the cached document is stale, and nothing in the retry path ever re-reads it,
    so the same row fails forever.
    """
    text = (message or "").lower()
    if "out of date" not in text and "version" not in text:
        return
    try:
        from core.online_catalog import invalidate

        invalidate("medicines")
    except Exception:
        pass


def _flush_loop() -> None:
    if not _flush_lock.acquire(blocking=False):
        return
    try:
        from core.online_guard import is_cloud_reachable

        # Documents whose head mutation keeps failing this pass. The loop used to
        # take pending[0] unconditionally, so ONE un-pushable row stopped the whole
        # queue for good: a purchase delete that kept failing a version check was
        # retried 48 times while a counter sale queued behind it was never tried
        # once, and the bill simply never reached the server. Stepping over a
        # poisoned document -- only after it has failed repeatedly, and only for
        # OTHER documents -- keeps per-document order while letting the rest drain.
        deferred: set[tuple[str, int]] = set()
        # Nothing queued for a sale whose bill row the server refused goes before that
        # row: an edit or a delete of a bill the server does not hold cannot land.
        try:
            deferred.update(
                _dep_key(r) for r in blocked_rows(all_stores=False) if _is_bill_row(r)
            )
        except Exception:
            pass

        while True:
            if not is_cloud_reachable(force=False) and not is_cloud_reachable(
                force=True
            ):
                return
            pending = pending_rows()
            if not pending:
                try:
                    from core.online_guard import _emit_status

                    _emit_status("Online · Live (Server)")
                except Exception:
                    pass
                return
            if deferred:
                pending = [r for r in pending if _dep_key(r) not in deferred] or None
                if pending is None:
                    # Everything left is a document we just backed off from; wait
                    # for the next tick rather than hammering the same rows.
                    return
            group = _take_simple_group(pending)
            try:
                if group:
                    _flush_simple_group(group)
                    cols = set()
                    for r in group:
                        _mark(str(r.get("id")), _STATUS_DONE)
                        cols.add(str(r.get("collection") or ""))
                    try:
                        from core.sync_status import note_collection_change

                        for col in cols:
                            if col:
                                note_collection_change(col)
                    except Exception:
                        pass
                else:
                    row = pending[0]
                    _flush_one(row)
                    _mark(str(row.get("id")), _STATUS_DONE)
                    try:
                        from core.sync_status import note_collection_change

                        note_collection_change(str(row.get("collection") or "sales"))
                    except Exception:
                        pass
                try:
                    health = queue_health()
                    from core.online_guard import _emit_status

                    if health.get("blocked"):
                        # A refusal is not a backlog: no amount of waiting or
                        # reconnecting will clear it, so say what it is -- and
                        # keep saying it. The status line is rewritten on every
                        # pass, so without this branch the message was replaced
                        # by "Live (Server)" a moment later and the shop never
                        # saw that a change had been rejected.
                        _emit_status(
                            f"Online · {health['blocked']} change(s) refused by the "
                            "server — see Settings → Sync"
                        )
                    elif health.get("warn"):
                        _emit_status(
                            f"Online · Sync backlog {health['pending']} "
                            f"(oldest {int(health['oldest_sec'] // 60)}m) — check connection"
                        )
                    elif health.get("pending"):
                        _emit_status(f"Online · Syncing {health['pending']}…")
                    else:
                        _emit_status("Online · Live (Server)")
                except Exception:
                    pass
            except Exception as exc:
                log.warning("mutation flush failed: %s", exc)
                failed = group[0] if group else pending[0]
                # After a partial commit only the bill row is left to send. Rewrite the
                # entry before it is retried or parked, so neither the replay nor the
                # shop's Retry sends the customer's balance and the stock again.
                failed = _keep_only_the_bill_row(str(failed.get("id")), exc) or failed
                fail_id = str(failed.get("id"))
                # A new sale sent with no answer may already be on the server: keep the id
                # it was sent under, so the next try asks for it instead of taking a number.
                _remember_sale_id(fail_id, exc)
                # Persist the reason. Without this a stuck row showed
                # attempts=544 and last_error=None, so nothing on the device
                # could say why it never drained.
                _record_error(fail_id, str(exc))
                _bump_attempt(fail_id)
                _refresh_after_version_conflict(str(exc))
                if _is_permanent_refusal(exc):
                    # The server did not fail, it REFUSED -- a business rule the
                    # retry can never satisfy. One purchase delete sat here 18
                    # times against "Cannot delete purchase - X has only 0 in
                    # stock", while the shop had been told the bill was gone and
                    # kept seeing its supplier due. Park it and say so.
                    _mark_blocked(fail_id, str(exc))
                    deferred.add(_dep_key(failed))
                    continue
                if int(failed.get("attempts") or 0) + 1 >= _DEFER_AFTER_ATTEMPTS:
                    # Stop this document from starving everything behind it.
                    deferred.add(_dep_key(failed))
                time.sleep(min(8.0, 1.5 * (int(failed.get("attempts") or 0) + 1)))
                if not is_cloud_reachable(force=True):
                    return
    finally:
        _flush_lock.release()


def _flush_one(row: dict[str, Any]) -> None:
    from core.sync_prefs import is_online_mode

    if not is_online_mode():
        return
    col = str(row.get("collection") or "")
    op = str(row.get("op") or "upsert")
    payload = dict(row.get("payload") or {})
    cu = str(row.get("client_uuid") or payload.get("client_uuid") or "")
    lid = int(row.get("local_id") or payload.get("id") or 0)

    if col == "sales" and op == "upsert" and _is_bill_row(row):
        _flush_sale_bill_row(payload, lid)
        return
    if col == "sales" and op == "upsert" and lid > 0:
        _flush_sale_update(payload, lid)
        return
    if col == "sales" and op == "upsert":
        _flush_sale_create(payload)
        return
    if col == "sales" and op == "delete":
        _flush_sale_delete(payload, lid, row_id=str(row.get("id") or ""))
        return
    if col == "purchases" and op == "upsert" and lid > 0:
        _flush_purchase_payload(payload)
        return
    if col == "purchases" and op == "upsert":
        _flush_purchase_payload(payload)
        return
    if col == "purchases" and op == "delete":
        _flush_purchase_delete(payload, lid, row_id=str(row.get("id") or ""))
        return
    if col in ("sales_returns", "purchase_returns") and op == "upsert":
        _flush_return_payload(col, payload)
        return
    if col in ("sales_returns", "purchase_returns") and op == "delete":
        _flush_return_delete(col, payload, lid)
        return
    if col in ("customer_payments", "supplier_payments") and op == "upsert":
        from core.server_crud import upsert_payment_online, push_bundle, get_doc, bump_meta

        clean = {k: v for k, v in payload.items() if not str(k).startswith("_")}
        upsert_payment_online(col, clean)
        fifo = payload.get("_fifo")
        if fifo == "customer":
            from core.desktop_settings_service import _fifo_clear_customer_sales_online

            _fifo_clear_customer_sales_online(
                int(payload.get("customer_id") or 0),
                str(payload.get("customer_name") or ""),
            )
        elif fifo == "supplier":
            from core.desktop_settings_service import _fifo_clear_supplier_purchases_online

            _fifo_clear_supplier_purchases_online(
                int(payload.get("supplier_id") or 0),
                str(payload.get("supplier_name") or ""),
            )
        # Always force party balance after payment + FIFO. Client `_customers` /
        # `_suppliers` often lose LWW (low version) so server cascade alone is
        # not enough when cascade was skipped or purchases were not yet updated.
        try:
            if col == "supplier_payments":
                sid = int(payload.get("supplier_id") or 0)
                if sid > 0:
                    due_after = float(payload.get("due_after") or 0)
                    credit = 0.0
                    for s in payload.get("_suppliers") or []:
                        if int(s.get("id") or s.get("local_id") or 0) == sid:
                            due_after = float(s.get("total_due") or due_after)
                            credit = float(s.get("total_credit") or 0)
                            break
                    existing = get_doc("suppliers", sid) or {}
                    doc = bump_meta(dict(existing) if existing else {})
                    doc["id"] = sid
                    doc["local_id"] = sid
                    doc["name"] = (
                        payload.get("supplier_name")
                        or existing.get("name")
                        or ""
                    )
                    doc["total_due"] = max(0.0, float(due_after))
                    doc["total_credit"] = max(0.0, float(credit))
                    push_bundle({"suppliers": [doc]})
            elif col == "customer_payments":
                cid = int(payload.get("customer_id") or 0)
                if cid > 0:
                    due_after = float(payload.get("due_after") or 0)
                    credit = 0.0
                    for c in payload.get("_customers") or []:
                        if int(c.get("id") or c.get("local_id") or 0) == cid:
                            due_after = float(c.get("total_due") or due_after)
                            credit = float(c.get("total_credit") or 0)
                            break
                    existing = get_doc("customers", cid) or {}
                    doc = bump_meta(dict(existing) if existing else {})
                    doc["id"] = cid
                    doc["local_id"] = cid
                    doc["name"] = (
                        payload.get("customer_name")
                        or existing.get("name")
                        or ""
                    )
                    doc["total_due"] = max(0.0, float(due_after))
                    doc["total_credit"] = max(0.0, float(credit))
                    push_bundle({"customers": [doc]})
        except Exception as exc:
            log.warning("post-payment party balance push: %s", exc)
        return
    if op == "delete" and col:
        from core.server_crud import delete_entity

        if lid > 0:
            delete_entity(col, lid)
        return
    if op == "upsert" and col in _GENERIC_UPSERTS:
        # Straightforward documents with no stock or ledger side effects. Without
        # this they fell through to the warning below and were dropped silently --
        # a write-off could never reach the server at all.
        from core.server_crud import _meta, push_bundle

        clean = {
            k: v for k, v in payload.items() if not str(k).startswith("_")
        }
        push_bundle({col: [_meta(clean)]})
        return
    log.warning("unknown mutation %s %s uuid=%s", col, op, cu)


def _remember_on_row(row_id: str, updates: dict[str, Any]) -> bool:
    """Write ``updates`` into a queued row's payload, so the row's next attempt sees them."""
    if not row_id:
        return False
    try:
        with _lock:
            rows = _load()
            for r in rows:
                if str(r.get("id")) == str(row_id):
                    payload = dict(r.get("payload") or {})
                    payload.update(updates)
                    r["payload"] = payload
                    _save(rows)
                    return True
    except Exception as exc:
        log.warning("mutation queue: could not keep %s on row %s: %s", sorted(updates), row_id, exc)
    return False


def _bill_on_store(collection: str, bill_id: int) -> dict[str, Any] | None:
    """The store's copy of a bill, None only when the store has none.

    server_crud.get_doc answers None for a network error too, and a delete that took an
    unreachable store for "no bill" deleted the bill on the next try with no stock taken back.
    """
    from core.server_crud import get_doc

    doc = get_doc(collection, bill_id)
    if doc:
        return doc
    from core import server_api as api
    from core.server_crud import _token

    return api.pull_doc(_token(), collection, int(bill_id)) or None


def _same_bill(held: dict[str, Any], kept: dict[str, Any]) -> bool:
    """Whether the store's ``held`` bill is still the bill a queued delete worked out ``kept`` from.

    The server removes deleted sales and purchases outright, so the next bill saved can take the
    same id (and the same number) while a delete of the old one is still being retried.
    """
    for key in ("client_uuid", "created_at"):
        a = str(held.get(key) or "").strip()
        b = str(kept.get(key) or "").strip()
        if a and b:
            return a == b
    return True


def _delete_reversal(doc: dict[str, Any], bill_id: int, *, sale: bool) -> list[dict[str, Any]]:
    """One stock movement per MEDICINE that deleting ``doc`` owes the shelf.

    The store knows a deleted bill's movement by "<sale|purchase>:<bill uuid>:med:<mid>:delete:v1"
    and applies one key once. One movement per LINE, as this was, put a medicine on two lines
    under that one key twice: the second was skipped as already applied (same qty) or refused
    (another qty), and that line's stock stayed where it was (staging R3 and G2, 14 Sep).
    """
    from core.server_crud import _device_id

    cu = str(doc.get("client_uuid") or bill_id)
    units: dict[int, float] = {}
    names: dict[int, str] = {}
    for it in doc.get("items") or []:
        if not isinstance(it, dict):
            continue
        try:
            mid = int(it.get("medicine_id") or 0)
        except (TypeError, ValueError):
            continue
        if mid <= 0:
            continue
        if sale:
            qty = abs(float(it.get("qty") or 0))
        else:
            from core.purchase_service import (
                _get_stock_increase,
                _normalize_purchase_item_stock_fields,
            )

            line = dict(it)
            try:
                _normalize_purchase_item_stock_fields(line)
                qty = abs(float(_get_stock_increase(line) or 0))
            except Exception:
                # The same arithmetic failed (a catalogue that could not be read, say). Take back
                # what the save added all the same: strips times tablets per strip for a strip
                # line. Taking back strips alone left most of the batch as phantom stock.
                qty = abs(_purchase_line_units_from_its_own_fields(it))
        if qty == 0:
            continue
        units[mid] = units.get(mid, 0.0) + qty
        names.setdefault(mid, str(it.get("name") or it.get("medicine_name") or ""))
    kind = "sale" if sale else "purchase"
    out: list[dict[str, Any]] = []
    for mid, qty in units.items():
        # Reverse exactly what the bill moved, negatives included (see _push_delete_reversal).
        delta = int(round(qty)) if sale else -int(round(qty))
        if delta == 0:
            continue
        out.append({
            "op_uuid": f"{kind}:{cu}:med:{mid}:delete:v1",
            "op": f"{kind}_delete",
            "qty_delta": delta,
            "medicine_id": mid,
            "ref_collection": "sales" if sale else "purchases",
            "ref_id": int(bill_id),
            "device_id": _device_id(),
            "_name": names.get(mid) or "",
        })
    return out


def _push_delete_reversal(ops: list[dict[str, Any]], *, hide_empty: bool) -> None:
    """Send a deleted bill's stock movements, one medicine document each.

    Server copy FIRST, and bump the version. Reading the cache and stamping _meta produced a
    document the server skipped on version -- HTTP 200, nothing written -- while the local cache
    still showed the new number until the next restart, which is why stock changes "came back".
    The store moves the shelf by the ops alone, and applies each key once, so sending these again
    after an attempt that got no answer never moves the stock twice.
    """
    from core.online_catalog import medicine_by_id, patch_docs
    from core.server_crud import bump_meta, get_doc, upsert_docs

    med_docs = []
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        mid = int(op.get("medicine_id") or 0)
        delta = int(op.get("qty_delta") or 0)
        if mid <= 0 or delta == 0:
            continue
        mp = get_doc("medicines", mid) or medicine_by_id(mid) or {
            "id": mid, "local_id": mid, "name": op.get("_name") or "", "stock_qty": 0,
        }
        mp = bump_meta(dict(mp))
        mp["id"] = mid
        mp["local_id"] = mid
        try:
            # A negative row is the honest answer: a clamp here disagreed with the op, which always
            # carries the full movement, and the difference simply vanished.
            mp["stock_qty"] = float(mp.get("stock_qty") or 0) + delta
        except Exception:
            pass
        mp["stock_ops"] = [{k: v for k, v in op.items() if not str(k).startswith("_")}]
        if hide_empty:
            try:
                if float(mp.get("stock_qty") or 0) <= 0:
                    mp["is_hidden"] = True
                    mp["stock_qty"] = 0
            except Exception:
                pass
        med_docs.append(mp)
    if not med_docs:
        return
    upsert_docs("medicines", med_docs)
    try:
        patch_docs("medicines", med_docs)
    except Exception:
        pass


def _flush_bill_delete(payload: dict[str, Any], lid: int, row_id: str, *, sale: bool) -> None:
    """Delete a sale or a purchase on the store, then take its stock back.

    The bill goes FIRST: the server can refuse a purchase delete (a business rule, not a network
    failure), and moving the stock before asking left the goods taken off the shelf against a
    bill that was still there. What the delete owes the shelf is kept on the queue row before
    the bill is deleted. A retry used to read the store again, find the bill gone and mark the
    row done with nothing moved, so a stock push lost after a delete that landed was lost for
    good; and a new bill that had taken the old one's id meanwhile was deleted in its place.
    """
    from core.server_crud import delete_purchase_online, delete_sale_online

    collection = "sales" if sale else "purchases"
    bill_id = int(payload.get("sale_id" if sale else "purchase_id") or lid)
    if bill_id <= 0:
        return
    kept = payload.get("_delete_reversal")
    kept_bill = payload.get("_deleted_bill") if isinstance(payload.get("_deleted_bill"), dict) else {}
    held = _bill_on_store(collection, bill_id)
    if isinstance(kept, list) and (not held or held.get("deleted") or not _same_bill(held, kept_bill)):
        # The delete landed on an earlier attempt; only its stock is still owed.
        _push_delete_reversal(kept, hide_empty=not sale)
        party = int(kept_bill.get("party_id") or 0)
    else:
        doc = held or {}
        ops = _delete_reversal(doc, bill_id, sale=sale)
        party = int(doc.get("customer_id" if sale else "supplier_id") or 0)
        _remember_on_row(row_id, {
            "_delete_reversal": ops,
            "_deleted_bill": {
                "client_uuid": doc.get("client_uuid") or "",
                "created_at": doc.get("created_at") or "",
                "party_id": party,
            },
        })
        if sale:
            delete_sale_online(bill_id)
        else:
            delete_purchase_online(bill_id)
        _push_delete_reversal(ops, hide_empty=not sale)
    # The server recomputes a party's balance on an upsert; a deleted bill kept counting until
    # something else ran it, so the shop kept seeing a due for a bill that no longer existed.
    if sale:
        _recompute_customer_balance_online(party)
    else:
        _recompute_supplier_balance_online(party)


def _flush_sale_delete(payload: dict[str, Any], lid: int, row_id: str = "") -> None:
    _flush_bill_delete(payload, lid, row_id, sale=True)


def _recompute_customer_balance_online(customer_id: int) -> None:
    """Rebuild one customer's balance from the server's own live documents.

    Same arithmetic as core.customer_service.recalculate_customer_due:
        net = sales - paid on the bills - receipts - return refunds
    """
    if customer_id <= 0:
        return
    try:
        from core.online_catalog import find_customer_by_id, invalidate
        from core.server_crud import get_doc, upsert_contact_online
        from core.store_query_client import (
            list_customer_payments,
            list_sales,
            list_sales_returns,
        )
    except Exception:
        return

    def _rows(res):
        if isinstance(res, dict):
            for key in ("rows", "items", "data", "results"):
                val = res.get(key)
                if isinstance(val, list):
                    return val
            return []
        return res if isinstance(res, list) else []

    def _live(row):
        return isinstance(row, dict) and not row.get("deleted") and not row.get("is_autosave")

    def _f(row, *names):
        for n in names:
            v = (row or {}).get(n)
            if v not in (None, ""):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return 0.0

    try:
        billed = paid = receipts = refunds = 0.0
        for row in _rows(list_sales(limit=5000)):
            if not _live(row) or int(row.get("customer_id") or 0) != customer_id:
                continue
            billed += _f(row, "total_amount")
            paid += _f(row, "amount_paid")
        for row in _rows(list_customer_payments(limit=5000)):
            if _live(row) and int(row.get("customer_id") or 0) == customer_id:
                receipts += _f(row, "amount")
        for row in _rows(list_sales_returns(limit=2000)):
            if _live(row) and int(row.get("customer_id") or 0) == customer_id:
                refunds += _f(row, "refund_amount")

        net = round(billed - paid - receipts - refunds, 2)
        cust = find_customer_by_id(customer_id) or get_doc("customers", customer_id) or {}
        if not cust:
            return
        cid = int(cust.get("id") or cust.get("local_id") or customer_id)
        upsert_contact_online(
            "customers",
            {
                "id": cid,
                "local_id": cid,
                "name": cust.get("name") or "",
                "phone": cust.get("phone") or "",
                "address": cust.get("address") or "",
                "total_due": max(0.0, net),
                "total_credit": max(0.0, -net),
            },
        )
        invalidate("customers")
    except Exception as exc:
        log.warning("customer balance after sale delete: %s", exc)


def _flush_return_delete(collection: str, payload: dict[str, Any], lid: int) -> None:
    """Undo a return's stock movement on the server, then delete the return.

    Deleting a return online used to enqueue nothing but the delete, so the
    stock the return had moved stayed moved: a cancelled sales return kept the
    goods on the shelf forever, and a cancelled purchase return kept them off
    it. Offline has always reversed this; the server side simply had no path.
    """
    from core.online_catalog import medicine_by_id, patch_docs
    from core.server_crud import (
        delete_entity, get_doc, upsert_docs, bump_meta, _device_id,
    )

    rid = int(payload.get("id") or lid)
    if rid <= 0:
        return
    doc = get_doc(collection, rid) or {}
    cu = str(doc.get("client_uuid") or payload.get("client_uuid") or rid)
    is_sale_return = collection == "sales_returns"
    # One movement per MEDICINE: the key below names the medicine, not the line, and the store
    # applies a key once -- a medicine on two lines of the return lost the second line's stock.
    moves: dict[int, float] = {}
    copies: dict[int, dict] = {}
    for it in doc.get("items") or []:
        if not isinstance(it, dict):
            continue
        mid = int(it.get("medicine_id") or 0)
        qty = abs(float(it.get("qty") or 0))
        if mid <= 0 or qty == 0:
            continue
        # Server copy first, version bumped -- a doc the server skips on version
        # is answered with HTTP 200 and written nowhere, which is how stock
        # changes used to reappear after a restart.
        if mid not in copies:
            copies[mid] = get_doc("medicines", mid) or medicine_by_id(mid) or {
                "id": mid, "local_id": mid, "name": it.get("name") or "", "stock_qty": 0,
            }
        mp = copies[mid]
        if not is_sale_return:
            # Strips on the line, tablets on the shelf -- give back what the
            # return actually removed. The medicine MUST be passed: a return
            # document on the server carries only medicine_id, name, qty, rate
            # and amount, so without it the type and pack size are unknown, the
            # line reads as loose units, and a cancelled 5-strip return of a 10s
            # pack gave back 5 tablets against the 50 the save took away.
            from core.desktop_returns_service import purchase_return_stock_units

            qty = abs(float(purchase_return_stock_units(it, medicine=mp) or qty))
        # A sales return had ADDED stock, so undoing it takes stock away; a
        # purchase return had removed it, so undoing it puts it back. Negatives
        # are kept: clamping here would disagree with the qty_delta logged below
        # and quietly lose the difference.
        moves[mid] = moves.get(mid, 0.0) + (-qty if is_sale_return else qty)
    med_docs = []
    for mid, delta in moves.items():
        mp = bump_meta(dict(copies[mid]))
        mp["id"] = mid
        mp["local_id"] = mid
        try:
            mp["stock_qty"] = float(mp.get("stock_qty") or 0) + delta
        except Exception:
            pass
        mp["stock_ops"] = [{
            "op_uuid": f"{collection}:{cu}:med:{mid}:delete:v1",
            "op": "sale_return_delete" if is_sale_return else "purchase_return_delete",
            "qty_delta": int(round(delta)),
            "medicine_id": mid,
            "ref_collection": collection,
            "ref_id": rid,
            "device_id": _device_id(),
        }]
        med_docs.append(mp)
    # Delete first, reverse stock after -- see the note on the sale delete.
    delete_entity(collection, rid)
    if med_docs:
        upsert_docs("medicines", med_docs)
        try:
            patch_docs("medicines", med_docs)
        except Exception:
            pass


def _purchase_line_units_from_its_own_fields(it: dict[str, Any]) -> float:
    """Stock units a purchase line added, read from the line alone (no catalogue).

    A tablet, capsule or bolus line is bought in strips and stocked in tablets: strips (with
    free strips) times tablets per strip, from the line's tablets_per_stripe or its pack text.
    Any other line is stocked as bought.
    """
    try:
        packs = float(it.get("qty") or 0) + float(it.get("free_qty") or 0)
    except (TypeError, ValueError):
        return 0.0
    try:
        from core.layout_config import is_strip_count_type, parse_tablets_per_stripe
    except Exception:
        return packs
    if not is_strip_count_type(str(it.get("type") or "")):
        return packs
    try:
        tps = int(float(it.get("tablets_per_stripe") or 0))
    except (TypeError, ValueError):
        tps = 0
    if tps <= 0:
        try:
            tps = int(parse_tablets_per_stripe(str(it.get("unit") or it.get("pack") or "")))
        except Exception:
            tps = 0
    return packs * max(1, tps)


def _flush_purchase_delete(payload: dict[str, Any], lid: int, row_id: str = "") -> None:
    _flush_bill_delete(payload, lid, row_id, sale=False)


def _recompute_supplier_balance_online(supplier_id: int) -> None:
    """Rebuild one supplier's balance from the server's own live documents.

    Same arithmetic as core.purchase_service.recalculate_supplier_due:
        net = purchases - paid at entry - payments - return refunds
    """
    if supplier_id <= 0:
        return
    try:
        from core.online_catalog import find_supplier_by_id, invalidate
        from core.server_crud import get_doc, upsert_contact_online
        from core.store_query_client import (
            list_purchase_returns,
            list_purchases,
            list_supplier_payments,
        )
    except Exception:
        return

    def _rows(res):
        if isinstance(res, dict):
            for key in ("rows", "items", "data", "results"):
                val = res.get(key)
                if isinstance(val, list):
                    return val
            return []
        return res if isinstance(res, list) else []

    def _live(row):
        return isinstance(row, dict) and not row.get("deleted") and not row.get("is_autosave")

    def _f(row, *names):
        for n in names:
            v = (row or {}).get(n)
            if v not in (None, ""):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return 0.0

    try:
        purchased = paid_at_entry = payments = refunds = 0.0
        for row in _rows(list_purchases(limit=5000)):
            if not _live(row) or int(row.get("supplier_id") or 0) != supplier_id:
                continue
            purchased += _f(row, "final_amount", "total_amount")
            paid_at_entry += _f(row, "amount_paid_at_entry", "amount_paid")
        for row in _rows(list_supplier_payments(limit=5000)):
            if _live(row) and int(row.get("supplier_id") or 0) == supplier_id:
                payments += _f(row, "amount")
        for row in _rows(list_purchase_returns(limit=2000)):
            if _live(row) and int(row.get("supplier_id") or 0) == supplier_id:
                refunds += _f(row, "refund_amount")

        net = round(purchased - paid_at_entry - payments - refunds, 2)
        sup = find_supplier_by_id(supplier_id) or get_doc("suppliers", supplier_id) or {}
        if not sup:
            return
        sid = int(sup.get("id") or sup.get("local_id") or supplier_id)
        upsert_contact_online(
            "suppliers",
            {
                "id": sid,
                "local_id": sid,
                "name": sup.get("name") or "",
                "phone": sup.get("phone") or "",
                "address": sup.get("address") or "",
                "gstin": sup.get("gstin") or "",
                "total_due": max(0.0, net),
                "total_credit": max(0.0, -net),
            },
        )
        invalidate("suppliers")
    except Exception as exc:
        log.warning("supplier balance after purchase delete: %s", exc)
    try:
        from core.purchase_service import cleanup_orphan_import_medicines_online

        cleanup_orphan_import_medicines_online(max_check=40, force=True)
    except Exception:
        pass


def _flush_sale_bill_row(payload: dict[str, Any], local_id: int = 0) -> None:
    """Send only the bill row of a sale whose balance and stock are already on the server."""
    from core.server_crud import push_sale_row_alone

    sale = push_sale_row_alone(
        dict(payload.get("_bill_row") or {}),
        renumber=bool(payload.get("_renumber")),
        tried_serial=int(payload.get("_tried_serial") or 0),
    )
    try:
        from core.online_catalog import patch_docs

        patch_docs("sales", [sale])
    except Exception:
        pass
    # The row has landed, so the form that owned it now owns the real bill. Its record still
    # named the queue's temp id, which nothing maps to the server's id: the form could not
    # carry on, and the record was offered on every start with a message about the connection.
    try:
        from core.autosave_session import repoint_sale
        from core.fy_serial import display_sales_bill_no

        repoint_sale(
            local_id,
            int(sale.get("id") or sale.get("local_id") or 0),
            display_sales_bill_no(str(sale.get("bill_no") or "")),
        )
    except Exception as exc:
        log.warning("unfinished-sale record after its bill row landed: %s", exc)


def _flush_sale_create(payload: dict[str, Any]) -> None:
    from core.quick_sale_medicine import resolve_quick_sale_medicines
    from core.server_crud import save_new_sale_online

    medicines = list(payload.get("medicines") or [])
    resolve_quick_sale_medicines(None, medicines)
    save_new_sale_online(
        customer_id=int(payload.get("customer_id") or 0),
        medicines=medicines,
        discount_pct=payload.get("discount_pct"),
        rounding=payload.get("rounding"),
        cash_paid=payload.get("cash_paid"),
        online_paid=payload.get("online_paid"),
        doctor_name=str(payload.get("doctor_name") or ""),
        doctor_phone=str(payload.get("doctor_phone") or ""),
        previous_due=payload.get("previous_due"),
        discount_rs=payload.get("discount_rs"),
        bill_date=payload.get("bill_date"),
        client_uuid=str(payload.get("client_uuid") or ""),
        created_at=str(payload.get("created_at") or ""),
        known_sale_id=int(payload.get("_sale_id") or 0),
    )


def _remember_sale_id(row_id: str, exc: Exception) -> None:
    """Keep the id a failed send gave a queued new sale, so its replay can ask for it first."""
    try:
        from core.server_crud import SaleNotConfirmed
    except Exception:
        return
    if not isinstance(exc, SaleNotConfirmed):
        return
    try:
        sid = int(exc.sale.get("id") or 0)
    except (TypeError, ValueError, AttributeError):
        return
    if sid <= 0:
        return
    with _lock:
        rows = _load()
        for r in rows:
            if str(r.get("id")) == str(row_id) and isinstance(r.get("payload"), dict):
                if not int(r["payload"].get("_sale_id") or 0):
                    r["payload"]["_sale_id"] = sid
                    _save(rows)
                return


def _flush_sale_update(payload: dict[str, Any], sale_id: int) -> None:
    """Replay update_existing_bill online branch via a tiny shim in billing_service."""
    from core.billing_service import update_existing_bill_online_now

    update_existing_bill_online_now(
        sale_id,
        payload.get("medicines") or [],
        payload.get("discount_pct"),
        payload.get("rounding"),
        payload.get("cash_paid"),
        payload.get("online_paid"),
        payload.get("customer_name") or "",
        payload.get("customer_phone") or "",
        payload.get("doctor_name") or "",
        payload.get("previous_due"),
        discount_rs=payload.get("discount_rs"),
        bill_date=payload.get("bill_date"),
    )


def _flush_purchase_payload(payload: dict[str, Any]) -> None:
    from core.purchase_service import save_purchase_online_now, update_purchase_online_now

    lid = int(payload.get("id") or payload.get("local_id") or payload.get("purchase_id") or 0)
    if lid > 0 and payload.get("calc_result") is not None:
        update_purchase_online_now(
            lid,
            int(payload.get("supplier_id") or 0),
            str(payload.get("bill_number") or ""),
            str(payload.get("purchase_date") or ""),
            payload.get("calc_result") or {},
            list(payload.get("items") or []),
            client_uuid=str(payload.get("client_uuid") or ""),
            purchase_no=str(payload.get("purchase_no") or ""),
            fy_start_year=payload.get("fy_start_year"),
            fy_serial=payload.get("fy_serial"),
        )
        return
    if payload.get("calc_result") is not None:
        save_purchase_online_now(
            int(payload.get("supplier_id") or 0),
            str(payload.get("purchase_date") or ""),
            str(payload.get("bill_number") or ""),
            payload.get("calc_result") or {},
            list(payload.get("items") or []),
            client_uuid=str(payload.get("client_uuid") or ""),
        )
        return
    from core.server_crud import save_new_purchase_online

    save_new_purchase_online(payload)


def _flush_return_payload(collection: str, payload: dict[str, Any]) -> None:
    from core.online_catalog import medicine_by_id, patch_docs
    from core.server_crud import (
        upsert_return_online,
        upsert_contact_online,
        bump_meta,
        get_doc,
        _device_id,
    )

    cu = str(payload.get("client_uuid") or "")
    prebuilt = [
        m for m in (payload.get("_medicines") or []) if isinstance(m, dict)
    ]
    if prebuilt:
        med_docs = prebuilt
    else:
        med_docs = []
        for it in payload.get("items") or []:
            if not isinstance(it, dict):
                continue
            mid = int(it.get("medicine_id") or 0)
            qty = float(it.get("qty") or 0)
            if mid <= 0 or qty == 0:
                continue
            # The SERVER's copy, not the local cache: a document built from a
            # stale cache carries an old version, and the server answers HTTP 200
            # while writing nothing -- the return's stock movement was silently
            # dropped and the shelf figure came back on the next restart.
            mp = get_doc("medicines", mid) or medicine_by_id(mid) or {
                "id": mid,
                "local_id": mid,
                "stock_qty": 0,
                "name": it.get("name") or "",
            }
            if collection == "purchase_returns":
                # Purchase returns are entered in strips; stock is tablets.
                from core.desktop_returns_service import purchase_return_stock_units

                qty = float(purchase_return_stock_units(it, medicine=mp) or qty)
            mp = bump_meta(dict(mp))
            mp["id"] = mid
            mp["local_id"] = mid
            if collection == "sales_returns":
                mp["stock_qty"] = float(mp.get("stock_qty") or 0) + qty
                delta = int(qty)
                op_name = "sale_return"
            else:
                # Unclamped on purpose: the qty_delta logged just below is
                # always the full -qty, so a clamp here would leave the row
                # and the stock ledger telling different stories.
                mp["stock_qty"] = float(mp.get("stock_qty") or 0) - qty
                delta = -int(qty)
                op_name = "purchase_return"
            mp["stock_ops"] = [
                {
                    "op_uuid": f"{collection}:{cu}:med:{mid}:v1",
                    "op": op_name,
                    "qty_delta": delta,
                    "medicine_id": mid,
                    "ref_collection": collection,
                    "device_id": _device_id(),
                }
            ]
            med_docs.append(mp)
    payload = dict(payload)
    payload["_medicines"] = med_docs
    upsert_return_online(collection, payload)

    # The party balance is the SERVER cascade's to own after a return, and it
    # has already run by the time we get here -- so our copy of the customer is
    # older by construction and the server rightly skips it. Treating that skip
    # as a failure meant the return was saved on the server but never marked
    # done here, so it was pushed again, and again: one live return reached 544
    # attempts, and every retry wrote another changelog row for the same doc.
    # A skipped party write is a no-op, not an error.
    from core.server_crud import StaleWriteRejected

    for kind, key in (("customers", "_customers"), ("suppliers", "_suppliers")):
        for party in payload.get(key) or []:
            try:
                upsert_contact_online(kind, party)
            except StaleWriteRejected:
                log.info(
                    "%s %s already current on server (cascade) — not retrying",
                    kind, party.get("id") or party.get("local_id"),
                )
    try:
        if med_docs:
            patch_docs("medicines", med_docs)
    except Exception:
        pass
