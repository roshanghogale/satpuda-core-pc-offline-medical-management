"""Apply server changelog rows to local SQLite and notify UI."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def apply_change(conn, change: dict[str, Any]) -> bool:
    """
    Apply one sync_changes row (from GET /api/sync/changes/full).
    Reuses existing server_live.apply_server_doc when possible.
    """
    if not change:
        return False
    collection = str(change.get("collection") or change.get("entity") or "").strip()
    revision = change.get("revision") or change.get("rev")
    doc = change.get("doc") or change.get("payload") or change.get("data") or {}
    op = str(change.get("op") or change.get("operation") or "upsert").lower()

    # Inbox dedup via SyncEngine helpers when available
    try:
        from core.sync_engine import SyncEngine

        if revision is not None and SyncEngine._inbox_has(conn, int(revision)):
            return True
    except Exception:
        pass

    try:
        from core import server_live as live

        if op in ("delete", "hard_delete"):
            if isinstance(doc, dict):
                doc = dict(doc)
                doc["deleted"] = 1
        ok = bool(live.apply_server_doc(conn, collection, doc))
    except Exception as exc:
        log.warning("apply_change %s: %s", collection, exc)
        ok = False

    if ok and revision is not None:
        try:
            from core.sync_engine import SyncEngine

            SyncEngine._inbox_mark(
                conn,
                revision=int(revision),
                collection=collection or "",
                local_id=(doc or {}).get("local_id") or (doc or {}).get("id"),
                operation=op,
            )
        except Exception:
            pass
        try:
            from core.sync_revision import set_head_revision, get_head_revision

            cur = int(get_head_revision() or 0)
            if int(revision) > cur:
                set_head_revision(int(revision))
        except Exception:
            pass

    if ok and collection:
        try:
            from core.sync_v3.data_change_bus import emit

            emit(collection, local=False)
        except Exception:
            pass
        try:
            from core.sync_status import note_collection_change

            note_collection_change(collection)
        except Exception:
            pass
    return ok


def apply_changes(conn, changes: list) -> int:
    applied = 0
    for ch in changes or []:
        if isinstance(ch, dict) and apply_change(conn, ch):
            applied += 1
    return applied
