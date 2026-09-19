"""Seed / enrich global server medicines_master from local master_medicine.db."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import server_api as api  # noqa: E402
from core.master_medicine_service import MASTER_TABLE, get_master_db_path, _init_schema  # noqa: E402

BATCH = 300


def _admin_token() -> str:
    """The administrator credentials, typed now.

    They used to be written out as literals right here, which put the password
    to every shop on the account in a file anybody with the repo could read.
    """
    import getpass

    user = input("Satpuda administrator username: ").strip()
    pw = getpass.getpass("Satpuda administrator password: ")
    return api.admin_login(user, pw)


def _post_admin(token: str, docs: list) -> dict:
    body = json.dumps({"docs": docs, "enrich": True}).encode("utf-8")
    req = urllib.request.Request(
        f"{api.api_base()}/api/admin/master-medicines/upsert",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    only_pending = "--pending-only" in sys.argv
    path = get_master_db_path()
    conn = sqlite3.connect(path)
    _init_schema(conn)
    cur = conn.cursor()
    if only_pending:
        cur.execute(
            f"""
            SELECT name, manufacturer, mrp, content_drug, med_type, pack_size,
                   schedule, hsn_code, gst_percent, updated_at, version, device_id
            FROM {MASTER_TABLE}
            WHERE COALESCE(sync_status,'')='pending'
               OR COALESCE(device_id,'')='stock-enrich'
            """
        )
    else:
        cur.execute(
            f"""
            SELECT name, manufacturer, mrp, content_drug, med_type, pack_size,
                   schedule, hsn_code, gst_percent, updated_at, version, device_id
            FROM {MASTER_TABLE}
            WHERE COALESCE(deleted,0)=0
            ORDER BY name COLLATE NOCASE
            """
        )
    print("counting…", flush=True)
    cur.execute(
        f"SELECT COUNT(*) FROM {MASTER_TABLE} WHERE COALESCE(deleted,0)=0"
        if not only_pending
        else f"""SELECT COUNT(*) FROM {MASTER_TABLE}
                 WHERE COALESCE(sync_status,'')='pending'
                    OR COALESCE(device_id,'')='stock-enrich'"""
    )
    total = int(cur.fetchone()[0] or 0)
    print("rows", total, flush=True)
    # re-run select cursor
    if only_pending:
        cur.execute(
            f"""
            SELECT name, manufacturer, mrp, content_drug, med_type, pack_size,
                   schedule, hsn_code, gst_percent, updated_at, version, device_id
            FROM {MASTER_TABLE}
            WHERE COALESCE(sync_status,'')='pending'
               OR COALESCE(device_id,'')='stock-enrich'
            """
        )
    else:
        cur.execute(
            f"""
            SELECT name, manufacturer, mrp, content_drug, med_type, pack_size,
                   schedule, hsn_code, gst_percent, updated_at, version, device_id
            FROM {MASTER_TABLE}
            WHERE COALESCE(deleted,0)=0
            ORDER BY name COLLATE NOCASE
            """
        )
    print("admin login…", flush=True)
    token = _admin_token()
    print("token ok", flush=True)
    upserted = skipped = errors = 0
    batch = []
    t0 = time.time()
    i = 0

    def flush():
        nonlocal batch, upserted, skipped, errors
        if not batch:
            return
        for attempt in range(3):
            try:
                res = _post_admin(token, batch)
                data = res.get("data") or res
                upserted += int(data.get("upserted") or 0)
                skipped += int(data.get("skipped") or 0)
                batch = []
                return
            except Exception as exc:
                print("retry", attempt, exc, flush=True)
                time.sleep(1.5 * (attempt + 1))
        errors += len(batch)
        batch = []

    while True:
        chunk = cur.fetchmany(BATCH)
        if not chunk:
            break
        for r in chunk:
            i += 1
            batch.append(
                {
                    "name": r[0],
                    "manufacturer": r[1] or "",
                    "mrp": float(r[2] or 0),
                    "content_drug": r[3] or "",
                    "med_type": r[4] or "",
                    "pack_size": r[5] or "",
                    "schedule": r[6] or "",
                    "hsn_code": r[7] or "",
                    "gst_percent": float(r[8] or 0),
                    "updated_at": r[9],
                    "version": int(r[10] or 1),
                    "device_id": r[11] or "seed",
                    "deleted": False,
                }
            )
        flush()
        if i % 3000 == 0 or i <= BATCH:
            elapsed = time.time() - t0
            rate = i / max(elapsed, 1)
            print(
                f"{i}/{total} upserted={upserted} skipped={skipped} rate={rate:.0f}/s",
                flush=True,
            )
    conn.close()
    print(
        f"DONE total={i} upserted={upserted} skipped={skipped} "
        f"errors={errors} elapsed={time.time()-t0:.1f}s",
        flush=True,
    )


if __name__ == "__main__":
    main()
