"""Feature flags for Sync V3 cutover.

Master flag USE_SYNC_V3 gates the new pipeline. Per-entity sub-flags allow
entity-by-entity migration while the app stays shippable.
"""
from __future__ import annotations

import os
import sys

ENTITY_PURCHASES = "purchases"
ENTITY_SALES = "sales"
ENTITY_PAYMENTS = "payments"
ENTITY_RETURNS = "returns"
ENTITY_MASTERS = "masters"

_VALID_ENTITIES = frozenset(
    {
        ENTITY_PURCHASES,
        ENTITY_SALES,
        ENTITY_PAYMENTS,
        ENTITY_RETURNS,
        ENTITY_MASTERS,
    }
)

# Default ON for rebuild implementation — set env SYNC_V3=0 to disable.
USE_SYNC_V3 = os.environ.get("SYNC_V3", "1").strip() not in ("0", "false", "False", "no")


def _appdata_dir() -> str:
    try:
        from core.license_manager import _appdata_dir as _dir

        return _dir()
    except Exception:
        if getattr(sys, "frozen", False):
            return os.path.join(
                os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                "VeterinaryApp",
            )
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "config",
        )


def _flags_path() -> str:
    return os.path.join(_appdata_dir(), "sync_v3_flags.txt")


def _load_file_flags() -> dict[str, bool]:
    out: dict[str, bool] = {}
    try:
        path = _flags_path()
        if not os.path.isfile(path):
            return out
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip().lower()
            val = val.strip().lower()
            out[key] = val in ("1", "true", "yes", "on")
    except Exception:
        pass
    return out


def is_sync_v3_enabled() -> bool:
    file_flags = _load_file_flags()
    if "use_sync_v3" in file_flags:
        return bool(file_flags["use_sync_v3"])
    return bool(USE_SYNC_V3)


def is_entity_v3_enabled(entity: str) -> bool:
    """True when master flag is on and entity sub-flag is on.

    Purchases/sales default OFF until allocate-deferred is proven in production
    (legacy path already uses local-first commit_local_then_push). Other entities
    default ON for outbox-on-failure behavior.
    """
    if not is_sync_v3_enabled():
        return False
    ent = (entity or "").strip().lower()
    if ent not in _VALID_ENTITIES:
        return False
    file_flags = _load_file_flags()
    key = f"sync_v3_{ent}"
    if key in file_flags:
        return bool(file_flags[key])
    # Env override e.g. SYNC_V3_PURCHASES=1
    env_key = f"SYNC_V3_{ent.upper()}"
    if env_key in os.environ:
        return os.environ[env_key].strip() not in ("0", "false", "False", "no")
    # Safe defaults: purchases/sales keep proven legacy writers.
    if ent in (ENTITY_PURCHASES, ENTITY_SALES):
        return False
    return True


def set_entity_flag(entity: str, enabled: bool) -> None:
    ent = (entity or "").strip().lower()
    if ent not in _VALID_ENTITIES and ent != "use_sync_v3":
        raise ValueError(f"unknown entity flag: {entity}")
    flags = _load_file_flags()
    if ent == "use_sync_v3":
        flags["use_sync_v3"] = bool(enabled)
    else:
        flags[f"sync_v3_{ent}"] = bool(enabled)
    path = _flags_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for k, v in sorted(flags.items()):
            fh.write(f"{k}={'1' if v else '0'}\n")
