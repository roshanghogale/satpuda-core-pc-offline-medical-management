"""Enrich master_medicine.db (+ Android patch CSV) from Matoshree & Shivkrupa stock."""
from __future__ import annotations

import csv
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.master_medicine_service import (  # noqa: E402
    MASTER_TABLE,
    _init_schema,
    enrich_master_from_store_dbs,
    get_master_db_path,
)

STORE_DBS = [
    os.path.join(
        ROOT,
        "config",
        "stores",
        "Store_Matoshree_Medical_Veterinary_Pet_Shop",
        "veterinary.db",
    ),
    os.path.join(
        ROOT,
        "config",
        "stores",
        "Store_Shivkrupa_Medical_General_Store",
        "veterinary.db",
    ),
]

ANDROID_ASSETS = os.path.normpath(
    os.path.join(
        ROOT,
        "..",
        "Satpuda Core",
        "app",
        "src",
        "main",
        "assets",
    )
)


def _collect_store_rows() -> dict[str, dict]:
    """name_upper -> best non-empty fields from both stores (later store fills blanks)."""
    merged: dict[str, dict] = {}
    for path in STORE_DBS:
        if not os.path.isfile(path):
            print("SKIP missing", path)
            continue
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT m.name,
                   COALESCE(m.manufacturer, ''),
                   COALESCE(m.mrp, 0),
                   COALESCE(m.content_drug, ''),
                   COALESCE(m.type, ''),
                   COALESCE(m.unit, ''),
                   COALESCE(m.schedule, ''),
                   COALESCE(m.hsn_code, ''),
                   COALESCE(m.gst_percent, 0)
            FROM medicines m
            JOIN (
                SELECT UPPER(TRIM(name)) AS nkey, MAX(id) AS max_id
                FROM medicines
                WHERE COALESCE(TRIM(name), '') <> ''
                GROUP BY UPPER(TRIM(name))
            ) latest ON latest.max_id = m.id
            """
        )
        for row in cur.fetchall():
            name = str(row[0] or "").strip()
            if not name:
                continue
            key = name.upper()
            prev = merged.get(key, {})
            def pick(new, old):
                n = str(new or "").strip()
                return n if n else (old or "")

            merged[key] = {
                "name": prev.get("name") or name.upper(),
                "manufacturer": pick(row[1], prev.get("manufacturer")),
                "mrp": float(row[2] or 0) if float(row[2] or 0) > 0 else float(prev.get("mrp") or 0),
                "content_drug": pick(row[3], prev.get("content_drug")),
                "med_type": pick(row[4], prev.get("med_type")),
                "pack_size": pick(row[5], prev.get("pack_size")),
                "schedule": pick(row[6], prev.get("schedule")),
                "hsn_code": pick(row[7], prev.get("hsn_code")),
                "gst_percent": float(row[8] or 0)
                if float(row[8] or 0) > 0
                else float(prev.get("gst_percent") or 0),
            }
        conn.close()
        print("Loaded", path, "unique so far", len(merged))
    return merged


def _write_android_patch(rows: dict[str, dict]) -> str:
    os.makedirs(ANDROID_ASSETS, exist_ok=True)
    out = os.path.join(ANDROID_ASSETS, "medicines_master_store_enrichment.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "name",
                "manufacturer",
                "mrp",
                "content_drug",
                "med_type",
                "pack_size",
                "schedule",
                "hsn_code",
                "gst_percent",
            ]
        )
        for key in sorted(rows.keys()):
            r = rows[key]
            w.writerow(
                [
                    r["name"],
                    r["manufacturer"],
                    r["mrp"] or "",
                    r["content_drug"],
                    r["med_type"],
                    r["pack_size"],
                    r["schedule"],
                    r["hsn_code"],
                    r["gst_percent"] or "",
                ]
            )
    return out


def _enrich_db(path: str, rows: dict[str, dict]) -> int:
    if not path:
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        _init_schema(conn)
        payload = [
            (
                r["name"],
                r["manufacturer"],
                float(r["mrp"] or 0),
                r["content_drug"],
                r["med_type"],
                r["pack_size"],
                r["schedule"],
                r["hsn_code"],
                float(r["gst_percent"] or 0),
            )
            for r in rows.values()
        ]
        conn.executemany(
            f"""
            INSERT INTO {MASTER_TABLE}
            (name, manufacturer, mrp, content_drug, med_type, pack_size,
             schedule, hsn_code, gst_percent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                manufacturer=CASE
                    WHEN TRIM(COALESCE(excluded.manufacturer,''))!='' THEN excluded.manufacturer
                    ELSE {MASTER_TABLE}.manufacturer END,
                mrp=CASE
                    WHEN COALESCE(excluded.mrp,0)>0 THEN excluded.mrp
                    ELSE {MASTER_TABLE}.mrp END,
                content_drug=CASE
                    WHEN TRIM(COALESCE(excluded.content_drug,''))!='' THEN excluded.content_drug
                    ELSE {MASTER_TABLE}.content_drug END,
                med_type=CASE
                    WHEN TRIM(COALESCE(excluded.med_type,''))!='' THEN excluded.med_type
                    ELSE {MASTER_TABLE}.med_type END,
                pack_size=CASE
                    WHEN TRIM(COALESCE(excluded.pack_size,''))!='' THEN excluded.pack_size
                    ELSE {MASTER_TABLE}.pack_size END,
                schedule=CASE
                    WHEN TRIM(COALESCE(excluded.schedule,''))!='' THEN excluded.schedule
                    ELSE {MASTER_TABLE}.schedule END,
                hsn_code=CASE
                    WHEN TRIM(COALESCE(excluded.hsn_code,''))!='' THEN excluded.hsn_code
                    ELSE {MASTER_TABLE}.hsn_code END,
                gst_percent=CASE
                    WHEN COALESCE(excluded.gst_percent,0)>0 THEN excluded.gst_percent
                    ELSE {MASTER_TABLE}.gst_percent END
            """,
            payload,
        )
        conn.commit()
        cur = conn.cursor()
        cur.execute(
            f"SELECT COUNT(*) FROM {MASTER_TABLE} "
            f"WHERE TRIM(COALESCE(schedule,''))!=''"
        )
        with_sched = cur.fetchone()[0]
        print(f"  {path}: upserted {len(payload)}, schedule filled now {with_sched}")
        return len(payload)
    finally:
        conn.close()


def main() -> None:
    rows = _collect_store_rows()
    if not rows:
        print("No store rows found — abort")
        sys.exit(1)

    stats = enrich_master_from_store_dbs(STORE_DBS)
    print("enrich_master_from_store_dbs", stats)

    targets = [
        get_master_db_path(),
        os.path.join(ROOT, "config", "master_medicine.db"),
    ]
    # De-dupe paths
    seen = set()
    for p in targets:
        ap = os.path.abspath(p)
        if ap in seen:
            continue
        seen.add(ap)
        _enrich_db(ap, rows)

    patch = _write_android_patch(rows)
    print("Wrote Android patch", patch, "rows", len(rows))
    with_sched = sum(1 for r in rows.values() if r.get("schedule"))
    print(f"Patch rows with schedule: {with_sched}/{len(rows)}")


if __name__ == "__main__":
    main()
