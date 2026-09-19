"""Bump versions after repair and push Shivkrupa store to server."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.server_sync import push_store_conn  # noqa: E402

DB = os.path.join(
    ROOT, "config", "stores", "Store_Shivkrupa_Medical_General_Store", "veterinary.db"
)
SESSION = os.path.join(
    ROOT, "config", "server_session_Store_Shivkrupa_Medical_General_Store.json"
)


def main() -> None:
    sess = json.load(open(SESSION, encoding="utf-8"))
    conn = sqlite3.connect(DB)
    now = datetime.now(timezone.utc).isoformat()
    for table in (
        "medicines",
        "sales",
        "purchases",
        "customers",
        "suppliers",
        "sales_returns",
        "purchase_returns",
    ):
        try:
            conn.execute(
                f"UPDATE {table} SET version=COALESCE(version,0)+1, updated_at=?",
                (now,),
            )
            print(f"bumped {table}")
        except Exception as exc:
            print(f"bump skip {table}: {exc}")
    conn.commit()
    n = push_store_conn(conn, sess["token"], progress_cb=print, label="Shivkrupa")
    print("upserted", n)
    conn.close()


if __name__ == "__main__":
    main()
