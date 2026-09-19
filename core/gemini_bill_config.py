"""Gemini API settings for bill photo import (key stored locally, never in source)."""

from __future__ import annotations

import os
import sys

_DEFAULT_ENABLED = True


def _config_dir() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "VeterinaryApp",
        )
    else:
        base = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config",
        )
    os.makedirs(base, exist_ok=True)
    return base


def _key_path() -> str:
    return os.path.join(_config_dir(), "gemini_api_key.txt")


def _enabled_path() -> str:
    return os.path.join(_config_dir(), "gemini_bill_enabled.txt")


def _default_schedule_path() -> str:
    return os.path.join(_config_dir(), "import_default_schedule.txt")


def load_import_default_schedule() -> str:
    """Fallback schedule when Gemini cannot answer (empty = leave non-scheduled)."""
    path = _default_schedule_path()
    if os.path.isfile(path):
        try:
            raw = open(path, encoding="utf-8").read().strip().upper()
            if raw == "":
                return ""
            if raw in ("H", "H1", "X", "G", "K", "C", "C1", "P", "N", "M"):
                return raw
        except OSError:
            pass
    return ""


def save_import_default_schedule(schedule: str) -> None:
    with open(_default_schedule_path(), "w", encoding="utf-8") as f:
        f.write((schedule or "").strip().upper())


def _bundled_key_path() -> str:
    """Key shipped with EXE / repo — not shown in Settings."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, "config", "gemini_api_key.txt")
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config",
        "gemini_api_key.txt",
    )


def _read_key_file(path: str) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8-sig") as f:
            return f.read().strip()
    except OSError:
        return ""


def load_gemini_api_key() -> str:
    # Optional AppData override, then the key baked into EXE / project config.
    user_key = _read_key_file(_key_path())
    if user_key:
        return user_key
    bundled = _read_key_file(_bundled_key_path())
    if bundled:
        return bundled
    return (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()


def save_gemini_api_key(key: str) -> None:
    path = _key_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write((key or "").strip())


def is_gemini_enabled() -> bool:
    from core.build_features import is_gemini_supported

    if not is_gemini_supported():
        return False
    path = _enabled_path()
    if not os.path.isfile(path):
        return _DEFAULT_ENABLED
    try:
        raw = open(path, encoding="utf-8").read().strip().lower()
        return raw not in ("0", "false", "no", "off")
    except OSError:
        return _DEFAULT_ENABLED


def set_gemini_enabled(enabled: bool) -> None:
    with open(_enabled_path(), "w", encoding="utf-8") as f:
        f.write("1" if enabled else "0")


def _sdk_supports_bill_import() -> bool:
    """Modern google-generativeai (>=0.8) exposes GenerativeModel; Win7 3.8 only has 0.1.0rc1."""
    try:
        import google.generativeai as genai  # noqa: F401
        return hasattr(genai, "GenerativeModel")
    except ImportError:
        return False


def is_gemini_package_available() -> bool:
    """True when bill import can run: REST client (always) or modern SDK."""
    from core.gemini_rest_client import is_rest_client_available

    return is_rest_client_available() or _sdk_supports_bill_import()


def is_gemini_configured() -> bool:
    return bool(load_gemini_api_key())


def gemini_availability_message() -> str:
    if not is_gemini_configured():
        return "Bill photo import is not available in this build (the AI key is missing)."
    return ""


def bill_photo_import_message() -> str:
    """
    Return an error message when bill photo import cannot run, else empty string.
    Bill photos use Gemini in the cloud — internet is required.
    """
    from core.build_features import is_gemini_supported

    if not is_gemini_supported():
        return (
            "Bill photo import is not included in this Windows 7 build.\n\n"
            "Use PDF, Excel, or CSV import instead."
        )
    if not is_gemini_enabled():
        return (
            "Bill photo import reads the photo online (internet required).\n\n"
            "Enable it in Settings → Import → Bill photo reading."
        )
    pkg = gemini_availability_message()
    if pkg:
        return pkg
    return ""
