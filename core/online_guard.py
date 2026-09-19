"""Online mode guard — Satpuda Core Server is source of truth; no CRUD without internet."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

log = logging.getLogger(__name__)

_CACHE_TTL = 12.0
_lock = threading.Lock()
_cached_ok: Optional[bool] = None
_cached_at: float = 0.0

# UI / status callbacks
_status_listeners: list[Callable[[str], None]] = []
_last_status: str = ""
_was_unreachable: bool = False


class OnlineUnavailableError(RuntimeError):
    """Raised when Online mode has no server connectivity."""

    def __init__(self, message: str | None = None):
        super().__init__(
            message
            or (
                "Online mode needs internet. Changes are not saved until "
                "Satpuda Core Server is reachable."
            )
        )


class CloudSaveError(RuntimeError):
    """Raised when server push failed — local write was rolled back."""

    def __init__(self, message: str | None = None):
        super().__init__(
            message
            or "Could not save to server. Your change was not kept locally."
        )


def register_status_listener(cb: Callable[[str], None]) -> None:
    if cb and cb not in _status_listeners:
        _status_listeners.append(cb)


def get_status_line() -> str:
    return _last_status or status_label()


def _emit_status(text: str) -> None:
    global _last_status
    _last_status = text
    for cb in list(_status_listeners):
        try:
            cb(text)
        except Exception:
            pass


def is_cloud_reachable(*, force: bool = False) -> bool:
    """Cached Satpuda Core Server health probe."""
    global _cached_ok, _cached_at
    now = time.monotonic()
    with _lock:
        if (
            not force
            and _cached_ok is not None
            and (now - _cached_at) < _CACHE_TTL
        ):
            return bool(_cached_ok)
    try:
        from core.server_api import health_ok
        ok = bool(health_ok(timeout=2.5 if force else 1.5))
    except Exception:
        ok = False
    with _lock:
        _cached_ok = ok
        _cached_at = time.monotonic()
    return ok


def invalidate_reachability_cache() -> None:
    global _cached_ok, _cached_at
    with _lock:
        _cached_ok = None
        _cached_at = 0.0


def ensure_can_mutate() -> None:
    """Block flush-path CRUD when Online mode cannot reach the server.

    UI enqueue paths skip this so short drops can still queue a save.
    """
    from core.sync_prefs import is_online_mode
    if not is_online_mode():
        return
    if not is_cloud_reachable(force=False):
        if not is_cloud_reachable(force=True):
            _emit_status("Online · No internet — will sync")
            raise OnlineUnavailableError(
                "Online mode needs internet to flush. Your change can still be "
                "queued from the save screen."
            )


def ensure_can_read() -> None:
    """Online mode: prefer cache/snapshot; only hard-fail if nothing to show."""
    from core.sync_prefs import is_online_mode
    if not is_online_mode():
        return
    if not is_cloud_reachable(force=False):
        if not is_cloud_reachable(force=True):
            _emit_status("Online · No internet — will sync")


def status_label() -> str:
    from core.sync_prefs import is_online_mode, is_offline_mode
    if is_offline_mode():
        return "Offline — Google Drive backup"
    try:
        from core.online_mutation_queue import pending_count, queue_health

        n = pending_count()
        health = queue_health()
    except Exception:
        n = 0
        health = {"warn": False}
    if is_cloud_reachable():
        if health.get("warn"):
            return (
                f"Online · Sync backlog {n} "
                f"(oldest {int(health.get('oldest_sec') or 0) // 60}m)"
            )
        if n:
            return f"Online · Syncing {n}…"
        return "Online · Live (Server)"
    if n:
        return f"Online · No internet — will sync ({n})"
    return "Online · No internet — will sync"


def refresh_status() -> str:
    label = status_label()
    _emit_status(label)
    return label


def commit_local_then_push(
    conn,
    push_fn: Callable[[], bool],
    *,
    before_commit: Optional[Callable[[], None]] = None,
    push_with_conn: Optional[Callable] = None,
) -> bool:
    """
    Local-first write discipline (rebuild target):
    1. Commit local row so UI / history never lose a saved record
    2. Push to server; push_fn should enqueue outbox on failure
    3. Never roll back after commit — push failure leaves pending sync

    Returns True if push succeeded (or Offline skip), False if push failed
    but local row was kept.
    """
    from core.sync_prefs import is_online_mode

    if before_commit:
        before_commit()
    conn.commit()

    if not is_online_mode():
        return True

    if not is_cloud_reachable(force=False):
        # Local row already committed; outbox/worker will retry when online.
        _emit_status("Online · Saved locally — will sync when reachable")
        return False

    try:
        ok = bool(push_fn())
    except Exception as exc:
        log.warning("cloud push failed after local commit: %s", exc)
        _emit_status("Online · Saved locally — sync pending")
        return False
    if not ok:
        _emit_status("Online · Saved locally — sync pending")
        return False
    return True


def commit_after_cloud_push(
    conn,
    push_fn: Callable[[], bool],
    *,
    before_commit: Optional[Callable[[], None]] = None,
    push_with_conn: Optional[Callable] = None,
) -> None:
    """
    Legacy name — now routes to local-first commit_local_then_push so a failed
    push can never roll back a committed row (and burn FY serials).
    """
    commit_local_then_push(
        conn,
        push_fn,
        before_commit=before_commit,
        push_with_conn=push_with_conn,
    )


def show_mutate_error(exc: BaseException, parent=None) -> None:
    """Show themed error for OnlineUnavailableError / CloudSaveError."""
    msg = str(exc) or "Save blocked."
    try:
        from core.themed_messagebox import showerror
        showerror("Online mode", msg, parent=parent)
    except Exception:
        try:
            from tkinter import messagebox
            messagebox.showerror("Online mode", msg)
        except Exception:
            pass


# ── Connectivity monitor (reconnect → pull server) ───────────────────────────

_monitor_started = False
_monitor_stop = threading.Event()
_on_reconnected: Optional[Callable[[], None]] = None


def set_reconnect_handler(cb: Optional[Callable[[], None]]) -> None:
    global _on_reconnected
    _on_reconnected = cb


def start_connectivity_monitor(interval_sec: float = 8.0) -> None:
    """Poll reachability while Online mode; pull server when net returns."""
    global _monitor_started, _was_unreachable
    from core.sync_prefs import is_online_mode
    if not is_online_mode():
        return
    if _monitor_started:
        return
    _monitor_started = True
    _monitor_stop.clear()

    def _loop():
        global _was_unreachable
        while not _monitor_stop.is_set():
            try:
                from core.sync_prefs import is_online_mode as _online
                if not _online():
                    _emit_status("Offline — Google Drive backup")
                    _monitor_stop.wait(interval_sec)
                    continue
                ok = is_cloud_reachable(force=True)
                if ok:
                    _emit_status(status_label())
                    if _was_unreachable:
                        _was_unreachable = False
                        try:
                            from core.online_mutation_queue import kick_flush

                            kick_flush()
                        except Exception as exc:
                            log.debug("kick_flush on reconnect: %s", exc)
                        cb = _on_reconnected
                        if cb:
                            try:
                                cb()
                            except Exception as exc:
                                log.warning("reconnect handler: %s", exc)
                else:
                    _was_unreachable = True
                    _emit_status(status_label())
            except Exception as exc:
                log.debug("connectivity monitor: %s", exc)
            _monitor_stop.wait(interval_sec)

    threading.Thread(target=_loop, daemon=True, name="online-guard-monitor").start()


def stop_connectivity_monitor() -> None:
    global _monitor_started
    _monitor_stop.set()
    _monitor_started = False
