"""Sales Return page preferences (Settings → Management → Sales Return)."""

from __future__ import annotations

import os
import sys

DEFAULT_LOOKUP_DAYS = 90
MIN_LOOKUP_DAYS = 1
MAX_LOOKUP_DAYS = 365


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


def _days_path() -> str:
    return os.path.join(_config_dir(), "sales_return_lookup_days.txt")


def load_sales_return_lookup_days() -> int:
    try:
        path = _days_path()
        if os.path.exists(path):
            val = int(open(path, encoding="utf-8").read().strip())
            return max(MIN_LOOKUP_DAYS, min(val, MAX_LOOKUP_DAYS))
    except Exception:
        pass
    return DEFAULT_LOOKUP_DAYS


def save_sales_return_lookup_days(days: int) -> int:
    os.makedirs(_config_dir(), exist_ok=True)
    val = max(MIN_LOOKUP_DAYS, min(int(days), MAX_LOOKUP_DAYS))
    with open(_days_path(), "w", encoding="utf-8") as f:
        f.write(str(val))
    return val
