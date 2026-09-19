"""
Mode-aware master medicine database service.

Medical mode:
  - keeps a populated SQLite master DB built from bundled Excel.
Veterinary mode:
  - keeps master DB cleared/removed.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from typing import Callable, Dict, List, Optional, Tuple

ProgressCb = Optional[Callable[[int, str], None]]

MASTER_TABLE = "medicines_master"


def _config_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "VeterinaryApp",
        )
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config",
    )


def get_master_db_path() -> str:
    return os.path.join(_config_dir(), "master_medicine.db")


def _bundled_excel_path() -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "assets", "medicines_master_with_cdsco.xlsx")
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets",
        "medicines_master_with_cdsco.xlsx",
    )


def _bundled_master_db_path() -> str:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled = os.path.join(sys._MEIPASS, "config", "master_medicine.db")
        if os.path.isfile(bundled):
            return bundled
    dev = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config",
        "master_medicine.db",
    )
    return dev if os.path.isfile(dev) else ""


def _connect_master() -> sqlite3.Connection:
    os.makedirs(_config_dir(), exist_ok=True)
    conn = sqlite3.connect(get_master_db_path(), timeout=30.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {MASTER_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            manufacturer TEXT,
            mrp REAL,
            content_drug TEXT,
            med_type TEXT,
            pack_size TEXT,
            schedule TEXT,
            hsn_code TEXT,
            gst_percent REAL,
            updated_at TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            device_id TEXT,
            deleted INTEGER NOT NULL DEFAULT 0,
            sync_status TEXT NOT NULL DEFAULT 'synced'
        )
        """
    )
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({MASTER_TABLE})")
    cols = {str(r[1]) for r in cur.fetchall()}
    for col, decl in (
        ("schedule", "TEXT"),
        ("hsn_code", "TEXT"),
        ("gst_percent", "REAL"),
        ("updated_at", "TEXT"),
        ("version", "INTEGER NOT NULL DEFAULT 1"),
        ("device_id", "TEXT"),
        ("deleted", "INTEGER NOT NULL DEFAULT 0"),
        ("sync_status", "TEXT NOT NULL DEFAULT 'synced'"),
    ):
        if col not in cols:
            conn.execute(f"ALTER TABLE {MASTER_TABLE} ADD COLUMN {col} {decl}")
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{MASTER_TABLE}_name ON {MASTER_TABLE}(name COLLATE NOCASE)"
    )
    conn.commit()


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _device_id() -> str:
    try:
        from core.sync_metadata_schema import get_sync_device_id

        return str(get_sync_device_id() or "")
    except Exception:
        return ""


def master_row_count() -> int:
    conn = _connect_master()
    try:
        return _count_rows(conn)
    finally:
        conn.close()


def row_to_doc(row: sqlite3.Row | tuple, cols: List[str]) -> Dict[str, object]:
    if isinstance(row, sqlite3.Row):
        d = {k: row[k] for k in row.keys()}
    else:
        d = dict(zip(cols, row))
    return {
        "name": d.get("name"),
        "manufacturer": d.get("manufacturer") or "",
        "mrp": float(d.get("mrp") or 0),
        "content_drug": d.get("content_drug") or "",
        "med_type": d.get("med_type") or "",
        "pack_size": d.get("pack_size") or "",
        "schedule": d.get("schedule") or "",
        "hsn_code": d.get("hsn_code") or "",
        "gst_percent": float(d.get("gst_percent") or 0),
        "updated_at": d.get("updated_at") or _now_iso(),
        "version": int(d.get("version") or 1),
        "device_id": d.get("device_id") or _device_id(),
        "deleted": bool(int(d.get("deleted") or 0)),
        "sync_status": d.get("sync_status") or "synced",
    }


def apply_remote_docs(docs: List[dict], *, replace: bool = False) -> int:
    """Apply server docs into local snapshot. replace=True clears table first."""
    if docs is None:
        docs = []
    conn = _connect_master()
    try:
        _init_schema(conn)
        if replace:
            conn.execute(f"DELETE FROM {MASTER_TABLE}")
        n = 0
        for doc in docs:
            name = str(doc.get("name") or "").strip()
            if not name:
                continue
            conn.execute(
                f"""
                INSERT INTO {MASTER_TABLE}
                (name, manufacturer, mrp, content_drug, med_type, pack_size,
                 schedule, hsn_code, gst_percent, updated_at, version, device_id,
                 deleted, sync_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    manufacturer=excluded.manufacturer,
                    mrp=excluded.mrp,
                    content_drug=excluded.content_drug,
                    med_type=excluded.med_type,
                    pack_size=excluded.pack_size,
                    schedule=excluded.schedule,
                    hsn_code=excluded.hsn_code,
                    gst_percent=excluded.gst_percent,
                    updated_at=excluded.updated_at,
                    version=excluded.version,
                    device_id=excluded.device_id,
                    deleted=excluded.deleted,
                    sync_status='synced'
                """,
                (
                    name.upper(),
                    str(doc.get("manufacturer") or "").strip(),
                    float(doc.get("mrp") or 0),
                    str(doc.get("content_drug") or "").strip(),
                    str(doc.get("med_type") or doc.get("type") or "").strip(),
                    str(doc.get("pack_size") or "").strip(),
                    str(doc.get("schedule") or "").strip(),
                    str(doc.get("hsn_code") or "").strip(),
                    float(doc.get("gst_percent") or 0),
                    str(doc.get("updated_at") or _now_iso()),
                    int(doc.get("version") or 1),
                    str(doc.get("device_id") or ""),
                    1 if doc.get("deleted") else 0,
                    "synced",
                ),
            )
            n += 1
        conn.commit()
        return n
    finally:
        conn.close()


def list_master_docs(limit: int = 0) -> List[Dict[str, object]]:
    conn = _connect_master()
    try:
        _init_schema(conn)
        cur = conn.cursor()
        sql = f"SELECT * FROM {MASTER_TABLE} WHERE COALESCE(deleted,0)=0 ORDER BY name COLLATE NOCASE"
        if limit and int(limit) > 0:
            cur.execute(sql + " LIMIT ?", (int(limit),))
        else:
            cur.execute(sql)
        cols = [d[0] for d in cur.description]
        return [row_to_doc(r, cols) for r in cur.fetchall()]
    finally:
        conn.close()


def clear_master_db() -> None:
    path = get_master_db_path()
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            # If file is locked, fallback to truncating table.
            conn = _connect_master()
            try:
                _init_schema(conn)
                conn.execute(f"DELETE FROM {MASTER_TABLE}")
                conn.commit()
            finally:
                conn.close()


def _count_rows(conn: sqlite3.Connection) -> int:
    _init_schema(conn)
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {MASTER_TABLE}")
    return int(cur.fetchone()[0] or 0)


def _detect_type(name: str, form: str) -> str:
    text = f"{name} {form}".lower()
    rules = [
        ("tablet", "Tablet"),
        ("capsule", "Capsule"),
        ("syrup", "Syrup"),
        ("injection", "Injection"),
        ("ointment", "Ointment"),
        ("cream", "Cream"),
        ("gel", "Gel"),
        ("drop", "Drops"),
        ("powder", "Powder"),
        ("bolus", "Bolus"),
        ("vaccine", "Vaccine"),
        ("liniment", "Liniment"),
        ("granule", "Granules"),
        ("liquid", "Liquid"),
    ]
    for kw, med_type in rules:
        if kw in text:
            return med_type
    return "Others"


def ensure_master_db_ready(progress_cb: ProgressCb = None) -> Tuple[bool, int, str]:
    """
    Ensure master DB exists and is populated.
    Returns (ready, row_count, message).
    """
    import shutil

    dest = get_master_db_path()
    bundled_db = _bundled_master_db_path()
    if bundled_db and (not os.path.isfile(dest) or os.path.getsize(dest) < 1024):
        try:
            shutil.copy2(bundled_db, dest)
            if progress_cb:
                progress_cb(100, "Master medicines ready (bundled catalog)")
            conn = _connect_master()
            try:
                count = _count_rows(conn)
            finally:
                conn.close()
            if count > 0:
                return True, count, "bundled-copy"
        except Exception:
            pass

    try:
        conn = _connect_master()
        try:
            existing = _count_rows(conn)
            if existing > 0:
                if progress_cb:
                    progress_cb(100, f"Master medicines ready ({existing:,})")
                return True, existing, "already-populated"
        finally:
            conn.close()

        excel_path = _bundled_excel_path()
        if not os.path.exists(excel_path):
            if progress_cb:
                progress_cb(100, "Master Excel not found")
            return False, 0, "excel-not-found"

        if progress_cb:
            progress_cb(5, "Reading master medicines Excel...")

        import re
        import openpyxl

        wb = openpyxl.load_workbook(excel_path, read_only=True, data_only=True)
        ws = wb.active
        max_rows = int(ws.max_row or 1)
        processed = 0

        conn = _connect_master()
        try:
            _init_schema(conn)
            conn.execute(f"DELETE FROM {MASTER_TABLE}")
            conn.commit()

            batch: List[Tuple[str, str, float, str, str, str]] = []
            for row in ws.iter_rows(min_row=2, values_only=True):
                processed += 1
                name = str(row[0]).strip() if row and row[0] else ""
                if not name or name.lower() == "none":
                    if processed % 2000 == 0 and progress_cb:
                        pct = min(98, 5 + int((processed / max(max_rows, 1)) * 90))
                        progress_cb(pct, f"Loading medicines... {processed:,}/{max_rows:,}")
                    continue

                mfg = str(row[1]).strip() if len(row) > 1 and row[1] else ""
                try:
                    mrp = float(row[10]) if len(row) > 10 and row[10] is not None else 0.0
                except Exception:
                    mrp = 0.0
                salt = str(row[11]).strip() if len(row) > 11 and row[11] else ""
                salt = re.sub(r"\s*\+\s*nan\s*", "", salt).strip().strip("+").strip()
                form = str(row[16]).strip() if len(row) > 16 and row[16] else ""
                pack = str(row[17]).strip() if len(row) > 17 and row[17] else ""
                batch.append((name, mfg, mrp, salt, _detect_type(name, form), pack))

                if len(batch) >= 5000:
                    conn.executemany(
                        f"""
                        INSERT INTO {MASTER_TABLE}
                        (name, manufacturer, mrp, content_drug, med_type, pack_size)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(name) DO UPDATE SET
                            manufacturer=excluded.manufacturer,
                            mrp=excluded.mrp,
                            content_drug=excluded.content_drug,
                            med_type=excluded.med_type,
                            pack_size=excluded.pack_size
                        """,
                        batch,
                    )
                    conn.commit()
                    batch.clear()

                if processed % 2000 == 0 and progress_cb:
                    pct = min(98, 5 + int((processed / max(max_rows, 1)) * 90))
                    progress_cb(pct, f"Loading medicines... {processed:,}/{max_rows:,}")

            if batch:
                conn.executemany(
                    f"""
                    INSERT INTO {MASTER_TABLE}
                    (name, manufacturer, mrp, content_drug, med_type, pack_size)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        manufacturer=excluded.manufacturer,
                        mrp=excluded.mrp,
                        content_drug=excluded.content_drug,
                        med_type=excluded.med_type,
                        pack_size=excluded.pack_size
                    """,
                    batch,
                )
                conn.commit()

            count = _count_rows(conn)
            if progress_cb:
                progress_cb(100, f"Master medicines loaded ({count:,})")
            return True, count, "imported"
        finally:
            conn.close()
            wb.close()
    except Exception as exc:
        if progress_cb:
            progress_cb(100, f"Master load failed: {exc}")
        return False, 0, str(exc)


def ensure_mode_master_state(mode: str, progress_cb: ProgressCb = None) -> Tuple[bool, int, str]:
    mode = (mode or "medical").strip().lower()
    if mode == "veterinary":
        clear_master_db()
        if progress_cb:
            progress_cb(100, "Veterinary mode: master DB cleared")
        return True, 0, "cleared-for-veterinary"
    return ensure_master_db_ready(progress_cb=progress_cb)


def search_master_names(typed: str, limit: int = 50) -> List[str]:
    query = (typed or "").strip()
    conn = _connect_master()
    try:
        _init_schema(conn)
        cur = conn.cursor()
        if not query:
            cur.execute(
                f"SELECT name FROM {MASTER_TABLE} ORDER BY name COLLATE NOCASE LIMIT ?",
                (limit,),
            )
            return [r[0] for r in cur.fetchall()]

        cur.execute(
            f"""
            SELECT name FROM {MASTER_TABLE}
            WHERE name LIKE ? COLLATE NOCASE
            ORDER BY name COLLATE NOCASE
            LIMIT ?
            """,
            (f"{query}%", limit),
        )
        prefix = [r[0] for r in cur.fetchall()]

        if len(prefix) >= limit:
            return prefix[:limit]

        seen = {n.lower() for n in prefix}
        cur.execute(
            f"""
            SELECT name FROM {MASTER_TABLE}
            WHERE name LIKE ? COLLATE NOCASE
              AND name NOT LIKE ? COLLATE NOCASE
            ORDER BY name COLLATE NOCASE
            LIMIT ?
            """,
            (f"%{query}%", f"{query}%", limit - len(prefix)),
        )
        contains = [r[0] for r in cur.fetchall() if r[0].lower() not in seen]
        return (prefix + contains)[:limit]
    finally:
        conn.close()


def get_all_master_names(limit: int = 0) -> List[str]:
    conn = _connect_master()
    try:
        _init_schema(conn)
        cur = conn.cursor()
        if limit and int(limit) > 0:
            cur.execute(
                f"SELECT name FROM {MASTER_TABLE} ORDER BY name COLLATE NOCASE LIMIT ?",
                (int(limit),),
            )
        else:
            cur.execute(f"SELECT name FROM {MASTER_TABLE} ORDER BY name COLLATE NOCASE")
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def lookup_master_details(name: str) -> Dict[str, object]:
    medicine_name = (name or "").strip()
    if not medicine_name:
        return {}
    conn = _connect_master()
    try:
        _init_schema(conn)
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT med_type, manufacturer, mrp, content_drug, pack_size,
                   schedule, hsn_code, gst_percent
            FROM {MASTER_TABLE}
            WHERE name = ? COLLATE NOCASE
            LIMIT 1
            """,
            (medicine_name,),
        )
        row = cur.fetchone()
        if not row:
            return {}
        return {
            "type": row[0] or "",
            "manufacturer": row[1] or "",
            "mrp": float(row[2] or 0),
            "content_drug": row[3] or "",
            "pack_size": row[4] or "",
            "schedule": row[5] or "",
            "hsn_code": row[6] or "",
            "gst_percent": float(row[7] or 0) if row[7] is not None else 0,
        }
    finally:
        conn.close()


def upsert_master_medicine(
    name: str,
    manufacturer: str = "",
    mrp: float = 0.0,
    content_drug: str = "",
    med_type: str = "",
    pack_size: str = "",
    schedule: str = "",
    hsn_code: str = "",
    gst_percent: float = 0.0,
    *,
    push_remote: bool = True,
) -> None:
    medicine_name = (name or "").strip()
    if not medicine_name:
        return
    now = _now_iso()
    device = _device_id()
    conn = _connect_master()
    try:
        _init_schema(conn)
        cur = conn.cursor()
        cur.execute(
            f"SELECT version FROM {MASTER_TABLE} WHERE name=? COLLATE NOCASE",
            (medicine_name,),
        )
        prev = cur.fetchone()
        next_ver = int(prev[0] or 1) + 1 if prev else 1
        conn.execute(
            f"""
            INSERT INTO {MASTER_TABLE}
            (name, manufacturer, mrp, content_drug, med_type, pack_size,
             schedule, hsn_code, gst_percent, updated_at, version, device_id,
             deleted, sync_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'pending')
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
                    ELSE {MASTER_TABLE}.gst_percent END,
                updated_at=excluded.updated_at,
                version=excluded.version,
                device_id=excluded.device_id,
                deleted=0,
                sync_status='pending'
            """,
            (
                medicine_name.upper(),
                (manufacturer or "").strip(),
                float(mrp or 0),
                (content_drug or "").strip(),
                (med_type or "").strip(),
                (pack_size or "").strip(),
                (schedule or "").strip(),
                (hsn_code or "").strip(),
                float(gst_percent or 0),
                now,
                next_ver,
                device,
            ),
        )
        conn.commit()
        doc = {
            "name": medicine_name.upper(),
            "manufacturer": (manufacturer or "").strip(),
            "mrp": float(mrp or 0),
            "content_drug": (content_drug or "").strip(),
            "med_type": (med_type or "").strip(),
            "pack_size": (pack_size or "").strip(),
            "schedule": (schedule or "").strip(),
            "hsn_code": (hsn_code or "").strip(),
            "gst_percent": float(gst_percent or 0),
            "updated_at": now,
            "version": next_ver,
            "device_id": device,
            "deleted": False,
        }
    finally:
        conn.close()

    if push_remote:
        try:
            from core.master_medicine_cloud import push_master_docs_async

            push_master_docs_async([doc], enrich=False)
        except Exception:
            pass


def sync_master_with_inventory(inventory_conn: sqlite3.Connection) -> int:
    """
    Upsert medicines from local inventory DB into master DB.
    Returns number of medicines processed.
    """
    if inventory_conn is None:
        return 0
    try:
        cur = inventory_conn.cursor()
        cur.execute("PRAGMA table_info(medicines)")
        cols = {str(r[1]) for r in cur.fetchall()}
        schedule_expr = "COALESCE(m.schedule, '')" if "schedule" in cols else "''"
        hsn_expr = "COALESCE(m.hsn_code, '')" if "hsn_code" in cols else "''"
        gst_expr = "COALESCE(m.gst_percent, 0)" if "gst_percent" in cols else "0"
        cur.execute(
            f"""
            SELECT m.name,
                   COALESCE(m.manufacturer, ''),
                   COALESCE(m.mrp, 0),
                   COALESCE(m.content_drug, ''),
                   COALESCE(m.type, ''),
                   COALESCE(m.unit, ''),
                   {schedule_expr},
                   {hsn_expr},
                   {gst_expr}
            FROM medicines m
            JOIN (
                SELECT name, MAX(id) AS max_id
                FROM medicines
                GROUP BY name
            ) latest ON latest.max_id = m.id
            WHERE COALESCE(m.name, '') <> ''
            """
        )
        rows = cur.fetchall()
    except Exception:
        return 0

    if not rows:
        return 0

    conn = _connect_master()
    try:
        _init_schema(conn)
        payload = [
            (
                str(r[0] or "").strip(),
                str(r[1] or "").strip(),
                float(r[2] or 0),
                str(r[3] or "").strip(),
                str(r[4] or "").strip(),
                str(r[5] or "").strip(),
                str(r[6] or "").strip(),
                str(r[7] or "").strip(),
                float(r[8] or 0),
            )
            for r in rows
            if str(r[0] or "").strip()
        ]
        # Stock enrich: non-blank incoming always wins (later stores overwrite earlier).
        conn.executemany(
            f"""
            INSERT INTO {MASTER_TABLE}
            (name, manufacturer, mrp, content_drug, med_type, pack_size,
             schedule, hsn_code, gst_percent, updated_at, version, device_id,
             deleted, sync_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), 1, 'stock-enrich', 0, 'pending')
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
                    ELSE {MASTER_TABLE}.gst_percent END,
                updated_at=datetime('now'),
                version=COALESCE({MASTER_TABLE}.version, 1) + 1,
                device_id='stock-enrich',
                sync_status='pending'
            """,
            payload,
        )
        conn.commit()
        return len(payload)
    finally:
        conn.close()


def enrich_master_from_store_dbs(store_db_paths: List[str]) -> Dict[str, int]:
    """Merge schedule/HSN/GST/etc from one or more store inventory DBs into master."""
    stats = {"stores": 0, "names": 0}
    for path in store_db_paths or []:
        if not path or not os.path.isfile(path):
            continue
        stats["stores"] += 1
        stats["names"] += int(sync_master_with_inventory_db_path(path) or 0)
    return stats


def sync_master_with_inventory_db_path(inventory_db_path: str) -> int:
    """
    Thread-safe variant: opens its own inventory DB connection by path.
    """
    if not inventory_db_path:
        return 0
    from core.db_utils import open_store_db

    conn = open_store_db(inventory_db_path, timeout=60.0)
    try:
        return sync_master_with_inventory(conn)
    finally:
        try:
            conn.close()
        except Exception:
            pass
