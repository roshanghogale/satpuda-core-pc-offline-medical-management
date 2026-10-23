"""This PC's place on the LAN: lan_config.json in the app's data folder.

{
  "role": "off" | "main" | "counter",
  "port": 8775,                 # the Main PC's LAN listener (TCP)
  "discovery_port": 8776,       # "find Main PC" (UDP broadcast)
  "main":    {"devices": [{"id", "name", "install_id", "token_hash", "paired_at",
                           "last_seen", "last_ip"}]},
  "counter": {"main_host", "main_port", "token", "main_name", "shop_name",
              "main_install_id", "pending_code", "pair_error"}
}

Off is the default and is today's behaviour exactly. The installer writes the same file
(installer/SatpudaCore.iss, "This PC is"), with a pending pairing code for a counter that the
engine uses once on its first start.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any

ROLE_OFF = "off"
ROLE_MAIN = "main"
ROLE_COUNTER = "counter"
ROLES = (ROLE_OFF, ROLE_MAIN, ROLE_COUNTER)

DEFAULT_PORT = 8775
DEFAULT_DISCOVERY_PORT = 8776
FILENAME = "lan_config.json"

_lock = threading.RLock()


def _dir() -> str:
    override = os.environ.get("SATPUDA_LAN_DIR")
    if override:
        os.makedirs(override, exist_ok=True)
        return override
    from core.license_manager import _appdata_dir

    return _appdata_dir()


def path() -> str:
    return os.path.join(_dir(), FILENAME)


def _defaults() -> dict[str, Any]:
    return {
        "role": ROLE_OFF,
        "port": DEFAULT_PORT,
        "discovery_port": DEFAULT_DISCOVERY_PORT,
        "main": {"devices": []},
        "counter": {},
    }


def _clean(raw: Any) -> dict[str, Any]:
    out = _defaults()
    if not isinstance(raw, dict):
        return out
    role = str(raw.get("role") or ROLE_OFF).strip().lower()
    out["role"] = role if role in ROLES else ROLE_OFF
    for key, default in (("port", DEFAULT_PORT), ("discovery_port", DEFAULT_DISCOVERY_PORT)):
        try:
            val = int(raw.get(key) or default)
        except (TypeError, ValueError):
            val = default
        out[key] = val if 1 <= val <= 65535 else default
    main = raw.get("main") if isinstance(raw.get("main"), dict) else {}
    devices = main.get("devices") if isinstance(main.get("devices"), list) else []
    out["main"] = {"devices": [d for d in devices if isinstance(d, dict) and d.get("token_hash")]}
    counter = raw.get("counter") if isinstance(raw.get("counter"), dict) else {}
    out["counter"] = dict(counter)
    return out


def load() -> dict[str, Any]:
    with _lock:
        try:
            # utf-8-sig: the installer writes the file with a byte-order mark.
            with open(path(), encoding="utf-8-sig") as fh:
                return _clean(json.load(fh))
        except (OSError, ValueError):
            return _defaults()


def save(cfg: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        clean = _clean(cfg)
        target = path()
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(clean, fh, indent=2)
        os.replace(tmp, target)
        return clean


def update(fn) -> dict[str, Any]:
    """Read, change (fn gets a copy and returns it or None), write -- under one lock."""
    with _lock:
        cfg = load()
        new = fn(copy.deepcopy(cfg))
        return save(new if isinstance(new, dict) else cfg)


def role() -> str:
    return load()["role"]
