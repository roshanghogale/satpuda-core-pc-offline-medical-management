"""Gemini App Tutor config — online Gemini + bundled offline FAQ."""
from __future__ import annotations

import os
import sys

_DEFAULT_ENABLED = True
_DEFAULT_WINDOW_HEIGHT = 560
_MIN_WINDOW_HEIGHT = 420
_MAX_WINDOW_HEIGHT = 900


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


def _enabled_path() -> str:
    return os.path.join(_config_dir(), "gemini_tutor_enabled.txt")


def _height_path() -> str:
    return os.path.join(_config_dir(), "gemini_tutor_window_height.txt")


def _clamp_window_height(value) -> int:
    try:
        height = int(float(value))
    except (TypeError, ValueError):
        height = _DEFAULT_WINDOW_HEIGHT
    return max(_MIN_WINDOW_HEIGHT, min(height, _MAX_WINDOW_HEIGHT))


def _read_enabled_flag() -> bool:
    path = _enabled_path()
    if not os.path.isfile(path):
        return _DEFAULT_ENABLED
    try:
        raw = open(path, encoding="utf-8").read().strip().lower()
        return raw not in ("0", "false", "no", "off")
    except OSError:
        return _DEFAULT_ENABLED


def is_tutor_enabled() -> bool:
    if not _read_enabled_flag():
        return False
    from core.build_features import is_gemini_supported
    from core.offline_tutor import offline_faq_loaded

    if is_gemini_supported():
        return True
    return offline_faq_loaded()


def set_tutor_enabled(enabled: bool) -> None:
    with open(_enabled_path(), "w", encoding="utf-8") as f:
        f.write("1" if enabled else "0")


def load_tutor_window_height() -> int:
    path = _height_path()
    if not os.path.isfile(path):
        return _DEFAULT_WINDOW_HEIGHT
    try:
        raw = open(path, encoding="utf-8").read().strip()
    except OSError:
        return _DEFAULT_WINDOW_HEIGHT
    return _clamp_window_height(raw)


def save_tutor_window_height(height) -> int:
    value = _clamp_window_height(height)
    with open(_height_path(), "w", encoding="utf-8") as f:
        f.write(str(value))
    return value


def tutor_availability_message() -> str:
    from core.build_features import is_gemini_supported
    from core.gemini_bill_config import (
        is_gemini_configured,
        is_gemini_package_available,
    )
    from core.offline_tutor import offline_faq_loaded

    offline_ok = offline_faq_loaded()

    if not is_tutor_enabled():
        return (
            "Satpuda AI is turned off.\n\n"
            "Enable it in Settings - Data and System - My Assist."
        )

    if not is_gemini_supported():
        if offline_ok:
            return ""
        return "Satpuda AI is not available on this build."

    if not is_gemini_configured():
        if offline_ok:
            return ""
        return (
            "Gemini API key not found.\n\n"
            "Add your key in Settings - Import - Gemini AI for online answers.\n"
            "Offline FAQ works without a key when internet is unavailable."
        )

    if not is_gemini_package_available():
        if offline_ok:
            return ""
        return "Gemini client is not available on this build."

    return ""