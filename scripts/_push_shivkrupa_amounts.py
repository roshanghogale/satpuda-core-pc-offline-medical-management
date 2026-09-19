"""Push Shivkrupa amount-aligned collections to server."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import server_api as api  # noqa: E402
from core.server_sync import _PUSH_ORDER, _build_docs  # noqa: E402

DB = os.path.join(
    ROOT, "config", "stores", "Store_Shivkrupa_Medical_General_Store", "veterinary.db"
)
SESSION = os.path.join(
    ROOT, "config", "server_session_Store_Shivkrupa_Medical_General_Store.json"
)
COLS = [
    "customers",
    "suppliers",
    "sales",
    "purchases",
    "sales_returns",
    "purchase_returns",
]


def main() -> None:
    sess = json.load(open(SESSION, encoding="utf-8"))
    conn = sqlite3.connect(DB)
    now = datetime.now(timezone.utc).isoformat()
    for table in COLS:
        try:
            conn.execute(
                f"UPDATE {table} SET version=COALESCE(version,0)+1, updated_at=?",
                (now,),
            )
            print("bumped", table)
        except Exception as exc:
            print("skip bump", table, exc)
    conn.commit()
    total = 0
    for col in COLS:
        docs = _build_docs(conn, col)
        if not docs:
            print(col, "empty")
            continue
        for i in range(0, len(docs), 80):
            chunk = docs[i : i + 80]
            result = api.push_collection(sess["token"], col, chunk)
            total += int(result.get("upserted") or 0)
            print(f"{col} {min(i+len(chunk), len(docs))}/{len(docs)}")
    print("upserted", total)
    conn.close()


if __name__ == "__main__":
    main()
