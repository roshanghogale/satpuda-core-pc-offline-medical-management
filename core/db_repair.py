"""One-time repairs for corrupted store databases."""
from __future__ import annotations

import glob
import os
import sqlite3


def _table_cols(cur: sqlite3.Cursor, table: str) -> list[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return [str(r[1]) for r in cur.fetchall()]


def _backup_paths(db_path: str) -> list[str]:
    store_dir = os.path.dirname(os.path.abspath(db_path))
    base = os.path.join(store_dir, "veterinary.db.pre_")
    found: list[str] = []
    for path in glob.glob(base + "*"):
        if os.path.isfile(path) and not path.endswith(("-shm", "-wal")):
            found.append(path)
    found.sort(key=os.path.getmtime, reverse=True)
    return found


def repair_missing_purchases(conn: sqlite3.Connection, *, db_path: str = "") -> int:
    """
    Restore purchases rows when the header table was lost but line items remain.
    Returns number of purchase rows restored.
    """
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM purchases")
        live_count = int(cur.fetchone()[0] or 0)
    except Exception:
        return 0
    if live_count > 0:
        return 0

    try:
        cur.execute("SELECT COUNT(*) FROM purchase_items")
        item_count = int(cur.fetchone()[0] or 0)
    except Exception:
        item_count = 0
    if item_count <= 0:
        return 0

    if not db_path:
        try:
            from core.store_manager import get_active_db_path

            db_path = get_active_db_path()
        except Exception:
            rows = cur.execute("PRAGMA database_list").fetchall()
            db_path = str(rows[0][2]) if rows else ""
    if not db_path or not os.path.isfile(db_path):
        return 0

    live_cols = _table_cols(cur, "purchases")
    if not live_cols:
        return 0

    for backup in _backup_paths(db_path):
        try:
            bconn = sqlite3.connect(backup)
            bcur = bconn.cursor()
            bcur.execute("SELECT COUNT(*) FROM purchases")
            bak_count = int(bcur.fetchone()[0] or 0)
            if bak_count <= 0:
                bconn.close()
                continue
            bak_cols = _table_cols(bcur, "purchases")
            common = [c for c in live_cols if c in bak_cols]
            if "id" not in common:
                bconn.close()
                continue
            cols_sql = ", ".join(common)
            cur.execute("ATTACH DATABASE ? AS purch_bak", (backup,))
            cur.execute(
                f"INSERT OR IGNORE INTO purchases ({cols_sql}) "
                f"SELECT {cols_sql} FROM purch_bak.purchases"
            )
            restored = cur.rowcount if cur.rowcount >= 0 else bak_count
            cur.execute("DETACH DATABASE purch_bak")
            bconn.close()
            if restored:
                print(
                    f"[REPAIR] Restored {restored} purchase rows from "
                    f"{os.path.basename(backup)}."
                )
                return int(restored)
        except Exception as exc:
            try:
                cur.execute("DETACH DATABASE purch_bak")
            except Exception:
                pass
            print(f"[REPAIR] purchase restore from {backup} failed: {exc}")
    return 0
