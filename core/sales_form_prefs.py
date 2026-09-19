"""Preferences for Sales (Billing) screen field layout."""

import os
import sys

POSITION_FIRST = "first"
POSITION_AFTER_BILL_DATE = "after_bill_date"

_POSITION_VALUES = (POSITION_FIRST, POSITION_AFTER_BILL_DATE)


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


def _payment_enabled_path() -> str:
    return os.path.join(_config_dir(), "sales_payment_mode_enabled.txt")


def _payment_position_path() -> str:
    return os.path.join(_config_dir(), "sales_payment_mode_position.txt")


def load_payment_mode_enabled() -> bool:
    path = _payment_enabled_path()
    try:
        if os.path.exists(path):
            return open(path, encoding="utf-8").read().strip().lower() not in (
                "0", "false", "no", "off",
            )
    except Exception:
        pass
    return True


def save_payment_mode_enabled(enabled: bool) -> None:
    os.makedirs(_config_dir(), exist_ok=True)
    with open(_payment_enabled_path(), "w", encoding="utf-8") as f:
        f.write("1" if enabled else "0")


def load_payment_mode_position() -> str:
    path = _payment_position_path()
    try:
        if os.path.exists(path):
            raw = open(path, encoding="utf-8").read().strip().lower()
            if raw in _POSITION_VALUES:
                return raw
    except Exception:
        pass
    return POSITION_FIRST


def save_payment_mode_position(position: str) -> None:
    pos = (position or "").strip().lower()
    if pos not in _POSITION_VALUES:
        pos = POSITION_FIRST
    os.makedirs(_config_dir(), exist_ok=True)
    with open(_payment_position_path(), "w", encoding="utf-8") as f:
        f.write(pos)


def payment_mode_position_label(position: str) -> str:
    if position == POSITION_AFTER_BILL_DATE:
        return "After bill date (customer name first)"
    return "First (before customer name)"
