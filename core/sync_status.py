"""Lightweight in-memory sync visibility for the desktop status strip."""
from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Optional

_lock = threading.Lock()
_last_sync_at: Optional[str] = None
_last_sync_kind: str = ""
_pending: Deque[str] = deque(maxlen=50)
_skips: Deque[str] = deque(maxlen=30)
_errors: Deque[str] = deque(maxlen=20)
_refresh_seq: int = 0
_refresh_collections: set[str] = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def note_last_sync(kind: str = "sync") -> None:
    global _last_sync_at, _last_sync_kind
    with _lock:
        _last_sync_at = _now()
        _last_sync_kind = kind or "sync"


def note_push(collection: str, doc_id: Any) -> None:
    with _lock:
        _pending.append(f"{collection}/{doc_id}")


def clear_pending() -> None:
    with _lock:
        _pending.clear()


def note_skip(collection: str, doc_id: str, reason: str = "") -> None:
    msg = f"{collection}/{doc_id}"
    if reason:
        msg += f" ({reason})"
    with _lock:
        _skips.append(msg)


def note_error(message: str) -> None:
    with _lock:
        _errors.append(str(message)[:200])


def note_collection_change(collection: str) -> None:
    """Record a server/SyncEngine pull so desktop UI can refresh open pages."""
    global _refresh_seq
    col = str(collection or "").strip()
    if not col:
        return
    with _lock:
        _refresh_collections.add(col)
        _refresh_seq += 1


def note_sync_engine_state(state: str) -> None:
    """Optional Live / Syncing / Offline hint for status strip."""
    global _last_sync_kind
    with _lock:
        _last_sync_kind = state or _last_sync_kind


def take_refresh_batch() -> dict[str, Any]:
    """Return and clear pending server refresh hints for the desktop UI."""
    with _lock:
        batch = {
            "refresh_seq": _refresh_seq,
            "collections": sorted(_refresh_collections),
        }
        _refresh_collections.clear()
        return batch


def snapshot() -> dict:
    """UI-facing status snapshot."""
    try:
        from core.conflict_resolver import recent_skips
        cr_skips = [
            f"{s.collection}/{s.doc_id}: {s.reason}"
            for s in recent_skips(10)
        ]
    except Exception:
        cr_skips = []
    with _lock:
        skips = list(_skips)[-10:] + cr_skips
        return {
            "last_sync_at": _last_sync_at,
            "last_sync_kind": _last_sync_kind,
            "pending_count": len(_pending),
            "pending": list(_pending)[-10:],
            "skip_count": len(skips),
            "skips": skips[-10:],
            "errors": list(_errors)[-5:],
        }


def format_status_line() -> str:
    try:
        from core.sync_prefs import is_offline_mode, is_online_mode

        if is_offline_mode():
            return "Offline — Google Drive backup"
        if is_online_mode():
            from core.online_guard import get_status_line

            og = (get_status_line() or "").strip()
            if og:
                return og
    except Exception:
        pass
    snap = snapshot()
    last = snap["last_sync_at"]
    if last:
        try:
            dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            last_disp = dt.astimezone().strftime("%H:%M:%S")
        except Exception:
            last_disp = last[-8:]
    else:
        last_disp = "never"
    parts = [f"Sync: {last_disp}"]
    if snap["pending_count"]:
        parts.append(f"pushes {snap['pending_count']}")
    if snap["skip_count"]:
        parts.append(f"skips {snap['skip_count']}")
    if snap["errors"]:
        parts.append("error")
    return " | ".join(parts)
