"""What each page's summary shows -- the shop's choice, the same on every PC and the phone.

The owner turns a page's whole summary off (the strip of totals, counts and dues), or single
figures in it, per page (25 Oct 2026). One settings row, ``page_summary_prefs``:

    {"version": 1, "pages": {"sales_history": {"hidden": true, "off": ["profit"]}, ...}}

A page or a figure that is not named is shown, so a shop that never touched it sees today's
screens. Hiding only hides on screen: the numbers, prints and exports never change.

  Offline-first  the settings triggers note the row (core/offline_first/schema.py, v11) and the
                 worker sends and brings it like every other shop-wide row.
  Online         a save goes to the server's settings (PUT /api/sync/settings/kv) before this
                 PC keeps it; a read takes the server's row into the in-memory settings table
                 (remembered for a minute), so every PC and the phone show the same.
  Offline        this PC's table; a mode switch carries it (core/store_kv_carry.py).

The phone keeps pages and figures the PC does not have in the same row (and the other way
round): a save changes only what it was given and keeps every other name as it was.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Optional

log = logging.getLogger(__name__)

SETTING_NAME = "page_summary_prefs"
VERSION = 1

_TTL = 60.0
_FAIL_TTL = 30.0
_FETCH_TIMEOUT = 8
_KEY = re.compile(r"^[a-z0-9_]{1,40}$")
_lock = threading.Lock()
# store key -> (when asked, the server's value; "" = no row; None = could not be asked)
_cache: dict[str, tuple[float, Optional[str]]] = {}


def empty() -> dict[str, Any]:
    return {"version": VERSION, "pages": {}}


def clean(raw: Any) -> dict[str, Any]:
    """The row as kept: only well-formed page / figure names, a page that hides nothing dropped.
    Anything unreadable is "show everything" -- a broken row must never hide a summary."""
    data = raw
    if isinstance(raw, (str, bytes)):
        try:
            data = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return empty()
    if not isinstance(data, dict):
        return empty()
    pages_in = data.get("pages")
    if not isinstance(pages_in, dict):
        return empty()
    pages: dict[str, Any] = {}
    for page, entry in pages_in.items():
        page = str(page or "").strip()
        if not _KEY.match(page) or not isinstance(entry, dict):
            continue
        off_in = entry.get("off")
        off = sorted({f.strip() for f in off_in if isinstance(f, str) and _KEY.match(f.strip())}) \
            if isinstance(off_in, (list, tuple)) else []
        hidden = entry.get("hidden") is True
        if not hidden and not off:
            continue
        out: dict[str, Any] = {}
        if hidden:
            out["hidden"] = True
        if off:
            out["off"] = off
        pages[page] = out
    return {"version": VERSION, "pages": pages}


def merge(current: Any, change: Any) -> dict[str, Any]:
    """``change`` = {"pages": {page: {"hidden": bool, "on": {field: bool}}}} -- only the pages
    and figures named change; every other name in the row (the phone's own pages, figures this
    PC does not show) stays as it was."""
    cur = clean(current)
    pages = {p: {"hidden": bool(e.get("hidden")), "off": set(e.get("off") or [])}
             for p, e in cur["pages"].items()}
    ch_pages = (change or {}).get("pages") if isinstance(change, dict) else None
    if isinstance(ch_pages, dict):
        for page, entry in ch_pages.items():
            page = str(page or "").strip()
            if not _KEY.match(page) or not isinstance(entry, dict):
                continue
            slot = pages.setdefault(page, {"hidden": False, "off": set()})
            if "hidden" in entry:
                slot["hidden"] = bool(entry.get("hidden"))
            on = entry.get("on")
            if isinstance(on, dict):
                for field, shown in on.items():
                    field = str(field or "").strip()
                    if not _KEY.match(field):
                        continue
                    if shown:
                        slot["off"].discard(field)
                    else:
                        slot["off"].add(field)
    return clean({"pages": {p: {"hidden": e["hidden"], "off": sorted(e["off"])}
                            for p, e in pages.items()}})


def dumps(prefs: dict[str, Any]) -> str:
    return json.dumps(clean(prefs), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


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


def _fetch() -> Optional[str]:
    """The server's row ("" when it has none), or None when it could not be asked."""
    try:
        from core import server_api as api
        from core.server_live import _token
        from core.store_images import _kv_rows

        res = api._request("GET", f"/api/sync/settings/kv?prefix={SETTING_NAME}",
                           token=_token(), timeout=_FETCH_TIMEOUT)
        if not isinstance(res, dict) or not res.get("ok"):
            return None
        for row in _kv_rows(res.get("data")):
            if str(row.get("name") or "") == SETTING_NAME:
                value = row.get("value")
                return "" if value is None else str(value)
        return ""
    except Exception as exc:
        log.debug("page summary prefs fetch: %s", exc)
        return None


def refresh_from_server(conn, force: bool = False) -> bool:
    """Online: take the server's row into this PC's settings table (the in-memory shell and the
    per-store mirror, so it also holds with no internet). No row on the server leaves this PC's
    as it is. Does nothing in the other modes. Never raises."""
    if conn is None or not _online():
        return False
    key = _store_key()
    now = time.time()
    value: Optional[str]
    hit = None
    if not force:
        with _lock:
            hit = _cache.get(key)
    if hit is not None and now - hit[0] < (_TTL if hit[1] is not None else _FAIL_TTL):
        value = hit[1]
    else:
        value = _fetch()
        with _lock:
            _cache[key] = (time.time(), value)
    if value is None:
        return False
    if not value.strip():
        return True
    try:
        from core.desktop_settings_service import _setting_get, _setting_set

        if _setting_get(conn, SETTING_NAME, "") != value:
            _setting_set(conn, SETTING_NAME, value)
            conn.commit()
        return True
    except Exception as exc:
        log.debug("page summary prefs refresh: %s", exc)
        return False


def load(conn) -> dict[str, Any]:
    """The shop's choice (Online: the server's, remembered for a minute)."""
    if conn is None:
        return empty()
    try:
        refresh_from_server(conn)
    except Exception:
        pass
    try:
        from core.desktop_settings_service import _setting_get

        return clean(_setting_get(conn, SETTING_NAME, ""))
    except Exception:
        return empty()


def save(conn, change: Any) -> dict[str, Any]:
    """Change the pages / figures given (see merge) and keep the row. Online the server first:
    a refused save raises and changes nothing here."""
    if conn is None:
        raise RuntimeError("Database not open")
    from core.desktop_settings_service import _setting_set

    prefs = merge(load(conn), change)
    raw = dumps(prefs)
    if _online():
        from core import server_api as api
        from core.server_live import _token

        api.push_settings_kv(_token(), {"settings": [{"name": SETTING_NAME, "value": raw}]})
        with _lock:
            _cache[_store_key()] = (time.time(), raw)
    _setting_set(conn, SETTING_NAME, raw)
    conn.commit()
    return prefs
