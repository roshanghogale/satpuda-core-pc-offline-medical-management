"""Optional app login gate for the Tauri desktop shell.

The login is the store's (core/login_prefs.py): its settings row in the open store, so it
is checked against this PC's own copy -- offline-first and with no internet alike.
"""
from __future__ import annotations

from typing import Any


def get_app_login_status(conn=None) -> dict[str, Any]:
    from core.login_prefs import _enabled, load_login_prefs

    prefs = load_login_prefs(conn)
    enabled = _enabled(prefs)
    return {
        "ok": True,
        "enabled": enabled,
        "username_hint": (prefs.get("username") or "").strip() if enabled else "",
    }


def verify_app_login(body: dict[str, Any], conn=None) -> dict[str, Any]:
    from core.login_prefs import is_login_enabled, verify_login

    if not is_login_enabled(conn):
        return {"ok": True, "authenticated": True}
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if not username or not password:
        return {"ok": False, "error": "Username and password are required."}
    if verify_login(username, password, conn):
        return {"ok": True, "authenticated": True}
    return {"ok": False, "error": "Invalid username or password."}
