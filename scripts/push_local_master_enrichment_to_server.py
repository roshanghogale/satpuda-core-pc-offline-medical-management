"""Push Matoshree/Shivkrupa-enriched master rows (pending/stock) to global server API."""
from __future__ import annotations

import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.master_medicine_cloud import upsert_master_remote  # noqa: E402
from core.master_medicine_service import MASTER_TABLE, get_master_db_path, _init_schema  # noqa: E402


def main() -> None:
    path = get_master_db_path()
    conn = sqlite3.connect(path)
    _init_schema(conn)
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT name, manufacturer, mrp, content_drug, med_type, pack_size,
               schedule, hsn_code, gst_percent, updated_at, version, device_id
        FROM {MASTER_TABLE}
        WHERE COALESCE(sync_status,'')='pending'
           OR COALESCE(device_id,'')='stock-enrich'
        """
    )
    rows = cur.fetchall()
    conn.close()
    print("pending/stock-enrich rows", len(rows))
    if not rows:
        return

    batch = []
    upserted = skipped = 0
    for r in rows:
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
                "device_id": r[11] or "stock-enrich",
                "deleted": False,
            }
        )
        if len(batch) >= 200:
            res = upsert_master_remote(batch, enrich=True)
            upserted += int(res.get("upserted") or 0)
            skipped += int(res.get("skipped") or 0)
            print("chunk", upserted, skipped)
            batch = []
    if batch:
        res = upsert_master_remote(batch, enrich=True)
        upserted += int(res.get("upserted") or 0)
        skipped += int(res.get("skipped") or 0)
    print("done upserted", upserted, "skipped", skipped)


if __name__ == "__main__":
    main()
