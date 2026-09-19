"""Verify Shivkrupa local SQLite IDs match Node /api/sync (limit=5000 pages)."""
from __future__ import annotations

import json
import sqlite3
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = "Store_Shivkrupa_Medical_General_Store"
DB = ROOT / "config" / "stores" / STORE / "veterinary.db"
SESSION = ROOT / "config" / f"server_session_{STORE}.json"
OUT = ROOT / "config" / "stores" / STORE / "sync_verify_report.json"
BASE = "https://api.satpudacore.online"
COLS = [
    "customers", "suppliers", "medicines", "doctors", "sales", "purchases",
    "customer_payments", "supplier_payments", "sales_returns", "purchase_returns",
]


def main() -> int:
    sess = json.loads(SESSION.read_text(encoding="utf-8"))
    token = sess["token"]
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=60)
    rows = []
    print(f"Store: {sess.get('store_name')} ({sess.get('store_id')})")
    print(f"{'collection':20} {'local':>7} {'server':>7} status")
    ok_all = True
    for col in COLS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({col})")}
        if "deleted" in cols:
            lids = {
                str(r[0])
                for r in conn.execute(
                    f"SELECT id FROM {col} WHERE COALESCE(deleted,0)=0"
                )
            }
        else:
            lids = {str(r[0]) for r in conn.execute(f"SELECT id FROM {col}")}
        req = urllib.request.Request(
            f"{BASE}/api/sync/{col}?include_deleted=0&limit=5000",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "SatpudaVerify/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            payload = json.loads(resp.read().decode())
        data = payload.get("data") or []
        sids = set()
        for d in data if isinstance(data, list) else []:
            if not isinstance(d, dict):
                continue
            if d.get("deleted") in (True, 1, "true", "1"):
                continue
            did = d.get("id") if d.get("id") is not None else d.get("local_id")
            if did is not None:
                sids.add(str(did))
        only_l = sorted(lids - sids, key=lambda x: int(x) if x.isdigit() else x)
        only_s = sorted(sids - lids, key=lambda x: int(x) if x.isdigit() else x)
        match = not only_l and not only_s
        ok_all = ok_all and match
        mark = "OK" if match else "DIFF"
        print(f"{col:20} {len(lids):7} {len(sids):7} {mark}")
        if not match:
            print(f"  only_local={len(only_l)} sample={only_l[:8]}")
            print(f"  only_server={len(only_s)} sample={only_s[:8]}")
        rows.append(
            {
                "collection": col,
                "local": len(lids),
                "server": len(sids),
                "only_local": len(only_l),
                "only_server": len(only_s),
                "only_local_sample": only_l[:20],
                "only_server_sample": only_s[:20],
                "match": match,
            }
        )
    conn.close()
    report = {
        "store_id": sess.get("store_id"),
        "android_key": sess.get("android_key"),
        "aligned": ok_all,
        "transport": "Node.js GET/POST /api/sync/:collection",
        "rows": rows,
    }
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Aligned:", ok_all)
    print("Report:", OUT)
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
