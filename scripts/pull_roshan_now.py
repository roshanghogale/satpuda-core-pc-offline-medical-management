"""One-shot Online pull for Store_Roshan into the active Mac2 store DB."""
from __future__ import annotations

import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from core.store_manager import get_store_db_path, set_active_store  # type: ignore
from core import server_live  # type: ignore
from core import sync_prefs  # type: ignore


def main() -> int:
    set_active_store("Store_Roshan")
    mode = sync_prefs.get_sync_mode()
    print("sync_mode", mode)
    if mode != sync_prefs.MODE_ONLINE:
        print("setting online for this pull…")
        sync_prefs.set_sync_mode(sync_prefs.MODE_ONLINE)
    db_path = get_store_db_path("Store_Roshan")
    print("db", db_path)
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    print("pulling incremental…")
    try:
        n = server_live.sync_down_all(conn, incremental=True)
    except Exception as exc:
        print("incremental failed:", exc)
        print("trying full download…")
        n = server_live.sync_down_all(conn, incremental=False)
    print("changed rows", n)
    rows = conn.execute(
        "SELECT id, bill_no, total_amount, updated_at FROM sales "
        "WHERE bill_no LIKE '%SCB58%' OR bill_no LIKE '%SCB57%' ORDER BY id"
    ).fetchall()
    print("sales", [tuple(r) for r in rows])
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
