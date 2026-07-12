"""
Idempotent sync-metadata columns for all synced SQLite tables.

Schema-only step: adds created_at / updated_at / version / device_id / deleted /
sync_status. Does NOT change conflict resolution, bootstrap, or listeners.

Medicine keeps existing synced_at (extended, not replaced).

NOTE: Desktop return-delete is still not wired to Firebase sync — `deleted`
prepares soft-delete; wiring delete→sync is a separate task.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Iterable

log = logging.getLogger(__name__)

# Tables that participate in sync / shelf management (per product request).
SYNC_METADATA_TABLES: tuple[str, ...] = (
    "customers",
    "suppliers",
    "doctors",
    "medicines",  # keep synced_at; add the rest
    "sales",
    "sales_items",
    "purchases",
    "purchase_items",
    "customer_payments",
    "supplier_payments",
    "sales_returns",
    "sales_return_items",
    "purchase_returns",
    "purchase_return_items",
    "racks",
    "sections",
    "boxes",
    "shelves",
    "medicine_shelf",
)

_META_COLUMNS: tuple[tuple[str, str], ...] = (
    ("created_at", "TEXT"),
    ("updated_at", "TEXT"),
    ("version", "INTEGER NOT NULL DEFAULT 1"),
    ("device_id", "TEXT"),
    ("deleted", "INTEGER NOT NULL DEFAULT 0"),
    ("sync_status", "TEXT NOT NULL DEFAULT 'synced'"),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_sync_device_id() -> str:
    """
    Existing identity only — prefer SC- connection key (store link), else
    license hardware fingerprint (PC device.key / hw cache). No new IDs.
    """
    try:
        from core.store_link import get_local_android_key
        key = (get_local_android_key() or "").strip()
        if key:
            return key
    except Exception:
        pass
    try:
        from core.license_manager import _get_hardware_hash
        hw = (_get_hardware_hash() or "").strip()
        if hw:
            return hw
    except Exception:
        pass
    return ""


def _table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    )
    return cur.fetchone() is not None


def _columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return {str(r[1]) for r in cur.fetchall()}


def _add_column_if_missing(cur: sqlite3.Cursor, table: str, column: str, decl: str) -> bool:
    if column in _columns(cur, table):
        return False
    cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    return True


def ensure_sync_metadata_schema(conn: sqlite3.Connection) -> dict[str, dict]:
    """
    Add sync metadata columns and backfill. Safe to run twice.

    Returns per-table stats: {table: {before, after, columns_added}}.
    """
    cur = conn.cursor()
    device_id = get_sync_device_id()
    now = _now_iso()
    report: dict[str, dict] = {}

    for table in SYNC_METADATA_TABLES:
        if not _table_exists(cur, table):
            report[table] = {"before": 0, "after": 0, "columns_added": [], "skipped": True}
            continue
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        before = int((cur.fetchone() or (0,))[0])
        added: list[str] = []
        for col, decl in _META_COLUMNS:
            try:
                if _add_column_if_missing(cur, table, col, decl):
                    added.append(col)
            except Exception as exc:
                log.warning("sync_metadata add %s.%s: %s", table, col, exc)

        # Backfill only NULL / empty legacy rows (idempotent).
        try:
            cur.execute(
                f"""
                UPDATE {table}
                SET created_at = COALESCE(
                        NULLIF(TRIM(CAST(created_at AS TEXT)), ''),
                        ?
                    )
                WHERE created_at IS NULL OR TRIM(CAST(created_at AS TEXT)) = ''
                """,
                (now,),
            )
            cur.execute(
                f"""
                UPDATE {table}
                SET updated_at = COALESCE(
                        NULLIF(TRIM(CAST(updated_at AS TEXT)), ''),
                        NULLIF(TRIM(CAST(created_at AS TEXT)), ''),
                        ?
                    )
                WHERE updated_at IS NULL OR TRIM(CAST(updated_at AS TEXT)) = ''
                """,
                (now,),
            )
            cur.execute(
                f"""
                UPDATE {table}
                SET version = 1
                WHERE version IS NULL OR version < 1
                """
            )
            cur.execute(
                f"""
                UPDATE {table}
                SET deleted = 0
                WHERE deleted IS NULL
                """
            )
            cur.execute(
                f"""
                UPDATE {table}
                SET sync_status = 'synced'
                WHERE sync_status IS NULL OR TRIM(CAST(sync_status AS TEXT)) = ''
                """
            )
            if device_id:
                cur.execute(
                    f"""
                    UPDATE {table}
                    SET device_id = ?
                    WHERE device_id IS NULL OR TRIM(CAST(device_id AS TEXT)) = ''
                    """,
                    (device_id,),
                )
        except Exception as exc:
            log.warning("sync_metadata backfill %s: %s", table, exc)

        cur.execute(f"SELECT COUNT(*) FROM {table}")
        after = int((cur.fetchone() or (0,))[0])
        report[table] = {
            "before": before,
            "after": after,
            "columns_added": added,
            "skipped": False,
        }

    conn.commit()
    return report


def count_rows(conn: sqlite3.Connection, tables: Iterable[str] | None = None) -> dict[str, int]:
    cur = conn.cursor()
    out: dict[str, int] = {}
    for table in tables or SYNC_METADATA_TABLES:
        if not _table_exists(cur, table):
            out[table] = -1
            continue
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        out[table] = int((cur.fetchone() or (0,))[0])
    return out