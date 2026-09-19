"""Mobile import server + JSON apply for the desktop API."""
from __future__ import annotations

import json
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


def mobile_server_status() -> dict[str, Any]:
    from core.mobile_import_apply import preview_mobile_data
    from core.mobile_import_server import current_port, get_lan_ip, get_receive_url, is_running

    preview = None
    if _last_data:
        try:
            preview = preview_mobile_data(_last_data)
        except Exception:
            preview = None
    url = get_receive_url(current_port()) if is_running() else ""
    qr, qr_error = _qr_for_url(url)
    return {
        "ok": True,
        "running": is_running(),
        "url": url,
        "ip": get_lan_ip(),
        "port": current_port() if is_running() else 0,
        "qr": qr,
        "qr_error": qr_error,
        "has_payload": bool(_last_data),
        "last_preview": preview,
        "last_raw": _last_raw if _last_data else "",
    }


def start_mobile_server() -> dict[str, Any]:
    from core.mobile_import_server import start_mobile_import_server

    url, port = start_mobile_import_server(_on_receive)
    qr, qr_error = _qr_for_url(url)
    return {"ok": True, "url": url, "port": port, "qr": qr, "qr_error": qr_error}


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


def import_mobile(conn, body: dict[str, Any]) -> dict[str, Any]:
    from core.mobile_import_apply import apply_mobile_data, parse_mobile_json

    if body.get("use_last_received") and _last_data:
        return apply_mobile_data(conn, _last_data)
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
    return apply_mobile_data(conn, data)
