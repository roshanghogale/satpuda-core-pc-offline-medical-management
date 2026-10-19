"""Mobile import server + JSON apply for the desktop API."""
from __future__ import annotations

import json
import os
from typing import Any, Optional

_last_raw: str = ""
_last_data: Optional[dict[str, Any]] = None


def _on_receive(raw: str, data: dict) -> None:
    global _last_raw, _last_data
    _last_raw = raw
    _last_data = data if isinstance(data, dict) else {}


def _qr_for_url(url: str) -> tuple[str, str]:
    """(png_data_uri, error) for the receive URL -- never raises.

    Classic draws this same QR in its own window and shows a "type the URL
    instead" note when qrcode/PIL are missing (ui/shared/import_from_mobile.py).
    The Tauri panel cannot draw one itself, so the engine hands it the picture
    and, when it cannot, the reason to put on screen.
    """
    if not url:
        return "", ""
    try:
        from core.qr_image import SCREEN_BOX_SIZE, qr_png_data_uri

        return qr_png_data_uri(url, box_size=SCREEN_BOX_SIZE), ""
    except Exception as exc:  # qrcode/PIL missing, or a broken install
        return "", str(exc)


def _store_kind() -> str:
    """'wholesale' when the PC's open store is a wholesale store, else 'retail'."""
    try:
        from core.wholesale_mode import is_wholesale

        return "wholesale" if is_wholesale() else "retail"
    except Exception:
        return "retail"


def _store_name() -> str:
    try:
        from core.store_manager import get_active_display_name

        return str(get_active_display_name() or "").strip()
    except Exception:
        return ""


def phone_catalog() -> dict[str, Any]:
    """What the phone entry page reads from /catalog.json: the shop's suppliers, schedules and
    types (the Web Entry page's catalog) plus the store kind and its Rate A/B/C margins.

    Runs on the receiver's own thread, so it opens its own connection to the store file
    (the engine's connection belongs to the engine's thread) and closes it again.
    """
    out: dict[str, Any] = {"ok": True, "store_kind": _store_kind(), "store_name": _store_name()}
    conn = None
    try:
        import sqlite3

        from core.store_manager import get_active_db_path

        path = get_active_db_path()
        if path and os.path.isfile(path):
            conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
    except Exception:
        conn = None
    try:
        from core.web_purchase_server import build_runtime_catalog

        cat = build_runtime_catalog(conn)
        out.update({k: v for k, v in cat.items() if k != "ok"})
    except Exception as exc:
        out["catalog_error"] = str(exc)
    try:
        from core.wholesale_mode import DEFAULT_MARGINS, margins

        out["margins"] = margins(conn) if conn is not None else dict(DEFAULT_MARGINS)
    except Exception:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return out


def _address_problem(url: str, ip: str) -> str:
    """Why a phone could not use this address, in the shop's words; "" when it can."""
    if not url:
        return ""
    if not ip or ip.startswith("127."):
        return (
            "This PC is not connected to any WiFi or network, so the address only works on "
            "this PC. Connect the PC to the shop WiFi (or the phone's hotspot) and press "
            "Start WiFi receiver again."
        )
    return ""


def _answer(url: str, port: int, running: bool) -> dict[str, Any]:
    from core.mobile_import_server import get_lan_ip, page_ready

    ip = get_lan_ip() if running else ""
    qr, qr_error = _qr_for_url(url)
    return {
        "ok": True,
        "running": running,
        "url": url,
        "ip": ip,
        "port": port if running else 0,
        "qr": qr,
        "qr_error": qr_error,
        "problem": _address_problem(url, ip),
        "store_kind": _store_kind() if running else "",
        "page_ready": page_ready() if running else False,
    }


def mobile_server_status() -> dict[str, Any]:
    from core.mobile_import_apply import preview_mobile_data
    from core.mobile_import_server import current_port, get_receive_url, is_running

    preview = None
    if _last_data:
        try:
            preview = preview_mobile_data(_last_data)
        except Exception:
            preview = None
    running = is_running()
    url = get_receive_url(current_port()) if running else ""
    out = _answer(url, current_port(), running)
    out.update(
        {
            "has_payload": bool(_last_data),
            "last_preview": preview,
            "last_raw": _last_raw if _last_data else "",
        }
    )
    return out


def start_mobile_server() -> dict[str, Any]:
    from core.mobile_import_server import set_catalog_provider, start_mobile_import_server

    set_catalog_provider(phone_catalog)
    url, port = start_mobile_import_server(_on_receive)
    return _answer(url, port, True)


def open_mobile_server() -> dict[str, Any]:
    """The Mobile Import screen was opened: the receiver runs and its QR is on screen.

    The QR used to appear only after someone pressed "Start WiFi receiver" -- the screen
    opened on two buttons and an empty box, which is the "QR is not visible" the owner
    kept reporting. Opening the screen now starts the receiver when it is not running
    and answers with the same status (QR included) either way. Never raises: a receiver
    that cannot start answers ok=False with the reason, for the screen to show.
    """
    from core.mobile_import_server import is_running

    try:
        if not is_running():
            start_mobile_server()
        return mobile_server_status()
    except Exception as exc:
        reason = f"The WiFi receiver could not start on this PC: {exc}"
        return {"ok": False, "running": False, "url": "", "qr": "", "qr_error": "",
                "problem": reason, "error": reason}


def stop_mobile_server() -> dict[str, Any]:
    from core.mobile_import_server import stop_mobile_import_server

    stop_mobile_import_server()
    return {"ok": True}


def preview_mobile(body: dict[str, Any]) -> dict[str, Any]:
    from core.mobile_import_apply import parse_mobile_json, preview_mobile_data

    if body.get("use_last_received") and _last_data:
        return preview_mobile_data(_last_data)
    raw = str(body.get("json") or body.get("raw") or "").strip()
    if not raw and isinstance(body.get("data"), dict):
        return preview_mobile_data(body["data"])
    if not raw:
        return {"ok": False, "error": "Paste JSON or receive from phone first."}
    return parse_mobile_json(raw)


def _forget_received() -> None:
    """The receiver's payload was imported: it is not offered again.

    It used to stay in memory until the engine restarted, so the Mobile Import screen
    opened again (or in a second window) offered the same bills once more under
    "Received ... press Import" -- a second press imported them twice.
    """
    global _last_raw, _last_data
    _last_raw = ""
    _last_data = None


def import_mobile(conn, body: dict[str, Any]) -> dict[str, Any]:
    from core.mobile_import_apply import apply_mobile_data, parse_mobile_json

    if body.get("use_last_received") and _last_data:
        result = apply_mobile_data(conn, _last_data)
        if result.get("ok"):
            _forget_received()
        return result
    if isinstance(body.get("data"), dict):
        return apply_mobile_data(conn, body["data"])
    raw = str(body.get("json") or body.get("raw") or "").strip()
    if not raw:
        return {"ok": False, "error": "Nothing to import."}
    parsed = parse_mobile_json(raw)
    if not parsed.get("ok"):
        return parsed
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": str(exc)}
    result = apply_mobile_data(conn, data)
    if result.get("ok") and _last_data and raw == (_last_raw or "").strip():
        _forget_received()
    return result
