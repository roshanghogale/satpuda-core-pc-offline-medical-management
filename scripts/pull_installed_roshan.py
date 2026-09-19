"""Pull latest Online server data into installed VeterinaryApp Store_Roshan DB."""
from __future__ import annotations

import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

VA = os.path.join(os.environ.get("LOCALAPPDATA", ""), "VeterinaryApp")


def main() -> int:
    import core.license_manager as lm

    lm._appdata_dir = lambda: VA  # type: ignore[method-assign]

    from core.store_manager import set_active_store
    from core import server_live as live
    from core import sync_prefs

    print("mode", sync_prefs.get_sync_mode())
    set_active_store("Store_Roshan")
    db = os.path.join(VA, "stores", "Store_Roshan", "veterinary.db")
    print("db", db)
    conn = sqlite3.connect(db, timeout=60.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    n = live.sync_down_all(conn, incremental=True)
    print("changes", n)
    rows = conn.execute(
        "SELECT id, bill_no, total_amount, updated_at FROM sales "
        "WHERE bill_no LIKE '%SCB5%' OR bill_no LIKE '%SCB6%' "
        "ORDER BY id DESC LIMIT 12"
    ).fetchall()
    print("sales", [tuple(r) for r in rows])
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
