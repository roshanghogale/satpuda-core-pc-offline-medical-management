"""Is the open store wholesale? -- GET /api/wholesale/state, POST /api/wholesale/mode.

Wholesale is a SEPARATE store (owner, 18 Oct 2026; D:\\projects\\WHOLESALE_DESIGN.md, "Wholesale
is a SEPARATE store"): its own medicines, rates, purchases, sales and stock. So:

  mode      the OPEN store's kind. The server says ``store_kind`` ('retail' | 'wholesale') in
            each store's licence and pairing answer; core.license_manager keeps it per local
            store key (memory + wholesale_stores.dat). Never heard = retail.
  allowed   the switch is on offer: the open store is wholesale, or it is retail and the
            server named its ``wholesale_store`` (the admin turned wholesale on for it).
  switch    POST /api/wholesale/mode OPENS THE OTHER STORE (core/wholesale_store_switch);
            nothing is copied between the two.

The old ``business_mode`` settings row of the same store is no longer read or written; a
store that still has it is unaffected by it.

The rate margins a wholesale purchase pre-fills Rate A / B / C with are store settings:
``ws_margin_a`` / ``ws_margin_b`` / ``ws_margin_c`` (% over the purchase rate; 8 / 10 / 12).

``SATPUDA_WHOLESALE_OVERRIDE`` (the test copy and tests only): '1' makes the open store read as
wholesale, '0' as retail with no companion.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from core.wholesale_schema import RETAIL, WHOLESALE

log = logging.getLogger(__name__)

OVERRIDE_ENV = "SATPUDA_WHOLESALE_OVERRIDE"   # '1' / '0': the test copy and tests only
LIVE_FOR_S = 10 * 60
REFRESH_EVERY_S = 60
FIRST_WAIT_S = 1.0
_KV_TTL = 20.0

MARGIN_KEYS = {"A": "ws_margin_a", "B": "ws_margin_b", "C": "ws_margin_c"}
DEFAULT_MARGINS = {"A": 8.0, "B": 10.0, "C": 12.0}
# Store settings that belong to the whole shop (every device of THAT store).
SHOP_WIDE_NAMES: tuple[str, ...] = ()
SHOP_WIDE_PREFIXES = ("ws_margin_",)

_lock = threading.Lock()
_last_attempt = 0.0
_worker: threading.Thread | None = None
_kv_cache: dict[str, Any] = {"at": 0.0, "store": None, "rows": {}}


class NotAllowed(PermissionError):
    """There is no companion store to open (the server has not offered one)."""


# ── the server's word about the open store ───────────────────────────────────

def _sync_mode() -> str:
    try:
        from core.sync_prefs import get_sync_mode

        return str(get_sync_mode() or "")
    except Exception:
        return ""


def _online() -> bool:
    return _sync_mode() == "online"


def _store_key() -> str:
    try:
        from core.store_manager import get_active_store_key

        return str(get_active_store_key() or "")
    except Exception:
        return ""


def _store_name(store_key: str = "") -> str:
    try:
        from core.store_manager import get_active_display_name, list_stores

        if not store_key:
            return str(get_active_display_name() or "")
        for s in list_stores():
            if s.get("store_key") == store_key:
                return str(s.get("display_name") or store_key)
    except Exception:
        pass
    return store_key


def _refresh() -> None:
    """Ask the server for the open store's licence (Online and offline-first only: a plain
    Offline shop is never sent to the network for this)."""
    if _sync_mode() not in ("online", "offline_first"):
        return
    try:
        from core import license_manager as lm
        from core import server_api as api

        key = _store_key()
        data = api.get_license(api.store_token_for_active())
        # the store may have been switched while the answer was on its way
        if isinstance(data, dict) and key and key == _store_key():
            lm.remember_server_wholesale(data, store_key=key)
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


def _override() -> str:
    ov = os.environ.get(OVERRIDE_ENV, "").strip().lower()
    if ov in ("1", "true", "on", "yes"):
        return WHOLESALE
    if ov in ("0", "false", "off", "no"):
        return RETAIL
    return ""


def store_info(store_key: str = "", *, refresh: bool = False) -> dict[str, Any]:
    """{store_kind, enabled, wholesale_store, retail_store, source} of a store of this PC
    (default: the open one). Never raises."""
    key = store_key or _store_key()
    ov = _override()
    info = None
    try:
        from core import license_manager as lm

        if refresh and not store_key and not ov:
            _maybe_refresh()
        if key:
            info = lm.store_wholesale_info(key, max_age_s=LIVE_FOR_S)
    except Exception:
        info = None
    info = dict(info or {"enabled": False, "store_kind": RETAIL, "wholesale_store": None,
                         "retail_store": None, "source": "default"})
    if ov and (not store_key or store_key == _store_key()):
        info["store_kind"] = ov
        info["source"] = "override"
        info["wholesale_store"] = None
        if ov == RETAIL:
            info["retail_store"] = None
    return info


def companion(info: dict[str, Any]) -> dict | None:
    """The store the switch opens from this one: a retail store's wholesale companion, or a
    wholesale store's retail one."""
    if info.get("store_kind") == WHOLESALE:
        return info.get("retail_store") or None
    return info.get("wholesale_store") or None


def allowed_status(*, refresh: bool = True) -> dict[str, Any]:
    """{allowed, source}: the switch is on offer. Never raises."""
    info = store_info(refresh=refresh)
    allowed = info.get("store_kind") == WHOLESALE or bool(info.get("wholesale_store"))
    return {"allowed": allowed, "source": info.get("source") or "default"}


def is_allowed(*, refresh: bool = True) -> bool:
    return bool(allowed_status(refresh=refresh)["allowed"])


def current_mode(conn=None, *, refresh: bool = False) -> str:
    """The open store's kind: 'wholesale' only for a wholesale store."""
    return WHOLESALE if store_info(refresh=refresh).get("store_kind") == WHOLESALE else RETAIL


def is_wholesale(conn=None, *, refresh: bool = False) -> bool:
    return current_mode(conn, refresh=refresh) == WHOLESALE


def state(conn) -> dict[str, Any]:
    """{ok, allowed, mode, store_name, companion_name, source, margins}."""
    info = store_info(refresh=True)
    mode = WHOLESALE if info.get("store_kind") == WHOLESALE else RETAIL
    other = companion(info)
    return {
        "ok": True,
        "allowed": mode == WHOLESALE or bool(info.get("wholesale_store")),
        "mode": mode,
        "store_name": _store_name(),
        "companion_name": str((other or {}).get("store_name") or ""),
        "source": info.get("source") or "default",
        "margins": margins(conn) if conn is not None else dict(DEFAULT_MARGINS),
    }


def set_mode(conn, mode: Any) -> dict[str, Any]:
    """Open the store of that kind (core/wholesale_store_switch.switch_to)."""
    from core.wholesale_store_switch import switch_to

    return switch_to(mode)


# ── the store settings (margins) ─────────────────────────────────────────────

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

    names = tuple(MARGIN_KEYS.values())
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
    offline-first trigger sends to the server (and so to the store's other devices)."""
    from core.desktop_settings_service import _setting_set

    if _online():
        from core.server_live import push_settings_kv

        push_settings_kv(name, value)
        rows = dict(_kv_cache.get("rows") or {})
        rows[name] = value
        _kv_cache.update(rows=rows)
    _setting_set(conn, name, value)
    conn.commit()


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
