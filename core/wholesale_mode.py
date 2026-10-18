"""Is this store wholesale right now? -- GET /api/wholesale/state, POST /api/wholesale/mode.

Two things decide it (D:\\projects\\WHOLESALE_DESIGN.md):

  allowed   the admin panel's per-store switch (stores.wholesale_enabled). The server sends
            it in the licence and the pairing answer; core.license_manager keeps the last
            answer in memory and in wholesale.dat. Never heard = not allowed.
  mode      the store setting ``business_mode`` ('retail' | 'wholesale'), a row in the
            store's settings key/value table, so it reaches the server and the store's other
            devices like every other shop-wide setting (offline-first: the settings trigger,
            core/offline_first/schema.py; Online: PUT /api/sync/settings/kv).

The store reads as retail whenever the server does not allow wholesale -- its wholesale data
(rates, parties, receipt numbers) stays where it is and comes back when it is allowed again.

The rate margins a wholesale purchase pre-fills Rate A / B / C with are store settings too:
``ws_margin_a`` / ``ws_margin_b`` / ``ws_margin_c`` (% over the purchase rate; 8 / 10 / 12).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from core.wholesale_schema import BUSINESS_MODE_KEY, RETAIL, WHOLESALE, get_business_mode

log = logging.getLogger(__name__)

OVERRIDE_ENV = "SATPUDA_WHOLESALE_OVERRIDE"   # '1' / '0': the test copy and tests only
LIVE_FOR_S = 10 * 60
REFRESH_EVERY_S = 60
FIRST_WAIT_S = 1.0
_KV_TTL = 20.0

MARGIN_KEYS = {"A": "ws_margin_a", "B": "ws_margin_b", "C": "ws_margin_c"}
DEFAULT_MARGINS = {"A": 8.0, "B": 10.0, "C": 12.0}
# Store settings that belong to the whole shop (every device): sync + mode-switch carry.
SHOP_WIDE_NAMES = (BUSINESS_MODE_KEY,)
SHOP_WIDE_PREFIXES = ("ws_margin_",)

_lock = threading.Lock()
_last_attempt = 0.0
_worker: threading.Thread | None = None
_kv_cache: dict[str, Any] = {"at": 0.0, "store": None, "rows": {}}


class NotAllowed(PermissionError):
    """The server has not switched wholesale on for this store."""


# ── allowed (the server's word) ──────────────────────────────────────────────

def _sync_mode() -> str:
    try:
        from core.sync_prefs import get_sync_mode

        return str(get_sync_mode() or "")
    except Exception:
        return ""


def _online() -> bool:
    return _sync_mode() == "online"


def _refresh() -> None:
    """Ask the server for this store's licence (Online and offline-first only: a plain
    Offline shop is never sent to the network for this)."""
    if _sync_mode() not in ("online", "offline_first"):
        return
    try:
        from core import license_manager as lm
        from core import server_api as api

        data = api.get_license(api.store_token_for_active())
        if isinstance(data, dict):
            lm.remember_server_wholesale(data)
    except Exception as exc:
        log.debug("wholesale licence refresh: %s", exc)


def _maybe_refresh(*, now: float | None = None) -> None:
    global _last_attempt, _worker
    now = time.time() if now is None else now
    with _lock:
        if _worker is not None and _worker.is_alive():
            return
        if now - _last_attempt < REFRESH_EVERY_S:
            return
        first = _last_attempt == 0.0
        _last_attempt = now
        _worker = threading.Thread(target=_refresh, name="wholesale-switch-refresh", daemon=True)
        _worker.start()
        worker = _worker
    if first:
        worker.join(FIRST_WAIT_S)


def allowed_status(*, refresh: bool = True) -> dict[str, Any]:
    """{allowed, source}: override | server | cache | default. Never raises."""
    ov = os.environ.get(OVERRIDE_ENV, "").strip().lower()
    if ov in ("1", "true", "on", "yes"):
        return {"allowed": True, "source": "override"}
    if ov in ("0", "false", "off", "no"):
        return {"allowed": False, "source": "override"}
    try:
        from core import license_manager as lm

        if refresh:
            _maybe_refresh()
        live = lm.live_server_wholesale(LIVE_FOR_S)
        if live is not None:
            return {"allowed": bool(live), "source": "server"}
        cached = lm.read_cached_wholesale()
        if cached is not None:
            return {"allowed": bool(cached), "source": "cache"}
    except Exception:
        pass
    return {"allowed": False, "source": "default"}


def is_allowed(*, refresh: bool = True) -> bool:
    return bool(allowed_status(refresh=refresh)["allowed"])


# ── the store settings (mode, margins) ───────────────────────────────────────

def _store_key() -> str:
    try:
        from core.store_manager import get_active_store_key

        return str(get_active_store_key() or "")
    except Exception:
        return ""


def _server_kv(force: bool = False) -> dict[str, str]:
    """Online: the store's wholesale settings rows from the server (cached briefly)."""
    now = time.time()
    store = _store_key()
    if (not force and _kv_cache["at"] and _kv_cache["store"] == store
            and now - _kv_cache["at"] < _KV_TTL):
        return _kv_cache["rows"]
    from urllib.parse import quote

    from core import server_api as api
    from core.server_live import _token
    from core.store_images import _kv_rows

    names = (BUSINESS_MODE_KEY, *MARGIN_KEYS.values())
    res = api._request("GET", "/api/sync/settings/kv?names=" + quote(",".join(names), safe=","),
                       token=_token(), timeout=15)
    if not isinstance(res, dict) or not res.get("ok"):
        raise RuntimeError("store settings could not be read from the server")
    rows = {str(r.get("name") or ""): str(r.get("value") if r.get("value") is not None else "")
            for r in _kv_rows(res.get("data")) if str(r.get("name") or "") in names}
    _kv_cache.update(at=now, store=store, rows=rows)
    return rows


def _kv_get(conn, name: str, default: str = "") -> str:
    if _online():
        try:
            rows = _server_kv()
            if name in rows:
                return rows[name]
        except Exception as exc:
            log.debug("wholesale kv read %s: %s", name, exc)
    try:
        row = conn.execute("SELECT value FROM settings WHERE name=?", (name,)).fetchone()
        return str(row[0]) if row and row[0] is not None else default
    except Exception:
        return default


def _kv_set(conn, name: str, value: str) -> None:
    """Store-wide: Online straight to the server; otherwise the settings table, which the
    offline-first trigger sends to the server (and so to the other devices)."""
    from core.desktop_settings_service import _setting_set

    if _online():
        from core.server_live import push_settings_kv

        push_settings_kv(name, value)
        rows = dict(_kv_cache.get("rows") or {})
        rows[name] = value
        _kv_cache.update(rows=rows)
    _setting_set(conn, name, value)
    conn.commit()


def stored_mode(conn) -> str:
    """The setting as saved, whatever the server allows."""
    if _online():
        raw = _kv_get(conn, BUSINESS_MODE_KEY, "")
        return WHOLESALE if raw.strip().lower() == WHOLESALE else RETAIL
    return get_business_mode(conn)


def current_mode(conn, *, refresh: bool = True) -> str:
    """'wholesale' only when the store is set to it AND the server allows it."""
    if not is_allowed(refresh=refresh):
        return RETAIL
    return stored_mode(conn)


def is_wholesale(conn, *, refresh: bool = False) -> bool:
    return current_mode(conn, refresh=refresh) == WHOLESALE


def state(conn) -> dict[str, Any]:
    st = allowed_status()
    stored = RETAIL
    try:
        stored = stored_mode(conn)
    except Exception:
        pass
    return {
        "ok": True,
        "allowed": bool(st["allowed"]),
        "mode": stored if st["allowed"] else RETAIL,
        "stored_mode": stored,
        "source": st["source"],
        "margins": margins(conn),
    }


def set_mode(conn, mode: Any) -> dict[str, Any]:
    m = str(mode or "").strip().lower()
    if m not in (RETAIL, WHOLESALE):
        raise ValueError("mode must be 'retail' or 'wholesale'")
    if not is_allowed(refresh=True):
        raise NotAllowed("Wholesale is not switched on for this store. Ask Satpuda to enable it.")
    _kv_set(conn, BUSINESS_MODE_KEY, m)
    return state(conn)


def margins(conn) -> dict[str, float]:
    out: dict[str, float] = {}
    for tier, key in MARGIN_KEYS.items():
        raw = _kv_get(conn, key, "")
        try:
            v = float(raw)
        except (TypeError, ValueError):
            v = DEFAULT_MARGINS[tier]
        out[tier] = v if 0 <= v < 1000 else DEFAULT_MARGINS[tier]
    return out


def set_margins(conn, values: dict) -> dict[str, float]:
    for tier, key in MARGIN_KEYS.items():
        raw = (values or {}).get(tier, (values or {}).get(tier.lower()))
        if raw in (None, ""):
            continue
        v = float(raw)
        if not 0 <= v < 1000:
            raise ValueError(f"Rate {tier} margin must be between 0 and 999 %")
        _kv_set(conn, key, f"{v:g}")
    return margins(conn)


def suggested_rates(purchase_rate: Any, marg: dict[str, float] | None = None) -> dict[str, float]:
    """Rate A / B / C = purchase rate + margin %, to 2 decimals (0 when no purchase rate)."""
    try:
        pr = float(purchase_rate or 0)
    except (TypeError, ValueError):
        pr = 0.0
    marg = marg or DEFAULT_MARGINS
    if pr <= 0:
        return {"A": 0.0, "B": 0.0, "C": 0.0}
    return {t: round(pr * (1 + float(marg.get(t, DEFAULT_MARGINS[t])) / 100.0), 2) for t in ("A", "B", "C")}


def is_shop_wide_setting(name: Any) -> bool:
    n = str(name or "")
    return n in SHOP_WIDE_NAMES or any(n.startswith(p) for p in SHOP_WIDE_PREFIXES)
