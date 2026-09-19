"""Which optional features are compiled into this EXE (standard vs Win7-lite)."""
from __future__ import annotations

import os
import sys
from typing import Optional

_PROFILE: Optional[str] = None


def _read_profile() -> str:
    global _PROFILE
    if _PROFILE is not None:
        return _PROFILE
    candidates: list[str] = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.append(os.path.join(sys._MEIPASS, "config", "build_profile.txt"))
    dev = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config",
        "build_profile.txt",
    )
    candidates.append(dev)
    for path in candidates:
        if os.path.isfile(path):
            try:
                raw = open(path, encoding="utf-8").read().strip().lower()
                _PROFILE = raw or "standard"
                return _PROFILE
            except OSError:
                pass
    _PROFILE = "standard"
    return _PROFILE


def is_lite_build() -> bool:
    """Release / Win7 / Win8 folder builds — no voice, Gemini, or Server."""
    return _read_profile() in ("win7", "win8", "release")


def is_release_build() -> bool:
    return _read_profile() == "release"


def is_win7_lite_build() -> bool:
    return is_lite_build()


def is_voice_supported() -> bool:
    return not is_lite_build()


def is_gemini_supported() -> bool:
    return not is_lite_build()


def is_server_sync_supported() -> bool:
    """Online sync uses Satpuda Core Server (not bundled in lite builds)."""
    return not is_lite_build()


def is_no_log_build() -> bool:
    """Release and lite portable builds write no log files."""
    return _read_profile() in ("win7", "win8", "release")
