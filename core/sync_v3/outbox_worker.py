"""Background outbox worker — push pending sync_outbox_v2 rows."""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Optional

log = logging.getLogger(__name__)

_worker_started = False
_kick = threading.Event()
_stop = threading.Event()
_conn_factory = None


def enqueue(
    conn,
    *,
    collection: str,
    local_id: int,
    operation: str = "update",
    payload: Optional[dict[str, Any]] = None,
    needs_fy: bool = False,
    fy_kind: Optional[str] = None,
    fy_date: Optional[str] = None,
    idempotency_key: str = "",
    commit: bool = True,
) -> None:
    from core.sync_v3.schema import ensure_sync_v3_schema

    ensure_sync_v3_schema(conn)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    key = idempotency_key or f"{collection}:{operation}:{local_id}"
    existing = conn.execute(
        "SELECT id FROM sync_outbox_v2 WHERE collection=? AND local_id=? "
        "AND status IN ('pending','failed','in_flight') LIMIT 1",
        (collection, int(local_id)),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE sync_outbox_v2
            SET operation=?, payload_json=?, needs_fy=?, fy_kind=?, fy_date=?,
                status='pending', next_attempt_at=0, updated_at=?,
                idempotency_key=COALESCE(idempotency_key, ?)
            WHERE id=?
            """,
            (
                operation,
                json.dumps(payload or {}, default=str),
                1 if needs_fy else 0,
                fy_kind,
                fy_date,
                now,
                key,
                int(existing[0]),
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO sync_outbox_v2
              (collection, local_id, operation, idempotency_key, payload_json,
               needs_fy, fy_kind, fy_date, status, retries, next_attempt_at,
               last_error, created_at, updated_at)
            VALUES (
              ?, ?, ?, ?, ?,
              ?, ?, ?, 'pending', 0, 0,
              '', ?, ?
            )
            """,
            (
                collection,
                int(local_id),
                operation,
                key,
                json.dumps(payload or {}, default=str),
                1 if needs_fy else 0,
                fy_kind,
                fy_date,
                now,
                now,
            ),
        )
    if commit:
        conn.commit()


def kick_worker() -> None:
    _kick.set()


def start_worker(conn_or_factory=None) -> None:
    global _worker_started, _conn_factory
    if conn_or_factory is not None:
        if callable(conn_or_factory):
            _conn_factory = conn_or_factory
        else:
            # Capture a path-based factory from an open connection when possible
            try:
                from core.background_workers import db_path_from_conn
                import sqlite3

                path = db_path_from_conn(conn_or_factory)
                if path:
                    _conn_factory = lambda p=path: sqlite3.connect(
                        p, check_same_thread=False
                    )
            except Exception:
                pass
    if _worker_started:
        kick_worker()
        return
    _worker_started = True
    _stop.clear()
    threading.Thread(target=_loop, daemon=True, name="sync-v3-outbox").start()


def stop_worker() -> None:
    global _worker_started
    _stop.set()
    _kick.set()
    _worker_started = False


def _loop() -> None:
    while not _stop.is_set():
        try:
            flush_once()
        except Exception as exc:
            log.debug("outbox worker: %s", exc)
        _kick.wait(timeout=5.0)
        _kick.clear()


def flush_once(limit: int = 20) -> int:
    from core.sync_prefs import is_online_mode

    if not is_online_mode():
        return 0
    conn = _open_conn()
    if conn is None:
        return 0
    own = True
    try:
        from core.sync_v3.schema import ensure_sync_v3_schema, set_sync_state, SYNC_STATE_SYNCED, SYNC_STATE_FAILED

        ensure_sync_v3_schema(conn)
        now = time.time()
        rows = conn.execute(
            """
            SELECT id, collection, local_id, operation, payload_json,
                   needs_fy, fy_kind, fy_date, retries
            FROM sync_outbox_v2
            WHERE status IN ('pending','failed') AND next_attempt_at <= ?
            ORDER BY id ASC LIMIT ?
            """,
            (now, limit),
        ).fetchall()
        flushed = 0
        for row in rows:
            (
                oid,
                collection,
                local_id,
                operation,
                payload_json,
                needs_fy,
                fy_kind,
                fy_date,
                retries,
            ) = row
            conn.execute(
                "UPDATE sync_outbox_v2 SET status='in_flight', updated_at=? WHERE id=?",
                (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    oid,
                ),
            )
            conn.commit()
            try:
                if needs_fy:
                    _allocate_fy_if_needed(conn, collection, local_id, fy_kind, fy_date)
                ok = _push_one(conn, collection, int(local_id), operation)
                if ok:
                    conn.execute(
                        "UPDATE sync_outbox_v2 SET status='done', last_error='', updated_at=? WHERE id=?",
                        (
                            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            oid,
                        ),
                    )
                    set_sync_state(conn, collection, int(local_id), SYNC_STATE_SYNCED)
                    flushed += 1
                else:
                    _backoff(conn, oid, int(retries or 0), "push returned false")
                    set_sync_state(conn, collection, int(local_id), SYNC_STATE_FAILED)
            except Exception as exc:
                _backoff(conn, oid, int(retries or 0), str(exc))
                set_sync_state(conn, collection, int(local_id), SYNC_STATE_FAILED)
        conn.commit()
        return flushed
    finally:
        if own:
            try:
                conn.close()
            except Exception:
                pass


def _backoff(conn, oid: int, retries: int, error: str) -> None:
    delays = (5, 15, 60, 120, 300)
    nxt = delays[min(retries, len(delays) - 1)]
    conn.execute(
        """
        UPDATE sync_outbox_v2
        SET status='failed', retries=?, next_attempt_at=?, last_error=?, updated_at=?
        WHERE id=?
        """,
        (
            retries + 1,
            time.time() + nxt,
            (error or "")[:200],
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            oid,
        ),
    )
    conn.commit()


def _allocate_fy_if_needed(conn, collection, local_id, fy_kind, fy_date) -> None:
    """Allocate-deferred: assign server serial immediately before first push."""
    kind = (fy_kind or "").strip()
    if kind not in ("sales", "purchases"):
        return
    date_s = (fy_date or "").strip() or time.strftime("%Y-%m-%d")
    from core.sync_v3 import transport

    data = transport.allocate_fy(kind, date_s)
    if kind == "purchases":
        purchase_no = (data.get("purchase_no") or "").strip()
        if not purchase_no:
            serial = int(data.get("fy_serial") or 0)
            if serial > 0:
                from core.fy_serial import encode_purchase_no, fy_start_year_for_date
                from datetime import date as _date

                fy = int(data.get("fy_start_year") or fy_start_year_for_date(_date.today()))
                purchase_no = encode_purchase_no(serial, fy)
        if purchase_no:
            conn.execute(
                "UPDATE purchases SET purchase_no=? WHERE id=?",
                (purchase_no, int(local_id)),
            )
            try:
                from core.fy_serial import patch_purchase_fy_fields

                patch_purchase_fy_fields(
                    conn.cursor(), int(local_id), purchase_no, date_s
                )
            except Exception:
                pass
            conn.commit()
    elif kind == "sales":
        bill_no = (data.get("bill_no") or data.get("sale_no") or "").strip()
        if not bill_no:
            serial = int(data.get("fy_serial") or 0)
            if serial > 0:
                from core.fy_serial import encode_sales_bill_no, fy_start_year_for_date
                from datetime import date as _date

                fy = int(data.get("fy_start_year") or fy_start_year_for_date(_date.today()))
                try:
                    bill_no = encode_sales_bill_no(serial, fy)
                except Exception:
                    bill_no = str(serial)
        if bill_no:
            conn.execute(
                "UPDATE sales SET bill_no=? WHERE id=?",
                (bill_no, int(local_id)),
            )
            conn.commit()


def _push_one(conn, collection: str, local_id: int, operation: str) -> bool:
    col = (collection or "").strip()
    op = (operation or "").strip().lower()
    try:
        if op == "delete":
            from core import server_live as live

            # Prefer coordinator delete hooks when available
            if col == "sales":
                from core.sync_coordinator import after_sale_deleted

                # already deleted locally — push delete doc
            from core.server_api import store_token_for_active, delete_doc

            token = store_token_for_active()
            if not token:
                return False
            return bool(delete_doc(token, col, local_id))

        if col == "purchases":
            from core.sync_coordinator import push_purchase_now

            return bool(push_purchase_now(conn, local_id, commit_meta=True))
        if col == "sales":
            from core.sync_coordinator import push_sale_now

            return bool(push_sale_now(conn, local_id, commit_meta=True))

        from core import server_live as live

        return bool(live.push_entity(conn, col, local_id))
    except Exception as exc:
        log.warning("push_one %s/%s: %s", col, local_id, exc)
        try:
            from core.sync_outbox import enqueue as legacy_enqueue

            legacy_enqueue(conn, col, local_id, error=str(exc))
            conn.commit()
        except Exception:
            pass
        return False


def _open_conn():
    if _conn_factory:
        try:
            return _conn_factory()
        except Exception as exc:
            log.debug("conn factory: %s", exc)
    return None
