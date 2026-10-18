"""HTTP routes of wholesale mode for core/desktop_api.py (kept here so the router gets two hooks).

GET  /api/wholesale/state                    {ok, allowed, mode, store_name, companion_name, source,
                                              margins}
GET  /api/wholesale/last-rates?name=&medicine_id=
                                             {ok, found, purchase_rate, rate_a, rate_b, rate_c}
POST /api/wholesale/mode     {mode}          OPENS THE OTHER STORE (core/wholesale_store_switch):
                                             {ok, reload, mode, store_name[, running, progress]},
                                             403 {ok:false, code:'no_companion'}, 409 'busy'
POST /api/wholesale/margins  {A, B, C}       {ok, margins}
"""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

GET_ROUTES = ("/api/wholesale/state", "/api/wholesale/last-rates")
POST_ROUTES = ("/api/wholesale/mode", "/api/wholesale/margins")


def handle_get(raw_path: str, conn) -> Optional[tuple[int, dict[str, Any]]]:
    path = urlparse(raw_path).path.rstrip("/")
    if path not in GET_ROUTES:
        return None
    from core import wholesale_mode as wm

    if path == "/api/wholesale/state":
        return 200, wm.state(conn)
    if conn is None:
        return 503, {"ok": False, "error": "Database not open"}
    qs = parse_qs(urlparse(raw_path).query)
    from core.wholesale_rates import last_rates

    try:
        mid = int((qs.get("medicine_id") or ["0"])[0] or 0)
    except ValueError:
        mid = 0
    out = last_rates(conn, name=(qs.get("name") or [""])[0], medicine_id=mid)
    return 200, {"ok": True, **out}


def handle_post(path: str, body: dict, conn) -> Optional[tuple[int, dict[str, Any]]]:
    path = path.rstrip("/")
    if path not in POST_ROUTES:
        return None
    from core import wholesale_mode as wm

    body = body if isinstance(body, dict) else {}
    if path == "/api/wholesale/mode":
        # A switch of STORE: it does not need the open store's database.
        from core import wholesale_store_switch as sw

        try:
            return 200, sw.switch_to(body.get("mode"))
        except sw.NoCompanion as exc:
            return 403, {"ok": False, "code": "no_companion", "error": str(exc)}
        except sw.SwitchBusy as exc:
            return 409, {"ok": False, "code": "busy", "error": str(exc)}
        except ValueError as exc:
            return 400, {"ok": False, "error": str(exc)}
        except Exception as exc:
            return 500, {"ok": False, "error": f"Could not open the other store: {exc}"}
    if conn is None:
        return 503, {"ok": False, "error": "Database not open"}
    try:
        return 200, {"ok": True, "margins": wm.set_margins(conn, body.get("margins") or body)}
    except (ValueError, TypeError) as exc:
        return 400, {"ok": False, "error": str(exc)}
    except Exception as exc:
        return 500, {"ok": False, "error": f"Could not save: {exc}"}
