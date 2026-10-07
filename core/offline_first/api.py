"""Engine routes for offline-first (called from core/desktop_api.py).

GET  /api/offline-first/status              what is waiting, flagged, last push / pull
GET  /api/offline-first/activate/progress   the switch-over job, while it runs
POST /api/offline-first/activate            Online PC -> offline-first (background job)
POST /api/offline-first/sync-now            run a push + pull now
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

_job: dict[str, Any] = {"running": False, "steps": [], "result": None, "error": None, "started": 0.0}
_job_lock = threading.Lock()


def _run_activate(app_version: str) -> None:
    from core.offline_first.runtime import activate_from_online

    def progress(msg: str) -> None:
        with _job_lock:
            _job["steps"].append(msg)
            _job["steps"] = _job["steps"][-40:]

    try:
        result = activate_from_online(progress_cb=progress, app_version=app_version)
        # Open the local copy now (offline-first connection + the background worker).
        try:
            from core.desktop_api import reopen_active_store

            progress("Opening the store on this PC…")
            reopen_active_store()
        except Exception as exc:
            progress(f"Restart the app to open the store: {exc}")
        with _job_lock:
            _job["result"] = result
    except Exception as exc:
        with _job_lock:
            _job["error"] = str(exc)
    finally:
        with _job_lock:
            _job["running"] = False


def handle_get(path: str, conn) -> Optional[tuple[int, dict]]:
    if path == "/api/offline-first/status":
        from core.offline_first import worker
        from core.offline_first.runtime import status

        out: dict = {"ok": True}
        try:
            out.update(status(conn) if conn is not None else {"active": False})
        except Exception as exc:
            out["error"] = str(exc)
        w = worker.current()
        out["worker_alive"] = bool(w and w.thread and w.thread.is_alive())
        out["last_cycle"] = w.last if w else None
        return 200, out
    if path == "/api/offline-first/activate/progress":
        with _job_lock:
            return 200, {"ok": True, **{k: v for k, v in _job.items()}}
    return None


def handle_post(path: str, body: dict, conn) -> Optional[tuple[int, dict]]:
    if path == "/api/offline-first/activate":
        from core.sync_prefs import is_online_mode

        if not is_online_mode():
            return 400, {"ok": False, "error": "Switch the store to Online first; the copy is made from the server."}
        with _job_lock:
            if _job["running"]:
                return 200, {"ok": True, "running": True}
            _job.update({"running": True, "steps": [], "result": None, "error": None, "started": time.time()})
        app_version = ""
        try:
            from core.app_version import APP_VERSION

            app_version = str(APP_VERSION)
        except Exception:
            pass
        threading.Thread(target=_run_activate, args=(app_version,), name="offline-first-activate",
                         daemon=True).start()
        return 200, {"ok": True, "running": True}
    if path == "/api/offline-first/sync-now":
        from core.offline_first import worker

        worker.kick()
        return 200, {"ok": True}
    return None
