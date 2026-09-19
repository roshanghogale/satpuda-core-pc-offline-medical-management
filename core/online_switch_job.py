"""Background Online-mode switch — pair + live WS hints only (server-only Online).

Does not download or keep business data in SQLite. Migrate leftover local DB
via online_migrate (Push|Wipe) after switch.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

ProgressCb = Optional[Callable[[str], None]]

_lock = threading.Lock()
_state: dict[str, Any] = {
    "running": False,
    "message": "",
    "done": False,
    "ok": True,
    "error": None,
    "bootstrap_ran": False,
    "bootstrap_message": "",
    "server_sync_started": False,
    "needs_migrate": False,
    "step": 0,
    "steps_total": 4,
}


def get_status() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def _set(**kwargs: Any) -> None:
    with _lock:
        _state.update(kwargs)


def _progress(msg: str, extra_cb: ProgressCb = None, *, step: int | None = None) -> None:
    text = str(msg or "").strip() or "Working…"
    kwargs: dict[str, Any] = {"message": text}
    if step is not None:
        kwargs["step"] = int(step)
    _set(**kwargs)
    if extra_cb:
        try:
            extra_cb(text)
        except Exception:
            pass


def run_online_switch(
    db_path: str,
    *,
    progress_cb: ProgressCb = None,
    on_change: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Pair store and start live hints. Does not pull into SQLite."""
    from core.sync_coordinator import start_online_sync, stop_online_sync

    total = 4
    _set(steps_total=total, step=0)
    result = {
        "ok": True,
        "bootstrap_ran": False,
        "bootstrap_message": (
            "Online is server-only: no local store DB for business data. "
            "If this PC still has local data, choose Push to server or Delete local."
        ),
        "server_sync_started": False,
        "needs_migrate": False,
        "error": None,
    }

    _progress("1/4 Checking Satpuda Core Server…", progress_cb, step=1)
    try:
        from core import server_api as api

        if not api.health_ok(timeout=8.0):
            result["ok"] = False
            result["error"] = "Server health check failed (no internet or API down)."
            _progress(f"Failed: {result['error']}", progress_cb, step=1)
            return result
    except Exception as exc:
        result["ok"] = False
        result["error"] = f"Server check failed: {exc}"
        _progress(f"Failed: {result['error']}", progress_cb, step=1)
        return result

    try:
        stop_online_sync()
    except Exception:
        pass

    try:
        from core.sync_bootstrap import clear_pending_bootstrap, mark_bootstrap_done

        clear_pending_bootstrap()
        mark_bootstrap_done()
    except Exception:
        pass

    _progress("2/4 Pairing this store on the server…", progress_cb, step=2)
    t0 = time.time()
    try:
        from core.sync_coordinator import ensure_online_store_link

        key = ensure_online_store_link()
        elapsed = int(time.time() - t0)
        if key:
            _progress(f"2/4 Store paired ({elapsed}s).", progress_cb, step=2)
        else:
            _progress(f"2/4 Pair finished ({elapsed}s).", progress_cb, step=2)
    except Exception as exc:
        result["ok"] = False
        result["error"] = f"Pairing failed: {exc}"
        _progress(f"Failed: {result['error']}", progress_cb, step=2)
        return result

    # Record Online activation date / default expiry on server
    _progress("3/4 Saving activation / licence on server…", progress_cb, step=3)
    try:
        from core.license_manager import record_activation_online

        record_activation_online()
    except Exception as exc:
        _progress(f"3/4 Licence note: {exc}", progress_cb, step=3)

    try:
        from core.online_migrate import ensure_online_server_only_ready

        gate = ensure_online_server_only_ready(db_path=db_path or None)
        result["needs_migrate"] = bool(gate.get("needs_migrate") and gate.get("has_data"))
        if result["needs_migrate"]:
            _progress(
                "3/4 Local data found — use Push or Delete when prompted.",
                progress_cb,
                step=3,
            )
        elif gate.get("removed_empty"):
            _progress("3/4 Empty local DB removed (server-only).", progress_cb, step=3)
        else:
            _progress("3/4 Server-only — no local store DB.", progress_cb, step=3)
    except Exception as exc:
        _progress(f"3/4 Migrate check: {exc}", progress_cb, step=3)

    _progress("4/4 Starting live update notifications…", progress_cb, step=4)
    try:
        started = start_online_sync(
            None,
            on_change=on_change,
            db_path=db_path or None,
            adopt_server_head=True,
            hints_only=True,
        )
        result["server_sync_started"] = bool(started)
        if started:
            try:
                from core.sync_status import note_last_sync

                note_last_sync("online")
            except Exception:
                pass
            _progress(
                "Done — Online live hints started (server-only).",
                progress_cb,
                step=4,
            )
        else:
            _progress(
                "Online mode saved — could not start live hints yet (check internet).",
                progress_cb,
                step=4,
            )
    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc)
        _progress(f"Live sync failed: {exc}", progress_cb, step=4)
    return result


def start_online_switch_async(
    db_path: str,
    *,
    on_change: Callable[[str], None] | None = None,
    on_done: Callable[[dict[str, Any]], None] | None = None,
) -> bool:
    """Start Online switch in a daemon thread. Returns False if already running."""
    with _lock:
        if _state.get("running"):
            return False
        _state.update(
            {
                "running": True,
                "message": "Starting Online mode…",
                "done": False,
                "ok": True,
                "error": None,
                "bootstrap_ran": False,
                "bootstrap_message": "",
                "server_sync_started": False,
                "needs_migrate": False,
                "step": 0,
                "steps_total": 4,
            }
        )

    def _worker() -> None:
        try:
            result = run_online_switch(db_path, on_change=on_change)
            _set(
                running=False,
                done=True,
                ok=bool(result.get("ok")),
                error=result.get("error"),
                bootstrap_ran=False,
                bootstrap_message=str(result.get("bootstrap_message") or ""),
                server_sync_started=bool(result.get("server_sync_started")),
                needs_migrate=bool(result.get("needs_migrate")),
                message=(
                    "Online ready (server-only)."
                    if result.get("server_sync_started")
                    else (
                        str(result.get("error") or result.get("bootstrap_message") or "")
                        or "Online mode saved."
                    )
                ),
            )
            if on_done:
                try:
                    on_done(result)
                except Exception:
                    pass
        except Exception as exc:
            _set(
                running=False,
                done=True,
                ok=False,
                error=str(exc),
                message=str(exc),
            )
            if on_done:
                try:
                    on_done({"ok": False, "error": str(exc)})
                except Exception:
                    pass

    threading.Thread(
        target=_worker, daemon=True, name="OnlineModeSwitch"
    ).start()
    return True
