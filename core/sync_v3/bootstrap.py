"""BootstrapSession — reconcile with server before UI mutations in Online mode."""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

log = logging.getLogger(__name__)

_state = "idle"  # idle | bootstrapping | ready | degraded
_state_lock = threading.Lock()
_listeners: list[Callable[[str], None]] = []


def get_bootstrap_state() -> str:
    with _state_lock:
        return _state


def _set_state(s: str) -> None:
    global _state
    with _state_lock:
        _state = s
    for cb in list(_listeners):
        try:
            cb(s)
        except Exception:
            pass


def register_state_listener(cb: Callable[[str], None]) -> None:
    if cb and cb not in _listeners:
        _listeners.append(cb)


def mutations_allowed() -> bool:
    """UI should gate editable forms on this in Online mode when V3 is on."""
    from core.sync_prefs import is_online_mode
    from core.sync_v3.flags import is_sync_v3_enabled

    if not is_sync_v3_enabled():
        return True
    if not is_online_mode():
        return True
    return get_bootstrap_state() in ("ready", "idle")


def run_bootstrap(conn, *, full: bool = False, on_progress: Optional[Callable[[str], None]] = None) -> bool:
    """
    Pull server changes and reconcile pending outbox before enabling writes.
    Returns True on success (ready), False on failure (degraded).
    """
    from core.sync_prefs import is_online_mode
    from core.sync_v3.flags import is_sync_v3_enabled
    from core.sync_v3.schema import ensure_sync_v3_schema

    if not is_sync_v3_enabled() or not is_online_mode():
        _set_state("ready")
        return True

    _set_state("bootstrapping")
    if on_progress:
        try:
            on_progress("Syncing with server…")
        except Exception:
            pass

    try:
        ensure_sync_v3_schema(conn)
    except Exception as exc:
        log.warning("bootstrap schema: %s", exc)

    try:
        from core.sync_v3 import transport
        from core.sync_v3.applier import apply_changes
        from core.sync_revision import get_head_revision, set_head_revision

        status = transport.get_sync_status()
        server_head = int(status.get("head_revision") or status.get("head") or 0)
        local_head = 0 if full else int(get_head_revision() or 0)

        after = local_head
        pages = 0
        while pages < 500:
            pages += 1
            data = transport.pull_changes_after(after, limit=200)
            changes = data.get("changes") or data.get("items") or []
            if not changes:
                remote_head = int(data.get("head_revision") or server_head or after)
                if remote_head > after:
                    set_head_revision(remote_head)
                break
            apply_changes(conn, changes)
            max_rev = after
            for ch in changes:
                try:
                    max_rev = max(max_rev, int(ch.get("revision") or ch.get("rev") or 0))
                except Exception:
                    pass
            after = max_rev
            set_head_revision(after)
            if len(changes) < 200:
                break
            if on_progress:
                try:
                    on_progress(f"Syncing with server… (rev {after})")
                except Exception:
                    pass

        _reconcile_outbox(conn)

        # Start background outbox worker
        try:
            from core.sync_v3.outbox_worker import start_worker

            start_worker(conn)
        except Exception as exc:
            log.debug("outbox worker start: %s", exc)

        _set_state("ready")
        if on_progress:
            try:
                on_progress("")
            except Exception:
                pass
        return True
    except Exception as exc:
        log.warning("bootstrap failed: %s", exc)
        _set_state("degraded")
        return False


def _reconcile_outbox(conn) -> None:
    """Mark superseded outbox rows when server already has newer state (best-effort)."""
    try:
        rows = conn.execute(
            "SELECT id, collection, local_id, operation FROM sync_outbox_v2 "
            "WHERE status IN ('pending','failed') ORDER BY id ASC LIMIT 200"
        ).fetchall()
    except Exception:
        return
    # Worker will retry; leave pending. Future: compare server versions.
    log.debug("bootstrap: %s pending outbox rows", len(rows or []))
