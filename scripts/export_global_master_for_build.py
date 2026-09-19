"""Export local/global master snapshot into bundled Mac2 DB + Android CSV for new builds.

Prefer: pull from server if online token available; else use local master_medicine.db.
"""
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
    get_master_db_path,
    list_master_docs,
)

OUT_DB = os.path.join(ROOT, "config", "master_medicine.db")
OUT_CSV = os.path.normpath(
    os.path.join(
        ROOT,
        "..",
        "Satpuda Core",
        "app",
        "src",
        "main",
        "assets",
        "medicines_master.csv",
    )
)


def _try_download_from_server() -> bool:
    if "--from-server" not in sys.argv:
        print("Using local master snapshot (pass --from-server to refresh from API)")
        return False
    try:
        from core.master_medicine_cloud import download_master_replace_local

        ok, msg, n = download_master_replace_local()
        print(msg)
        return ok and n > 0
    except Exception as exc:
        print("Server download skipped:", exc)
        return False


def _write_csv(docs: list[dict]) -> None:
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
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
        for d in docs:
            w.writerow(
                [
                    d.get("name") or "",
                    d.get("manufacturer") or "",
                    d.get("mrp") or "",
                    d.get("content_drug") or "",
                    d.get("med_type") or "",
                    d.get("pack_size") or "",
                    d.get("schedule") or "",
                    d.get("hsn_code") or "",
                    d.get("gst_percent") or "",
                ]
            )
    print(f"Wrote {len(docs):,} rows -> {OUT_CSV} ({os.path.getsize(OUT_CSV)/1e6:.1f} MB)")


def _ensure_config_db_copy() -> None:
    src = get_master_db_path()
    if os.path.abspath(src) == os.path.abspath(OUT_DB):
        print("Config master DB already at", OUT_DB)
        return
    if not os.path.isfile(src):
        print("No source master DB at", src)
        return
    import shutil

    os.makedirs(os.path.dirname(OUT_DB), exist_ok=True)
    shutil.copy2(src, OUT_DB)
    print("Copied", src, "→", OUT_DB)


def main() -> None:
    _try_download_from_server()
    docs = list_master_docs()
    if not docs:
        # fallback: open OUT_DB / get_master_db_path directly
        path = get_master_db_path()
        if os.path.isfile(path):
            conn = sqlite3.connect(path)
            try:
                _init_schema(conn)
                cur = conn.cursor()
                cur.execute(f"SELECT COUNT(*) FROM {MASTER_TABLE}")
                print("Local master count:", cur.fetchone()[0])
            finally:
                conn.close()
        print("No docs to export")
        sys.exit(1)
    _write_csv(docs)
    _ensure_config_db_copy()
    print("Build seed export complete.")


if __name__ == "__main__":
    main()
