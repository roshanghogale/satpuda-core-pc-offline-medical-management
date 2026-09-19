"""Preferences for periodic autosave of in-progress sales and purchases."""

import os
import sys

DEFAULT_INTERVAL_SECONDS = 120


def _config_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "VeterinaryApp",
        )
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config",
    )


def _enabled_path() -> str:
    return os.path.join(_config_dir(), "autosave_enabled.txt")


def _interval_path() -> str:
    return os.path.join(_config_dir(), "autosave_interval_seconds.txt")


def load_autosave_enabled() -> bool:
    path = _enabled_path()
    try:
        if os.path.exists(path):
            return open(path, encoding="utf-8").read().strip().lower() not in (
                "0", "false", "no", "off",
            )
    except Exception:
        pass
    return False


def save_autosave_enabled(enabled: bool) -> None:
    os.makedirs(_config_dir(), exist_ok=True)
    with open(_enabled_path(), "w", encoding="utf-8") as f:
        f.write("1" if enabled else "0")


def load_autosave_interval_seconds() -> int:
    path = _interval_path()
    try:
        if os.path.exists(path):
            raw = open(path, encoding="utf-8").read().strip()
            val = int(raw)
            return max(30, min(val, 3600))
    except Exception:
        pass
    return DEFAULT_INTERVAL_SECONDS


def save_autosave_interval_seconds(seconds: int) -> None:
    os.makedirs(_config_dir(), exist_ok=True)
    val = max(30, min(int(seconds), 3600))
    with open(_interval_path(), "w", encoding="utf-8") as f:
        f.write(str(val))
