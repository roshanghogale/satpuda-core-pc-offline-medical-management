"""Bump sync metadata on local entity writes (version / updated_at / device_id)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def bump_row_meta(
    conn: sqlite3.Connection,
    table: str,
    row_id: int,
    *,
    mark_pending: bool = True,
    commit: bool = True,
) -> None:
    """
    Increment version, stamp updated_at + device_id for a local edit.
    No-op if metadata columns are missing.
    Set commit=False when the caller holds an open transaction (Online Server-first).
    """
    cur = conn.cursor()
    try:
        cur.execute(f"PRAGMA table_info({table})")
        cols = {str(r[1]) for r in cur.fetchall()}
    except Exception:
        return
    if "version" not in cols and "updated_at" not in cols:
        return
    try:
        from core.sync_metadata_schema import get_sync_device_id
        device_id = get_sync_device_id() or ""
    except Exception:
        device_id = ""
    sets = []
    vals = []
    if "updated_at" in cols:
        sets.append("updated_at=?")
        vals.append(_now_iso())
    if "version" in cols:
        sets.append("version=COALESCE(version,1)+1")
    if "device_id" in cols and device_id:
        sets.append("device_id=?")
        vals.append(device_id)
    if mark_pending and "sync_status" in cols:
        sets.append("sync_status=?")
        vals.append("pending")
    if not sets:
        return
    vals.append(int(row_id))
    cur.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id=?", vals)
    if commit:
        try:
            conn.commit()
        except Exception:
            pass
