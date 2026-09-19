"""Option B feature flags (Phase B3).

USE_REVISION_SYNC=true (default) → SyncEngine (WebSocket + revision delta + 45s safety).
USE_REVISION_SYNC=false → legacy watermark poller (rollback only).

SYNC_ENGINE_V2 is accepted as a legacy alias of USE_REVISION_SYNC.
"""
from __future__ import annotations

import os


def _parse_bool(raw: str) -> bool | None:
    val = (raw or "").strip().lower()
    if val in ("0", "false", "off", "no"):
        return False
    if val in ("1", "true", "on", "yes"):
        return True
    return None


def is_revision_sync_enabled() -> bool:
    """Default True. Set USE_REVISION_SYNC=0 (or SYNC_ENGINE_V2=0) to rollback."""
    for key in ("USE_REVISION_SYNC", "SYNC_ENGINE_V2"):
        parsed = _parse_bool(os.environ.get(key) or "")
        if parsed is not None:
            return parsed

    try:
        from core.license_manager import _appdata_dir

        for name in ("use_revision_sync.txt", "sync_engine_v2.txt"):
            p = os.path.join(_appdata_dir(), name)
            if os.path.isfile(p):
                parsed = _parse_bool(open(p, encoding="utf-8").read())
                if parsed is not None:
                    return parsed
    except Exception:
        pass
    return True


# Back-compat alias used by B1 wiring
def is_sync_engine_v2_enabled() -> bool:
    return is_revision_sync_enabled()
