"""HTTP transport helpers for Sync V3 (thin wrappers over server_api)."""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)


def store_token() -> Optional[str]:
    try:
        from core import server_api as api

        return api.store_token_for_active()
    except Exception as exc:
        log.debug("store_token: %s", exc)
        return None


def get_sync_status() -> dict[str, Any]:
    token = store_token()
    if not token:
        return {}
    try:
        from core import server_api as api

        return api.get_sync_status(token) or {}
    except Exception as exc:
        log.warning("get_sync_status: %s", exc)
        return {}


def pull_changes_after(after_revision: int, *, limit: int = 200) -> dict[str, Any]:
    token = store_token()
    if not token:
        return {"changes": [], "head_revision": after_revision}
    try:
        from core import server_api as api

        return api.get_sync_changes_full(token, after=int(after_revision), limit=limit) or {}
    except Exception as exc:
        log.warning("pull_changes_after: %s", exc)
        return {"changes": [], "head_revision": after_revision, "error": str(exc)}


def allocate_fy(kind: str, date_s: str) -> dict[str, Any]:
    token = store_token()
    if not token:
        return {}
    try:
        from core import server_api as api

        return api.allocate_fy(token, kind, date_s) or {}
    except Exception as exc:
        log.warning("allocate_fy: %s", exc)
        return {}
