"""Settings > Data & System > Danger Zone > "Delete all data", on every kind of PC.

Before (v1.0.11) it dropped sixteen tables of THIS PC's store file and nothing else: payments,
returns, the cash register, drafts and the change bookkeeping survived; the server was never
told, so an offline-first PC pulled everything back on its next pull, and every other PC and
phone of the shop kept its copy.

Now:
  * Offline (no server): this PC's store file is emptied -- every table, not a list.
  * Online and offline-first: the SERVER deletes every business row of the store
    (server-live services/storeWipe.js, an export is written there first) and raises the
    store's wipe generation. Every device -- this one at once, the others on their next sync --
    sees the higher generation and clears its own copy, keeping its pairing and licence.
    Unsent changes made before the wipe are dropped: they belong to the deleted data.

Kept on a device: the licence (AppData, not the store file), the pairing, this PC's own
settings rows (printer, layout, one-time migration marks), the offline-first registration
(device number, id range, the server's last event number) and, unless "also the profile" was
chosen, the shop profile with its bill heading, bank details and pictures.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Optional

CONFIRM_WORD = "DELETE ALL TABLES"

# Settings rows that are shop DATA (they name parties, bills or doctors): always deleted.
SHOP_DATA_SETTING_PREFIXES = ("regular_meds:", "customer_gst:", "gst_filed:")
SHOP_DATA_SETTING_NAMES = ("doctor_margin_rules", "doctor_margin_rules_recalc")

# Tables a wipe never empties: bookkeeping that makes the store file work, and the rows handled
# one by one below (settings, the profile).
_KEEP_TABLES = {
    "sqlite_sequence", "settings", "pharmacy_profile", "shelf_settings", "app_meta",
    "of_meta", "of_flags", "of_id_counters",
}
# The shop profile: emptied only with also_profile.
_PROFILE_TABLES = ("pharmacy_profile", "shelf_settings")


def _tables(conn: sqlite3.Connection) -> list[str]:
    return [str(r[0]) for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\'"
    ).fetchall()]


def _setting_is_shop_data(name: str) -> bool:
    n = str(name or "")
    return n in SHOP_DATA_SETTING_NAMES or n.startswith(SHOP_DATA_SETTING_PREFIXES)


def _shop_wide(name: str) -> bool:
    try:
        from core.offline_first.schema import SHOP_WIDE_SETTING_NAMES, SHOP_WIDE_SETTING_PREFIXES
    except Exception:
        return False
    return name in SHOP_WIDE_SETTING_NAMES or name.startswith(SHOP_WIDE_SETTING_PREFIXES)


def business_tables(conn: sqlite3.Connection) -> list[str]:
    """Every table of the store file a wipe empties (all of them but _KEEP_TABLES)."""
    return [t for t in _tables(conn) if t not in _KEEP_TABLES]


def local_counts(conn: sqlite3.Connection, *, also_profile: bool = False) -> dict[str, int]:
    """Rows a local wipe would delete, per table (only tables that have any)."""
    out: dict[str, int] = {}
    for t in business_tables(conn):
        if t.startswith("of_"):
            continue                       # bookkeeping, reported as "unsent changes" instead
        try:
            n = int(conn.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0] or 0)
        except sqlite3.Error:
            continue
        if n:
            out[t] = n
    try:
        names = [str(r[0]) for r in conn.execute("SELECT name FROM settings").fetchall()]
        n = sum(1 for nm in names if _setting_is_shop_data(nm) or (also_profile and _shop_wide(nm)))
        if n:
            out["settings"] = n
    except sqlite3.Error:
        pass
    if also_profile:
        for t in _PROFILE_TABLES:
            try:
                n = int(conn.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0] or 0)
            except sqlite3.Error:
                continue
            if n:
                out[t] = n
    return out


def empty_store_file(conn: sqlite3.Connection, *, also_profile: bool = False,
                     reset_ids: bool = True) -> dict[str, int]:
    """Delete every business row of this store file, keeping its tables, views and triggers.

    The caller holds the write transaction and commits it. Offline-first capture
    is paused for the deletes, so they are never noted as this PC's own changes to send.
    ``reset_ids``: plain Offline starts ids again at 1; offline-first keeps its id counters, so
    an id another device may still hold is never handed out twice.
    """
    counts = local_counts(conn, also_profile=also_profile)
    has_flags = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='of_flags'").fetchone() is not None
    if has_flags:
        conn.execute("UPDATE of_flags SET v='0' WHERE k='capture'")
    try:
        emptied = []
        for t in business_tables(conn):
            conn.execute(f"DELETE FROM [{t}]")
            emptied.append(t)
        names = [str(r[0]) for r in conn.execute("SELECT name FROM settings").fetchall()] if \
            "settings" in _tables(conn) else []
        going = [n for n in names if _setting_is_shop_data(n) or (also_profile and _shop_wide(n))]
        for n in going:
            conn.execute("DELETE FROM settings WHERE name=?", (n,))
        if also_profile:
            for t in _PROFILE_TABLES:
                if t in _tables(conn):
                    conn.execute(f"DELETE FROM [{t}]")
                    emptied.append(t)
        if reset_ids and "sqlite_sequence" in {
                str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
            q = ",".join("?" * len(emptied))
            if emptied:
                conn.execute(f"DELETE FROM sqlite_sequence WHERE name IN ({q})", tuple(emptied))
    finally:
        if has_flags:
            conn.execute("UPDATE of_flags SET v='1' WHERE k='capture'")
    return counts


# ── offline-first: the server's wipe reaches this PC's copy ──────────────────────────────

def local_wipe_generation(conn: sqlite3.Connection) -> int:
    from core.offline_first.schema import meta_get

    try:
        return int(meta_get(conn, "wipe_generation", 0) or 0)
    except (sqlite3.Error, TypeError, ValueError):
        return 0


def apply_server_wipe(conn: sqlite3.Connection, info: dict) -> bool:
    """The server's store is on a later wipe than this offline-first copy: clear the copy.

    info: {wipe_generation, wipe_revision, wipe_also_profile}. Inside the caller's write
    transaction. Returns True when the copy was cleared (False: already on that wipe).

    Dropped with the data: everything noted, every unsent event and stock movement (they were
    made on the deleted records), the number blocks (the server's numbering starts again at 1)
    and this device's document counters. Kept: the registration -- install id, device number,
    id range, the id counters, and the server's last event number from this device, so the next
    event follows on with no gap.
    """
    from core.offline_first.schema import meta_get, meta_set

    try:
        gen = int((info or {}).get("wipe_generation") or 0)
        rev = int((info or {}).get("wipe_revision") or 0)
    except (TypeError, ValueError):
        return False
    if gen <= 0 or gen <= local_wipe_generation(conn):
        return False
    empty_store_file(conn, also_profile=bool((info or {}).get("wipe_also_profile")), reset_ids=False)
    # Events the server already acknowledged went with the server's own device_events; the next
    # event number must follow the server's last_seq (kept), never a deleted local row.
    server_last = int(meta_get(conn, "server_last_seq", 0) or 0)
    meta_set(conn, "server_last_seq", server_last)
    meta_set(conn, "pull_cursor", rev)
    meta_set(conn, "wipe_generation", gen)
    meta_set(conn, "wipe_revision", rev)
    return True


def server_wipe_info(page_or_status: Optional[dict]) -> dict:
    d = page_or_status if isinstance(page_or_status, dict) else {}
    return {
        "wipe_generation": int(d.get("wipe_generation") or 0),
        "wipe_revision": int(d.get("wipe_revision") or 0),
        "wipe_also_profile": bool(d.get("wipe_also_profile")),
    }


def note_screens_reload() -> None:
    """Tell every open screen that everything changed."""
    try:
        from core.sync_status import note_collection_change

        for col in ("medicines", "customers", "suppliers", "doctors", "sales", "purchases",
                    "customer_payments", "supplier_payments", "sales_returns", "purchase_returns",
                    "stock_disposals", "pending_orders", "cash_days", "cash_expenses", "settings",
                    "pharmacy_profile"):
            note_collection_change(col)
    except Exception:
        pass


# ── Online (server-only) PCs: their caches ────────────────────────────────────────────────

def _seen_path() -> str:
    from core.license_manager import _appdata_dir

    return os.path.join(_appdata_dir(), "store_wipe_seen.json")


def _active_key() -> str:
    try:
        from core.store_manager import get_active_store_key

        return get_active_store_key() or "default"
    except Exception:
        return "default"


def seen_generation(store_key: Optional[str] = None) -> Optional[int]:
    """The last wipe generation this Online PC acted on for the store (None: never recorded)."""
    try:
        with open(_seen_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        v = (data or {}).get(store_key or _active_key())
        return None if v is None else int(v)
    except Exception:
        return None


def remember_generation(gen: int, store_key: Optional[str] = None) -> None:
    path = _seen_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    data[store_key or _active_key()] = int(gen)
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except OSError:
        pass


def forget_online_caches() -> None:
    """Online PC: the server's store was emptied -- drop every list held in memory and the
    catalogue snapshot on disk (an empty catalogue never overwrites it, so the old one would
    come back on the next start without a network), then reload the open screens."""
    try:
        from core import online_catalog

        online_catalog.invalidate()
        try:
            path = online_catalog._snapshot_path()
            if os.path.isfile(path):
                os.remove(path)
        except Exception:
            pass
    except Exception:
        pass
    try:
        from core import store_live_refresh

        store_live_refresh.emit({"head_revision": 0, "changes": [], "full_refresh": True})
    except Exception:
        pass
    note_screens_reload()


def check_online_wipe(status: Optional[dict]) -> bool:
    """Online PC, from a /sync/status answer: act on a wipe it has not seen. A store that was
    wiped before this PC ever recorded a generation is treated as unseen once: the lists in
    memory reload anyway, and the catalogue snapshot on disk may be older than the wipe."""
    info = server_wipe_info(status)
    gen = info["wipe_generation"]
    seen = seen_generation()
    if gen <= (seen if seen is not None else 0):
        if seen is None:
            remember_generation(gen)
        return False
    forget_online_caches()
    remember_generation(gen)
    return True
