"""The shop's alert thresholds and reorder default -- the same on every PC and the phone.

    low_stock_<type>          low-stock level, one per medicine type
    near_expiry_<type>        near-expiry window in months, one per medicine type
    customer_due_min_amount   the Alerts screen's customer-due filter: smallest due listed
    customer_due_min_days     ... and the fewest days a due must be old
    reorder_default_qty       the quantity a reorder line starts with

They used to belong to one PC: Offline and offline-first kept them in that PC's settings table,
Online in a per-PC mirror file (core/settings_mirror.py) that never reached the server. A shop
with two counters and a phone had three different sets of alerts (25 Oct 2026).

  Offline-first  the settings triggers note them (core/offline_first/schema.py, v10) and the
                 worker sends and brings them like every other shop-wide row.
  Online         a save goes to the server's settings (PUT /api/sync/settings/kv) before this
                 PC keeps it; reads take the server's rows into the in-memory settings table
                 (remembered for a minute), so every PC and the phone show the same.
  Offline        this PC's table; a mode switch carries them (core/store_kv_carry.py).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

log = logging.getLogger(__name__)

PREFIXES = ("low_stock_", "near_expiry_")
NAMES = ("customer_due_min_amount", "customer_due_min_days", "reorder_default_qty")

_TTL = 60.0
_FAIL_TTL = 30.0
_FETCH_TIMEOUT = 8
_lock = threading.Lock()
# store key -> (when asked, the rows or None when the server could not be asked)
_cache: dict[str, tuple[float, Optional[dict[str, str]]]] = {}


def is_alert_setting(name: Any) -> bool:
    n = str(name or "")
    return n in NAMES or n.startswith(PREFIXES)


def _online() -> bool:
    try:
        from core.sync_prefs import is_online_mode

        return bool(is_online_mode())
    except Exception:
        return False


def _store_key() -> str:
    try:
        from core.store_manager import get_active_store_key

        return str(get_active_store_key() or "").strip()
    except Exception:
        return ""


def forget() -> None:
    """Drop what was remembered (a store switch, a test)."""
    with _lock:
        _cache.clear()


def _fetch() -> Optional[dict[str, str]]:
    """The server's rows of these settings, or None when it could not be asked."""
    try:
        from urllib.parse import quote

        from core import server_api as api
        from core.server_live import _token
        from core.store_images import _kv_rows

        path = ("/api/sync/settings/kv?prefix=" + quote(",".join(PREFIXES), safe=",")
                + "&names=" + quote(",".join(NAMES), safe=","))
        res = api._request("GET", path, token=_token(), timeout=_FETCH_TIMEOUT)
        if not isinstance(res, dict) or not res.get("ok"):
            return None
        out: dict[str, str] = {}
        # An older server answers every row; only these are kept.
        for row in _kv_rows(res.get("data")):
            name = str(row.get("name") or "")
            if is_alert_setting(name):
                value = row.get("value")
                out[name] = "" if value is None else str(value)
        return out
    except Exception as exc:
        log.debug("shop alert settings fetch: %s", exc)
        return None


def server_rows(force: bool = False) -> Optional[dict[str, str]]:
    """Online: the server's rows (remembered for a minute); None when it could not be asked."""
    key = _store_key()
    now = time.time()
    if not force:
        with _lock:
            hit = _cache.get(key)
        if hit is not None:
            ttl = _TTL if hit[1] is not None else _FAIL_TTL
            if now - hit[0] < ttl:
                return hit[1]
    rows = _fetch()
    with _lock:
        _cache[key] = (time.time(), rows)
    return rows


def refresh_from_server(conn, force: bool = False) -> bool:
    """Online: take the server's rows into this PC's settings table (the in-memory shell and
    the per-store mirror, so they also hold with no internet). A row the server does not have
    is left as this PC has it. Does nothing in the other modes. Never raises."""
    if conn is None or not _online():
        return False
    rows = server_rows(force=force)
    if rows is None:
        return False
    try:
        from core.desktop_settings_service import _ensure_settings_table, _setting_set

        _ensure_settings_table(conn)
        changed = 0
        for name, value in rows.items():
            if not str(value).strip():
                continue
            cur = conn.execute("SELECT value FROM settings WHERE name=?", (name,)).fetchone()
            if cur is not None and str(cur[0] if cur[0] is not None else "") == value:
                continue
            _setting_set(conn, name, value)
            changed += 1
        if changed:
            conn.commit()
        return True
    except Exception as exc:
        log.debug("shop alert settings refresh: %s", exc)
        return False


def push_to_server(rows: dict[str, Any]) -> int:
    """Online: send these rows to the server in one request. Raises when it refuses or cannot
    be reached, so the save is refused instead of leaving this PC on values the rest of the
    shop never got. Returns rows sent (0 in the other modes)."""
    if not _online():
        return 0
    docs = [{"name": str(n), "value": "" if v is None else str(v)}
            for n, v in rows.items() if is_alert_setting(n)]
    if not docs:
        return 0
    from core import server_api as api
    from core.server_live import _token

    api.push_settings_kv(_token(), {"settings": docs})
    key = _store_key()
    with _lock:
        hit = _cache.get(key)
        if hit is not None and hit[1] is not None:
            merged = dict(hit[1])
            merged.update({d["name"]: d["value"] for d in docs})
            _cache[key] = (hit[0], merged)
    return len(docs)
