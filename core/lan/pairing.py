"""Main PC side of pairing: the 6-digit code shown on screen, and the device tokens.

A counter types the code once; the Main PC answers with a long random token and keeps only its
SHA-256. Every LAN request must carry the token (header X-Satpuda-Lan-Token). The code lives
for CODE_TTL seconds and is thrown away after MAX_TRIES wrong answers, so it cannot be guessed
from the LAN; a new code is one click in Settings.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from core.lan import config

CODE_TTL = 30 * 60
MAX_TRIES = 10
SEEN_PERSIST_EVERY = 60.0

_lock = threading.Lock()
_code: dict[str, Any] = {"code": "", "expires": 0.0, "tries": 0}
_seen: dict[str, dict] = {}          # device id -> {"at": epoch, "ip": str}
_seen_saved_at = 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def token_hash(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def new_code() -> dict:
    with _lock:
        _code.update({"code": f"{secrets.randbelow(1_000_000):06d}", "expires": time.time() + CODE_TTL,
                      "tries": 0})
        return current_code_locked()


def current_code_locked() -> dict:
    if not _code["code"] or time.time() > _code["expires"]:
        return {"code": "", "expires_in": 0}
    return {"code": _code["code"], "expires_in": int(_code["expires"] - time.time())}


def current_code() -> dict:
    with _lock:
        return current_code_locked()


def set_code_for_tests(code: str) -> None:
    with _lock:
        _code.update({"code": str(code), "expires": time.time() + CODE_TTL, "tries": 0})


def clear_code() -> None:
    with _lock:
        _code.update({"code": "", "expires": 0.0, "tries": 0})


def check_code(code: str) -> Optional[str]:
    """None when the code is right (and it is used up); else the reason, in plain words."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    with _lock:
        if not _code["code"] or time.time() > _code["expires"]:
            return "No pairing code is open on the Main PC. Open Settings > Data & System > This PC on the LAN there and press New pairing code."
        if not hmac.compare_digest(code, _code["code"]):
            _code["tries"] += 1
            if _code["tries"] >= MAX_TRIES:
                _code.update({"code": "", "expires": 0.0, "tries": 0})
                return "Too many wrong codes. Make a new pairing code on the Main PC."
            return "That pairing code is not right. Check the 6 digits on the Main PC."
        _code.update({"code": "", "expires": 0.0, "tries": 0})
        return None


def add_device(name: str, install_id: str, ip: str = "") -> tuple[str, dict]:
    """A new counter: returns (token, device record). A counter paired again (same install id)
    replaces its old token."""
    token = secrets.token_urlsafe(32)
    dev = {
        "id": uuid.uuid4().hex[:12],
        "name": str(name or "Counter PC")[:60],
        "install_id": str(install_id or "")[:80],
        "token_hash": token_hash(token),
        "paired_at": _now_iso(),
        "last_seen": _now_iso(),
        "last_ip": str(ip or ""),
    }

    def change(cfg):
        devices = [d for d in cfg["main"]["devices"]
                   if not (dev["install_id"] and d.get("install_id") == dev["install_id"])]
        devices.append(dev)
        cfg["main"]["devices"] = devices
        return cfg

    config.update(change)
    return token, dev


def remove_device(device_id: str) -> bool:
    removed = {"n": 0}

    def change(cfg):
        before = len(cfg["main"]["devices"])
        cfg["main"]["devices"] = [d for d in cfg["main"]["devices"] if d.get("id") != device_id]
        removed["n"] = before - len(cfg["main"]["devices"])
        return cfg

    config.update(change)
    with _lock:
        _seen.pop(device_id, None)
    return bool(removed["n"])


_devices_cache: dict[str, Any] = {"at": 0.0, "by_hash": {}}


def _devices_by_hash() -> dict[str, dict]:
    now = time.time()
    if now - _devices_cache["at"] > 2.0:
        cfg = config.load()
        _devices_cache["by_hash"] = {d["token_hash"]: d for d in cfg["main"]["devices"]}
        _devices_cache["at"] = now
    return _devices_cache["by_hash"]


def forget_cache() -> None:
    _devices_cache["at"] = 0.0


def device_for_token(token: str, ip: str = "") -> Optional[dict]:
    """The paired counter this token belongs to, or None. Marks it seen."""
    global _seen_saved_at
    if not token:
        return None
    th = token_hash(token)
    dev = None
    for h, d in _devices_by_hash().items():
        if hmac.compare_digest(h, th):
            dev = d
            break
    if dev is None:
        return None
    now = time.time()
    with _lock:
        _seen[dev["id"]] = {"at": now, "ip": ip}
        persist = now - _seen_saved_at > SEEN_PERSIST_EVERY
        if persist:
            _seen_saved_at = now
            snapshot = dict(_seen)
    if persist:
        def change(cfg):
            for d in cfg["main"]["devices"]:
                s = snapshot.get(d.get("id"))
                if s:
                    d["last_seen"] = datetime.fromtimestamp(s["at"], timezone.utc).isoformat(timespec="seconds")
                    d["last_ip"] = s.get("ip") or d.get("last_ip") or ""
            return cfg

        try:
            config.update(change)
        except OSError:
            pass
    return dev


def devices_status() -> list[dict]:
    """For the Settings screen: every paired counter, when it was last heard from."""
    cfg = config.load()
    out = []
    now = time.time()
    with _lock:
        seen = dict(_seen)
    for d in cfg["main"]["devices"]:
        s = seen.get(d.get("id"))
        ago = None
        if s:
            ago = int(now - s["at"])
        out.append({
            "id": d.get("id"),
            "name": d.get("name"),
            "paired_at": d.get("paired_at"),
            "last_seen": (datetime.fromtimestamp(s["at"], timezone.utc).isoformat(timespec="seconds")
                          if s else d.get("last_seen")),
            "seconds_ago": ago,
            "connected": ago is not None and ago <= 10,
            "ip": (s or {}).get("ip") or d.get("last_ip") or "",
        })
    return out
