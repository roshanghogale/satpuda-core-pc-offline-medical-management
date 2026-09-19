"""Satpuda Core Private Limited brand assets — theme-aware logos."""
from __future__ import annotations

import os
import sys
from typing import Optional

COMPANY_NAME = "Satpuda Core Private Limited"
PRODUCT_TAGLINE = "Medical Management Software · Satpuda Core Private Limited"
APP_TITLE_BASE = "Satpuda Core Private Limited"

# Filenames in assets/ (spaces preserved)
_ICON_LIGHT = "Logo 01.png"
_ICON_DARK = "Logo 03.png"
_LOGO_LIGHT = "Logo 02.png"
_LOGO_DARK = "Logo 04.png"
_FALLBACK_PNG = "satpuda_logo.png"
_FALLBACK_ICO = "satpuda_logo.ico"
_HOME_BANNER = "home_banner.png"


def assets_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "assets")
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets",
    )


def asset_path(filename: str) -> str:
    return os.path.join(assets_dir(), filename)


def _exists(filename: str) -> Optional[str]:
    p = asset_path(filename)
    return p if os.path.isfile(p) else None


def is_dark_theme(theme_name: Optional[str] = None) -> bool:
    name = (theme_name or "").strip()
    if not name:
        try:
            from core.app_prefs import load_theme
            name = load_theme()
        except Exception:
            name = "navy-light"
    if name.endswith("-dark") or name.endswith("_dark"):
        return True
    if name.endswith("-light") or name.endswith("_light"):
        return False
    try:
        from core.custom_themes import CUSTOM_THEMES
        meta = CUSTOM_THEMES.get(name) or {}
        return str(meta.get("type", "")).lower() == "dark"
    except Exception:
        return "dark" in name.lower()


def get_icon_png(theme_name: Optional[str] = None) -> str:
    dark = is_dark_theme(theme_name)
    preferred = _ICON_DARK if dark else _ICON_LIGHT
    return (
        _exists(preferred)
        or _exists(_ICON_LIGHT if dark else _ICON_DARK)
        or _exists(_FALLBACK_PNG)
        or asset_path(preferred)
    )


def get_full_logo_png(theme_name: Optional[str] = None) -> str:
    dark = is_dark_theme(theme_name)
    preferred = _LOGO_DARK if dark else _LOGO_LIGHT
    return (
        _exists(preferred)
        or _exists(_LOGO_LIGHT if dark else _LOGO_DARK)
        or _exists(_FALLBACK_PNG)
        or asset_path(preferred)
    )


def get_home_banner_asset() -> str:
    """Home page banner — always assets/home_banner.png (replace file to update)."""
    return _exists(_HOME_BANNER) or asset_path(_HOME_BANNER)


def ensure_theme_ico(theme_name: Optional[str] = None) -> str:
    """
    Build/cache a .ico from the theme icon PNG under AppData.
    Falls back to bundled satpuda_logo.ico.
    """
    base = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "VeterinaryApp",
    )
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        pass

    dark = is_dark_theme(theme_name)
    ico_name = "satpuda_icon_dark.ico" if dark else "satpuda_icon_light.ico"
    dst = os.path.join(base, ico_name)
    png = get_icon_png(theme_name)

    try:
        if os.path.isfile(png):
            need = (not os.path.isfile(dst)) or (
                os.path.getmtime(png) > os.path.getmtime(dst)
            )
            if need:
                from PIL import Image
                img = Image.open(png).convert("RGBA")
                # Multi-size ICO for Windows title bar / taskbar
                sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)]
                img.save(dst, format="ICO", sizes=sizes)
            if os.path.isfile(dst):
                return dst
    except Exception:
        pass

    bundled = _exists(_FALLBACK_ICO)
    if bundled:
        return bundled
    return dst if os.path.isfile(dst) else ""
