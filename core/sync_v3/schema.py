"""Local schema for Sync V3: outbox_v2, sync_state columns, purchase_drafts."""
from __future__ import annotations

import logging
import time
from typing import Iterable

log = logging.getLogger(__name__)

SYNC_STATE_SYNCED = "synced"
SYNC_STATE_PENDING = "pending_sync"
SYNC_STATE_FAILED = "failed"

_ENTITY_TABLES = (
    "sales",
    "purchases",
    "customer_payments",
    "supplier_payments",
    "sales_returns",
    "purchase_returns",
    "medicines",
    "customers",
    "suppliers",
    "doctors",
    "stock_disposals",
    "pending_orders",
    "general_products",
)


def ensure_sync_v3_schema(conn) -> None:
    """Idempotent: create outbox_v2, drafts, sync_state columns, migrate old outbox."""
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_outbox_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection TEXT NOT NULL,
            local_id INTEGER NOT NULL,
            operation TEXT NOT NULL DEFAULT 'create',
            idempotency_key TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL DEFAULT '{}',
            needs_fy INTEGER NOT NULL DEFAULT 0,
            fy_kind TEXT,
            fy_date TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            retries INTEGER NOT NULL DEFAULT 0,
            next_attempt_at REAL NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_sync_outbox_v2_status "
        "ON sync_outbox_v2(status, next_attempt_at)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_sync_outbox_v2_col "
        "ON sync_outbox_v2(collection, local_id)"
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS purchase_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_id INTEGER,
            purchase_date DATE,
            bill_number TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT,
            status TEXT NOT NULL DEFAULT 'open'
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection TEXT NOT NULL,
            local_id INTEGER NOT NULL,
            reason TEXT,
            local_json TEXT,
            server_json TEXT,
            created_at TEXT
        )
        """
    )

    for table in _ENTITY_TABLES:
        _ensure_column(cur, table, "sync_state", "TEXT DEFAULT 'synced'")

    _migrate_old_outbox(conn)
    try:
        conn.commit()
    except Exception:
        pass


def _ensure_column(cur, table: str, col: str, col_type: str) -> None:
    try:
        cur.execute(f"PRAGMA table_info({table})")
        cols = {str(r[1]).lower() for r in cur.fetchall()}
        if col.lower() not in cols:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
    except Exception as exc:
        log.debug("ensure column %s.%s: %s", table, col, exc)


def _migrate_old_outbox(conn) -> None:
    """Copy pending rows from legacy sync_outbox into sync_outbox_v2."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sync_outbox'"
        )
        if not cur.fetchone():
            return
        rows = cur.execute(
            "SELECT collection, local_id, last_error, created_at "
            "FROM sync_outbox WHERE status='pending'"
        ).fetchall()
    except Exception:
        return
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for collection, local_id, last_error, created_at in rows:
        key = f"legacy:{collection}:{local_id}"
        try:
            cur.execute(
                """
                INSERT OR IGNORE INTO sync_outbox_v2
                  (collection, local_id, operation, idempotency_key, payload_json,
                   status, retries, next_attempt_at, last_error, created_at, updated_at)
                VALUES (?, ?, 'update', ?, '{}', 'pending', 0, 0, ?, ?, ?)
                """,
                (
                    collection,
                    int(local_id),
                    key,
                    (last_error or "")[:200],
                    created_at or now,
                    now,
                ),
            )
        except Exception as exc:
            log.debug("migrate outbox row: %s", exc)


def set_sync_state(conn, table: str, local_id: int, state: str, *, commit: bool = True) -> None:
    table = (table or "").strip()
    if table not in _ENTITY_TABLES:
        return
    try:
        conn.execute(
            f"UPDATE {table} SET sync_state=? WHERE id=?",
            (state, int(local_id)),
        )
        if commit:
            conn.commit()
    except Exception as exc:
        log.debug("set_sync_state %s/%s: %s", table, local_id, exc)


def entity_tables() -> Iterable[str]:
    return _ENTITY_TABLES
