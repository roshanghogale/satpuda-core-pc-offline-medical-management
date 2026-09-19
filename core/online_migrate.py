"""Online-mode local DB gate: migrate (push→wipe) or wipe-only, then server-only.

Local store SQLite is allowed Online only while migrating / Drive-restore→push.
After wipe, business code must not open the store DB.
"""
from __future__ import annotations

import logging
import contextlib
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[str], None]]

_tls = threading.local()

_BUSINESS_TABLES = (
    "sales",
    "purchases",
    "medicines",
    "customers",
    "suppliers",
    "customer_payments",
    "supplier_payments",
    "sales_returns",
    "purchase_returns",
    "doctors",
    "stock_disposals",
    "general_products",
)


class OnlineLocalDbForbidden(RuntimeError):
    """Raised when Online mode tries to use store SQLite after migrate gate."""

    def __init__(self, msg: str | None = None):
        super().__init__(
            msg
            or "Online mode is server-only — local store database is not available. "
            "Restore from Drive and Push to Server if you need to migrate data."
        )


def _progress(cb: ProgressCb, msg: str) -> None:
    if cb:
        try:
            cb(msg)
        except Exception:
            pass


@contextmanager
def allow_local_store_db():
    """Temporarily permit opening store SQLite while Online (migrate/restore)."""
    prev = getattr(_tls, "allow", False)
    _tls.allow = True
    try:
        yield
    finally:
        _tls.allow = prev


def local_db_allowed() -> bool:
    if getattr(_tls, "allow", False):
        return True
    try:
        from core.sync_prefs import is_online_mode

        if not is_online_mode():
            return True
    except Exception:
        return True
    # Online: allow only while migrate still pending (data or empty leftover DB setup)
    return needs_online_migrate()


def assert_can_open_store_db() -> None:
    if not local_db_allowed():
        raise OnlineLocalDbForbidden()


def store_db_path(store_key: str | None = None) -> str:
    from core.store_manager import get_active_db_path, get_store_db_path, get_active_store_key

    if store_key:
        return get_store_db_path(store_key)
    try:
        return get_active_db_path()
    except Exception:
        key = get_active_store_key() or ""
        return get_store_db_path(key) if key else ""


def local_store_has_business_data(db_path: str | None = None) -> bool:
    path = (db_path or store_db_path() or "").strip()
    if not path or not os.path.isfile(path):
        return False
    try:
        conn = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True, timeout=10.0)
    except Exception:
        return False
    try:
        for table in _BUSINESS_TABLES:
            try:
                row = conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
            except sqlite3.Error:
                continue
            if row:
                return True
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def local_store_db_exists(db_path: str | None = None) -> bool:
    path = (db_path or store_db_path() or "").strip()
    return bool(path and os.path.isfile(path))


_MIRROR_MARKER = "offline_mirror_prepared_at"


def _mark_prepared_mirror(conn) -> None:
    """Record that this local DB is a clean download, not offline work."""
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)",
            (_MIRROR_MARKER, time.strftime("%Y-%m-%dT%H:%M:%S")),
        )
    except Exception:
        pass


def is_clean_prepared_mirror(db_path: str | None = None) -> bool:
    """True when the local DB is a download we made and nothing was added to it.

    A prepared offline copy holds the same rows as the server, so a plain
    "does it have data?" check would flag it and offer to push it back or delete
    it -- neither of which is right for a read-only mirror. Work actually done
    offline is written with sync_status = 'pending', so the marker plus the
    absence of pending rows identifies a mirror that can simply be discarded.

    A store that predates this feature has no marker, so it still prompts.
    """
    path = (db_path or store_db_path() or "").strip()
    if not path or not os.path.isfile(path):
        return False
    try:
        conn = sqlite3.connect(path)
    except Exception:
        return False
    try:
        row = conn.execute(
            "SELECT value FROM app_meta WHERE key=?", (_MIRROR_MARKER,)
        ).fetchone()
        if not row:
            return False
        # Master rows count as offline work too: a medicine, customer or
        # supplier created while offline is pending, and discarding the mirror
        # would lose it. A freshly downloaded mirror writes 'synced' for all of
        # these, so including them cannot make a clean mirror look dirty.
        for table in ("sales", "purchases", "sales_returns", "purchase_returns",
                      "customer_payments", "supplier_payments",
                      "medicines", "customers", "suppliers"):
            try:
                n = conn.execute(
                    f"SELECT COUNT(*) FROM {table} "
                    "WHERE COALESCE(sync_status, 'synced') <> 'synced'"
                ).fetchone()[0]
            except Exception:
                continue
            if int(n or 0) > 0:
                return False
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def needs_online_migrate(db_path: str | None = None) -> bool:
    """True when Online and a local store DB still exists (with or without data).

    Empty leftover DBs from activation setup are wiped on first gate check
    via ensure_online_server_only_ready (wipe_empty).
    """
    try:
        from core.sync_prefs import is_online_mode

        if not is_online_mode():
            return False
    except Exception:
        return False
    path = db_path or store_db_path()
    return local_store_db_exists(path)


def delete_store_db_files(db_path: str | None = None) -> list[str]:
    """Delete veterinary.db (+ wal/shm). Returns list of removed paths."""
    path = (db_path or store_db_path() or "").strip()
    if not path:
        return []
    removed: list[str] = []
    errors: list[str] = []
    for p in (path, path + "-wal", path + "-shm", path + "-journal"):
        if not os.path.isfile(p):
            continue
        try:
            os.chmod(p, 0o666)
        except Exception:
            pass
        try:
            os.remove(p)
            removed.append(p)
        except Exception as exc:
            # Windows: file in use — rename aside so Online can continue
            try:
                tomb = p + f".deleted-{int(time.time())}"
                os.replace(p, tomb)
                removed.append(p)
                try:
                    os.remove(tomb)
                except Exception:
                    pass
            except Exception as exc2:
                errors.append(f"{p}: {exc2 or exc}")
                log.warning("delete_store_db_files %s: %s", p, exc2 or exc)
    if errors and not removed:
        raise RuntimeError(
            "Could not delete local database (file in use). "
            "Close other Satpuda windows / Task Manager python.exe, then retry.\n"
            + "\n".join(errors)
        )
    return removed


def wipe_local_store(*, db_path: str | None = None, progress_cb: ProgressCb = None) -> dict[str, Any]:
    """Delete local store DB files. Fast path — no server upload."""
    _progress(progress_cb, "Stopping sync / closing DB locks…")
    try:
        from core.sync_coordinator import stop_online_sync

        stop_online_sync()
    except Exception:
        pass
    try:
        from core.backup_manager import _close_all_db_users

        _close_all_db_users()
    except Exception:
        pass
    _progress(progress_cb, "Deleting local store database…")
    removed = delete_store_db_files(db_path)
    _progress(progress_cb, "Local database removed — Online is server-only.")
    return {"ok": True, "removed": removed, "pushed": 0}



def _local_business_counts(conn) -> dict[str, int]:
    """Live (non-deleted, non-draft) row counts for every business table."""
    counts: dict[str, int] = {}
    for table in _BUSINESS_TABLES:
        try:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        except Exception:
            continue
        if not cols:
            continue
        where = []
        if "deleted" in cols:
            where.append("COALESCE(deleted,0)=0")
        if "is_autosave" in cols:
            where.append("COALESCE(is_autosave,0)=0")
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        try:
            counts[table] = int(conn.execute(sql).fetchone()[0] or 0)
        except Exception:
            continue
    return counts


def _server_collection_count(token: str, collection: str) -> int:
    """Count rows the server actually holds. There is no count endpoint, so page."""
    from core import server_api as api

    after_id = None
    total = 0
    while True:
        docs, _ = api.pull_collection(
            token, collection, after_id=after_id,
            include_deleted=False, limit=5000, timeout=120.0,
        )
        if not docs:
            break
        total += len(docs)
        last = docs[-1]
        nxt = last.get("id") or last.get("local_id")
        if nxt is None or nxt == after_id:
            break
        after_id = nxt
    return total


def verify_push_before_wipe(conn, token: str, push_result: dict) -> tuple[bool, str]:
    """Refuse to delete a store's only local copy unless the server really has it.

    Three separate ways data used to disappear here:
      * the push reported HTTP 200 while individual rows failed inside it;
      * general_products and stock_disposals count as business data but are not in
        _PUSH_ORDER, so they were never uploaded -- and then deleted;
      * nothing ever compared what the server holds against what is about to go.
    """
    from core.server_sync import _PUSH_ORDER

    local = _local_business_counts(conn)

    failed = int(push_result.get("failed") or 0)
    if failed:
        detail = "; ".join(push_result.get("failures") or [])[:300]
        return False, (
            f"{failed} record(s) were rejected by the server during upload. "
            f"Local data kept. {detail}"
        )

    unpushable = {
        t: n for t, n in local.items() if n and t not in _PUSH_ORDER
    }
    if unpushable:
        listed = ", ".join(f"{t} ({n})" for t, n in sorted(unpushable.items()))
        return False, (
            "These tables hold data but the sync has no uploader for them, so "
            f"deleting the local database would lose them: {listed}. Local data kept."
        )

    short: list[str] = []
    for collection in _PUSH_ORDER:
        want = local.get(collection, 0)
        if not want:
            continue
        try:
            got = _server_collection_count(token, collection)
        except Exception as exc:
            return False, (
                f"Could not verify {collection} on the server ({exc}). Local data kept."
            )
        if got < want:
            short.append(f"{collection}: server {got} < local {want}")
    if short:
        return False, (
            "Server is missing rows after the upload, so the local database was NOT "
            "deleted: " + "; ".join(short)
        )
    return True, "Verified: the server holds every local business record."


def push_local_then_wipe(
    *,
    db_path: str | None = None,
    progress_cb: ProgressCb = None,
) -> dict[str, Any]:
    """Upload local store to server, then delete local DB files.

    Refused when this PC has JOINED a store that already existed on the server.
    The local database then holds one shop's records and the server store holds
    another's, and this function's whole job is to merge the first into the
    second -- shop A's bills landing in shop B's books. The dialog that offers
    this opens by itself on any Online launch that finds local rows, so the
    refusal has to live here rather than in one caller.
    """
    from core.server_sync import push_active_store_to_server

    try:
        from core import server_live as live

        if live.active_store_was_joined():
            return {
                "ok": False,
                "error": (
                    "This PC was connected to a store that already existed on "
                    "the server. Uploading this PC's records into it would mix "
                    "two shops' books together, so it has not been done. If "
                    "these records really belong to that store, ask support to "
                    "merge them."
                ),
            }
    except Exception:
        pass

    path = (db_path or store_db_path() or "").strip()
    if not path or not os.path.isfile(path):
        return wipe_local_store(db_path=path, progress_cb=progress_cb)

    # Release any leftover locks from a previous hung process before upload.
    try:
        from core.backup_manager import _close_all_db_users

        _progress(progress_cb, "Closing other database connections…")
        _close_all_db_users()
    except Exception:
        pass

    with allow_local_store_db():
        from core.db_utils import open_store_db
        from core.server_sync import push_active_store_to_server_detailed

        _progress(progress_cb, "Opening local database for upload…")
        conn = open_store_db(path, timeout=120.0, force=True)
        try:
            _progress(progress_cb, "Pushing local data to Satpuda Core Server…")
            result = push_active_store_to_server_detailed(conn, progress_cb=progress_cb)
            count = int(result.get("upserted") or 0)
            _progress(progress_cb, "Verifying the server received everything…")
            ok, message = verify_push_before_wipe(
                conn, str(result.get("store_token") or ""), result
            )
        finally:
            try:
                conn.close()
            except Exception:
                pass

    if not ok:
        _progress(progress_cb, "Upload could not be verified — local database kept.")
        return {"ok": False, "removed": 0, "pushed": count, "error": message}

    _progress(progress_cb, f"Uploaded {count:,} record(s). Removing local database…")
    removed = delete_store_db_files(path)
    _progress(progress_cb, "Done — local DB deleted; Online uses the server only.")
    return {"ok": True, "removed": removed, "pushed": count, "verified": message}


def _restore_server_stock(conn, *, progress_cb: ProgressCb = None) -> int:
    """Overwrite local stock_qty AND is_hidden with the server's figures.

    sync_down_all does not carry is_hidden, so every medicine landed locally as
    visible. Switching Online -> Offline -> Online then pushed that back and
    un-hid everything the shop had removed with "Remove out of stock" or "Remove
    expired" -- 24 rows reappeared in one round trip during testing. The server
    copy is authoritative for both fields, and this loop is already fetching
    exactly those documents.
    """
    from core import server_api as api

    token = _token_for_pull()
    if not token:
        return 0
    after_id = None
    fixed = 0
    while True:
        docs, _ = api.pull_collection(
            token, "medicines", after_id=after_id,
            include_deleted=False, limit=5000, timeout=90.0,
        )
        if not docs:
            break
        for d in docs:
            try:
                mid = int(d.get("id") or d.get("local_id") or 0)
                if mid <= 0:
                    continue
                qty = int(float(d.get("stock_qty") or 0))
                hidden = 1 if d.get("is_hidden") else 0
                conn.execute(
                    "UPDATE medicines SET stock_qty=?, is_hidden=? WHERE id=?",
                    (qty, hidden, mid),
                )
                fixed += 1
            except Exception:
                continue
        after_id = docs[-1].get("id") or docs[-1].get("local_id")
        if len(docs) < 5000:
            break
    _progress(progress_cb, f"Stock corrected for {fixed} medicine(s).")
    return fixed


def _token_for_pull() -> str:
    try:
        from core.server_crud import _token

        return _token()
    except Exception:
        return ""


@contextlib.contextmanager
def _preserved_sync_watermarks():
    """Run a block without letting it advance this machine's sync watermarks.

    sync_down_all bumps watermarks in a single machine-wide JSON file, not in the
    connection it was handed. That is right for a real download, but the backup
    captures into a THROWAWAY database -- so an hourly backup would keep telling
    the poller "you already have everything up to T" for changes that were never
    written to the store the app actually uses.
    """
    from core import sync_watermarks as wm

    path = wm._path()
    try:
        with open(path, "rb") as fh:
            saved = fh.read()
    except Exception:
        saved = None
    try:
        yield
    finally:
        try:
            if saved is None:
                if os.path.exists(path):
                    os.remove(path)
            else:
                with open(path, "wb") as fh:
                    fh.write(saved)
        except Exception:
            pass


def download_store_for_offline(
    *,
    progress_cb: ProgressCb = None,
    db_path: str | None = None,
    preserve_sync_state: bool = False,
) -> dict[str, Any]:
    """Fill the local SQLite from the server so Offline mode is a real fallback.

    Online mode is server-only, so this PC normally holds no store database at
    all. Switching to Offline therefore handed the user a brand-new EMPTY store:
    no medicines, no bills, nothing sellable. Android never had this problem --
    it keeps a local mirror while online and offers Pull / Full Sync buttons.

    The download machinery already existed here (server_live.sync_down_all, from
    before Online went server-only); it was simply never called. This wires it
    back up: create the schema if needed, then pull every collection down,
    replacing whatever was there so the copy is clean rather than merged.
    """
    # db_path lets a caller materialise the server store somewhere else --
    # the backup uses a throwaway file so it can capture Online data without
    # touching (or needing) the active store's database.
    path = (db_path or store_db_path() or "").strip()
    if not path:
        return {"ok": False, "error": "No active store selected."}

    _progress(progress_cb, "Preparing local store…")
    rows = 0
    guard = _preserved_sync_watermarks() if preserve_sync_state else contextlib.nullcontext()
    with guard, allow_local_store_db():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path)
        try:
            from core.db_setup import initialise

            initialise(conn)
            conn.commit()
            from core import server_live

            rows = int(
                server_live.sync_down_all(
                    conn,
                    replace_local=True,
                    progress_cb=progress_cb,
                )
                or 0
            )
            conn.commit()

            # sync_down_all replays purchases, sales and returns into local
            # stock ON TOP of the absolute stock_qty it already wrote from the
            # medicine doc, so every quantity came out double-counted: a
            # medicine at 200 on the server landed as 400 locally. The server
            # figure is authoritative, so stamp it back over the replayed one.
            _progress(progress_cb, "Correcting stock from server…")
            fixed = _restore_server_stock(conn, progress_cb=progress_cb)
            _mark_prepared_mirror(conn)
            conn.commit()
        finally:
            try:
                conn.close()
            except Exception:
                pass
    _progress(progress_cb, f"Offline copy ready ({rows} records).")
    return {"ok": True, "rows": rows, "db_path": path}


def ensure_online_server_only_ready(
    *,
    db_path: str | None = None,
    auto_wipe_empty: bool = True,
) -> dict[str, Any]:
    """
    Return migrate status for Online startup / settings.

    If DB exists but has no business rows and auto_wipe_empty, delete it.
    If DB has business data, caller must show Push | Delete popup.
    """
    try:
        from core.sync_prefs import is_online_mode

        if not is_online_mode():
            return {"online": False, "needs_migrate": False, "has_data": False}
    except Exception:
        return {"online": False, "needs_migrate": False, "has_data": False}

    path = db_path or store_db_path()
    if not local_store_db_exists(path):
        return {
            "online": True,
            "needs_migrate": False,
            "has_data": False,
            "server_only": True,
            "db_path": path,
        }

    # A copy WE downloaded for offline use holds the same rows as the server, so
    # the plain has-data check would flag it and offer to push it back (pointless)
    # or delete it (throwing away the preparation). Leave a clean mirror alone;
    # the moment real offline work lands in it, it stops being clean and prompts.
    if is_clean_prepared_mirror(path):
        return {
            "online": True,
            "needs_migrate": False,
            "has_data": True,
            "server_only": True,
            "offline_mirror": True,
            "db_path": path,
            "message": "Offline copy ready — nothing to migrate.",
        }

    has_data = local_store_has_business_data(path)
    if has_data:
        return {
            "online": True,
            "needs_migrate": True,
            "has_data": True,
            "server_only": False,
            "db_path": path,
            "message": (
                "This PC still has a local store database. "
                "Push it to the server (then it will be deleted), "
                "or delete it and use server data only."
            ),
        }

    try:
        from core.pharmacy_profile_io import salvage_profile_from_db_path

        salvage_profile_from_db_path(path)
    except Exception:
        pass

    if auto_wipe_empty:
        removed = delete_store_db_files(path)
        return {
            "online": True,
            "needs_migrate": False,
            "has_data": False,
            "server_only": True,
            "db_path": path,
            "removed_empty": removed,
        }

    return {
        "online": True,
        "needs_migrate": True,
        "has_data": False,
        "server_only": False,
        "db_path": path,
    }


def drive_restore_then_push_wipe(
    restore_fn: Callable[[], str],
    *,
    progress_cb: ProgressCb = None,
) -> dict[str, Any]:
    """
    Online Drive restore: restore_fn returns path to restored db (or active path),
    then push to server and delete local.
    """
    with allow_local_store_db():
        _progress(progress_cb, "Restoring backup into temporary local database…")
        path = restore_fn()
    return push_local_then_wipe(db_path=path, progress_cb=progress_cb)
