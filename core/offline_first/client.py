"""Server calls for offline-first sync (server-live/src/routes/syncV2.js)."""
from __future__ import annotations

from typing import Optional


def _token() -> str:
    from core import server_api as api

    return api.store_token_for_active()


def _call(method: str, path: str, body=None, *, timeout: float = 60.0) -> dict:
    from core import server_api as api

    res = api._request(method, path, body=body, token=_token(), timeout=timeout)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or f"{method} {path} failed")
    data = res.get("data")
    return data if isinstance(data, dict) else {"value": data}


def install_id() -> str:
    from core import server_api as api

    did = api._pc_device_id()
    if not did:
        raise RuntimeError("offline-first: this PC has no device id")
    return did


def register(app_version: str = "", device_name: str = "") -> dict:
    import platform

    return _call("POST", "/api/sync/v2/register", {
        "install_id": install_id(),
        "device_type": "pc",
        "device_name": device_name or platform.node()[:60],
        "app_version": app_version,
    })


def number_block(kind: str, fy_start_year: int, size: int) -> dict:
    return _call("POST", "/api/sync/v2/number-block", {
        "install_id": install_id(), "kind": kind, "fy_start_year": int(fy_start_year), "size": int(size),
    })


def push(events: list[dict]) -> dict:
    return _call("POST", "/api/sync/v2/push", {"install_id": install_id(), "events": events}, timeout=180.0)


def status() -> dict:
    from urllib.parse import quote

    return _call("GET", f"/api/sync/v2/status?install_id={quote(install_id())}")


def stock(ids: Optional[list[int]] = None) -> dict:
    return _call("POST", "/api/sync/v2/stock", {"ids": list(ids) if ids is not None else None}, timeout=120.0)


def changes(after: int, limit: int = 200) -> dict:
    from core import server_api as api

    return api.get_sync_changes_full(_token(), after=int(after), limit=int(limit))


def head_revision() -> int:
    from core import server_api as api

    data = api.get_sync_status(_token())
    return int((data or {}).get("head_revision") or 0)
