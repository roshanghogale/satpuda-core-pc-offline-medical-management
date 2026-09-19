"""Push Shivkrupa medicines (stock/schedules) to server after stock repair."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import server_api as api  # noqa: E402
from core.server_sync import _build_docs  # noqa: E402

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
    conn.execute(
        "UPDATE medicines SET version=COALESCE(version,0)+1, updated_at=?",
        (now,),
    )
    conn.commit()
    docs = _build_docs(conn, "medicines")
    total = 0
    chunk = 80
    for i in range(0, len(docs), chunk):
        part = docs[i : i + chunk]
        result = api.push_collection(sess["token"], "medicines", part)
        total += int(result.get("upserted") or 0)
        print(f"Uploaded medicines {min(i + len(part), len(docs))}/{len(docs)}")
    print("upserted", total)
    conn.close()


if __name__ == "__main__":
    main()
