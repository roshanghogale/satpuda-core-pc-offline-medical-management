"""Other devices' changes -> this PC's copy.

Records are written with the same code the "Go Offline" download uses
(server_live.apply_server_doc / server_entity_sync.sync_down_doc). That code also moves stock
for a bill it writes; those movements are not trusted. Stock comes from one place only: for
every medicine a page touched, local stock = the server's figure now + this PC's own movements
the server does not have yet. Stock movements are never replayed, so nothing is counted twice.

A record this PC has changed and not yet sent is not overwritten: its own change goes up first,
and the server keeps both and flags a clash.
"""
from __future__ import annotations

import sqlite3

from core.offline_first.outbox import pending_stock_by_medicine
from core.offline_first.schema import meta_set

_SINGLETONS = ("pharmacy_profile", "dropdowns", "shelf_settings", "settings")
_LINE_DOCS = ("sales", "purchases", "sales_returns", "purchase_returns")


def touched_medicines(changes: list[dict]) -> set[int]:
    """Medicines whose stock a page of changes may have moved."""
    out: set[int] = set()
    for ch in changes:
        col = ch.get("collection")
        doc = ch.get("doc") or {}
        if col == "medicines":
            out.add(int(ch.get("local_id") or 0))
        elif col == "stock_operations":
            try:
                out.add(int(doc.get("medicine_id") or 0))
            except (TypeError, ValueError):
                pass
        elif col in _LINE_DOCS:
            for it in doc.get("items") or []:
                try:
                    out.add(int(it.get("medicine_id") or 0))
                except (TypeError, ValueError, AttributeError):
                    pass
    out.discard(0)
    return out


def _locally_pending(conn: sqlite3.Connection) -> set[tuple[str, int]]:
    rows = conn.execute("SELECT collection, local_id FROM of_dirty").fetchall()
    rows += conn.execute(
        "SELECT collection, local_id FROM of_events WHERE status='pending' AND local_id IS NOT NULL"
    ).fetchall()
    return {(str(c), int(i)) for c, i in rows}


def apply_page(conn: sqlite3.Connection, changes: list[dict], *, to_revision: int) -> dict:
    """Write one page of changes. Call inside the worker's write transaction with capture paused."""
    from core import server_entity_sync as fb
    from core import server_live as sl

    pending = _locally_pending(conn)
    applied = skipped = 0
    with fb.offline_first_apply():
        for ch in changes:
            col = str(ch.get("collection") or "")
            lid = int(ch.get("local_id") or 0)
            doc = ch.get("doc")
            if col == "stock_operations":
                continue                      # stock comes from the server's figure, below
            if col in _SINGLETONS:
                if isinstance(doc, dict):
                    sl.apply_server_doc(conn, col, doc)
                    applied += 1
                continue
            if (col, lid) in pending:
                skipped += 1                  # this PC's own change goes up first
                continue
            if ch.get("operation") == "delete" or not isinstance(doc, dict):
                try:
                    fb._soft_delete_local(conn, col, str(lid), {"deleted": True})
                except Exception:
                    pass
                applied += 1
                continue
            status = sl.apply_server_doc(conn, col, doc)
            if status in ("applied", "soft_deleted", "skipped", "kept_local"):
                applied += 1
            try:
                ver = int(doc.get("version") or ch.get("entity_version") or 0)
            except (TypeError, ValueError):
                ver = 0
            if ver > 0:
                conn.execute(
                    "INSERT INTO of_versions (collection, local_id, version) VALUES (?,?,?) "
                    "ON CONFLICT(collection, local_id) DO UPDATE SET version=MAX(version, excluded.version)",
                    (col, lid, ver),
                )
    meta_set(conn, "pull_cursor", int(to_revision))
    return {"applied": applied, "skipped": skipped}


def set_stock_from_server(conn: sqlite3.Connection, server_stock: list) -> int:
    """Local stock = server's figure + this PC's own unsent movements. Call inside the
    worker's write transaction with capture paused. server_stock: [[id, qty, hidden], ...]."""
    pending = pending_stock_by_medicine(conn)
    n = 0
    for row in server_stock or []:
        try:
            mid, qty, hidden = int(row[0]), int(row[1]), int(row[2] or 0)
        except (TypeError, ValueError, IndexError):
            continue
        local = qty + pending.get(mid, 0)
        cur = conn.execute(
            "UPDATE medicines SET stock_qty=?, is_hidden=? WHERE id=? AND "
            "(COALESCE(stock_qty,0) <> ? OR COALESCE(is_hidden,0) <> ?)",
            (local, hidden, mid, local, hidden),
        )
        n += cur.rowcount or 0
    return n
