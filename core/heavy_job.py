"""Generic background heavy job (push/pull/verify/Drive/wipe) with progress status.

Keeps the desktop API responsive: start returns immediately; UI polls status.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

ProgressCb = Optional[Callable[[str], None]]

_lock = threading.Lock()
_state: dict[str, Any] = {
    "running": False,
    "action": "",
    "message": "",
    "done": False,
    "ok": True,
    "error": None,
    "result": None,
}


def get_status() -> dict[str, Any]:
    with _lock:
        out = dict(_state)
        # Shallow-copy result dict so callers cannot mutate shared state.
        if isinstance(out.get("result"), dict):
            out["result"] = dict(out["result"])
        return out


def _set(**kwargs: Any) -> None:
    with _lock:
        _state.update(kwargs)


def report_progress(msg: str) -> None:
    """Update the visible status message for an in-flight job."""
    text = str(msg or "").strip() or "Working…"
    _set(message=text)


def _progress(msg: str) -> None:
    report_progress(msg)


def is_running() -> bool:
    with _lock:
        return bool(_state.get("running"))


def start_async(
    action: str,
    worker: Callable[[ProgressCb], dict[str, Any]],
    *,
    start_message: str = "Starting…",
) -> bool:
    """Start worker in a daemon thread. Returns False if a job is already running."""
    action_key = str(action or "").strip().lower()
    with _lock:
        if _state.get("running"):
            return False
        _state.update(
            {
                "running": True,
                "action": action_key,
                "message": start_message,
                "done": False,
                "ok": True,
                "error": None,
                "result": None,
            }
        )

    def _thread() -> None:
        try:
            result = worker(_progress) or {}
            ok = bool(result.get("ok", True))
            err = result.get("error")
            msg = (
                str(result.get("message") or "")
                or (str(err) if err else "")
                or ("Done." if ok else "Failed.")
            )
            _set(
                running=False,
                done=True,
                ok=ok,
                error=str(err) if err else None,
                message=msg,
                result=result,
            )
        except Exception as exc:
            _set(
                running=False,
                done=True,
                ok=False,
                error=str(exc),
                message=str(exc),
                result={"ok": False, "error": str(exc)},
            )

    threading.Thread(
        target=_thread, daemon=True, name=f"HeavyJob:{action_key or 'work'}"
    ).start()
    return True
