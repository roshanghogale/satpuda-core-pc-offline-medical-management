"""Optional app login gate for the Tauri desktop shell."""
from __future__ import annotations

from typing import Any


def get_app_login_status() -> dict[str, Any]:
    from core.login_prefs import is_login_enabled, load_login_prefs

    enabled = is_login_enabled()
    prefs = load_login_prefs()
    return {
        "ok": True,
        "enabled": enabled,
        "username_hint": (prefs.get("username") or "").strip() if enabled else "",
    }


def verify_app_login(body: dict[str, Any]) -> dict[str, Any]:
    from core.login_prefs import is_login_enabled, verify_login

    if not is_login_enabled():
        return {"ok": True, "authenticated": True}
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if not username or not password:
        return {"ok": False, "error": "Username and password are required."}
    if verify_login(username, password):
        return {"ok": True, "authenticated": True}
    return {"ok": False, "error": "Invalid username or password."}
