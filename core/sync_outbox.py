"""B1.5 / B2 parity — Mac2 sync outbox for failed Online pushes."""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

log = logging.getLogger(__name__)


def ensure_sync_outbox(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection TEXT NOT NULL,
            local_id INTEGER NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            retries INTEGER NOT NULL DEFAULT 0,
            next_attempt_at REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            last_error TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sync_outbox_status ON sync_outbox(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sync_outbox_col "
        "ON sync_outbox(collection, local_id)"
    )


def enqueue(conn, collection: str, local_id: int, *, error: str = "") -> None:
    ensure_sync_outbox(conn)
    col = str(collection or "").strip()
    lid = int(local_id)
    if not col or lid <= 0:
        return
    row = conn.execute(
        "SELECT id FROM sync_outbox WHERE collection=? AND local_id=? "
        "AND status='pending' LIMIT 1",
        (col, lid),
    ).fetchone()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if row:
        conn.execute(
            "UPDATE sync_outbox SET last_error=?, next_attempt_at=0 WHERE id=?",
            ((error or "")[:200], int(row[0])),
        )
    else:
        conn.execute(
            """
            INSERT INTO sync_outbox
              (collection, local_id, payload, status, retries, next_attempt_at,
               created_at, last_error)
            VALUES (?, ?, '{}', 'pending', 0, 0, ?, ?)
            """,
            (col, lid, now, (error or "")[:200]),
        )
    # Do not commit here — caller owns the transaction (commit_after_cloud_push rollback).


def flush_pending(conn, *, limit: int = 20) -> int:
    """Retry pending outbox rows. Returns number flushed OK."""
    from core import server_live as live
    from core.sync_prefs import is_online_mode

    if not is_online_mode():
        return 0
    ensure_sync_outbox(conn)
    now = time.time()
    rows = conn.execute(
        """
        SELECT id, collection, local_id, retries FROM sync_outbox
        WHERE status='pending' AND next_attempt_at <= ?
        ORDER BY id ASC LIMIT ?
        """,
        (now, int(limit)),
    ).fetchall()
    ok_n = 0
    for oid, collection, local_id, retries in rows:
        try:
            pushed = _flush_one(conn, live, str(collection), int(local_id))
            if pushed:
                conn.execute("DELETE FROM sync_outbox WHERE id=?", (int(oid),))
                ok_n += 1
            else:
                _backoff(conn, int(oid), int(retries or 0), "push returned false")
        except Exception as exc:
            _backoff(conn, int(oid), int(retries or 0), str(exc))
    try:
        conn.commit()
    except Exception:
        pass
    return ok_n


def _flush_one(conn, live, collection: str, local_id: int) -> bool:
    if collection == "sales":
        return bool(live.push_sale_bundle(conn, local_id))
    if collection == "purchases":
        return bool(live.push_purchase_bundle(conn, local_id))
    try:
        from core import server_entity_sync as fb

        builders = {
            "medicines": fb.build_medicine_payload,
            "customers": fb.build_customer_payload,
            "suppliers": fb.build_supplier_payload,
            "doctors": getattr(fb, "build_doctor_payload", None),
            "racks": getattr(fb, "build_rack_payload", None),
            "sections": getattr(fb, "build_section_payload", None),
            "boxes": getattr(fb, "build_box_payload", None),
            "customer_payments": fb.build_customer_payment_payload,
            "supplier_payments": fb.build_supplier_payment_payload,
            "sales_returns": fb.build_sales_return_payload,
            "purchase_returns": fb.build_purchase_return_payload,
        }
        builder = builders.get(collection)
        if not builder:
            return False
        payload = builder(conn, local_id)
        if not payload:
            return False
        payload = dict(payload)
        payload["id"] = local_id
        return bool(live.push_docs(collection, [payload]))
    except Exception:
        return False


def _backoff(conn, oid: int, retries: int, err: str) -> None:
    nxt = time.time() + min(300.0, (2 ** min(retries, 6)) * 2.0)
    conn.execute(
        """
        UPDATE sync_outbox
        SET retries=?, next_attempt_at=?, last_error=?
        WHERE id=?
        """,
        (retries + 1, nxt, (err or "")[:200], oid),
    )
