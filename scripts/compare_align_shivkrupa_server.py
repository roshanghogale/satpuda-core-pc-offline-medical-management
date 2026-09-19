"""Compare Shivkrupa local SQLite vs Node server, then align via Node sync APIs.

Uses only /api/sync (no Server).
  1) Compare non-deleted IDs per collection
  2) Push local-only docs in chunks
  3) Pull all pages into local (download merge)
  4) Re-compare
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

STORE_KEY = "Store_Shivkrupa_Medical_General_Store"
DB = ROOT / "config" / "stores" / STORE_KEY / "veterinary.db"
SESSION = ROOT / "config" / f"server_session_{STORE_KEY}.json"
REPORT = ROOT / "config" / "stores" / STORE_KEY / "sync_align_report.json"

COLS = [
    "customers",
    "suppliers",
    "medicines",
    "doctors",
    "sales",
    "purchases",
    "customer_payments",
    "supplier_payments",
    "sales_returns",
    "purchase_returns",
]

UA = "SatpudaCoreAlign/1.0"
BASE = "https://api.satpudacore.online"
PAGE = 800
PUSH_CHUNK = 25


def load_session() -> dict:
    return json.loads(SESSION.read_text(encoding="utf-8"))


def req(token: str, method: str, path: str, body=None, timeout: float = 120.0) -> dict:
    data = None
    headers = {
        "Accept": "application/json",
        "User-Agent": UA,
        "Authorization": f"Bearer {token}",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        BASE + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail[:300]}") from exc


def open_local(readonly: bool = True) -> sqlite3.Connection:
    if readonly:
        uri = f"file:{DB.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=60)
    else:
        conn = sqlite3.connect(str(DB), timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def local_ids(conn: sqlite3.Connection, col: str) -> set[str]:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({col})")}
    if "deleted" in cols:
        rows = conn.execute(
            f"SELECT id FROM {col} WHERE COALESCE(deleted,0)=0"
        ).fetchall()
    else:
        rows = conn.execute(f"SELECT id FROM {col}").fetchall()
    return {str(r[0]) for r in rows}


def pull_server_ids(token: str, col: str) -> set[str]:
    ids: set[str] = set()
    cursor = None
    while True:
        q = f"include_deleted=0&limit={PAGE}"
        if cursor:
            q += f"&since={urllib.parse.quote(cursor)}"
        try:
            res = req(token, "GET", f"/api/sync/{col}?{q}", timeout=90)
        except Exception:
            time.sleep(1.2)
            res = req(token, "GET", f"/api/sync/{col}?{q}", timeout=120)
        if not res.get("ok"):
            raise RuntimeError(res.get("error") or f"pull {col} failed")
        data = res.get("data")
        docs = data if isinstance(data, list) else []
        if not docs:
            break
        last = cursor
        new = 0
        for d in docs:
            if not isinstance(d, dict):
                continue
            if d.get("deleted") in (True, 1, "true", "1"):
                continue
            did = d.get("id") if d.get("id") is not None else d.get("local_id")
            if did is None:
                continue
            sid = str(did)
            if sid not in ids:
                ids.add(sid)
                new += 1
            ts = d.get("updated_at") or d.get("synced_at")
            if isinstance(ts, str) and ts and (last is None or ts > last):
                last = ts
        if len(docs) < PAGE or new == 0 or last == cursor:
            break
        cursor = last
        time.sleep(0.04)
    return ids


def compare(token: str, conn: sqlite3.Connection) -> list[dict]:
    rows = []
    for col in COLS:
        print(f"  compare {col}…", flush=True)
        lids = local_ids(conn, col)
        sids = pull_server_ids(token, col)
        only_local = sorted(lids - sids, key=lambda x: int(x) if x.isdigit() else x)
        only_server = sorted(sids - lids, key=lambda x: int(x) if x.isdigit() else x)
        row = {
            "collection": col,
            "local": len(lids),
            "server": len(sids),
            "only_local": len(only_local),
            "only_server": len(only_server),
            "only_local_ids": only_local[:50],
            "only_server_ids": only_server[:50],
            "only_local_all": only_local,
            "only_server_all": only_server,
        }
        rows.append(row)
        mark = "OK" if not only_local and not only_server else "DIFF"
        print(
            f"    {col:20} local={len(lids):5} server={len(sids):5} "
            f"loc_only={len(only_local):5} srv_only={len(only_server):5} {mark}",
            flush=True,
        )
    return rows


def push_local_only(token: str, conn: sqlite3.Connection, compare_rows: list[dict]) -> int:
    """Push local-only IDs using Node collection push (chunked)."""
    from core import server_entity_sync as fb
    from core.server_sync import _sanitize_dates_in_doc

    uploaded = 0
    builders = {
        "customers": fb.build_customer_payload,
        "suppliers": fb.build_supplier_payload,
        "medicines": fb.build_medicine_payload,
        "doctors": fb.build_doctor_payload,
        "sales": fb.build_sale_payload,
        "purchases": fb.build_purchase_payload,
        "customer_payments": fb.build_customer_payment_payload,
        "supplier_payments": fb.build_supplier_payment_payload,
        "sales_returns": fb.build_sales_return_payload,
        "purchase_returns": fb.build_purchase_return_payload,
    }
    for row in compare_rows:
        col = row["collection"]
        ids = row.get("only_local_all") or []
        if not ids:
            continue
        build = builders.get(col)
        if not build:
            continue
        print(f"  push local-only {col}: {len(ids)}…", flush=True)
        docs = []
        for sid in ids:
            try:
                payload = build(conn, int(sid))
            except Exception as exc:
                print(f"    build {col}/{sid} failed: {exc}")
                continue
            if not payload:
                continue
            payload = dict(payload)
            payload["id"] = int(sid)
            _sanitize_dates_in_doc(payload)
            docs.append(payload)
        for i in range(0, len(docs), PUSH_CHUNK):
            chunk = docs[i : i + PUSH_CHUNK]
            for attempt in range(1, 5):
                try:
                    res = req(
                        token,
                        "POST",
                        f"/api/sync/{col}",
                        body=chunk,
                        timeout=180,
                    )
                    data = res.get("data") or {}
                    uploaded += int(data.get("upserted") or 0)
                    print(
                        f"    {col} {min(i+len(chunk), len(docs))}/{len(docs)} "
                        f"upserted={data.get('upserted')}",
                        flush=True,
                    )
                    break
                except Exception as exc:
                    wait = min(8.0, 1.2 * attempt * attempt)
                    print(f"    retry {attempt}: {exc}")
                    if attempt >= 4:
                        raise
                    time.sleep(wait)
            time.sleep(0.08)
    return uploaded


def pull_all_into_local(token: str, conn: sqlite3.Connection) -> int:
    """Full Node download merge into local using server_live.apply_server_doc."""
    from core import server_live as live
    from core import sync_watermarks as wm

    # Force online apply path
    os.environ.setdefault("SATPADA_FORCE", "1")
    try:
        from core.sync_prefs import set_sync_mode, MODE_ONLINE

        set_sync_mode(MODE_ONLINE)
    except Exception:
        pass

    live.set_suppress_entity_repush(True)
    count = 0
    try:
        for idx, col in enumerate(COLS, 1):
            print(f"  pull {col} ({idx}/{len(COLS)})…", flush=True)

            def progress(msg: str) -> None:
                print(f"    {msg}", flush=True)

            docs, _meta = live._pull_collection_pages(
                token, col, since=None, progress_cb=progress, page_size=PAGE
            )
            print(f"    merging {len(docs)}…", flush=True)
            max_ts = None
            for i, doc in enumerate(docs, 1):
                if not isinstance(doc, dict):
                    continue
                status = live.apply_server_doc(conn, col, doc)
                if status in ("applied", "soft_deleted"):
                    count += 1
                ts = doc.get("updated_at") or doc.get("synced_at")
                if isinstance(ts, str) and ts and (max_ts is None or ts > max_ts):
                    max_ts = ts
                if i % 400 == 0:
                    print(f"    merged {i}/{len(docs)}", flush=True)
                    conn.commit()
            if max_ts:
                wm.bump_watermark(col, max_ts)
            else:
                wm.seed_from_local(conn, col)
            conn.commit()
            # Clear KEEP_LOCAL queue — download only
            with live._repush_lock:
                live._repush_queue.clear()
        for special in ("pharmacy_profile", "dropdowns", "shelf_settings", "settings"):
            try:
                print(f"  pull {special}…", flush=True)
                res = req(
                    token,
                    "GET",
                    f"/api/sync/{special}?include_deleted=1&limit=5000",
                    timeout=60,
                )
                data = res.get("data")
                docs = data if isinstance(data, list) else ([data] if isinstance(data, dict) and data else [])
                for doc in docs:
                    if isinstance(doc, dict) and doc:
                        live.apply_server_doc(conn, special, doc)
                conn.commit()
            except Exception as exc:
                print(f"    {special} skip: {exc}")
    finally:
        live.set_suppress_entity_repush(False)
        with live._repush_lock:
            live._repush_queue.clear()
    return count


def main() -> int:
    if not DB.is_file():
        print("DB missing:", DB)
        return 1
    if not SESSION.is_file():
        print("Session missing:", SESSION)
        return 1
    sess = load_session()
    token = sess["token"]
    print("Store:", sess.get("store_name"), sess.get("store_id"))
    print("API:", BASE)
    print("\n=== BEFORE ===")
    ro = open_local(True)
    before = compare(token, ro)
    ro.close()

    need_push = any(r["only_local"] for r in before)
    need_pull = any(r["only_server"] for r in before)
    uploaded = 0
    pulled = 0

    if need_push:
        print("\n=== PUSH local-only → Node server ===")
        # Prefer a write-capable connection for builders; readonly is enough for SELECT.
        rw = open_local(False)
        try:
            uploaded = push_local_only(token, rw, before)
        finally:
            rw.close()

    if need_pull or need_push:
        print("\n=== PULL Node server → local (merge) ===")
        # App may hold the DB; try write, else warn.
        try:
            rw = open_local(False)
        except Exception as exc:
            print("Cannot open DB for write (is Mac2 open?):", exc)
            print("Close the app and re-run this script, or use Pull from Server in UI.")
            REPORT.write_text(
                json.dumps({"before": before, "error": str(exc)}, indent=2),
                encoding="utf-8",
            )
            return 2
        try:
            pulled = pull_all_into_local(token, rw)
            rw.commit()
        finally:
            rw.close()

    print("\n=== AFTER ===")
    ro2 = open_local(True)
    after = compare(token, ro2)
    ro2.close()

    summary = {
        "store_id": sess.get("store_id"),
        "android_key": sess.get("android_key"),
        "uploaded": uploaded,
        "pulled_applied": pulled,
        "before": [
            {k: v for k, v in r.items() if not k.endswith("_all")} for r in before
        ],
        "after": [
            {k: v for k, v in r.items() if not k.endswith("_all")} for r in after
        ],
        "aligned": all(r["only_local"] == 0 and r["only_server"] == 0 for r in after),
        "transport": "Node.js /api/sync (collection pull pages + collection push chunks)",
    }
    REPORT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\nAligned:" , summary["aligned"])
    print("Report:", REPORT)
    return 0 if summary["aligned"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
