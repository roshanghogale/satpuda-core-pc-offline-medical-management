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


def number_head(fy_start_year: int, ranges: dict) -> dict:
    """Which of the numbers this device has left are already held on the server (read-only):
    ranges = {"sales": [(51, 200)], "purchases": [(11, 50)]} -> per kind {"head": N,
    "ranges": [{"from", "to", "taken"}]}, taken 0 when the range is free."""
    q = [f"fy={int(fy_start_year)}"]
    for kind in ("sales", "purchases"):
        spans = [f"{int(lo)}-{int(hi)}" for lo, hi in (ranges.get(kind) or [])][:10]
        if spans:
            q.append(f"{kind}={','.join(spans)}")
    return _call("GET", "/api/sync/v2/number-head?" + "&".join(q), timeout=30.0)


def push(events: list[dict], wipe_generation: Optional[int] = None) -> dict:
    """wipe_generation: the store's "Delete all data" this copy is on; the server refuses the
    push ("store_wiped") while the store is on a later one (core/store_wipe.py)."""
    body = {"install_id": install_id(), "events": events}
    if wipe_generation is not None:
        body["wipe_generation"] = int(wipe_generation)
    return _call("POST", "/api/sync/v2/push", body, timeout=180.0)


def is_store_wiped(exc: BaseException) -> bool:
    """The server refused a push because the store's data was deleted after this copy was made."""
    return "store_wiped" in str(exc)


def sync_status() -> dict:
    """GET /api/sync/status: head_revision and the store's wipe generation / revision."""
    from core import server_api as api

    return api.get_sync_status(_token()) or {}


def store_wipe_preview(also_profile: bool = False) -> dict:
    """What "Delete all data" would remove from the server's store, and its devices (read-only)."""
    return _call("GET", "/api/sync/v2/store-wipe/preview" + ("?also_profile=1" if also_profile else ""),
                 timeout=60.0)


def store_wipe(*, admin_token: str, confirm: str, also_profile: bool, request_id: str,
               store_id: Optional[str] = None) -> dict:
    """Delete every business row of THIS store on the server (server-live storeWipe.js). The
    store is the one in this PC's store token; admin_token is the vendor administrator's."""
    body = {"admin_token": admin_token, "confirm": confirm, "also_profile": bool(also_profile),
            "request_id": request_id}
    if store_id:
        body["store_id"] = store_id
    return _call("POST", "/api/sync/v2/store-wipe", body, timeout=600.0)


def push_as(install: str, events: list[dict]) -> dict:
    """A Main PC on the LAN sending a counter's handed-over events under the counter's own
    device id (core/lan/bridge.py). The server keeps one copy of each event, whoever brings it."""
    return _call("POST", "/api/sync/v2/push", {"install_id": str(install), "events": events}, timeout=180.0)


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
