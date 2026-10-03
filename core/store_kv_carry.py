"""The per-customer settings a mode switch must carry: regular medicines and GSTINs.

Both are kept as store settings -- ``regular_meds:<id>`` and ``customer_gst:<id>`` -- in
the store's own ``settings`` table Offline and the server's key-value settings Online.
The switch carried the bills, medicines and customers but not these: going Offline the
server's lists stayed on the server (download_store_for_offline pulls with
replace_local, which leaves settings alone), and going Online a list made Offline was
never sent -- either way the shop's lists vanished the moment the mode changed
(3 Oct 2026). These two calls move them with the rest of the store.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

PREFIXES = ("regular_meds:", "customer_gst:")


def _carried(name: str) -> bool:
    return any(str(name or "").startswith(p) for p in PREFIXES)


def pull_to_local(conn) -> int:
    """Server -> this PC's settings table (before working Offline). Returns rows written."""
    from core import server_api as api
    from core.desktop_settings_service import _setting_set
    from core.server_live import _token
    from core.store_images import _kv_rows

    res = api._request("GET", "/api/sync/settings/kv", token=_token(), timeout=60)
    if not isinstance(res, dict) or not res.get("ok"):
        raise RuntimeError("server settings could not be read")
    n = 0
    for row in _kv_rows(res.get("data")):
        name = str(row.get("name") or "")
        if _carried(name) and str(row.get("value") or "").strip():
            _setting_set(conn, name, str(row.get("value")))
            n += 1
    conn.commit()
    return n


def push_from_local(conn) -> int:
    """This PC's settings table -> the server (when its Offline work goes Online). Returns rows sent."""
    from core.desktop_settings_service import _ensure_settings_table
    from core.server_live import push_settings_kv

    _ensure_settings_table(conn)
    n = 0
    for name, value in conn.execute(
        "SELECT name, value FROM settings WHERE name LIKE 'regular_meds:%' OR name LIKE 'customer_gst:%'"
    ).fetchall():
        if _carried(name) and str(value or "").strip():
            push_settings_kv(str(name), str(value))
            n += 1
    return n


def try_pull(conn) -> dict[str, Any]:
    try:
        return {"ok": True, "rows": pull_to_local(conn)}
    except Exception as exc:
        log.warning("carry settings down: %s", exc)
        return {"ok": False, "error": str(exc)}


def try_push(conn) -> dict[str, Any]:
    try:
        return {"ok": True, "rows": push_from_local(conn)}
    except Exception as exc:
        log.warning("carry settings up: %s", exc)
        return {"ok": False, "error": str(exc)}
