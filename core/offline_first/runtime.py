"""Turning offline-first on for a store, and the facts other modules ask about it."""
from __future__ import annotations

import os
import sqlite3
from datetime import date
from typing import Callable, Optional

from core.offline_first import schema
from core.offline_first.schema import meta_get, meta_set

ProgressCb = Optional[Callable[[str], None]]


def is_active() -> bool:
    try:
        from core.sync_prefs import is_offline_first

        return bool(is_offline_first())
    except Exception:
        return False


def registered(conn: sqlite3.Connection) -> bool:
    try:
        return bool(meta_get(conn, "device_no"))
    except sqlite3.Error:
        return False


def current_fy() -> int:
    from core.fy_serial import fy_start_year_for_date

    return int(fy_start_year_for_date(date.today()))


def _progress(cb: ProgressCb, msg: str) -> None:
    if cb:
        try:
            cb(msg)
        except Exception:
            pass


def _seed_versions(conn: sqlite3.Connection) -> None:
    """The server version of every record the download brought, so an edit names it."""
    for table in schema.COLLECTIONS:
        try:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.Error:
            continue
        if "version" not in cols:
            continue
        conn.execute(
            f"INSERT OR REPLACE INTO of_versions (collection, local_id, version) "
            f"SELECT '{table}', id, COALESCE(version, 1) FROM {table}"
        )


def register_and_prepare(conn: sqlite3.Connection, *, head_revision: int, app_version: str = "",
                         progress_cb: ProgressCb = None) -> dict:
    """On a store file that already holds the server's copy: bookkeeping tables, triggers,
    device registration, versions, number blocks and the pull cursor."""
    from core.offline_first import client, numbers

    _progress(progress_cb, "Registering this PC with the server…")
    reg = client.register(app_version=app_version)
    schema.ensure_schema(conn)
    meta_set(conn, "install_id", client.install_id())
    meta_set(conn, "device_no", int(reg["device_no"]))
    meta_set(conn, "id_base", int(reg["id_base"]))
    meta_set(conn, "id_max", int(reg["id_max"]))
    prev = int(meta_get(conn, "server_last_seq", 0) or 0)
    meta_set(conn, "server_last_seq", max(prev, int(reg.get("last_seq") or 0)))
    if meta_get(conn, "pull_cursor") is None:
        meta_set(conn, "pull_cursor", int(head_revision))
    # Carry on after what this device already made (a reinstalled PC must not start at 1).
    for table, top in (reg.get("id_floor") or {}).items():
        conn.execute(
            "INSERT INTO of_id_counters (tbl, next_id) VALUES (?, ?) "
            "ON CONFLICT(tbl) DO UPDATE SET next_id=MAX(next_id, excluded.next_id)",
            (str(table), int(top) + 1),
        )
    for prefix, top in (reg.get("doc_floor") or {}).items():
        conn.execute(
            "INSERT INTO of_doc_counters (prefix, next_no) VALUES (?, ?) "
            "ON CONFLICT(prefix) DO UPDATE SET next_no=MAX(next_no, excluded.next_no)",
            (str(prefix), int(top) + 1),
        )
    _seed_versions(conn)
    # Numbers left in blocks this device already held (reinstalled, or switched on again).
    for blk in reg.get("open_blocks") or []:
        try:
            numbers.add_block(conn, str(blk["kind"]), int(blk["fy_start_year"]), int(blk["from_serial"]),
                              int(blk["to_serial"]), int(blk["next_serial"]))
        except (KeyError, TypeError, ValueError):
            continue
    fy = current_fy()
    for kind, size in numbers.BLOCK_SIZE.items():
        if numbers.remaining(conn, kind, fy) < size // 2:
            _progress(progress_cb, f"Reserving {kind} numbers…")
            blk = client.number_block(kind, fy, size)
            numbers.add_block(conn, kind, fy, int(blk["from_serial"]), int(blk["to_serial"]))
    conn.commit()
    return {"device_no": int(reg["device_no"]), "id_base": int(reg["id_base"])}


def activate_from_online(progress_cb: ProgressCb = None, app_version: str = "") -> dict:
    """Online PC -> offline-first: the full copy (the same download as Go Offline), then
    registration. The mode changes only after everything is in place."""
    from core.offline_first import client
    from core.online_migrate import download_store_for_offline, store_db_path
    from core.sync_prefs import MODE_OFFLINE_FIRST, set_sync_mode

    _progress(progress_cb, "Checking the server…")
    head = client.head_revision()
    path = store_db_path()
    if path and os.path.isfile(path):
        # A file that was on offline-first before: start its bookkeeping clean (refused while
        # anything on it is unsent -- that work is sent by offline-first, not thrown away).
        old = sqlite3.connect(path, timeout=30)
        try:
            if schema.unsent_work(old):
                raise RuntimeError(
                    f"{schema.unsent_work(old)} change(s) made on this PC in offline-first have not "
                    "reached the server. Nothing was changed."
                )
            schema.clear_bookkeeping(old)
        finally:
            old.close()
    _progress(progress_cb, "Copying the store from the server… (app band karu naka)")
    result = download_store_for_offline(progress_cb=progress_cb)
    if isinstance(result, dict) and result.get("ok") is False:
        raise RuntimeError(result.get("error") or "The store could not be copied from the server.")
    path = store_db_path()
    conn = sqlite3.connect(path, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        info = register_and_prepare(conn, head_revision=head, app_version=app_version,
                                    progress_cb=progress_cb)
    finally:
        conn.close()
    set_sync_mode(MODE_OFFLINE_FIRST)
    _progress(progress_cb, "Done: this PC now works on its own copy and keeps it in step with the server.")
    return {"ok": True, **info, "head_revision": head}


def status(conn: sqlite3.Connection) -> dict:
    from core.offline_first.outbox import counts

    out = {"active": is_active(), "registered": registered(conn)}
    if not out["registered"]:
        return out
    out.update(counts(conn))
    for k in ("device_no", "server_last_seq", "pull_cursor", "last_push_at", "last_pull_at",
              "last_error", "last_error_at"):
        out[k] = meta_get(conn, k)
    try:
        from core.offline_first import numbers

        fy = current_fy()
        out["numbers_left"] = {k: numbers.remaining(conn, k, fy) for k in numbers.BLOCK_SIZE}
    except Exception:
        pass
    return out
