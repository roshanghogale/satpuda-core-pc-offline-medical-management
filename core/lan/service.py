"""Starting the LAN role of this PC, and the engine's /api/lan/* routes (Settings screen).

GET  /api/lan/status          role, this PC's addresses, the pairing code, paired counters /
                              whether the Main PC is reachable
POST /api/lan/role            {role: off|main|counter, port?}
POST /api/lan/pairing-code    Main PC: a new 6-digit code (30 minutes)
POST /api/lan/forget          Main PC: {device_id} -- that counter can no longer connect
POST /api/lan/discover        Counter: Main PCs answering on the LAN
POST /api/lan/pair            Counter: {code, host?, port?} (no host: found on the LAN)
POST /api/lan/unpair          Counter: forget the Main PC
POST /api/lan/sync-now        Counter: hand over / copy now
POST /api/lan/firewall        Main PC: one Windows prompt that opens the two ports to the LAN
"""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from core.lan import config

_lock = threading.RLock()
_state: dict[str, Any] = {
    "role": config.ROLE_OFF,
    "main": None,
    "counter": None,
    "engine_port": 8765,
    "conn": lambda: None,
    "path": lambda: "",
    "error": "",
}


def role() -> str:
    return _state["role"]


def start(*, engine_port: int, conn: Callable[[], Any], db_path: Callable[[], str]) -> None:
    """Called once the engine listens (core/desktop_api.start_desktop_api)."""
    with _lock:
        _state.update({"engine_port": int(engine_port), "conn": conn, "path": db_path})
        _start_role()


def _start_role() -> None:
    _stop_role()
    cfg = config.load()
    r = cfg["role"]
    _state["role"] = r
    _state["error"] = ""
    if r == config.ROLE_MAIN:
        from core.lan.main_server import MainServer

        srv = MainServer(port=cfg["port"], discovery_port=cfg["discovery_port"],
                         engine_port=lambda: _state["engine_port"], db_path=lambda: _state["path"]() or "")
        try:
            srv.start()
            _state["main"] = srv
            _state["error"] = srv.error
        except OSError as exc:
            _state["error"] = srv.error or str(exc)
    elif r == config.ROLE_COUNTER:
        from core.lan.counter import CounterLink

        link = CounterLink(conn=lambda: _state["conn"](), db_path=lambda: _state["path"]() or "",
                           discovery_port=cfg["discovery_port"])
        link.start()
        _state["counter"] = link


def _stop_role() -> None:
    srv = _state.get("main")
    if srv is not None:
        srv.stop()
    _state["main"] = None
    link = _state.get("counter")
    if link is not None:
        link.stop()
    _state["counter"] = None


def stop() -> None:
    with _lock:
        _stop_role()
        _state["role"] = config.ROLE_OFF
        try:
            from core.lan import bridge

            bridge.close_all()
        except Exception:
            pass


def restart() -> None:
    with _lock:
        _start_role()


# ----------------------------------------------------------------------------- status

def brief() -> dict:
    """For /api/sync/status, which every screen polls: the banner and how fast to poll."""
    r = _state["role"]
    out: dict = {"role": r}
    link = _state.get("counter")
    if r == config.ROLE_COUNTER and link is not None:
        st = link.status()
        out.update({"main_reachable": st["main_reachable"], "banner": st["banner"], "paired": st["paired"],
                    "main_name": st["main_name"]})
    elif r == config.ROLE_MAIN:
        from core.lan import pairing

        devs = pairing.devices_status()
        out.update({"counters_connected": sum(1 for d in devs if d["connected"]), "counters": len(devs),
                    "banner": _state.get("error") or ""})
    return out


def status() -> dict:
    from core.lan.counter import local_addresses

    cfg = config.load()
    out: dict = {"ok": True, "role": _state["role"], "configured_role": cfg["role"], "port": cfg["port"],
                 "discovery_port": cfg["discovery_port"], "addresses": local_addresses(),
                 "error": _state.get("error") or ""}
    try:
        from core.lan.main_server import pc_name, shop_name

        out["pc_name"] = pc_name()
        out["shop"] = shop_name()
    except Exception:
        pass
    if _state["role"] == config.ROLE_MAIN:
        from core.lan import firewall, pairing

        out["pairing"] = pairing.current_code()
        out["devices"] = pairing.devices_status()
        out["listening"] = _state.get("main") is not None
        try:
            out["firewall"] = firewall.rules_present()
        except Exception:
            out["firewall"] = None
        try:
            from core.lan import bridge

            path = _state["path"]()
            if path:
                conn = sqlite3.connect(path, timeout=5)
                try:
                    out.update(bridge.relay_counts(conn))
                finally:
                    conn.close()
        except Exception:
            pass
    link = _state.get("counter")
    if _state["role"] == config.ROLE_COUNTER and link is not None:
        out["counter"] = link.status()
    return out


# ----------------------------------------------------------------------------- routes

def _set_role(body: dict) -> tuple[int, dict]:
    r = str(body.get("role") or "").strip().lower()
    if r not in config.ROLES:
        return 400, {"ok": False, "error": "Pick Off, Main PC or Counter PC."}
    port = body.get("port")

    def change(cfg):
        cfg["role"] = r
        if port:
            try:
                p = int(port)
                if 1024 <= p <= 65000:
                    cfg["port"] = p
                    cfg["discovery_port"] = p + 1
            except (TypeError, ValueError):
                pass
        return cfg

    config.update(change)
    restart()
    if r == config.ROLE_MAIN:
        from core.lan import pairing

        if not pairing.current_code().get("code"):
            pairing.new_code()
    return 200, status()


def handle_api(method: str, path: str, body: dict) -> tuple[int, dict]:
    if path == "/api/lan/status":
        return 200, status()
    if method != "POST":
        return 405, {"ok": False, "error": "POST"}
    if path == "/api/lan/role":
        return _set_role(body)
    if path == "/api/lan/pairing-code":
        if _state["role"] != config.ROLE_MAIN:
            return 409, {"ok": False, "error": "Set this PC as the Main PC first."}
        from core.lan import pairing

        return 200, {"ok": True, **pairing.new_code()}
    if path == "/api/lan/forget":
        from core.lan import pairing

        ok = pairing.remove_device(str(body.get("device_id") or ""))
        pairing.forget_cache()
        return (200 if ok else 404), {"ok": ok}
    if path == "/api/lan/discover":
        from core.lan.counter import discover

        cfg = config.load()
        return 200, {"ok": True, "found": discover(cfg["discovery_port"])}
    if path == "/api/lan/pair":
        from core.lan.counter import discover, pair

        cfg = config.load()
        code = "".join(ch for ch in str(body.get("code") or "") if ch.isdigit())
        if len(code) != 6:
            return 400, {"ok": False, "error": "The pairing code is the 6 digits shown on the Main PC."}
        host = str(body.get("host") or "").strip()
        try:
            port = int(body.get("port") or cfg["port"])
        except (TypeError, ValueError):
            port = cfg["port"]
        if ":" in host:
            host, _, p = host.rpartition(":")
            try:
                port = int(p)
            except ValueError:
                pass
        if not host:
            found = [m for m in discover(cfg["discovery_port"]) if m["same_shop"]]
            if not found:
                return 404, {"ok": False, "error": "No Main PC of this shop answered on the LAN. Type its "
                                                   "address (shown on the Main PC) instead."}
            if len(found) > 1:
                return 409, {"ok": False, "error": "More than one Main PC answered; pick one.", "found": found}
            host, port = found[0]["host"], found[0]["port"]
        res = pair(host, port, code)
        if res.get("ok"):
            restart()
            return 200, {**res, **status()}
        return 400, res
    if path == "/api/lan/unpair":
        def change(cfg):
            cfg["counter"] = {}
            return cfg

        config.update(change)
        restart()
        return 200, status()
    if path == "/api/lan/sync-now":
        link = _state.get("counter")
        if link is None:
            return 409, {"ok": False, "error": "This PC is not a Counter PC."}
        try:
            return 200, link.sync_now()
        except Exception as exc:
            return 503, {"ok": False, "error": str(exc)}
    if path == "/api/lan/firewall":
        from core.lan import firewall

        cfg = config.load()
        return 200, firewall.ask_to_allow(cfg["port"], cfg["discovery_port"])
    return 404, {"ok": False, "error": f"{path} not found"}


# ----------------------------------------------------------------------------- engine hooks

ORIGIN_HEADER = "X-Satpuda-Lan-Origin"


def engine_pre(handler, method: str) -> bool:
    """The engine's first step for every request. True when answered here."""
    path = urlparse(handler.path).path.rstrip("/") or "/"
    if path.startswith("/api/lan/"):
        body: dict = {}
        if method in ("POST", "PUT"):
            n = int(handler.headers.get("Content-Length") or 0)
            try:
                parsed = json.loads(handler.rfile.read(n).decode("utf-8") or "{}") if n > 0 else {}
                body = parsed if isinstance(parsed, dict) else {}
            except ValueError:
                body = {}
        try:
            status, payload = handle_api(method, path, body)
        except Exception as exc:
            status, payload = 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        from core.lan.counter import CounterLink

        CounterLink._reply(handler, status, "application/json; charset=utf-8",
                           json.dumps(payload, default=str).encode("utf-8"))
        return True
    link = _state.get("counter")
    if link is not None and _state["role"] == config.ROLE_COUNTER:
        return bool(link.handle(handler, method, path))
    return False


_write_lock = threading.RLock()


class _NoGuard:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def write_guard(method: str, raw_path: str):
    """A Main PC serves several cashiers on one store connection: their saves go one at a
    time (the sales saves already did; now purchases, returns and payments too)."""
    if _state["role"] != config.ROLE_MAIN or method not in ("POST", "PUT"):
        return _NoGuard()
    from core.lan.hints import collections_for_write

    path = urlparse(raw_path).path.rstrip("/") or "/"
    if "/print" in path:
        return _NoGuard()          # its save takes the sales lock; the printing need not wait
    if collections_for_write(path) or "/autosave" in path:
        return _write_lock
    return _NoGuard()


def total_changes() -> int:
    try:
        conn = _state["conn"]()
        return int(conn.total_changes) if conn is not None else 0
    except Exception:
        return 0


def engine_post(handler, method: str, before: int) -> None:
    """After a write on the Main PC: the counters' screens are told, and the sync loop is woken
    so the change is ready for the counters' copies (and the cloud) at once."""
    if _state["role"] != config.ROLE_MAIN or method not in ("POST", "PUT"):
        return
    status = int(getattr(handler, "_lan_status", 0) or 0)
    if not (200 <= status < 300):
        return
    if total_changes() == before:
        return
    from core.lan.hints import collections_for_write

    path = urlparse(handler.path).path.rstrip("/") or "/"
    cols = collections_for_write(path)
    if not cols:
        return
    try:
        from core.sync_status import note_screen_change

        note_screen_change(cols, origin=str(handler.headers.get(ORIGIN_HEADER) or ""))
    except Exception:
        pass
    try:
        from core.offline_first import worker

        worker.kick()
    except Exception:
        pass
