"""Local HTTP API for the Tauri desktop UI (Win10/11). Bind 127.0.0.1 only."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import urlparse

_DEFAULT_PORT = 8765
_PID_FILE = "desktop_api.pid"
# Set once the app shell has asked for the closing backup, so both of its exit
# events cannot each fire one.
_CLOSE_BACKUP_DONE = False
# Bump when routes/payload change so the Tauri shell can replace a stale engine.
API_REVISION = 65
_server: Optional[ThreadingHTTPServer] = None
_server_thread: Optional[threading.Thread] = None
_port = _DEFAULT_PORT
_db: dict[str, Any] = {
    "conn": None,
    "path": None,
    "server_only": False,
    "migrate_gate": {},
}


class _ReusableHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def _pid_path() -> str:
    from core.license_manager import _appdata_dir

    return os.path.join(_appdata_dir(), _PID_FILE)


def _pid_candidates() -> list[str]:
    """AppData pid + legacy repo config/desktop_api.pid."""
    paths = [_pid_path()]
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        paths.append(os.path.join(root, "config", _PID_FILE))
    except Exception:
        pass
    # unique preserve order
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _kill_pid(pid: int) -> None:
    if pid <= 0 or pid == os.getpid():
        return
    try:
        if os.name == "nt":
            os.system(f'taskkill /PID {pid} /F >NUL 2>&1')
        else:
            os.kill(pid, 15)
    except Exception:
        pass


def _kill_listeners_on_port(port: int = _DEFAULT_PORT) -> None:
    """Windows: free 127.0.0.1:port if a stale python API is holding it."""
    if os.name != "nt":
        return
    try:
        import subprocess

        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "TCP"],
            text=True,
            errors="ignore",
        )
        needle = f"127.0.0.1:{port}"
        pids: set[int] = set()
        for line in out.splitlines():
            if needle not in line or "LISTENING" not in line:
                continue
            parts = line.split()
            if not parts:
                continue
            try:
                pids.add(int(parts[-1]))
            except ValueError:
                continue
        for pid in pids:
            _kill_pid(pid)
    except Exception:
        pass


def _stop_stale_api_process() -> None:
    """Stop a previous desktop API started by run_desktop_api.py (same machine)."""
    for path in _pid_candidates():
        try:
            if not os.path.isfile(path):
                continue
            raw = open(path, encoding="utf-8").read().strip()
            old_pid = int(raw)
            _kill_pid(old_pid)
            try:
                os.remove(path)
            except Exception:
                pass
        except Exception:
            pass
    _kill_listeners_on_port(_DEFAULT_PORT)


def _write_pid() -> None:
    path = _pid_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except Exception:
        pass


def _clear_pid() -> None:
    try:
        os.remove(_pid_path())
    except Exception:
        pass


_ERROR_LOG = "engine_errors.log"


def _error_log_path() -> str:
    from core.license_manager import _appdata_dir

    return os.path.join(_appdata_dir(), _ERROR_LOG)


def _failure_payload(path: str, exc: Exception) -> dict:
    """A failure the shop can act on, and that we can diagnose from a screenshot.

    A bare str(exc) told the shop only "Error -3 while decompressing data:
    incorrect header check" -- no page, no step, no file. Nobody, here or there,
    could tell what had actually gone wrong. The message now names the request
    and the kind of fault, and the full traceback is appended to
    engine_errors.log in AppData so it can be sent on.
    """
    import datetime
    import traceback

    kind = type(exc).__name__
    detail = str(exc) or kind
    try:
        with open(_error_log_path(), "a", encoding="utf-8") as fh:
            fh.write(
                "\n=== %s  %s\n%s\n"
                % (
                    datetime.datetime.now().isoformat(timespec="seconds"),
                    path,
                    traceback.format_exc(),
                )
            )
    except Exception:
        pass
    hint = ""
    low = detail.lower()
    if "decompress" in low or "header check" in low or "not a gzipped file" in low:
        hint = (
            " A compressed file could not be read -- most often a backup or an "
            "update copied while it was still being written. Copy the folder "
            "again, or restore from a different backup."
        )
    elif "database is locked" in low or "database is busy" in low:
        hint = " The store file is busy; wait a moment and try again."
    elif "no such column" in low or "no such table" in low:
        hint = (
            " The store database is older than this build; reopen the store to "
            "upgrade it."
        )
    return {
        "error": "%s failed: %s.%s" % (path, detail, hint),
        "path": path,
        "kind": kind,
        "log": _error_log_path(),
    }


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    if status != 204:
        handler.wfile.write(body)


def _file_response(handler: BaseHTTPRequestHandler, file_path: str, content_type: str) -> None:
    with open(file_path, "rb") as fh:
        data = fh.read()
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _open_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
    except Exception:
        pass
    # Offline owns a real settings table. Mirror it, so switching to Online --
    # which runs on :memory: -- shows the same thresholds instead of defaults.
    try:
        from core.settings_mirror import capture_from

        capture_from(conn)
    except Exception:
        pass
    return conn


def _open_memory_shell() -> sqlite3.Connection:
    """Ephemeral schema shell — same as main.py Online `_init_database`."""
    from core.db_setup import initialise as db_initialise

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
    except Exception:
        pass
    db_initialise(conn)
    return conn


def _close_current_conn() -> None:
    old = _db.get("conn")
    _db["conn"] = None
    if old is not None:
        try:
            old.close()
        except Exception:
            pass



def _hydrate_online_memory(conn) -> None:
    """Reload villages (and durable mirror) into Online in-memory SQLite."""
    try:
        from core.village_service import hydrate_villages_for_conn

        info = hydrate_villages_for_conn(conn)
        print(
            f"[online] villages hydrated: {info.get('count', 0)} "
            f"(server_ok={info.get('server_ok')})",
            flush=True,
        )
    except Exception as exc:
        print(f"[online] villages hydrate failed: {exc}", flush=True)
    # Thresholds, reorder defaults, the show-location flag and friends live in
    # the settings table, which in Online mode is part of the throwaway :memory:
    # shell. Reload the durable mirror so they are the same values the shop set
    # last time -- and the same ones Offline shows.
    try:
        from core.settings_mirror import apply_to

        n = apply_to(conn)
        if n:
            print(f"[online] settings restored: {n}", flush=True)
    except Exception as exc:
        print(f"[online] settings hydrate failed: {exc}", flush=True)


def _adopt_online_runtime() -> sqlite3.Connection:
    """Server-only Online: never open stores/<key>/veterinary.db."""
    _close_current_conn()
    conn = _open_memory_shell()
    _db["conn"] = conn
    _db["path"] = ""
    _db["server_only"] = True
    try:
        from core.backup_manager import reload_slots_for_active_store

        reload_slots_for_active_store()
    except Exception:
        pass
    try:
        from core.online_catalog import invalidate

        invalidate()
    except Exception:
        pass
    _hydrate_online_memory(conn)
    return conn


def _online_migrate_status() -> dict[str, Any]:
    try:
        from core.online_migrate import ensure_online_server_only_ready

        gate = ensure_online_server_only_ready(auto_wipe_empty=False)
    except Exception as exc:
        gate = {"online": True, "needs_migrate": False, "has_data": False, "error": str(exc)}
    _db["migrate_gate"] = gate
    return gate


def _conn_fresh_read(conn: sqlite3.Connection | None) -> None:
    """End any read snapshot so WAL writes from the poller are visible."""
    if conn is None:
        return
    try:
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def _catalog_error_text() -> str:
    """Last online-catalog read failure, or "". Never raises: this is a poll."""
    try:
        from core.sync_prefs import is_online_mode

        if not is_online_mode():
            return ""
        from core import online_catalog

        return str(online_catalog.last_error() or "")
    except Exception:
        return ""


def _store_link_error_text() -> str:
    """Last store-link failure, or "". Never raises: this is a 10s poll."""
    try:
        from core.sync_prefs import is_online_mode

        if not is_online_mode():
            return ""
        from core import server_live as live

        return str(live.last_link_error() or "")
    except Exception:
        return ""


def _dashboard_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    from core.home_dashboard import query_dashboard_stats

    stats = query_dashboard_stats(conn)
    labels = [
        "Today Sales",
        "Today Collected",
        "Today Bills",
        "Customer Due",
        "Supplier Due",
        "Stock Value",
        "Month Sales",
        "Month Collected",
        "Month Bills",
        f"Year Sales ({stats.get('fy_label', '')})",
        f"Year Collected ({stats.get('fy_label', '')})",
        f"Year Bills ({stats.get('fy_label', '')})",
    ]
    cards = [
        {"label": labels[i], "value": stats["values"][i]}
        for i in range(min(len(labels), len(stats["values"])))
    ]
    return {
        "today": stats.get("today_str"),
        "fy_label": stats.get("fy_label"),
        # Online, a failed server read falls through to an EMPTY :memory: DB,
        # so every card above reads zero. Home used to show that as a healthy
        # day with no sales. Inventory and both history pages already say what
        # went wrong; this is Home saying it too.
        "server_error": str(stats.get("server_error") or ""),
        "cards": cards,
        "today_cards": cards[:6],
        "period_cards": cards[6:],
        "quick_actions": _home_quick_actions_payload(),
        # Both of these were saved, stored and read back perfectly and then
        # consumed by nothing: the banner width only ever resized the OLD Tk
        # home page, and the Dashboard Sections checkboxes had no reader at all
        # outside Tk. This is the payload Home already fetches, and the one the
        # appearance-saved listener refreshes -- so they apply without a restart.
        "home_banner_size": _home_banner_width(),
        # The shop's share of the banner panel (10-100), 0 when it never chose
        # one and the pixel width above still decides. A pixel number above the
        # panel's own width drew nothing new -- see layout_config.banner_width_pct.
        "home_banner_width_pct": _home_banner_width_pct(),
        "dashboard_sections": _dashboard_sections_payload(),
    }


def _home_banner_width() -> int:
    try:
        from core.layout_config import get_home_banner_size

        return int(get_home_banner_size()[0])
    except Exception:
        return 1500


def _home_banner_width_pct() -> int:
    try:
        from core.layout_config import get_home_banner_width_pct

        return int(get_home_banner_width_pct())
    except Exception:
        return 0


def _dashboard_sections_payload() -> dict[str, bool]:
    try:
        from core.column_config import get_dashboard_section_settings

        return {str(k): bool(v) for k, v in (get_dashboard_section_settings() or {}).items()}
    except Exception:
        return {}


def _home_quick_actions_payload() -> list[dict[str, Any]]:
    from core.column_config import QUICK_ACCESS_BUTTONS, get_quick_access_settings

    styles = {
        "new_bill": "success",
        "new_purchase": "primary",
        "search_medicine": "info",
        "contacts": "secondary",
        "ledger": "danger",
        "export_sales": "success",
        "export_purchases": "primary",
        "export_inventory": "info",
        "export_all": "warning",
        "alerts": "warning",
        "gst_reports": "success",
        "general_products": "indigo",
    }
    vis = get_quick_access_settings()
    out: list[dict[str, Any]] = []
    for key, text in QUICK_ACCESS_BUTTONS:
        if not vis.get(key, True):
            continue
        label = str(text)
        if " " in label:
            first, rest = label.split(" ", 1)
            if first and rest:
                label = rest
        out.append(
            {
                "key": key,
                "label": label,
                "style": styles.get(key, "primary"),
            }
        )
    return out


def _meta_payload() -> dict[str, Any]:
    from core.app_prefs import load_theme
    from core.app_version import APP_NAME, APP_VERSION
    from core.brand_assets import COMPANY_NAME, PRODUCT_TAGLINE
    from core.desktop_ui_prefs import load_desktop_ui_prefs
    from core.font_config import _load_font_size
    from core.store_manager import (
        active_store_was_auto_selected,
        get_active_display_name,
        get_active_store_key,
    )
    from core.sync_prefs import get_sync_mode, mode_label

    sync = get_sync_mode()
    ui = load_desktop_ui_prefs()
    try:
        font_size = int(_load_font_size())
    except Exception:
        font_size = 10
    return {
        "app_name": APP_NAME,
        "company_name": COMPANY_NAME,
        "tagline": PRODUCT_TAGLINE,
        "version": APP_VERSION,
        "store_key": get_active_store_key() or "",
        "store_name": get_active_display_name() or "",
        # True when a registry rebuild chose this store instead of a person.
        # Offline there is no server call to fail, so this flag is the ONLY
        # thing standing between a rebuilt registry and a shop billing all day
        # into the wrong database without a word on screen.
        "store_auto_selected": active_store_was_auto_selected(),
        "sync_mode": sync,
        "sync_label": mode_label(sync),
        "theme": load_theme(),
        "theme_pack": str(ui.get("theme_pack") or "modern"),
        "font_size": font_size,
        "api_port": _port,
        "server_time": datetime.now().isoformat(timespec="seconds"),
        "server_only": bool(_db.get("server_only")) or sync == "online",
        "needs_migrate": bool(
            (_db.get("migrate_gate") or {}).get("needs_migrate")
            and (_db.get("migrate_gate") or {}).get("has_data")
        ),
    }


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    data = json.loads(raw.decode("utf-8"))
    return data if isinstance(data, dict) else {}


class _DesktopApiHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def do_OPTIONS(self) -> None:
        _json_response(self, 204, {})

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            if path not in (
                "/api/health",
                "/api/sync/status",
                "/api/backup/close",
                "/api/purchase/import/progress",
            ):
                _conn_fresh_read(_db.get("conn"))
            if path == "/api/backup/close":
                # The closing backup of the day, asked for by the app shell just
                # before it quits. It could not live in the engine's own
                # shutdown path: on a real quit the shell hard-kills this
                # process and no Python line after that runs, so the close slot
                # was simply never filled -- only a dev-terminal Ctrl-C ever
                # reached it. Idempotent, because the shell can raise both
                # ExitRequested and Exit.
                global _CLOSE_BACKUP_DONE
                if _CLOSE_BACKUP_DONE:
                    _json_response(self, 200, {"ok": True, "skipped": "already_run"})
                    return
                _CLOSE_BACKUP_DONE = True
                try:
                    from core.backup_manager import (
                        is_auto_backup_enabled,
                        run_backup_now,
                    )
                    from core.sync_coordinator import should_run_drive_backup

                    if should_run_drive_backup() and is_auto_backup_enabled():
                        run_backup_now()
                        _json_response(self, 200, {"ok": True, "ran": True})
                        return
                    _json_response(self, 200, {"ok": True, "ran": False})
                except Exception as exc:
                    # Never let a backup problem hold the window open.
                    print(f"[backup] close backup: {exc}")
                    _json_response(self, 200, {"ok": False, "error": str(exc)})
                return

            if path == "/api/health":
                from core.sync_prefs import is_online_mode

                online = is_online_mode()
                _json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "service": "desktop-api",
                        "revision": API_REVISION,
                        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
                        "python_minor": sys.version_info.minor,
                        "port": _port,
                        "db": _db.get("conn") is not None,
                        "db_path": _db.get("path") or "",
                        "online_mode": online,
                        "server_only": bool(_db.get("server_only")) or online,
                        "needs_migrate": bool(
                            (_db.get("migrate_gate") or {}).get("needs_migrate")
                            and (_db.get("migrate_gate") or {}).get("has_data")
                        ),
                        "routes": [
                            "/api/health",
                            "/api/meta",
                            "/api/prefs",
                            "/api/settings/bundle",
                            "/api/settings/layout_lists",
                            "/api/home/dashboard",
                            "/api/home/banner",
                            "/api/brand/icon",
                            "/api/brand/logo",
                            "/api/brand/card",
                            "/api/export/options",
                            "/api/export/schedules",
                            "/api/export/run",
                            "/api/startup/alerts",
                            "/api/startup/alerts/snooze",
                            "/api/sync/status",
                            "/api/license/status",
                            "/api/voice/enabled",
                            "/api/reports/gst",
                            "/api/reports/gst/export",
                            "/api/customers/gst",
                            "/api/voice/pack",
                            "/api/login/status",
                            "/api/login/verify",
                            "/api/sales/history/delete",
                            "/api/sales/print-all",
                            "/api/sales/print-all/candidates",
                            "/api/purchase/history/delete",
                            "/api/inventory",
                            "/api/inventory/medicine",
                            "/api/inventory/reorder-prefill",
                            "/api/inventory/bulk-delete-zero",
                            "/api/inventory/bulk-delete-expired",
                            "/api/inventory/repair-from-purchases",
                            "/api/sales/history",
                            "/api/purchase/history",
                            "/api/sales/form",
                            "/api/sales/calc",
                            "/api/sales/save",
                            "/api/sales/build-line",
                            "/api/sales/print",
                            "/api/sales/bill/preview",
                            "/api/sales/bill/details",
                            "/api/sales/bill/pdf",
                            "/api/sales/autosave",
                            "/api/sales/autosave/sessions",
                            "/api/sales/autosave/resume",
                            "/api/sales/load",
                            "/api/sales/last",
                            "/api/sales/prefs",
                            "/api/sales/quick-line",
                            "/api/sales/recent",
                            "/api/medicines/batches",
                            "/api/medicines/names",
                            "/api/customers/lookup",
                            "/api/suppliers/lookup",
                            "/api/purchase/form",
                            "/api/purchase/calc",
                            "/api/purchase/save",
                            "/api/purchase/lookup-medicine",
                            "/api/purchase/merge-lines",
                            "/api/purchase/autosave",
                            "/api/purchase/load",
                            "/api/purchase/last",
                            "/api/purchase/prefs",
                            "/api/purchase/recent",
                            "/api/purchase/import/capabilities",
                            "/api/purchase/import/pick",
                            "/api/purchase/import/start",
                            "/api/purchase/import/apply",
                            "/api/purchase/import/preview",
                            "/api/purchase/medicines/search",
                            "/api/returns/summary",
                            "/api/returns/sales/bills",
                            "/api/returns/sales/bill",
                            "/api/returns/sales/save",
                            "/api/returns/purchase/search",
                            "/api/returns/purchase/load",
                            "/api/returns/purchase/save",
                            "/api/returns/purchase/return",
                            "/api/returns/sales/delete",
                            "/api/returns/purchase/delete",
                            "/api/returns/purchase/replace",
                            "/api/returns/purchase/return/pdf",
                            "/api/returns/disposal/lookup",
                            "/api/returns/disposal/submit",
                            "/api/returns/bulk/prefill",
                            "/api/returns/bulk/save",
                            "/api/license/activate",
                            "/api/license/provision-trial",
                            "/api/license/pair-key",
                            "/api/license/recheck",
                            "/api/general-products",
                            "/api/online/migrate",
                        ],
                    },
                )
                return
            if path == "/api/online/migrate":
                _json_response(self, 200, {"ok": True, **_online_migrate_status()})
                return
            if path == "/api/meta":
                _json_response(self, 200, _meta_payload())
                return
            if path == "/api/prefs":
                from core.desktop_ui_prefs import load_desktop_ui_prefs

                _json_response(self, 200, load_desktop_ui_prefs())
                return
            if path == "/api/sync/status":
                from core.sync_status import format_status_line, snapshot, take_refresh_batch
                from core.sync_prefs import is_online_mode

                refresh = take_refresh_batch()
                poller_alive = False
                try:
                    from core.sync_engine import is_sync_engine_alive

                    poller_alive = bool(is_sync_engine_alive())
                except Exception:
                    try:
                        from core import server_live as live

                        poller_alive = live.is_poller_alive()
                    except Exception:
                        pass
                _json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "status_line": format_status_line(),
                        "snapshot": snapshot(),
                        "refresh_seq": refresh.get("refresh_seq", 0),
                        "refresh_collections": refresh.get("collections") or [],
                        "poller_alive": poller_alive,
                        "online_mode": is_online_mode(),
                        # Why the dropdowns are empty, when they are. Every
                        # page already polls this every 10s, so one key here
                        # reaches all of them; the alternative was a banner in
                        # each of the twenty-odd components that draw a list.
                        "catalog_error": _catalog_error_text(),
                        # Set when this PC's store no longer resolves on the
                        # server. Every unattended caller of
                        # ensure_active_store_on_server swallows the exception,
                        # so without this the sentence reaches nobody.
                        "store_link_error": _store_link_error_text(),
                    },
                )
                return
            if path == "/api/license/status":
                from core.desktop_license_service import get_license_status

                _json_response(self, 200, get_license_status())
                return
            if path == "/api/voice/enabled":
                # The admin panel's per-store voice switch (see core/voice_switch.py).
                from core.voice_switch import voice_status

                _json_response(self, 200, voice_status())
                return
            if path == "/api/voice/pack":
                # The downloadable voice pack (core/voice_pack.py): installed? downloading?
                from core import voice_pack

                _json_response(self, 200, voice_pack.status())
                return
            if path == "/api/login/status":
                from core.desktop_login_service import get_app_login_status

                _json_response(self, 200, get_app_login_status())
                return
            if path == "/api/settings/bundle":
                from core.desktop_settings_service import get_settings_bundle

                _json_response(self, 200, get_settings_bundle(_db.get("conn")))
                return
            if path == "/api/settings/layout_lists":
                from core.desktop_settings_service import get_layout_lists

                _json_response(self, 200, get_layout_lists())
                return
            if path in ("/api/reports/gst", "/api/customers/gst"):
                # GST reports for a period (core/gst_reports.py) / a customer's GSTIN (core/customer_gst.py)
                conn = _db.get("conn")
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                try:
                    from urllib.parse import parse_qs

                    qs = parse_qs(urlparse(self.path).query)
                    if path == "/api/reports/gst":
                        from core import gst_reports

                        out = gst_reports.build(conn, (qs.get("from") or [""])[0], (qs.get("to") or [""])[0])
                    else:
                        from core import customer_gst

                        cid = (qs.get("customer_id") or [""])[0].strip()
                        out = (customer_gst.get_customer_gst(conn, cid) if cid
                               else {"customers": list(customer_gst.all_customer_gst(conn).values())})
                    _json_response(self, 200, {"ok": True, **out})
                except Exception as exc:
                    _json_response(self, 400, {"ok": False, "error": str(exc)})
                return
            if path == "/api/sales/regulars":
                # A customer's regular medicines (core/regular_medicines.py), or every list
                from core import regular_medicines as rm

                conn = _db.get("conn")
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                try:
                    from urllib.parse import parse_qs

                    cid = (parse_qs(urlparse(self.path).query).get("customer_id") or [""])[0].strip()
                    if cid:
                        _json_response(self, 200, {"ok": True, **rm.get_regulars(conn, cid)})
                    else:
                        _json_response(self, 200, {"ok": True, "lists": rm.list_regulars(conn)})
                except Exception as exc:
                    _json_response(self, 400, {"ok": False, "error": str(exc)})
                return
            if path == "/api/settings/alerts":
                from core.desktop_settings_service import get_alerts

                conn = _db.get("conn")
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                _json_response(self, 200, get_alerts(conn))
                return
            if path == "/api/settings/contacts":
                from core.desktop_settings_service import get_contacts

                conn = _db.get("conn")
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                _json_response(self, 200, get_contacts(conn))
                return
            if path == "/api/settings/payments":
                from core.desktop_settings_service import get_payments
                from urllib.parse import parse_qs

                qs = parse_qs(urlparse(self.path).query)
                kind = (qs.get("kind") or ["supplier"])[0]
                conn = _db.get("conn")
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                _json_response(self, 200, get_payments(conn, kind))
                return
            if path == "/api/settings/ledger":
                from core.desktop_settings_service import get_ledger
                from urllib.parse import parse_qs

                qs = parse_qs(urlparse(self.path).query)
                conn = _db.get("conn")
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                _json_response(
                    self,
                    200,
                    get_ledger(
                        conn,
                        kind=(qs.get("kind") or ["supplier"])[0],
                        party=(qs.get("party") or [""])[0],
                        date_from=(qs.get("from") or [""])[0],
                        date_to=(qs.get("to") or [""])[0],
                    ),
                )
                return
            if path == "/api/settings/reorder":
                from core.desktop_settings_service import get_reorder

                conn = _db.get("conn")
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                _json_response(self, 200, get_reorder(conn))
                return
            if path == "/api/settings/shelf":
                from core.desktop_settings_service import get_shelf_full

                conn = _db.get("conn")
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                _json_response(self, 200, get_shelf_full(conn))
                return
            if path == "/api/home/dashboard":
                conn = _db.get("conn")
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                _json_response(self, 200, _dashboard_payload(conn))
                return
            if path == "/api/home/banner":
                from core.layout_config import get_home_banner_path

                banner = get_home_banner_path()
                if not banner or not os.path.isfile(banner):
                    _json_response(self, 404, {"error": "Banner not found", "path": banner})
                    return
                ext = os.path.splitext(banner)[1].lower()
                ctype = {
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".gif": "image/gif",
                    ".webp": "image/webp",
                    ".bmp": "image/bmp",
                }.get(ext, "application/octet-stream")
                _file_response(self, banner, ctype)
                return

            if path == "/api/brand/icon":
                from core.app_prefs import load_theme
                from core.brand_assets import get_icon_png

                icon = get_icon_png(load_theme())
                if not icon or not os.path.isfile(icon):
                    _json_response(self, 404, {"error": "Icon not found"})
                    return
                _file_response(self, icon, "image/png")
                return

            if path == "/api/brand/logo":
                from core.app_prefs import load_theme
                from core.brand_assets import get_full_logo_png

                logo = get_full_logo_png(load_theme())
                if not logo or not os.path.isfile(logo):
                    _json_response(self, 404, {"error": "Logo not found"})
                    return
                _file_response(self, logo, "image/png")
                return

            if path == "/api/brand/card":
                from core.brand_assets import asset_path

                card = asset_path("card design.png")
                if not card or not os.path.isfile(card):
                    _json_response(self, 404, {"error": "Card not found"})
                    return
                _file_response(self, card, "image/png")
                return

            if path == "/api/settings/pharmacy/logo":
                # The preview used to print the file PATH, so the shop could not
                # tell whether it had picked the right picture until it printed
                # a bill. /api/brand/logo serves the SOFTWARE's logo, not the
                # shop's; this is the shop's.
                try:
                    from core.pharmacy_profile_io import load_pharmacy_profile

                    prof = load_pharmacy_profile(_db.get("conn")) or {}
                    logo = str(prof.get("logo_path") or "").strip()
                except Exception:
                    logo = ""
                if not logo or not os.path.isfile(logo):
                    _json_response(self, 404, {"error": "No logo selected"})
                    return
                ext = os.path.splitext(logo)[1].lower()
                mime = {
                    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
                }.get(ext, "application/octet-stream")
                _file_response(self, logo, mime)
                return

            if path == "/api/brand/status-icon":
                from urllib.parse import parse_qs

                from core.brand_assets import assets_dir, is_dark_theme
                from core.desktop_pages_service import _STATUS_ICON_FILES

                qs = parse_qs(urlparse(self.path).query)
                status = (qs.get("status") or [""])[0].strip()
                base = _STATUS_ICON_FILES.get(status)
                if not base:
                    _json_response(self, 404, {"error": "Unknown status"})
                    return
                folder = os.path.join(assets_dir(), "status")
                dark_name = base.replace(".png", "_dark.png")
                candidates = []
                if is_dark_theme():
                    candidates.append(dark_name)
                candidates.append(base)
                for name in candidates:
                    path_icon = os.path.join(folder, name)
                    if os.path.isfile(path_icon):
                        _file_response(self, path_icon, "image/png")
                        return
                _json_response(self, 404, {"error": "Icon file not found"})
                return

            if path == "/api/export/options":
                from urllib.parse import parse_qs

                from core.desktop_export_service import export_options

                qs = parse_qs(urlparse(self.path).query)
                page = (qs.get("page") or ["inventory"])[0]
                _json_response(
                    self, 200, {"page": page, "options": export_options(page)}
                )
                return

            if path == "/api/export/schedules":
                from core.desktop_export_service import list_schedules_for_export

                _json_response(self, 200, list_schedules_for_export())
                return

            if path == "/api/startup/alerts":
                from urllib.parse import parse_qs, urlparse as _up

                from core.desktop_startup_service import get_startup_alerts

                conn = _db.get("conn")
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                _q = parse_qs(_up(self.path).query)
                _json_response(
                    self,
                    200,
                    get_startup_alerts(
                        conn,
                        force=(_q.get("force") or [""])[0] == "1",
                        recheck=(_q.get("recheck") or [""])[0] == "1",
                    ),
                )
                return

            if path == "/api/settings/alert_prefs":
                from core.desktop_startup_service import get_alert_prefs

                _json_response(self, 200, get_alert_prefs(_db.get("conn")))
                return

            if path == "/api/sales/print-all/candidates":
                from urllib.parse import parse_qs

                from core import desktop_sales_service as sales_svc

                conn = _db.get("conn")
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                qs = parse_qs(urlparse(self.path).query)
                # The whole filter bar, not just the dates: the picker used to
                # offer every bill in the range while the list behind it showed
                # the handful the shop had filtered down to.
                body = {
                    "from": (qs.get("from") or [""])[0],
                    "to": (qs.get("to") or [""])[0],
                    "schedule": (qs.get("schedule") or [""])[0],
                    "q": (qs.get("q") or [""])[0],
                    "customer": (qs.get("customer") or [""])[0],
                    "medicine": (qs.get("medicine") or [""])[0],
                    "batch": (qs.get("batch") or [""])[0],
                    "due": (qs.get("due") or [""])[0],
                }
                _json_response(self, 200, sales_svc.list_print_all_candidates(conn, body))
                return

            if path == "/api/purchase/prefs":
                from core.desktop_purchase_service import purchase_runtime_prefs

                _json_response(self, 200, purchase_runtime_prefs())
                return

            if path == "/api/purchase/import/progress":
                # A cosmetic poll while a bill is being scanned. Deliberately
                # needs no database: it is read while the import holds the
                # connection, and it must never be the thing that fails.
                from urllib.parse import parse_qs

                from core.desktop_purchase_service import purchase_import_progress

                q = parse_qs(urlparse(self.path).query)
                _json_response(
                    self,
                    200,
                    purchase_import_progress(str((q.get("progress_id") or [""])[0] or "")),
                )
                return

            if path == "/api/purchase/import/preview":
                from urllib.parse import parse_qs

                from core.desktop_purchase_service import get_purchase_import_preview

                qs = parse_qs(urlparse(self.path).query)
                token = str((qs.get("import_token") or [""])[0] or "")
                result = get_purchase_import_preview(token)
                _json_response(self, 200, result)
                return

            # ── Page migration read APIs ──────────────────────────────────
            if path in (
                "/api/inventory",
                "/api/inventory/medicine",
                "/api/sales/history",
                "/api/purchase/history",
                "/api/sales/form",
                "/api/sales/recent",
                "/api/sales/last",
                "/api/sales/prefs",
                "/api/sales/load",
                "/api/purchase/form",
                "/api/purchase/recent",
                "/api/purchase/last",
                "/api/purchase/load",
                "/api/purchase/import/capabilities",
                "/api/inventory/reorder-prefill",
                "/api/sales/bill/preview",
                "/api/sales/bill/details",
                "/api/purchase/medicines/search",
                "/api/returns/summary",
                "/api/returns/sales/bills",
                "/api/returns/sales/bill",
                "/api/returns/purchase/search",
                "/api/returns/purchase/load",
                "/api/returns/purchase/return",
                "/api/returns/bulk/prefill",
                "/api/general-products",
                "/api/medicines/search",
                "/api/medicines/batches",
                "/api/medicines/names",
                "/api/customers/lookup",
                "/api/suppliers/lookup",
                "/api/sync/blocked",
            ):
                from urllib.parse import parse_qs

                from core import desktop_pages_service as pages

                conn = _db.get("conn")
                # The refused-changes list lives in a file, not the store
                # database, so it must still answer when no store is open.
                if conn is None and path != "/api/sync/blocked":
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                qs = parse_qs(urlparse(self.path).query)
                if path == "/api/inventory":
                    low_raw = (qs.get("low") or ["0"])[0]
                    _json_response(
                        self,
                        200,
                        pages.list_inventory(
                            conn,
                            q=(qs.get("q") or [""])[0],
                            type_filter=(qs.get("type") or [""])[0],
                            stock_status=(qs.get("stock") or [""])[0],
                            expiry_status=(qs.get("expiry") or [""])[0],
                            schedule=(qs.get("schedule") or [""])[0],
                            low_only=str(low_raw).strip() in ("1", "true", "yes"),
                            sort=(qs.get("sort") or [""])[0],
                        ),
                    )
                    return
                if path == "/api/inventory/medicine":
                    from core.desktop_inventory_service import get_medicine

                    mid = int((qs.get("id") or ["0"])[0] or 0)
                    result = get_medicine(conn, mid)
                    _json_response(
                        self, 200 if result.get("ok") else 404, result
                    )
                    return
                if path == "/api/inventory/reorder-prefill":
                    from core.desktop_inventory_service import reorder_medicine

                    mid = int((qs.get("medicine_id") or qs.get("id") or ["0"])[0] or 0)
                    _json_response(self, 200, reorder_medicine(conn, mid))
                    return
                if path == "/api/sales/bill/preview":
                    from core.desktop_sales_service import get_bill_preview

                    sid = int((qs.get("sale_id") or ["0"])[0] or 0)
                    _json_response(self, 200, get_bill_preview(conn, sid))
                    return
                if path == "/api/sales/bill/details":
                    from core.desktop_sales_service import get_bill_details

                    sid = int((qs.get("sale_id") or ["0"])[0] or 0)
                    _json_response(self, 200, get_bill_details(conn, sid))
                    return
                if path == "/api/medicines/search":
                    _json_response(
                        self,
                        200,
                        {
                            "medicines": pages.search_medicines(
                                conn,
                                q=(qs.get("q") or [""])[0],
                                limit=int((qs.get("limit") or ["40"])[0] or 40),
                            )
                        },
                    )
                    return
                if path == "/api/medicines/batches":
                    import json as _json

                    from core.desktop_sales_service import list_batches_for_name

                    reserved_raw = (qs.get("reserved") or [""])[0]
                    reserved: dict = {}
                    if reserved_raw:
                        try:
                            parsed = _json.loads(reserved_raw)
                            if isinstance(parsed, dict):
                                reserved = parsed
                        except Exception:
                            reserved = {}
                    _json_response(
                        self,
                        200,
                        list_batches_for_name(
                            conn,
                            (qs.get("name") or qs.get("q") or [""])[0],
                            bill_date=(qs.get("bill_date") or [""])[0],
                            reserved=reserved,
                        ),
                    )
                    return
                if path == "/api/medicines/names":
                    from core.desktop_sales_service import list_medicine_names
                    import json as _json

                    reserved_raw = (qs.get("reserved") or [""])[0]
                    reserved: dict = {}
                    if reserved_raw:
                        try:
                            parsed = _json.loads(reserved_raw)
                            if isinstance(parsed, dict):
                                reserved = parsed
                        except Exception:
                            reserved = {}
                    _json_response(
                        self,
                        200,
                        list_medicine_names(
                            conn,
                            q=(qs.get("q") or qs.get("name") or [""])[0],
                            limit=int((qs.get("limit") or ["40"])[0] or 40),
                            bill_date=(qs.get("bill_date") or [""])[0],
                            reserved=reserved,
                        ),
                    )
                    return
                if path == "/api/customers/lookup":
                    from core.customer_service import get_customer_by_name

                    name = (qs.get("name") or qs.get("q") or [""])[0]
                    force = (qs.get("force") or ["1"])[0] not in (
                        "0",
                        "false",
                        "False",
                    )
                    found = get_customer_by_name(
                        conn, name, force_refresh=force
                    )
                    if not found:
                        _json_response(
                            self,
                            200,
                            {
                                "ok": True,
                                "found": False,
                                "customer": None,
                            },
                        )
                        return
                    _json_response(
                        self,
                        200,
                        {
                            "ok": True,
                            "found": True,
                            "customer": {
                                "id": int(found.get("id") or 0),
                                "name": found.get("name") or name,
                                "phone": found.get("phone") or "",
                                "address": found.get("address") or "",
                                "due": float(
                                    found.get("total_due")
                                    or found.get("due")
                                    or 0
                                ),
                                "credit": float(
                                    found.get("total_credit")
                                    or found.get("credit")
                                    or 0
                                ),
                            },
                        },
                    )
                    return
                if path == "/api/suppliers/lookup":
                    from core.online_catalog import find_supplier_by_name
                    from core.purchase_service import get_supplier_due
                    from core.sync_prefs import is_online_mode

                    name = (qs.get("name") or qs.get("q") or [""])[0]
                    force = (qs.get("force") or ["1"])[0] not in (
                        "0",
                        "false",
                        "False",
                    )
                    found = None
                    try:
                        if is_online_mode():
                            found = find_supplier_by_name(
                                name, force=force
                            )
                    except Exception:
                        found = None
                    if not found:
                        # Offline / fallback: due by name + optional SQLite row.
                        due, credit = (0.0, 0.0)
                        try:
                            due, credit = get_supplier_due(
                                conn, name, force_refresh=False
                            )
                        except Exception:
                            pass
                        row = None
                        try:
                            row = conn.execute(
                                "SELECT id, name, COALESCE(phone,''), "
                                "COALESCE(address,''), COALESCE(gstin,''), "
                                "COALESCE(dl_numbers,'') "
                                "FROM suppliers WHERE UPPER(TRIM(name))=UPPER(TRIM(?)) "
                                "ORDER BY id DESC LIMIT 1",
                                (name,),
                            ).fetchone()
                        except Exception:
                            row = None
                        if not row and due == 0 and credit == 0:
                            _json_response(
                                self,
                                200,
                                {
                                    "ok": True,
                                    "found": False,
                                    "supplier": None,
                                },
                            )
                            return
                        _json_response(
                            self,
                            200,
                            {
                                "ok": True,
                                "found": True,
                                "supplier": {
                                    "id": int(row[0]) if row else 0,
                                    "name": (row[1] if row else name) or name,
                                    "phone": (row[2] if row else "") or "",
                                    "address": (row[3] if row else "") or "",
                                    "gstin": (row[4] if row else "") or "",
                                    "dl": (row[5] if row else "") or "",
                                    "due": float(due),
                                    "credit": float(credit),
                                },
                            },
                        )
                        return
                    due = float(found.get("total_due") or 0)
                    credit = float(found.get("total_credit") or 0)
                    _json_response(
                        self,
                        200,
                        {
                            "ok": True,
                            "found": True,
                            "supplier": {
                                "id": int(found.get("id") or 0),
                                "name": found.get("name") or name,
                                "phone": found.get("phone") or "",
                                "address": found.get("address") or "",
                                "gstin": found.get("gstin") or "",
                                "dl": found.get("dl_numbers")
                                or found.get("dl")
                                or "",
                                "due": due,
                                "credit": credit,
                            },
                        },
                    )
                    return
                if path == "/api/sales/history":
                    _json_response(
                        self,
                        200,
                        pages.list_sales_history(
                            conn,
                            q=(qs.get("q") or [""])[0],
                            from_date=(qs.get("from") or [""])[0],
                            to_date=(qs.get("to") or [""])[0],
                            medicine=(qs.get("medicine") or [""])[0],
                            batch=(qs.get("batch") or [""])[0],
                            customer=(qs.get("customer") or [""])[0],
                            due=(qs.get("due") or [""])[0],
                            schedule=(qs.get("schedule") or [""])[0],
                            sort=(qs.get("sort") or [""])[0],
                        ),
                    )
                    return
                if path == "/api/purchase/history":
                    _json_response(
                        self,
                        200,
                        pages.list_purchase_history(
                            conn,
                            q=(qs.get("q") or [""])[0],
                            from_date=(qs.get("from") or [""])[0],
                            to_date=(qs.get("to") or [""])[0],
                            supplier=(qs.get("supplier") or [""])[0],
                            due=(qs.get("due") or [""])[0],
                            schedule=(qs.get("schedule") or [""])[0],
                            medicine=(qs.get("medicine") or [""])[0],
                            batch=(qs.get("batch") or [""])[0],
                            sort=(qs.get("sort") or [""])[0],
                        ),
                    )
                    return
                if path == "/api/sync/blocked":
                    # Changes the server REFUSED. They are parked rather than
                    # retried forever, and the shop needs to see them and decide.
                    from core.online_mutation_queue import blocked_rows

                    rows = blocked_rows()
                    _json_response(
                        self,
                        200,
                        {
                            "ok": True,
                            "count": len(rows),
                            "rows": [
                                {
                                    "id": str(r.get("id") or ""),
                                    "collection": str(r.get("collection") or ""),
                                    "op": str(r.get("op") or ""),
                                    "local_id": r.get("local_id"),
                                    "attempts": r.get("attempts"),
                                    "error": str(r.get("last_error") or ""),
                                }
                                for r in rows
                            ],
                        },
                    )
                    return
                if path == "/api/sales/form":
                    if (qs.get("hint_only") or [""])[0] in ("1", "true"):
                        # The Invoice No box follows the Bill Date: a back-dated
                        # bill belongs to that date's financial-year series.
                        bill_date_q = (qs.get("bill_date") or [""])[0].strip()
                        _json_response(
                            self,
                            200,
                            {
                                "next_bill_hint": pages._next_sales_bill_hint(
                                    conn, bill_date_q or None
                                ),
                                "bill_date": bill_date_q,
                            },
                        )
                        return
                    _json_response(self, 200, pages.sales_form_defaults(conn))
                    return
                if path == "/api/sales/recent":
                    from core.desktop_sales_service import recent_sales

                    _json_response(
                        self,
                        200,
                        recent_sales(
                            conn, limit=int((qs.get("limit") or ["5"])[0] or 5)
                        ),
                    )
                    return
                if path == "/api/sales/last":
                    from core.desktop_sales_service import last_sale

                    result = last_sale(conn)
                    _json_response(
                        self, 200 if result.get("ok") else 404, result
                    )
                    return
                if path == "/api/sales/load":
                    from core.desktop_sales_service import load_sale

                    sid = int((qs.get("id") or ["0"])[0] or 0)
                    result = load_sale(conn, sid)
                    _json_response(
                        self, 200 if result.get("ok") else 404, result
                    )
                    return
                if path == "/api/sales/prefs":
                    from core.desktop_sales_service import sales_runtime_prefs

                    _json_response(self, 200, sales_runtime_prefs(conn))
                    return
                if path == "/api/purchase/form":
                    _json_response(self, 200, pages.purchase_form_defaults(conn))
                    return
                if path == "/api/purchase/recent":
                    from core.desktop_purchase_service import recent_purchases

                    _json_response(
                        self,
                        200,
                        recent_purchases(
                            conn, limit=int((qs.get("limit") or ["5"])[0] or 5)
                        ),
                    )
                    return
                if path == "/api/purchase/last":
                    from core.desktop_purchase_service import last_purchase

                    result = last_purchase(conn)
                    _json_response(
                        self, 200 if result.get("ok") else 404, result
                    )
                    return
                if path == "/api/purchase/load":
                    from core.desktop_purchase_service import load_purchase

                    pid = int((qs.get("id") or ["0"])[0] or 0)
                    result = load_purchase(conn, pid)
                    _json_response(
                        self, 200 if result.get("ok") else 404, result
                    )
                    return
                if path == "/api/purchase/import/capabilities":
                    from core.desktop_purchase_service import import_capabilities

                    _json_response(self, 200, import_capabilities())
                    return
                if path == "/api/purchase/medicines/search":
                    from core.desktop_purchase_service import (
                        search_purchase_medicines,
                    )

                    _json_response(
                        self,
                        200,
                        search_purchase_medicines(
                            conn,
                            q=(qs.get("q") or qs.get("name") or [""])[0],
                            limit=int((qs.get("limit") or ["50"])[0] or 50),
                        ),
                    )
                    return
                if path == "/api/returns/summary":
                    _json_response(self, 200, pages.returns_bundle(conn))
                    return
                if path == "/api/returns/sales/bills":
                    from urllib.parse import parse_qs

                    from core import desktop_returns_service as ret_svc

                    qs = parse_qs(urlparse(self.path).query)
                    _json_response(
                        self,
                        200,
                        ret_svc.search_sales_bills(
                            conn,
                            q=(qs.get("q") or [""])[0],
                            medicine=(qs.get("medicine") or [""])[0],
                        ),
                    )
                    return
                if path == "/api/returns/sales/bill":
                    from urllib.parse import parse_qs

                    from core import desktop_returns_service as ret_svc

                    qs = parse_qs(urlparse(self.path).query)
                    sale_id = int((qs.get("sale_id") or ["0"])[0] or 0)
                    _json_response(
                        self, 200, ret_svc.load_sales_bill_for_return(conn, sale_id)
                    )
                    return
                if path == "/api/returns/purchase/search":
                    from urllib.parse import parse_qs

                    from core import desktop_returns_service as ret_svc

                    qs = parse_qs(urlparse(self.path).query)
                    _json_response(
                        self,
                        200,
                        ret_svc.search_purchases_for_return(
                            conn, (qs.get("q") or [""])[0]
                        ),
                    )
                    return
                if path == "/api/returns/purchase/load":
                    from urllib.parse import parse_qs

                    from core import desktop_returns_service as ret_svc

                    qs = parse_qs(urlparse(self.path).query)
                    pid = int((qs.get("purchase_id") or ["0"])[0] or 0)
                    _json_response(
                        self, 200, ret_svc.load_purchase_for_return(conn, pid)
                    )
                    return
                if path == "/api/returns/purchase/return":
                    from urllib.parse import parse_qs

                    from core import desktop_returns_service as ret_svc

                    qs = parse_qs(urlparse(self.path).query)
                    rid = int((qs.get("return_id") or ["0"])[0] or 0)
                    result = ret_svc.get_purchase_return_details(conn, rid)
                    status = 200 if result.get("ok") else 404
                    _json_response(self, status, result)
                    return
                if path == "/api/returns/bulk/prefill":
                    from urllib.parse import parse_qs

                    from core import desktop_returns_service as ret_svc

                    qs = parse_qs(urlparse(self.path).query)
                    inc_exp = (qs.get("include_expired") or ["1"])[0] != "0"
                    inc_near = (qs.get("include_near_expiry") or ["1"])[0] != "0"
                    _json_response(
                        self,
                        200,
                        ret_svc.bulk_purchase_prefill(
                            conn,
                            include_expired=inc_exp,
                            include_near_expiry=inc_near,
                        ),
                    )
                    return
                if path == "/api/general-products":
                    from urllib.parse import parse_qs

                    from core import desktop_general_products_service as gp_svc

                    qs = parse_qs(urlparse(self.path).query)
                    _json_response(
                        self,
                        200,
                        gp_svc.list_general_products(conn, (qs.get("q") or [""])[0]),
                    )
                    return

            _json_response(self, 404, {"error": "Not found", "path": path})
        except Exception as exc:
            _json_response(self, 500, _failure_payload(path, exc))

    def do_PUT(self) -> None:
        self._handle_settings_write()

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path.startswith("/api/settings/"):
            self._handle_settings_write()
            return
        try:
            if path in ("/api/voice/pack/install", "/api/voice/pack/cancel", "/api/voice/pack/remove",
                        "/api/voice/pack/start", "/api/voice/pack/check"):
                from core import voice_pack

                action = path.rsplit("/", 1)[-1]
                _json_response(self, 200, getattr(voice_pack, action)() if action != "start"
                               else voice_pack.start_service())
                return
            if path == "/api/prefs":
                from core.desktop_ui_prefs import save_desktop_ui_prefs

                body = _read_json_body(self)
                _json_response(self, 200, save_desktop_ui_prefs(body))
                return
            if path in (
                "/api/sales/calc",
                "/api/sales/save",
                "/api/sales/build-line",
                "/api/sales/print",
                "/api/sales/autosave",
                "/api/sales/autosave/discard",
                "/api/sales/autosave/sessions",
                "/api/sales/autosave/resume",
                "/api/sales/quick-line",
                "/api/sales/history/delete",
                "/api/sales/print-all",
                "/api/sales/print-all/candidates",
            ):
                from core import desktop_sales_service as sales_svc

                body = _read_json_body(self)
                conn = _db.get("conn")
                if conn is None and path != "/api/sales/quick-line":
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                if path == "/api/sales/calc":
                    result = sales_svc.calc_sale(conn, body)
                elif path == "/api/sales/build-line":
                    result = sales_svc.build_line(conn, body)
                elif path == "/api/sales/quick-line":
                    result = sales_svc.build_quick_sale_line(body)
                elif path == "/api/sales/autosave":
                    result = sales_svc.autosave_sale(conn, body)
                elif path == "/api/sales/autosave/discard":
                    result = sales_svc.discard_autosave(conn, body)
                elif path == "/api/sales/autosave/sessions":
                    result = sales_svc.list_autosave_sessions(conn, body)
                elif path == "/api/sales/autosave/resume":
                    result = sales_svc.resume_autosave(conn, body)
                elif path == "/api/sales/print":
                    result = sales_svc.print_sale(
                        conn, body, db_path=_db.get("path")
                    )
                elif path == "/api/sales/history/delete":
                    result = sales_svc.delete_saved_sale(conn, body)
                elif path in ("/api/sales/print-all", "/api/sales/print-all/candidates"):
                    if path.endswith("/candidates"):
                        result = sales_svc.list_print_all_candidates(conn, body)
                    else:
                        result = sales_svc.print_all_sales(conn, body)
                else:
                    result = sales_svc.save_sale(conn, body)
                status = 200
                if result.get("need_confirm"):
                    status = 409
                elif not result.get("ok"):
                    status = 400
                _json_response(self, status, result)
                return
            if path in (
                "/api/purchase/calc",
                "/api/purchase/save",
                "/api/purchase/lookup-medicine",
                "/api/purchase/merge-lines",
                "/api/purchase/autosave",
                "/api/purchase/autosave/discard",
                "/api/sync/blocked/retry",
                "/api/sync/blocked/discard",
                "/api/purchase/import/pick",
                "/api/purchase/import/start",
                "/api/purchase/import/apply",
                "/api/purchase/import/cancel",
                "/api/purchase/register-medicine",
                "/api/purchase/history/delete",
                "/api/inventory/medicine/update",
                "/api/inventory/medicine/delete",
            ):
                from core import desktop_purchase_service as purchase_svc
                from core import desktop_inventory_service as inv_svc

                body = _read_json_body(self)
                conn = _db.get("conn")
                if conn is None and path not in (
                    "/api/purchase/import/cancel",
                    "/api/purchase/import/pick",
                    # These act on the pending-changes queue, which is a file in
                    # AppData -- no store database needed.
                    "/api/sync/blocked/retry",
                    "/api/sync/blocked/discard",
                ):
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                if path == "/api/purchase/calc":
                    result = purchase_svc.calc_purchase(conn, body)
                elif path == "/api/purchase/lookup-medicine":
                    result = purchase_svc.lookup_medicine(conn, body)
                elif path == "/api/purchase/merge-lines":
                    # "Junya bill madhe ughad": the rows in the refused tab are added to the
                    # purchase that already holds this bill number, not thrown away.
                    result = purchase_svc.merge_lines_into_purchase(conn, body)
                elif path == "/api/purchase/autosave":
                    result = purchase_svc.autosave_purchase(conn, body)
                elif path == "/api/purchase/autosave/discard":
                    result = purchase_svc.discard_autosave(conn, body)
                elif path == "/api/sync/blocked/retry":
                    from core.online_mutation_queue import retry_blocked

                    n = retry_blocked(str(body.get("id") or ""))
                    result = {
                        "ok": True,
                        "retried": n,
                        "message": f"{n} refused change(s) queued again.",
                    }
                elif path == "/api/sync/blocked/discard":
                    from core.online_mutation_queue import discard_blocked

                    n = discard_blocked(str(body.get("id") or ""))
                    result = {
                        "ok": True,
                        "discarded": n,
                        "message": f"{n} refused change(s) abandoned.",
                    }
                elif path == "/api/purchase/import/pick":
                    result = purchase_svc.pick_purchase_import_files()
                elif path == "/api/purchase/import/start":
                    result = purchase_svc.start_purchase_import(conn, body)
                elif path == "/api/purchase/import/apply":
                    result = purchase_svc.apply_purchase_import(conn, body)
                elif path == "/api/purchase/import/cancel":
                    result = purchase_svc.cancel_purchase_import(body)
                elif path == "/api/purchase/register-medicine":
                    result = purchase_svc.register_purchase_medicine(conn, body)
                elif path == "/api/purchase/history/delete":
                    result = purchase_svc.delete_saved_purchase(conn, body)
                elif path == "/api/inventory/medicine/update":
                    result = inv_svc.update_medicine(conn, body)
                elif path == "/api/inventory/medicine/delete":
                    result = inv_svc.delete_medicine(conn, body)
                else:
                    result = purchase_svc.save_purchase_bill(conn, body)
                status = 200
                if result.get("need_confirm"):
                    status = 409
                elif not result.get("ok"):
                    status = 400
                _json_response(self, status, result)
                return
            if path == "/api/export/run":
                from core.desktop_export_service import export_to_file, run_export

                body = _read_json_body(self)
                conn = _db.get("conn")
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                fmt = str(body.get("format") or "").strip().lower()
                if fmt and fmt not in ("json", "data"):
                    result = export_to_file(
                        conn,
                        str(body.get("page") or ""),
                        str(body.get("report") or "current_view"),
                        fmt,
                        from_date=str(body.get("from") or ""),
                        to_date=str(body.get("to") or ""),
                        current_columns=body.get("columns"),
                        current_rows=body.get("rows"),
                        schedule=body.get("schedule")
                        if isinstance(body.get("schedule"), dict)
                        else None,
                        do_print=bool(body.get("print") or body.get("do_print")),
                        print_to=str(body.get("print_to") or ""),
                        page_layout=str(body.get("page_layout") or ""),
                    )
                    status = 200 if result.get("ok") else 400
                    _json_response(self, status, result)
                    return
                result = run_export(
                    conn,
                    str(body.get("page") or ""),
                    str(body.get("report") or "current_view"),
                    from_date=str(body.get("from") or ""),
                    to_date=str(body.get("to") or ""),
                    current_columns=body.get("columns"),
                    current_rows=body.get("rows"),
                    schedule=body.get("schedule")
                    if isinstance(body.get("schedule"), dict)
                    else None,
                )
                if result.get("error"):
                    _json_response(self, 400, result)
                    return
                _json_response(self, 200, result)
                return
            if path == "/api/startup/alerts/snooze":
                from core.desktop_startup_service import snooze_startup_alerts

                _json_response(self, 200, snooze_startup_alerts())
                return
            if path == "/api/startup/alerts/action":
                from core.desktop_startup_service import startup_alert_action

                conn = _db.get("conn")
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                body = _read_json_body(self)
                _json_response(self, 200, startup_alert_action(conn, body))
                return
            if path == "/api/license/activate":
                from core.desktop_license_service import activate_license

                body = _read_json_body(self)
                result = activate_license(body)
                status = 200 if result.get("ok") else 400
                _json_response(self, status, result)
                return
            if path == "/api/license/provision-trial":
                # Two answers: the shop name and Online or Offline. No username,
                # no password, no device key copied out of a file.
                from core.desktop_license_service import activate_trial

                body = _read_json_body(self)
                result = activate_trial(body)
                status = 200 if result.get("ok") else 400
                _json_response(self, status, result)
                return
            if path == "/api/license/pair-key":
                # "I already have a shop" -- the SC- key the owner can see in
                # the admin panel. Pairs by KEY only: it can reach exactly the
                # one store that key belongs to, and needs no administrator
                # credential (see desktop_license_service.pair_with_store_key).
                from core.desktop_license_service import pair_with_store_key

                body = _read_json_body(self)
                result = pair_with_store_key(body)
                status = 200 if result.get("ok") else 400
                _json_response(self, status, result)
                return
            if path == "/api/license/recheck":
                # "Try again now" from the blocked screen. One forced look for a
                # licence, then the fresh status.
                from core.desktop_license_service import recheck_license

                _json_response(self, 200, recheck_license())
                return
            if path == "/api/login/verify":
                from core.desktop_login_service import verify_app_login

                body = _read_json_body(self)
                result = verify_app_login(body)
                status = 200 if result.get("ok") else 401
                _json_response(self, status, result)
                return
            if path == "/api/sales/bill/pdf":
                from core.desktop_sales_service import save_bill_pdf

                body = _read_json_body(self)
                conn = _db.get("conn")
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                sid = int(body.get("sale_id") or 0)
                result = save_bill_pdf(conn, sid)
                status = 200 if result.get("ok") else 400
                _json_response(self, status, result)
                return
            if path in (
                "/api/inventory/bulk-delete-zero",
                "/api/inventory/bulk-delete-expired",
                "/api/inventory/repair-from-purchases",
            ):
                from core.desktop_inventory_service import delete_expired, delete_zero_stock
                if path == "/api/inventory/repair-from-purchases":
                    from core.purchase_service import repair_purchased_medicines_online
                    result = repair_purchased_medicines_online()
                    _json_response(self, 200 if result.get("ok") else 400, result)
                    return

                conn = _db.get("conn")
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                if path.endswith("zero"):
                    result = delete_zero_stock(conn)
                else:
                    result = delete_expired(conn)
                status = 200 if result.get("ok") else 400
                _json_response(self, status, result)
                return
            if path in (
                "/api/returns/sales/save",
                "/api/returns/purchase/save",
                "/api/returns/sales/delete",
                "/api/returns/purchase/delete",
                "/api/returns/purchase/replace",
                "/api/returns/purchase/return/pdf",
                "/api/returns/disposal/lookup",
                "/api/returns/disposal/submit",
            ):
                from core import desktop_returns_service as ret_svc

                body = _read_json_body(self)
                conn = _db.get("conn")
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                if path == "/api/returns/sales/save":
                    result = ret_svc.save_sales_return(conn, body)
                elif path == "/api/returns/purchase/save":
                    result = ret_svc.save_purchase_return(conn, body)
                elif path == "/api/returns/sales/delete":
                    result = ret_svc.delete_sales_return(conn, body)
                elif path == "/api/returns/purchase/delete":
                    result = ret_svc.delete_purchase_return(conn, body)
                elif path == "/api/returns/purchase/replace":
                    result = ret_svc.replace_purchase_return(conn, body)
                elif path == "/api/returns/purchase/return/pdf":
                    result = ret_svc.save_purchase_return_pdf(
                        conn, int(body.get("return_id") or body.get("id") or 0)
                    )
                elif path == "/api/returns/disposal/lookup":
                    result = ret_svc.lookup_disposal_medicine(conn, body)
                else:
                    result = ret_svc.submit_disposal(conn, body)
                status = 200 if result.get("ok") else 400
                _json_response(self, status, result)
                return
            if path == "/api/returns/bulk/save":
                from core import desktop_returns_service as ret_svc

                body = _read_json_body(self)
                conn = _db.get("conn")
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                result = ret_svc.bulk_purchase_save(conn, body)
                status = 200 if result.get("ok") else 400
                _json_response(self, status, result)
                return
            if path in (
                "/api/general-products/save",
                "/api/general-products/delete",
            ):
                from core import desktop_general_products_service as gp_svc

                body = _read_json_body(self)
                conn = _db.get("conn")
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                if path.endswith("/save"):
                    result = gp_svc.save_general_product(conn, body)
                else:
                    result = gp_svc.delete_general_product(conn, body)
                status = 200 if result.get("ok") else 400
                _json_response(self, status, result)
                return
            _json_response(self, 404, {"error": "Not found", "path": path})
        except Exception as exc:
            _json_response(self, 500, _failure_payload(path, exc))

    def _handle_settings_write(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            from core import desktop_settings_service as svc

            body = _read_json_body(self)
            conn = _db.get("conn")
            if path in ("/api/settings/appearance", "/api/settings/save/appearance"):
                result = svc.save_appearance(body)
                if result.get("error"):
                    _json_response(self, 400, result)
                    return
                _json_response(self, 200, result)
                return
            if path in ("/api/settings/layout_lists", "/api/settings/save/layout_lists"):
                _json_response(self, 200, svc.save_layout_lists(body))
                return
            if path in ("/api/settings/sales_billing", "/api/settings/save/sales_billing"):
                _json_response(self, 200, svc.save_sales_billing(body))
                return
            if path in ("/api/settings/pharmacy", "/api/settings/save/pharmacy"):
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                _json_response(self, 200, svc.save_pharmacy(conn, body))
                return
            if path in ("/api/settings/system", "/api/settings/save/system"):
                # Going Online -> Offline used to hand over a brand-new EMPTY
                # store: no medicines, nothing sellable. Fill the local copy
                # from the server FIRST, while the session is still online and
                # authenticated, then flip the mode.
                prepared = None
                if str(body.get("sync_mode") or "").strip().lower() == "offline":
                    try:
                        from core.sync_prefs import is_online_mode

                        if is_online_mode():
                            from core.online_migrate import (
                                download_store_for_offline,
                            )

                            prepared = download_store_for_offline()
                    except Exception as exc:
                        # Switching offline BECAUSE the network died is the
                        # normal case, so never block the switch -- report it.
                        prepared = {"ok": False, "error": str(exc)}

                # Going Offline -> Online, upload what this PC has been working on
                # FIRST, while the local store is still the open connection.
                #
                # Online is server-only: the moment the mode flips, the local file
                # stops being read. A shop that had been offline for weeks would
                # simply stop seeing its own bills -- they were still on disk, but
                # nothing showed them and nothing sent them. The old flow left this
                # to a separate "Push to server" button that nobody knew to press.
                # The local file is NOT deleted here; it stays as a safety copy.
                # Before any of that: if the server already has a DIFFERENT store
                # under this store's name, stop. Pushing would merge this shop's
                # books into that one, and pairing would show theirs here. The
                # shop is asked to rename first -- which is only possible while
                # still Offline, so the check has to happen before the flip.
                #
                # The check used to read the whole account's store list, which
                # meant signing this shop's PC in as the vendor ADMINISTRATOR
                # with a password compiled into the build -- on a button a
                # shopkeeper presses. That credential is gone (see
                # core/admin_session.py), so the list is no longer readable from
                # here, and the pre-flight is gone with it. What it guarded
                # against cannot happen any more either: an Offline shop holds
                # no SC- key, so after the flip ensure_online_store_link refuses
                # outright rather than adopting anything, and once a key IS
                # entered it names one store and no name is ever compared
                # (server_live._link_by_pairing_key). The 409 the browser used
                # to receive for this, store_name_taken_on_server, can no longer
                # be raised from here.
                pushed = None
                # connect_existing means "this PC belongs to a store that is
                # ALREADY on the server" -- someone else's shop, with its own
                # ledger. Uploading this PC's local rows into it is the same
                # disaster as the one the join is meant to repair, running in
                # the other direction: shop A's bills land in shop B's books.
                # The flag was already honoured for the name check above and
                # never checked here.
                joining_existing = bool(body.get("connect_existing"))
                upload_into_joined = bool(body.get("upload_local_into_joined_store"))
                if str(body.get("sync_mode") or "").strip().lower() == "online":
                    try:
                        from core.sync_prefs import is_online_mode

                        if joining_existing and not upload_into_joined:
                            pushed = {"ok": True, "skipped": True, "error": ""}
                        elif not is_online_mode() and conn is not None:
                            from core.online_migrate import _local_business_counts
                            from core.server_sync import (
                                push_active_store_to_server_detailed,
                            )

                            counts = _local_business_counts(conn)
                            if any(int(n or 0) > 0 for n in counts.values()):
                                pushed = push_active_store_to_server_detailed(conn)
                            # and the regular-medicine lists / customer GSTINs made
                            # Offline, which live in settings (core/store_kv_carry.py)
                            from core.store_kv_carry import try_push

                            carried = try_push(conn)
                            if pushed is not None and not carried.get("ok"):
                                pushed = {**pushed, "error": pushed.get("error") or
                                          f"regular medicines / GSTINs: {carried.get('error')}"}
                    except Exception as exc:
                        # Never block the switch -- a shop with no internet still
                        # needs to be able to choose Online and retry later.
                        pushed = {"ok": False, "error": str(exc)}

                result = svc.save_system(body, conn=conn)
                if joining_existing and not upload_into_joined:
                    result = {
                        **result,
                        "upload_skipped_reason": (
                            "This PC joined a store that already exists on the "
                            "server. Its local records were NOT uploaded into "
                            "that store."
                        ),
                    }
                if "sync_mode" in body:
                    reopen = reopen_active_store()
                    result = {**result, **reopen, "sync_reopened": True}
                if prepared is not None:
                    result = {
                        **result,
                        "offline_prepared": bool(prepared.get("ok")),
                        "offline_records": prepared.get("rows", 0),
                        "offline_error": prepared.get("error") or "",
                    }
                if pushed is not None:
                    # push_store_conn_detailed reports {"upserted", "failed",
                    # "failures", "per_collection"} -- there is no "ok" key, so
                    # success is "nothing was rejected".
                    failed = int(pushed.get("failed") or 0)
                    result = {
                        **result,
                        "uploaded": not failed and not pushed.get("error"),
                        "uploaded_records": int(pushed.get("upserted") or 0),
                        "uploaded_failed": failed,
                        "upload_error": (
                            pushed.get("error")
                            or ("; ".join(pushed.get("failures") or [])[:300] if failed else "")
                        ),
                    }
                _json_response(self, 200, result)
                return
            if path in ("/api/settings/import", "/api/settings/save/import"):
                _json_response(self, 200, svc.save_import_prefs(body))
                return
            if path == "/api/settings/alert_prefs":
                from core.desktop_startup_service import save_alert_prefs

                _json_response(self, 200, save_alert_prefs(conn, body))
                return
            if path in ("/api/settings/thresholds", "/api/settings/save/thresholds"):
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                _json_response(self, 200, svc.save_thresholds(conn, body))
                return
            if path in ("/api/settings/contacts", "/api/settings/contacts/mutate"):
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                _json_response(self, 200, svc.mutate_contact(conn, body))
                return
            if path in ("/api/settings/payments", "/api/settings/payments/save"):
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                _json_response(self, 200, svc.save_payment(conn, body))
                return
            if path in ("/api/settings/payments/delete",):
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                _json_response(self, 200, svc.delete_payment(conn, body))
                return
            if path in ("/api/settings/shelf", "/api/settings/shelf/mutate"):
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                _json_response(self, 200, svc.mutate_shelf(conn, body))
                return
            if path in ("/api/settings/system/action",):
                # Preparing an offline copy must work in Online mode, where there
                # is deliberately NO local database open -- that is precisely when
                # you want it. Handle it before the conn guard; it opens its own.
                if str(body.get("action") or "").strip().lower() in (
                    "download_offline",
                    "prepare_offline",
                ):
                    try:
                        from core.online_migrate import download_store_for_offline

                        _json_response(
                            self, 200, download_store_for_offline()
                        )
                    except Exception as exc:
                        _json_response(self, 200, {"ok": False, "error": str(exc)})
                    return
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                from core.admin_gate import AdminPinRequired

                try:
                    result = svc.system_action(conn, body)
                except AdminPinRequired as exc:
                    # A distinct answer, so the screen can ask for the PIN rather
                    # than showing a generic failure the shop cannot act on.
                    _json_response(
                        self,
                        200,
                        {
                            "ok": False,
                            "code": "admin_pin_wrong" if exc.wrong else "admin_pin_required",
                            "admin_action": exc.action,
                            "error": str(exc),
                        },
                    )
                    return
                action = str(body.get("action") or "").strip().lower()
                # A join repoints this PC at a different store on the server,
                # so the open database and every cached read belong to the old
                # one. Same treatment as a switch.
                if action in ("switch_store", "join_server_store") and result.get("ok"):
                    reopen = reopen_active_store()
                    result = {**result, **reopen}
                if action in ("online_migrate_push", "online_migrate_wipe") and result.get(
                    "ok"
                ):
                    reopen = reopen_active_store()
                    result = {**result, **reopen, "server_only": True}
                if action in (
                    "restore_drive_backup",
                    "restore_usb_backup",
                    "sync_from_drive",
                    "restore_active_store",
                    "danger_wipe",
                ) and result.get("ok"):
                    # Live conn was closed during restore; reopen file handles
                    # so Restart is not the only way to see the new DB.
                    reopen = reopen_active_store()
                    result = {
                        **result,
                        **reopen,
                        "needs_restart": True,
                        "message": str(
                            result.get("message")
                            or reopen.get("message")
                            or "Database updated. Restart the app."
                        ),
                    }
                _json_response(self, 200, result)
                return
            if path in ("/api/settings/import/action",):
                _json_response(self, 200, svc.import_action(conn, body))
                return
            if path in ("/api/settings/reorder/action",):
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                _json_response(self, 200, svc.reorder_action(conn, body))
                return
            if path in ("/api/settings/alerts/action",):
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                from core.desktop_alert_service import alert_action

                _json_response(self, 200, alert_action(conn, body))
                return
            if path in ("/api/reports/gst/export", "/api/customers/gst"):
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                try:
                    if path == "/api/reports/gst/export":
                        from core import gst_reports

                        _json_response(self, 200, gst_reports.export(conn, body))
                    else:
                        from core import customer_gst

                        out = customer_gst.save_customer_gst(conn, body.get("customer_id"), body.get("gstin"),
                                                             str(body.get("legal_name") or ""),
                                                             str(body.get("since") or ""))
                        _json_response(self, 200, {"ok": True, **out})
                except Exception as exc:
                    _json_response(self, 400, {"ok": False, "error": str(exc)})
                return
            if path in ("/api/sales/regulars",):
                from core import regular_medicines as rm

                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                try:
                    out = rm.save_regulars(conn, body.get("customer_id"), str(body.get("customer") or ""),
                                           str(body.get("phone") or ""), body.get("items") or [])
                    _json_response(self, 200, {"ok": True, **out})
                except Exception as exc:
                    _json_response(self, 400, {"ok": False, "error": str(exc)})
                return
            if path in ("/api/settings/alerts/export",):
                # Alert & Monitoring: CSV / Excel / PDF, or dot matrix / normal printer
                from core.desktop_export_service import export_alert_sections

                result = export_alert_sections(body)
                _json_response(self, 200 if result.get("ok") else 400, result)
                return
            if path in ("/api/settings/printer/test",):
                _json_response(self, 200, svc.test_printer_setup(body))
                return
            if path in ("/api/settings/printer/alignment-test",):
                _json_response(self, 200, svc.print_dot_matrix_alignment_test(body))
                return
            if path in ("/api/settings/pharmacy/action",):
                if conn is None:
                    _json_response(self, 503, {"error": "Database not open"})
                    return
                _json_response(self, 200, svc.pharmacy_action(conn, body))
                return
            if path in ("/api/settings/appearance/browse_banner",):
                # The browser picked the file and sent the bytes: the engine is
                # a windowless sidecar built without tkinter and cannot open a
                # dialog of its own. A body means an upload; no body still means
                # the old Tk path, which the Tk build uses.
                if body.get("data_base64"):
                    _json_response(
                        self,
                        200,
                        svc.save_uploaded_home_banner(
                            str(body.get("filename") or ""),
                            str(body.get("data_base64") or ""),
                        ),
                    )
                    return
                _json_response(self, 200, svc.browse_home_banner())
                return
            _json_response(self, 404, {"error": "Not found", "path": path})
        except Exception as exc:
            _json_response(self, 500, _failure_payload(path, exc))


def get_api_base_url() -> str:
    return f"http://127.0.0.1:{_port}"


def reopen_active_store() -> dict[str, Any]:
    """Close current SQLite connection and open the active store (or memory if Online)."""
    from core.store_manager import get_active_db_path, get_active_store_key
    from core.sync_prefs import is_online_mode

    if is_online_mode():
        try:
            conn = _adopt_online_runtime()
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "needs_ui_refresh": False,
                "needs_restart": True,
            }
        try:
            from core.desktop_sync_launch import start_desktop_online_sync

            start_desktop_online_sync(None, "")
        except Exception:
            pass
        # Re-check for a stranded local store DB. This early-returns before the
        # refresh further down, so switching Offline -> Online in one session
        # left migrate_gate holding whatever it said at startup: usually False.
        # Anything recorded while offline then vanished from the UI with no
        # prompt, even though the data was still sitting in the local DB and a
        # Push-to-Server migration was available.
        gate = _online_migrate_status()
        return {
            "ok": True,
            "db_path": "",
            "server_only": True,
            "active_store_key": get_active_store_key() or "",
            "needs_ui_refresh": True,
            "needs_restart": True,
            "needs_migrate": bool(
                gate.get("needs_migrate") and gate.get("has_data")
            ),
            "migrate_message": gate.get("message") or "",
            "message": (
                "Store switched. Online mode uses the server only — "
                "local store database is not opened."
            ),
        }

    _close_current_conn()
    _db["server_only"] = False
    _db["migrate_gate"] = {}

    db_path = get_active_db_path()
    if not db_path or not os.path.isfile(db_path):
        return {
            "ok": False,
            "error": f"Store database not found: {db_path}",
            "needs_ui_refresh": False,
            "needs_restart": True,
        }
    try:
        conn = _open_conn(db_path)
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "needs_ui_refresh": False,
            "needs_restart": True,
        }
    _db["conn"] = conn
    _db["path"] = db_path
    return {
        "ok": True,
        "db_path": db_path,
        "active_store_key": get_active_store_key() or "",
        "needs_ui_refresh": True,
        "needs_restart": True,
        "message": (
            "Store switched and database reopened. "
            "Use Restart App for a full clean reload of all pages."
        ),
    }


def _start_backup_schedule() -> None:
    """Run the Drive backup schedule from the ENGINE, not the UI.

    The Tk app schedules backups itself (main.py: on open, hourly, on close).
    The Tauri shell has no equivalent, so a shop moved onto the new desktop
    silently stopped getting ANY automatic backup -- it only happened if
    someone remembered to press the button. Putting it here means the schedule
    is the same whichever front-end is running, and it keeps working while the
    window is closed.
    """

    def _loop() -> None:
        try:
            from core.backup_manager import (
                is_auto_backup_enabled,
                run_backup_on_open,
                run_backup_silently,
            )
            from core.sync_coordinator import should_run_drive_backup
        except Exception:
            return

        try:
            if should_run_drive_backup() and is_auto_backup_enabled():
                run_backup_on_open()
        except Exception as exc:
            print(f"[backup] on-open backup failed: {exc}", flush=True)

        # How often. Offline is an hour: the copy is of a file already on this
        # disk. Online is six hours, because a backup there first pulls the
        # WHOLE store down from the server (download_store_for_offline) -- with
        # automatic backup now on by default, an hourly loop would have every
        # shop on the fleet doing that around the clock for a file that changes
        # by a few bills.
        while True:
            try:
                from core.sync_prefs import is_online_mode

                every = 6 * 3600 if is_online_mode() else 3600
            except Exception:
                every = 3600
            time.sleep(every)
            try:
                if should_run_drive_backup() and is_auto_backup_enabled():
                    run_backup_silently()
            except Exception as exc:
                print(f"[backup] scheduled backup failed: {exc}", flush=True)

    threading.Thread(target=_loop, daemon=True, name="BackupSchedule").start()


def start_desktop_api(
    conn: Optional[sqlite3.Connection] = None,
    db_path: Optional[str] = None,
    port: int = _DEFAULT_PORT,
) -> str:
    """Start the desktop API server. Returns base URL."""
    global _server, _server_thread, _port

    stop_desktop_api()
    _stop_stale_api_process()

    from core.sync_prefs import is_online_mode

    if is_online_mode():
        try:
            from core.online_migrate import ensure_online_server_only_ready

            gate = ensure_online_server_only_ready(auto_wipe_empty=True)
            _db["migrate_gate"] = gate
            if gate.get("needs_migrate") and gate.get("has_data"):
                print(
                    "Online migrate pending — local store DB still has data.",
                    flush=True,
                )
        except Exception as exc:
            print(f"[online] migrate gate: {exc}", flush=True)
            _db["migrate_gate"] = {}
        if conn is None:
            conn = _open_memory_shell()
        db_path = ""
        _db["server_only"] = True
        try:
            _hydrate_online_memory(conn)
        except Exception:
            pass
    else:
        if db_path is None:
            from core.store_manager import get_active_db_path

            db_path = get_active_db_path()
        if conn is None:
            if not db_path or not os.path.isfile(db_path):
                raise FileNotFoundError(f"Store database not found: {db_path}")
            conn = _open_conn(db_path)
        _db["server_only"] = False
        _db["migrate_gate"] = {}

    _db["conn"] = conn
    _db["path"] = db_path or ""

    last_err = None
    for attempt in range(port, port + 10):
        try:
            _server = _ReusableHTTPServer(("127.0.0.1", attempt), _DesktopApiHandler)
            _port = attempt
            break
        except OSError as exc:
            last_err = exc
            _server = None
    else:
        raise RuntimeError(
            f"Could not start desktop API on ports {port}-{port + 9}: {last_err}"
        )

    _write_pid()
    _server_thread = threading.Thread(
        target=_server.serve_forever, daemon=True, name="DesktopApi"
    )
    _server_thread.start()
    try:
        from core.desktop_sync_launch import start_desktop_online_sync

        start_desktop_online_sync(conn, db_path)
    except Exception:
        pass
    _start_backup_schedule()
    try:
        # the voice pack, when this store has it and voice is on: start its service
        import atexit

        from core import voice_pack

        voice_pack.autostart()
        atexit.register(voice_pack.stop_service)
    except Exception:
        pass
    try:
        from core.sync_prefs import is_online_mode as _online_flush

        if _online_flush():
            from core.online_mutation_queue import kick_flush

            threading.Thread(
                target=kick_flush, daemon=True, name="KickFlush"
            ).start()
    except Exception:
        pass
    return get_api_base_url()


def stop_desktop_api() -> None:
    global _server, _server_thread
    try:
        from core.desktop_sync_launch import stop_desktop_online_sync

        stop_desktop_online_sync()
    except Exception:
        pass
    srv = _server
    _server = None
    if srv is not None:
        try:
            srv.shutdown()
        except Exception:
            pass
        try:
            srv.server_close()
        except Exception:
            pass
    _server_thread = None
    _clear_pid()
    old = _db.get("conn")
    _db["conn"] = None
    if old is not None:
        try:
            old.close()
        except Exception:
            pass


def serve_forever_blocking(port: int = _DEFAULT_PORT) -> str:
    """Start API and block the main thread (for run_desktop_api.py)."""
    url = start_desktop_api(port=port)
    print(f"Desktop API listening on {url}", flush=True)
    print(
        "Endpoints: /api/health  /api/meta  /api/prefs  /api/settings/bundle  "
        "/api/home/dashboard  /api/home/banner  /api/inventory  "
        "/api/sales/history  /api/purchase/history  /api/sales/form  "
        "/api/purchase/form  /api/purchase/calc  /api/purchase/save  "
        "/api/returns/summary",
        flush=True,
    )
    try:
        while _server is not None:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        print("\nStopping desktop API…", flush=True)
    finally:
        stop_desktop_api()
    return url
