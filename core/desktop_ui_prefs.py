"""Preferences for the Tauri desktop UI (stored under AppData)."""
from __future__ import annotations

import json
import os
from typing import Any

_FILENAME = "desktop_ui_prefs.json"

THEME_PACKS = ("classic", "accessible", "modern")

_DEFAULTS: dict[str, Any] = {
    # Show digit shortcuts (0–7) on top nav buttons
    "show_nav_shortcut_keys": True,
    # Color pack: classic | accessible (Gemini) | modern (ChatGPT)
    "theme_pack": "modern",
    # Scroll long table cell text (marquee); off by default
    "table_text_marquee": False,
}


def normalize_theme_pack(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if s in THEME_PACKS:
        return s
    return "modern"


def _prefs_path() -> str:
    from core.license_manager import _appdata_dir

    return os.path.join(_appdata_dir(), _FILENAME)


def load_desktop_ui_prefs() -> dict[str, Any]:
    data = dict(_DEFAULTS)
    path = _prefs_path()
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-8-sig") as fh:
                saved = json.load(fh)
            if isinstance(saved, dict):
                for key in _DEFAULTS:
                    if key in saved:
                        data[key] = saved[key]
    except Exception:
        pass
    data["show_nav_shortcut_keys"] = bool(data.get("show_nav_shortcut_keys", True))
    data["theme_pack"] = normalize_theme_pack(data.get("theme_pack"))
    data["table_text_marquee"] = bool(data.get("table_text_marquee", False))
    return data


def save_desktop_ui_prefs(updates: dict[str, Any]) -> dict[str, Any]:
    data = load_desktop_ui_prefs()
    if "show_nav_shortcut_keys" in updates:
        data["show_nav_shortcut_keys"] = bool(updates["show_nav_shortcut_keys"])
    if "theme_pack" in updates:
        data["theme_pack"] = normalize_theme_pack(updates.get("theme_pack"))
    if "table_text_marquee" in updates:
        data["table_text_marquee"] = bool(updates["table_text_marquee"])
    path = _prefs_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return data
